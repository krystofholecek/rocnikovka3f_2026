from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Document:
    """Jeden dokument ze zdroje: článek, kniha, příspěvek…"""

    source: str  # např. "wikipedia", "gutenberg"
    source_id: str  # ID v rámci zdroje (název článku, číslo knihy…)
    date: str  # datum dokumentu ve formátu ISO (YYYY-MM-DD)
    text: str
    title: str = ""
    url: str = ""
