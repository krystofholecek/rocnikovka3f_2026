"""Knihy PG-19 (DeepMind): ~28 700 knih z Projektu Gutenberg vydaných před rokem 1919.

Oficiální zdroj: https://github.com/google-deepmind/pg19
Texty leží na Google Storage (https://storage.googleapis.com/deepmind-gutenberg/),
seznamy souborů pro train/validation/test jsou na Hugging Face (deepmind/pg19).

Stažení (asi 11,7 GB, odhad):
    python -m llm2005.download pg19 D:\\llm2005-raw\\pg19
Import do databáze:
    python -m llm2005.ingest pg19 D:\\llm2005-raw\\pg19

Očekávaná složka:
    metadata.csv              řádky: id,název,rok vydání,url (bez hlavičky)
    train/10.txt, validation/…, test/…

Datum = rok vydání z metadata.csv. Kniha bez roku dostane prázdné datum,
takže ji ingest odmítne (tabulka `rejected`, důvod no_date).
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

from ..document import Document
from ._io import fetch

SPLITS = ("train", "validation", "test")
ASSET_ROOT_URL = "https://storage.googleapis.com/deepmind-gutenberg/"
SPLIT_LIST_URL = "https://huggingface.co/datasets/deepmind/pg19/resolve/main/data/{split}_files.txt"


def read_metadata(path: Path) -> dict[str, dict]:
    """Načte metadata.csv -> {id knihy: {title, year, url}}."""
    meta = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 4:
                continue
            book_id, title, year, url = row[0], row[1], row[2].strip(), row[3]
            meta[book_id] = {"title": title, "year": year, "url": url}
    return meta


def clean_year(year: str) -> str:
    """Vrátí rok jako text 'RRRR', nebo '' když rok chybí či nedává smysl."""
    if year.isdigit() and 1000 <= int(year) <= 9999:
        return year
    return ""


def read_pg19(path: str) -> Iterator[Document]:
    root = Path(path)
    meta = read_metadata(root / "metadata.csv")
    for split in SPLITS:
        folder = root / split
        if not folder.is_dir():
            continue
        for file in sorted(folder.glob("*.txt")):
            book_id = file.stem
            m = meta.get(book_id, {})
            # Windows konce řádků (\r\n) převedeme na obyčejné \n
            text = file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()
            yield Document(
                source="pg19",
                source_id=book_id,
                title=m.get("title", ""),
                date=clean_year(m.get("year", "")),
                text=text,
                url=m.get("url", ""),
            )


# ---------- stažení ----------

_fetch = fetch  # společná funkce ze _io.py


def download_pg19(dest: str, workers: int = 16) -> None:
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    _fetch(ASSET_ROOT_URL + "metadata.csv", root / "metadata.csv")
    files = []
    for split in SPLITS:
        list_file = root / f"{split}_files.txt"
        _fetch(SPLIT_LIST_URL.format(split=split), list_file)
        files += list_file.read_text(encoding="utf-8").split()
    print(f"PG-19: {len(files)} knih ke stažení do {root}", flush=True)

    done = total = 0
    with ThreadPoolExecutor(workers) as pool:
        jobs = [pool.submit(_fetch, ASSET_ROOT_URL + f, root / f) for f in files]
        for job in jobs:
            total += job.result()
            done += 1
            if done % 1000 == 0 or done == len(files):
                print(f"{done:>6}/{len(files)} hotovo, nově staženo {total / 1e9:.2f} GB", flush=True)
