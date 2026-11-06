"""Zdroje dat. Každý zdroj je funkce: cesta -> iterátor Document.

Nový zdroj = nový soubor v této složce + záznam do SOURCES.
Klíč (název na příkazové řádce) je jen z malých písmen – tak ho propustí most .bridge.
"""
from .arxiv import read_arxiv
from .blogs import read_blogs
from .courtlistener import read_courtlistener
from .enron import read_enron
from .jsonl import read_jsonl
from .mediawiki import (
    read_wikibooks,
    read_wikinews,
    read_wikiquote,
    read_wikisource,
    read_wikitalk,
)
from .pg19 import read_pg19
from .usenet import read_usenet
from .wikipedia import read_wikipedia_history

SOURCES = {
    "wikipedia": read_wikipedia_history,
    "jsonl": read_jsonl,
    "pg19": read_pg19,
    "enron": read_enron,
    "blogs": read_blogs,
    "arxiv": read_arxiv,
    "wikitalk": read_wikitalk,  # v databázi jako source "wikipedia-talk"
    "wikibooks": read_wikibooks,
    "wikinews": read_wikinews,
    "wikiquote": read_wikiquote,
    "wikisource": read_wikisource,
    "usenet": read_usenet,
    "courtlistener": read_courtlistener,
}
