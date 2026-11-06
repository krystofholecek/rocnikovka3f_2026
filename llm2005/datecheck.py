"""Kontrola data: do databáze smí jen dokumenty s datem do konce roku 2005.

Datum uvedené u zdroje bereme jako pravdivé (rozhodnutí týmu).
"""
from __future__ import annotations

from datetime import date, datetime

CUTOFF = date(2005, 12, 31)

# „Nulová“ data: počítač neznal čas a zapsal výchozí hodnotu. Nejsou to skutečná data.
# - 1969-12-31 / 1970-01-01: unixový čas 0 nebo -1 (chyba). V Usenetu je v hlavičce
#   doslova "Date: Wed, 31-Dec-69 18:59:59 EDT" = čas -1, tedy „datum nejde zjistit“.
# - 1979-12-31 / 1980-01-01: nula ve formátu DOS/Outlook (1. 1. 1980 0:00 UTC, v USA ještě 31. 12. 1979).
PLACEHOLDER_DATES = {"1969-12-31", "1970-01-01", "1979-12-31", "1980-01-01"}


def is_placeholder(iso_date: str) -> bool:
    """True = datum je výchozí „nulová“ hodnota (viz PLACEHOLDER_DATES), ne skutečné datum."""
    return iso_date[:10] in PLACEHOLDER_DATES


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
