"""Stažení surových dat zdroje na disk (pak se importují přes llm2005.ingest).

Použití:
    python -m llm2005.download pg19 D:\\llm2005-raw\\pg19
    python -m llm2005.download enron D:\\llm2005-raw\\enron
    python -m llm2005.download blogs D:\\llm2005-raw\\blogs
    python -m llm2005.download arxiv D:\\llm2005-raw\\arxiv
    python -m llm2005.download wikimedia D:\\llm2005-raw\\wikimedia
    python -m llm2005.download utzoo D:\\llm2005-raw\\utzoo
    python -m llm2005.download courtlistener D:\\llm2005-raw\\courtlistener
"""
from __future__ import annotations

import argparse

from .sources.arxiv import download_arxiv
from .sources.blogs import download_blogs
from .sources.courtlistener import download_courtlistener
from .sources.enron import download_enron
from .sources.mediawiki import download_wikimedia
from .sources.pg19 import download_pg19
from .sources.usenet import download_utzoo

DOWNLOADERS = {
    "pg19": download_pg19,
    "enron": download_enron,
    "blogs": download_blogs,
    "arxiv": download_arxiv,
    "wikimedia": download_wikimedia,
    "utzoo": download_utzoo,
    "courtlistener": download_courtlistener,
}


def main() -> None:
    p = argparse.ArgumentParser(description="Stáhne surová data zdroje.")
    p.add_argument("source", choices=list(DOWNLOADERS))
    p.add_argument("dest", help="cílová složka, např. D:\\llm2005-raw\\pg19")
    a = p.parse_args()
    DOWNLOADERS[a.source](a.dest)


if __name__ == "__main__":
    main()
