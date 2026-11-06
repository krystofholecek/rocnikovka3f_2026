"""Vlastní BPE tokenizer natrénovaný jen na naší databázi (žádný cizí slovník).

    python -m llm2005.tokenizer                 # vyvážená směs podle configs/mix.json, slovník 32 000
    python -m llm2005.tokenizer --max-chars 5000000 --out data/demo/tok.json   # rychlá zkouška

Postup (fáze, které ukazuje živý přehled na http://localhost:8800):
1. Vzorkování  – z databáze vybereme vyváženou směs (llm2005/mix.py) a uložíme ji
                 do data/tokenizer-sample/ (jeden soubor JSONL na zdroj).
2. Trénink 1/2 – knihovna `tokenizers` projde vzorek a spočítá, jak často je které „slovo“.
3. Trénink 2/2 – z četností postupně skládá nejčastější dvojice do nových tokenů (BPE merges),
                 dokud slovník nemá 32 000 tokenů. Tahle část průběh nehlásí, ukazujeme odhad.
4. Uložení     – data/tokenizer.json
5. Kontrola    – data/progress/tokenizer-report.json (llm2005/tokenizer_report.py)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Iterator

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from .mix import DEFAULT_CONFIG, MixConfig, iter_mix, load_mix_config
from .progress import Progress

EOT = "<|endoftext|>"  # oddělovač dokumentů

PHASES = [
    ("sampling", "Vzorkování (výběr směsi z databáze)", 20),
    ("counting", "Trénink 1/2: počítání slov", 45),
    ("merges", "Trénink 2/2: skládání tokenů (BPE merges)", 25),
    ("saving", "Uložení tokenizéru", 1),
    ("check", "Kontrola (report)", 9),
]
# Hrubý odhad: skládání tokenů trvá asi takový díl času počítání slov (jen pro ETA, je to odhad).
MERGE_TIME_RATIO = 0.5
TRAIN_BATCH = 1000  # kolik dokumentů posíláme knihovně najednou


def build_tokenizer() -> Tokenizer:
    """Prázdný byte-level BPE: každý text se dá zapsat aspoň po bajtech, nic není „neznámé“."""
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    return tok


def make_trainer(vocab_size: int) -> trainers.BpeTrainer:
    return trainers.BpeTrainer(
        vocab_size=vocab_size,
        special_tokens=[EOT],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        show_progress=False,  # vlastní průběh je v data/progress/
    )


# ---------------------------------------------------------------- 1) vzorek

def _cfg_dict(cfg: MixConfig) -> dict:
    return {"seed": cfg.seed, "max_chars": cfg.max_chars, "weights": cfg.weights, "whole": cfg.whole}


def load_manifest(sample_dir: Path, cfg: MixConfig) -> dict | None:
    """Hotový vzorek se stejným nastavením se dá použít znovu (ušetří čas)."""
    f = sample_dir / "manifest.json"
    if not f.exists():
        return None
    m = json.loads(f.read_text(encoding="utf-8"))
    return m if m.get("complete") and m.get("config") == _cfg_dict(cfg) else None


def write_sample(db_path: str, cfg: MixConfig, sample_dir: Path, progress: Progress) -> dict:
    """Vybere směs z databáze a uloží ji do sample_dir/<zdroj>.jsonl (1 řádek = 1 dokument)."""
    sample_dir.mkdir(parents=True, exist_ok=True)
    (sample_dir / "manifest.json").unlink(missing_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    files: dict = {}
    stats: dict[str, dict] = {}
    total = 0
    t_last = 0.0

    def on_plan(source: str, info: dict) -> None:
        stats.setdefault(source, {"docs": 0, "chars": 0})
        stats[source]["target_chars"] = info["target_chars"]
        progress.source(source, target_chars=info["target_chars"], available_docs=info["available_docs"],
                        whole=info["whole"])
        cil = "celý zdroj" if info["whole"] else f"cíl {info['target_chars'] / 1e6:,.1f} mil. znaků"
        progress.log(f"Vzorkuji {source}: {cil} ({info['available_docs']:,} dokumentů v DB)")

    try:
        for source, _doc_id, text in iter_mix(conn, cfg, on_plan):
            if source not in files:
                files[source] = open(sample_dir / f"{source}.jsonl", "w", encoding="utf-8")
            files[source].write(json.dumps(text, ensure_ascii=False) + "\n")
            n = len(text)
            stats[source]["docs"] += 1
            stats[source]["chars"] += n
            total += n
            progress.add(n)
            progress.source_add(source, sampled_chars=n, sampled_docs=1)
            now = time.time()
            if now - t_last > 1:
                t_last = now
                rate = progress.phase_rate()
                left = max(0, cfg.max_chars - total)
                progress.set_fraction(total / cfg.max_chars, eta_s=left / rate if rate else None)
    finally:
        for f in files.values():
            f.close()
        conn.close()
    manifest = {"config": _cfg_dict(cfg), "sources": stats, "total_chars": total,
                "total_docs": sum(s["docs"] for s in stats.values()), "complete": True,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (sample_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def iter_sample(sample_dir: Path, sources: list[str]) -> Iterator[tuple[str, str]]:
    """Projde uložený vzorek: (zdroj, text)."""
    for s in sources:
        with open(sample_dir / f"{s}.jsonl", encoding="utf-8") as f:
            for line in f:
                yield s, json.loads(line)


# ---------------------------------------------------------------- 2) trénink

class _MergeTicker(threading.Thread):
    """Během skládání tokenů knihovna nic nehlásí – posouváme odhad podle uplynulého času."""

    def __init__(self, progress: Progress):
        super().__init__(daemon=True)
        self.progress = progress
        self.estimate_s = 60.0
        self.stop = threading.Event()
        self.t0 = time.time()

    def run(self) -> None:
        while not self.stop.wait(1.0):
            el = time.time() - self.t0
            if el < self.estimate_s:
                self.progress.set_fraction(0.97 * el / self.estimate_s, eta_s=self.estimate_s - el, estimate=True,
                                           note="knihovna průběh nehlásí – procenta i ETA jsou odhad")
            else:
                self.progress.set_fraction(0.97, eta_s=None, estimate=True,
                                           note=f"trvá déle než odhad ({self.estimate_s / 60:.0f} min) – počkej")


def train_on_sample(sample_dir: Path, manifest: dict, vocab_size: int, progress: Progress) -> Tokenizer:
    sources = [s for s, st in manifest["sources"].items() if st["docs"] > 0]
    total = max(1, manifest["total_chars"])
    state = {"chars": 0, "t_last": 0.0}
    ticker = _MergeTicker(progress)

    def report(batch: list[str], per_source: dict[str, list[int]]) -> None:
        n = sum(map(len, batch))
        state["chars"] += n
        progress.add(n)
        for s, (c, d) in per_source.items():
            progress.source_add(s, trained_chars=c, trained_docs=d)
        now = time.time()
        if now - state["t_last"] > 1:
            state["t_last"] = now
            rate = progress.phase_rate()
            progress.set_fraction(state["chars"] / total, eta_s=(total - state["chars"]) / rate if rate else None)

    def batches() -> Iterator[list[str]]:
        batch: list[str] = []
        per_source: dict[str, list[int]] = {}
        for s, text in iter_sample(sample_dir, sources):
            batch.append(text)
            acc = per_source.setdefault(s, [0, 0])
            acc[0] += len(text)
            acc[1] += 1
            if len(batch) >= TRAIN_BATCH:
                report(batch, per_source)
                yield batch
                batch, per_source = [], {}
        if batch:
            report(batch, per_source)
            yield batch
        # vše předáno knihovně -> začíná skládání tokenů (běží v Rustu, Python vlákna jedou dál)
        count_s = time.time() - progress.current["started"]
        ticker.estimate_s = max(30.0, count_s * MERGE_TIME_RATIO)
        progress.phase("merges", note=f"odhad ~{ticker.estimate_s / 60:.0f} min")
        ticker.t0 = time.time()
        ticker.start()

    tok = build_tokenizer()
    progress.phase("counting", note=f"{total / 1e9:.2f} mld. znaků, {manifest['total_docs']:,} dokumentů")
    try:
        tok.train_from_iterator(batches(), trainer=make_trainer(vocab_size), length=manifest["total_docs"])
    finally:
        ticker.stop.set()
    progress.log(f"Trénink hotov, slovník {tok.get_vocab_size():,} tokenů")
    return tok


# ---------------------------------------------------------------- celé

def train_mixed(db_path: str, cfg: MixConfig, vocab_size: int, out: Path, sample_dir: Path,
                progress_dir: Path, resample: bool = False, eval_chars: int = 2_000_000) -> dict:
    from .tokenizer_report import build_report, print_report

    progress = Progress(progress_dir / "tokenizer-status.json", "Trénink BPE tokenizéru", PHASES).start()
    progress.extra = {"vocab_target": vocab_size, "max_chars": cfg.max_chars, "seed": cfg.seed,
                      "out": str(out), "report": None, "result": None}
    t0 = time.time()
    try:
        progress.log(f"Start: slovník {vocab_size:,}, směs max {cfg.max_chars / 1e9:.2f} mld. znaků, seed {cfg.seed}")
        manifest = None if resample else load_manifest(sample_dir, cfg)
        if manifest:
            progress.skip("sampling", "použit hotový vzorek se stejným nastavením")
            progress.log(f"Používám hotový vzorek {sample_dir} ({manifest['total_chars']:,} znaků)")
            for s, st in manifest["sources"].items():
                progress.source(s, target_chars=st.get("target_chars"), sampled_chars=st["chars"],
                                sampled_docs=st["docs"])
        else:
            progress.phase("sampling")
            manifest = write_sample(db_path, cfg, sample_dir, progress)
            progress.log(f"Vzorek hotov: {manifest['total_chars']:,} znaků, {manifest['total_docs']:,} dokumentů")
        for s, st in manifest["sources"].items():
            share = 100 * st["chars"] / max(1, manifest["total_chars"])
            progress.source(s, share_percent=round(share, 2))

        tok = train_on_sample(sample_dir, manifest, vocab_size, progress)
        train_s = time.time() - t0

        progress.phase("saving")
        out.parent.mkdir(parents=True, exist_ok=True)
        tok.save(str(out))
        progress.log(f"Uloženo: {out}")

        progress.phase("check")
        report = build_report(tok, sample_dir, manifest, eval_chars=eval_chars,
                              extra={"tokenizer_path": str(out), "train_seconds": round(train_s)},
                              on_source=lambda s: progress.log(f"Kontrola: {s}"))
        report_path = progress_dir / "tokenizer-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        progress.extra["report"] = report_path.name
        progress.extra["result"] = {"vocab_size": report["vocab_size"],
                                    "chars_per_token": report["chars_per_token"]["mix_weighted"],
                                    "total_seconds": round(time.time() - t0)}
        print_report(report)
        progress.log(f"Hotovo za {(time.time() - t0) / 60:.1f} min")
        progress.finish("done")
        return report
    except BaseException as e:
        progress.log(f"CHYBA: {e!r}")
        progress.finish("error", error="".join(traceback.format_exception_only(e)).strip())
        raise


def main() -> None:
    p = argparse.ArgumentParser(description="Trénink vlastního BPE tokenizéru na vyvážené směsi")
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--mix", default=DEFAULT_CONFIG, help="konfigurace směsi")
    p.add_argument("--vocab", type=int, default=32000)
    p.add_argument("--max-chars", type=int, default=None, help="přepíše max_chars z konfigurace")
    p.add_argument("--out", default="data/tokenizer.json")
    p.add_argument("--sample-dir", default="data/tokenizer-sample")
    p.add_argument("--progress-dir", default="data/progress")
    p.add_argument("--resample", action="store_true", help="vytvořit vzorek znovu, i když existuje")
    p.add_argument("--eval-chars", type=int, default=2_000_000, help="kolik znaků na zdroj pro kontrolu")
    a = p.parse_args()
    cfg = load_mix_config(a.mix)
    if a.max_chars:
        cfg.max_chars = a.max_chars
    train_mixed(a.db, cfg, a.vocab, Path(a.out), Path(a.sample_dir), Path(a.progress_dir),
                resample=a.resample, eval_chars=a.eval_chars)


if __name__ == "__main__":
    main()
