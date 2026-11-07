"""Analýza šumu v datech: kolik textu by čištění (llm2005/clean.py) odstranilo, podle zdroje.

    python -m llm2005.clean_report                  # vzorek: prvních 20 000 dokumentů každého zdroje
    python -m llm2005.clean_report --docs 5000

Čte uložený vzorek směsi (data/tokenizer-sample/<zdroj>.jsonl). Ten je už náhodně zamíchaný
(seed 2005), takže začátek souboru je náhodný vzorek zdroje. Výsledek:
data/progress/cleaning-report.json (zobrazí ho přehled na http://localhost:8800).

Kromě čištění počítá i „ASCII art“ (aspoň 3 řádky skoro jen ze symbolů) – ten se nemaže,
jen se v něm zkracují dlouhé řady stejných znaků.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .clean import MIN_CHARS, RULES, clean_text


def _is_art_line(line: str) -> bool:
    """Řádek „ASCII artu“: aspoň 10 znaků a aspoň 60 % z nich nejsou písmena, číslice ani mezery."""
    s = line.strip()
    if len(s) < 10:
        return False
    sym = sum(1 for ch in s if not ch.isalnum() and not ch.isspace())
    return sym / len(s) >= 0.6


def analyze_source(path: Path, source: str, max_docs: int) -> dict:
    r = {"source": source, "docs": 0, "chars": 0, "chars_after": 0, "dropped_docs": 0, "dropped_chars": 0,
         "ascii_art_docs": 0, "removed": {k: 0 for k in RULES}, "docs_with": {k: 0 for k in RULES}}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if r["docs"] >= max_docs:
                break
            text = json.loads(line)
            st: dict = {}
            out = clean_text(text, source, st)
            r["docs"] += 1
            r["chars"] += len(text)
            r["chars_after"] += len(out)
            for k, v in st.items():
                r["removed"][k] += v
                r["docs_with"][k] += 1
            if len(out) < MIN_CHARS:
                r["dropped_docs"] += 1
                r["dropped_chars"] += len(text)
            if sum(1 for ln in text.split("\n") if _is_art_line(ln)) >= 3:
                r["ascii_art_docs"] += 1
    r["removed_chars"] = r["chars"] - r["chars_after"]
    r["removed_percent"] = round(100 * r["removed_chars"] / max(1, r["chars"]), 3)
    return r


def build(sample_dir: Path, max_docs: int) -> dict:
    sources = sorted(p.stem for p in sample_dir.glob("*.jsonl"))
    per = []
    for s in sources:
        t0 = time.time()
        per.append(analyze_source(sample_dir / f"{s}.jsonl", s, max_docs))
        print(f"{s:<16} {per[-1]['docs']:>7,} dok.  odstraněno {per[-1]['removed_percent']:>6.2f} %  "
              f"({time.time() - t0:.0f} s)", flush=True)
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sample": f"prvních max. {max_docs:,} dokumentů každého zdroje z {sample_dir.as_posix()} (před čištěním)",
            "min_chars": MIN_CHARS, "rules": RULES, "per_source": per}


def merge_into_report(path: Path, key: str, value) -> None:
    """Přidá/přepíše jednu část cleaning-report.json (ostatní části nechá)."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Analýza šumu v datech (co odstraní čištění)")
    p.add_argument("--sample-dir", default="data/tokenizer-sample")
    p.add_argument("--docs", type=int, default=20_000, help="max. dokumentů na zdroj")
    p.add_argument("--out", default="data/progress/cleaning-report.json")
    a = p.parse_args()
    report = build(Path(a.sample_dir), a.docs)
    out = Path(a.out)
    for k, v in report.items():
        merge_into_report(out, k, v)
    dates = Path("data/progress/date-fix.json")
    if dates.exists():
        merge_into_report(out, "dates", json.loads(dates.read_text(encoding="utf-8")))
    print(f"Uloženo: {out}")


if __name__ == "__main__":
    main()
