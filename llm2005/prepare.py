"""Převede vyváženou směs na tokeny pro trénink: data/train.bin a data/val.bin.

    python -m llm2005.prepare                    # směs podle configs/mix.json, průběh na http://localhost:8800
    python -m llm2005.prepare --max-chars 5000000 --out-dir data/demo   # rychlá zkouška

Postup:
1. Texty = STEJNÁ směs jako pro tokenizér (llm2005/mix.py, seed 2005), vyčištěná clean_text.
   Když existuje hotový vzorek tokenizéru (data/tokenizer-sample/) se stejným nastavením,
   vezme se rovnou on (je to přesně ta směs, už vyčištěná). Jinak se směs vybere z databáze znovu.
2. Každý dokument se převede na tokeny a za něj se přidá <|endoftext|>.
3. Náhoda se seedem 2005 pošle 0,5 % dokumentů do val.bin, zbytek do train.bin
   (celé dokumenty, takže validační text model při tréninku nikdy neuvidí).
4. Tokeny se ukládají jako uint16 (2 bajty na token; stačí pro slovník < 65 536).
Soubory se zapisují jako *.bin.tmp a přejmenují se až na konci – nedokončený běh nic nepřepíše.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Iterator

import numpy as np
from tokenizers import Tokenizer

from .mix import DEFAULT_CONFIG, MixConfig, SourceCursor, iter_mix, load_mix_config
from .progress import Progress
from .tokenizer import EOT, iter_sample, load_manifest

PHASES = [
    ("load", "Příprava (tokenizér, plán směsi)", 1),
    ("tokenize", "Tokenizace dokumentů", 97),
    ("finish", "Uložení train.bin / val.bin", 2),
]
BATCH = 1000  # dokumentů na jedno volání encode_batch (běží paralelně v Rustu)


def iter_texts(db_path: str, cfg: MixConfig, sample_dir: Path, progress: Progress,
               cursors: dict[str, SourceCursor]) -> tuple[Iterator[tuple[str, str]], int, str]:
    """Vrátí (iterátor (zdroj, text), plánovaný počet znaků, popis původu textů)."""
    manifest = load_manifest(sample_dir, cfg)
    if manifest:
        for s, st in manifest["sources"].items():
            progress.source(s, target_chars=st["chars"], target_docs=st["docs"])
        sources = [s for s, st in manifest["sources"].items() if st["docs"] > 0]
        return iter_sample(sample_dir, sources), manifest["total_chars"], f"hotový vyčištěný vzorek {sample_dir}"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def on_plan(source: str, info: dict) -> None:
        progress.source(source, target_chars=info["target_chars"], whole=info["whole"])

    def gen() -> Iterator[tuple[str, str]]:
        try:
            for s, _id, text in iter_mix(conn, cfg, on_plan, clean=True, cursors=cursors):
                yield s, text
        finally:
            conn.close()

    return gen(), cfg.max_chars, "výběr z databáze (s čištěním)"


def prepare(db_path: str, cfg: MixConfig, tokenizer_path: str, out_dir: Path, sample_dir: Path,
            progress_dir: Path, val_fraction: float = 0.005, seed: int = 2005) -> dict:
    progress = Progress(progress_dir / "prepare-status.json", "Příprava dat pro trénink (prepare)", PHASES,
                        unit="tokenů/s").start()
    progress.extra = {"kind": "prepare", "out_dir": str(out_dir), "val_fraction": val_fraction, "seed": seed,
                      "docs": 0, "chars": 0, "tokens": {"train": 0, "val": 0}, "docs_split": {"train": 0, "val": 0},
                      "mb_written": 0.0, "result": None}
    t0 = time.time()
    tmp = {s: out_dir / f"{s}.bin.tmp" for s in ("train", "val")}
    files: dict = {}
    try:
        progress.phase("load")
        tok = Tokenizer.from_file(tokenizer_path)
        assert tok.get_vocab_size() < 2**16, "uint16 stačí jen pro slovník < 65 536"
        eot = tok.token_to_id(EOT)
        cursors: dict[str, SourceCursor] = {}
        texts, total_chars, origin = iter_texts(db_path, cfg, sample_dir, progress, cursors)
        progress.extra["origin"] = origin
        progress.extra["planned_chars"] = total_chars
        progress.log(f"Tokenizér {tokenizer_path} ({tok.get_vocab_size():,} tokenů), texty: {origin}")
        progress.log(f"Plán: ~{total_chars / 1e9:.2f} mld. znaků, val {100 * val_fraction:.1f} % dokumentů, seed {seed}")

        out_dir.mkdir(parents=True, exist_ok=True)
        files = {s: open(p, "wb") for s, p in tmp.items()}
        rng = random.Random(seed)
        ex = progress.extra
        state = {"t_last": 0.0}

        def flush(batch: list[tuple[str, str]]) -> None:
            encs = tok.encode_batch([t for _, t in batch])
            out: dict[str, list[np.ndarray]] = {"train": [], "val": []}
            per_source: dict[str, list[int]] = {}
            for (s, text), enc in zip(batch, encs):
                split = "val" if rng.random() < val_fraction else "train"
                arr = np.array(enc.ids + [eot], dtype=np.uint16)
                out[split].append(arr)
                ex["tokens"][split] += len(arr)
                ex["docs_split"][split] += 1
                acc = per_source.setdefault(s, [0, 0, 0])
                acc[0] += 1
                acc[1] += len(text)
                acc[2] += len(arr)
            for split, arrs in out.items():
                if arrs:
                    np.concatenate(arrs).tofile(files[split])
            n_tok = sum(len(a) for arrs in out.values() for a in arrs)
            ex["docs"] += len(batch)
            ex["chars"] += sum(len(t) for _, t in batch)
            ex["mb_written"] = round(2 * (ex["tokens"]["train"] + ex["tokens"]["val"]) / 1e6, 1)
            progress.add(n_tok)
            for s, (d, c, tk) in per_source.items():
                progress.source_add(s, done_docs=d, done_chars=c, tokens=tk)
            now = time.time()
            if now - state["t_last"] > 1:
                state["t_last"] = now
                el = now - progress.current["started"]
                frac = min(1.0, ex["chars"] / max(1, total_chars))
                eta = el * (1 - frac) / frac if frac > 0 else None
                progress.set_fraction(frac, eta_s=eta)

        progress.phase("tokenize", note=origin)
        batch: list[tuple[str, str]] = []
        for item in texts:
            batch.append(item)
            if len(batch) >= BATCH:
                flush(batch)
                batch = []
        if batch:
            flush(batch)

        progress.phase("finish")
        for f in files.values():
            f.close()
        for s, p in tmp.items():
            os.replace(p, out_dir / f"{s}.bin")
        if cursors:  # směs se brala z databáze -> statistiky čištění
            ex["cleaning"] = {s: c.stats() for s, c in cursors.items()}
        sizes = {s: (out_dir / f"{s}.bin").stat().st_size for s in ("train", "val")}
        result = {"tokens_train": ex["tokens"]["train"], "tokens_val": ex["tokens"]["val"],
                  "docs_train": ex["docs_split"]["train"], "docs_val": ex["docs_split"]["val"],
                  "bytes_train": sizes["train"], "bytes_val": sizes["val"], "chars": ex["chars"],
                  "chars_per_token": round(ex["chars"] / max(1, ex["tokens"]["train"] + ex["tokens"]["val"]), 3),
                  "total_seconds": round(time.time() - t0)}
        ex["result"] = result
        progress.log(f"train.bin: {result['tokens_train']:,} tokenů ({sizes['train'] / 1e6:,.1f} MB), "
                     f"val.bin: {result['tokens_val']:,} tokenů ({sizes['val'] / 1e6:,.1f} MB)")
        progress.log(f"Hotovo za {(time.time() - t0) / 60:.1f} min")
        progress.finish("done")
        return result
    except BaseException as e:
        for f in files.values():
            f.close()
        progress.log(f"CHYBA: {e!r}")
        progress.finish("error", error="".join(traceback.format_exception_only(e)).strip())
        raise


def main() -> None:
    p = argparse.ArgumentParser(description="Směs -> tokeny: data/train.bin a data/val.bin")
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--mix", default=DEFAULT_CONFIG)
    p.add_argument("--max-chars", type=int, default=None, help="přepíše max_chars z konfigurace")
    p.add_argument("--tokenizer", default="data/tokenizer.json")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--sample-dir", default="data/tokenizer-sample")
    p.add_argument("--progress-dir", default="data/progress")
    p.add_argument("--val-fraction", type=float, default=0.005)
    a = p.parse_args()
    cfg = load_mix_config(a.mix)
    if a.max_chars:
        cfg.max_chars = a.max_chars
    r = prepare(a.db, cfg, a.tokenizer, Path(a.out_dir), Path(a.sample_dir), Path(a.progress_dir),
                val_fraction=a.val_fraction, seed=cfg.seed)
    print(json.dumps(r, indent=1))
    print(f"train: {r['tokens_train']:,} tokenů ({r['bytes_train'] / 1e6:,.1f} MB), "
          f"val: {r['tokens_val']:,} tokenů ({r['bytes_val'] / 1e6:,.1f} MB)")


if __name__ == "__main__":
    main()
