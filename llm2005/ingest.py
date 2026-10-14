"""Hlavní řetězec: ZDROJ -> KONTROLA DATA -> DATABÁZE.

Použití:
    python -m llm2005.ingest wikipedia cesta/k/historii.xml
    python -m llm2005.ingest stats
"""
from __future__ import annotations

import argparse
import time
from typing import Iterable

from .datecheck import CUTOFF, is_allowed
from .db import Database
from .document import Document
from .sources import SOURCES

MIN_CHARS = 200  # kratší texty (pahýly, rozcestníky) nemají pro trénink cenu


def ingest(docs: Iterable[Document], db: Database, log_every: int = 10_000) -> dict:
    counts = {"added": 0, "too_new": 0, "no_date": 0, "too_short": 0, "duplicate": 0}
    t0 = time.time()
    for i, doc in enumerate(docs, 1):
        if not doc.date:
            counts["no_date"] += 1
            db.reject(doc, "no_date")
        elif not is_allowed(doc.date):
            counts["too_new"] += 1
            db.reject(doc, "too_new")
        elif len(doc.text) < MIN_CHARS:
            counts["too_short"] += 1
        elif db.add(doc):
            counts["added"] += 1
        else:
            counts["duplicate"] += 1
        if i % log_every == 0:
            db.commit()
            print(f"{i:>10} zpracováno  {counts}  ({time.time() - t0:.0f} s)", flush=True)
    db.commit()
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description=f"Naplní databázi dokumenty do {CUTOFF}.")
    p.add_argument("source", choices=[*SOURCES, "stats"])
    p.add_argument("path", nargs="?", help="vstupní soubor zdroje")
    p.add_argument("--db", default="data/llm2005.sqlite")
    a = p.parse_args()

    db = Database(a.db)
    if a.source != "stats":
        if not a.path:
            p.error("chybí cesta ke vstupnímu souboru")
        print(ingest(SOURCES[a.source](a.path), db))
    for source, n, chars, dmin, dmax in db.stats():
        print(f"{source:<12} {n:>9} dokumentů  {chars or 0:>14,} znaků  {dmin} až {dmax}")
    db.close()


if __name__ == "__main__":
    main()
