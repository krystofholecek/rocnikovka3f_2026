"""Zdroje dat. Každý zdroj je funkce: cesta -> iterátor Document.

Nový zdroj = nový soubor v této složce + záznam do SOURCES.
"""
from .jsonl import read_jsonl
from .pg19 import read_pg19
from .wikipedia import read_wikipedia_history

SOURCES = {
    "wikipedia": read_wikipedia_history,
    "jsonl": read_jsonl,
    "pg19": read_pg19,
}
