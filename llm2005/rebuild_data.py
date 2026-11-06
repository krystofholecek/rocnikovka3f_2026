"""Celý řetězec po čištění dat, jedna úloha za druhou:

    python -m llm2005.rebuild_data
    python -m llm2005.rebuild_data --max-chars 3000000 --work-dir data/demo   # rychlá zkouška v malém

1. tokenizér znovu (vyčištěná směs podle configs/mix.json) + report + porovnání se starým v1,
2. prepare -> train.bin / val.bin (stejná směs, seed 2005, val 0,5 %),
3. krátký zkušební trénink nejmenšího modelu (configs/tiny.json, pár minut) – jen ověřit,
   že trénink z train.bin běží a loss klesá. Výsledek: data/progress/train-check.json.

Proč jedna úloha: úlohy z .bridge/inbox startují souběžně, ale tyhle kroky na sobě závisí.
Průběh všech kroků ukazuje přehled na http://localhost:8800.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .mix import DEFAULT_CONFIG, load_mix_config
from .prepare import prepare
from .tokenizer import train_mixed

LOSS = re.compile(r"krok\s+(\d+)\s+loss ([\d.]+)")
VAL = re.compile(r"krok\s+(\d+)\s+val loss ([\d.]+)")


def train_check(config: str, data_dir: Path, progress_dir: Path, hours: float, max_steps: int) -> dict:
    """Spustí krátký trénink a z výpisu vytáhne loss na začátku a na konci."""
    out_dir = Path("checkpoints") / f"tiny-check-{time.strftime('%Y%m%d-%H%M%S')}"
    cmd = [sys.executable, "-m", "llm2005.train", "--config", config, "--data-dir", str(data_dir),
           "--hours", str(hours), "--max-steps", str(max_steps), "--out-dir", str(out_dir)]
    print("Spouštím:", " ".join(cmd), flush=True)
    t0 = time.time()
    losses, vals, lines = [], [], []
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace") as proc:
        for line in proc.stdout:  # type: ignore[union-attr]
            print(line, end="", flush=True)
            lines.append(line.rstrip())
            m = VAL.search(line)
            if m:
                vals.append([int(m.group(1)), float(m.group(2))])
                continue
            m = LOSS.search(line)
            if m:
                losses.append([int(m.group(1)), float(m.group(2))])
    result = {"config": config, "out_dir": str(out_dir), "returncode": proc.returncode,
              "seconds": round(time.time() - t0), "train_loss": losses, "val_loss": vals,
              "first_loss": losses[0] if losses else None, "last_loss": losses[-1] if losses else None,
              "loss_decreased": bool(losses) and losses[-1][1] < losses[0][1],
              "log_tail": lines[-8:], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    progress_dir.mkdir(parents=True, exist_ok=True)
    (progress_dir / "train-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="Tokenizér -> prepare -> krátký test tréninku")
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--mix", default=DEFAULT_CONFIG)
    p.add_argument("--max-chars", type=int, default=None, help="přepíše max_chars (pro zkoušku v malém)")
    p.add_argument("--work-dir", default="data", help="kam ukládat tokenizer.json, vzorek a *.bin")
    p.add_argument("--progress-dir", default="data/progress")
    p.add_argument("--vocab", type=int, default=32000)
    p.add_argument("--check-config", default="configs/tiny.json")
    p.add_argument("--check-hours", type=float, default=0.05, help="délka zkušebního tréninku (0.05 h = 3 min)")
    p.add_argument("--check-steps", type=int, default=20000)
    p.add_argument("--skip-tokenizer", action="store_true")
    a = p.parse_args()

    cfg = load_mix_config(a.mix)
    if a.max_chars:
        cfg.max_chars = a.max_chars
    work, prog = Path(a.work_dir), Path(a.progress_dir)
    tok_path, sample_dir = work / "tokenizer.json", work / "tokenizer-sample"
    t0 = time.time()

    if not a.skip_tokenizer:
        print("\n===== 1/3 Tokenizér =====", flush=True)
        train_mixed(a.db, cfg, a.vocab, tok_path, sample_dir, prog, resample=False,
                    compare_with=Path("data/tokenizer-v1.json"))

    print("\n===== 2/3 Prepare =====", flush=True)
    r = prepare(a.db, cfg, str(tok_path), work, sample_dir, prog, val_fraction=0.005, seed=cfg.seed)
    print(f"train: {r['tokens_train']:,} tokenů ({r['bytes_train'] / 1e6:,.1f} MB), "
          f"val: {r['tokens_val']:,} tokenů ({r['bytes_val'] / 1e6:,.1f} MB)", flush=True)

    print("\n===== 3/3 Zkušební trénink (tiny) =====", flush=True)
    c = train_check(a.check_config, work, prog, a.check_hours, a.check_steps)
    print(f"\nLoss: začátek {c['first_loss']}, konec {c['last_loss']}, klesá: {c['loss_decreased']}")
    print(f"Celý řetězec hotov za {(time.time() - t0) / 60:.1f} min")
    if c["returncode"] != 0:
        sys.exit(c["returncode"])


if __name__ == "__main__":
    main()
