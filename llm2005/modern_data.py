"""Stažení a import zdrojů s moderním jazykem – jedna dlouhá úloha, kroky jdou PO SOBĚ.

Použití (přes most .bridge jako jeden *.run.txt):
    python -m llm2005.modern_data
    python -m llm2005.modern_data --only usenet wikitalk   # jen vybrané kroky

Proč jedna úloha: SQLite snese jen jeden zapisující proces, takže importy
nesmí běžet souběžně. Kroky jsou opakovatelné: stažené soubory se znovu nestahují
a zdroj, který už v databázi je, se znovu neimportuje.

Před každým stahováním kontrolujeme limit místa pro surová data (RAW_LIMIT_GB).
"""
from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

from .db import Database
from .download import DOWNLOADERS
from .ingest import ingest
from .sources import SOURCES

RAW = Path(r"D:\llm2005-raw")
RAW_LIMIT_GB = 60
# kolik GB si krok stažení nejvýš vezme (odhad podle velikosti souborů na serveru)
DOWNLOAD_GB = {"wikimedia": 0.4, "utzoo": 3.5, "courtlistener": 10.0}
WIKIMEDIA = {
    "wikibooks": "enwikibooks-20061223-pages-meta-history.xml.7z",
    "wikinews": "enwikinews-20061223-pages-meta-history.xml.7z",
    "wikiquote": "enwikiquote-20061223-pages-meta-history.xml.7z",
    "wikisource": "enwikisource-20061224-pages-meta-history.xml.7z",
}

# (název kroku, stáhnout, [(zdroj, cesta, název zdroje v databázi)])
STEPS = [
    ("wikimedia", "wikimedia",
     [(k, RAW / "wikimedia" / v, k) for k, v in WIKIMEDIA.items()]),
    ("usenet", "utzoo", [("usenet", RAW / "utzoo", "usenet")]),
    ("wikitalk", None,
     [("wikitalk", RAW / "enwiki-20061104-pages-meta-history.xml.7z", "wikipedia-talk")]),
    ("courtlistener", "courtlistener",
     [("courtlistener", RAW / "courtlistener", "courtlistener")]),
]


def folder_gb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e9


def has_source(db: Database, source: str) -> bool:
    return db.conn.execute("SELECT 1 FROM documents WHERE source = ? LIMIT 1", (source,)).fetchone() is not None


def main() -> None:
    p = argparse.ArgumentParser(description="Stáhne a naimportuje moderní zdroje (postupně).")
    p.add_argument("--only", nargs="*", help="jen tyto kroky: " + ", ".join(s[0] for s in STEPS))
    p.add_argument("--db", default="data/llm2005.sqlite")
    a = p.parse_args()

    db = Database(a.db)
    for name, downloader, imports in STEPS:
        if a.only and name not in a.only:
            continue
        print(f"\n=== {name} ===", flush=True)
        if downloader:
            used = folder_gb(RAW)
            free = shutil.disk_usage(RAW).free / 1e9
            need = DOWNLOAD_GB[downloader]
            print(f"surová data {used:.1f} GB z limitu {RAW_LIMIT_GB} GB, volno na disku {free:.0f} GB", flush=True)
            if used + need > RAW_LIMIT_GB or free < need + 5:
                print(f"PŘESKOČENO: stažení {downloader} (~{need} GB) by překročilo limit místa", flush=True)
                continue
            DOWNLOADERS[downloader](str(RAW / downloader))
        for source, path, db_source in imports:
            if has_source(db, db_source):
                print(f"{db_source}: už je v databázi, import přeskočen", flush=True)
                continue
            if not path.exists():
                print(f"{db_source}: chybí {path}, import přeskočen", flush=True)
                continue
            t0 = time.time()
            counts = ingest(SOURCES[source](str(path)), db, log_every=50_000)
            print(f"{db_source}: {counts}  ({(time.time() - t0) / 60:.0f} min)", flush=True)

    print("\n=== stav databáze ===", flush=True)
    for source, n, chars, dmin, dmax in db.stats():
        print(f"{source:<15} {n:>9} dokumentů  {chars or 0:>14,} znaků  {dmin} až {dmax}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
