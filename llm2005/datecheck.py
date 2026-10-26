"""Kontrola data: do databáze smí jen dokumenty s datem do konce roku 2005.

Datum uvedené u zdroje bereme jako pravdivé (rozhodnutí týmu).
"""
from __future__ import annotations

from datetime import date, datetime

CUTOFF = date(2005, 12, 31)


def to_date(value: date | datetime | str | None) -> date | None:
    """Převede datum z různých formátů. Vrací None, když datum chybí nebo nejde přečíst."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 4 and text.isdigit():  # jen rok, např. "1899"
        return date(int(text), 12, 31)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def is_allowed(value: date | datetime | str | None, cutoff: date = CUTOFF) -> bool:
    """True = dokument je z doby do cutoff (včetně). Bez data dokument nebereme."""
    d = to_date(value)
    return d is not None and d <= cutoff
