"""Obecný zdroj: soubor JSON Lines, jeden dokument na řádek.

Každý řádek musí mít: source, source_id, date, text (title, url volitelně).
Hodí se pro knihy, Usenet atd., když si je nejdřív převedeme do tohoto formátu.
"""
from __future__ import annotations

import json
from typing import Iterator

from ..document import Document
from ._io import open_text


def read_jsonl(path: str) -> Iterator[Document]:
    with open_text(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            yield Document(
                source=r["source"],
                source_id=str(r["source_id"]),
                date=str(r.get("date") or ""),
                text=r["text"],
                title=r.get("title", ""),
                url=r.get("url", ""),
            )
