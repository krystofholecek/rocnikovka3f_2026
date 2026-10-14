"""Zdroje dat. Každý zdroj je funkce: cesta -> iterátor Document.

Nový zdroj = nový soubor v této složce + záznam do SOURCES.
"""
from .jsonl import read_jsonl
from .wikipedia import read_wikipedia_history

SOURCES = {
    "wikipedia": read_wikipedia_history,
    "jsonl": read_jsonl,
}
