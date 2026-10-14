"""Wikipedie ve stavu k 31. 12. 2005.

Vstup: XML s historií revizí (pages-meta-history), např. záloha ze 16. 8. 2006:
https://archive.org/details/enwiki-20060816-pages-meta-history.xml.7z

Soubor je obří, proto ho čteme průběžně (streamujeme) a nic nerozbalujeme na disk:
    7z x -so enwiki-20060816-pages-meta-history.xml.7z | python -m llm2005.ingest wikipedia -

U každého článku vezmeme poslední revizi s datem <= CUTOFF.
Články založené až po CUTOFF nemají žádnou takovou revizi, takže vypadnou samy.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Iterator

from ..datecheck import CUTOFF, to_date
from ..document import Document
from ._io import open_binary

SKIP_PREFIXES = (
    "Wikipedia:", "Talk:", "User:", "User talk:", "Template:", "Template talk:", "Category:",
    "Category talk:", "Image:", "Image talk:", "Portal:", "Portal talk:", "Help:", "MediaWiki:",
    "Wikipedia talk:", "File:", "Special:",
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def clean_wikitext(t: str) -> str:
    """Hrubé odstranění wiki značek. Stačí pro trénink, není to dokonalý parser."""
    t = html.unescape(t)
    for _ in range(3):  # vnořené šablony {{ ... {{ ... }} ... }}
        t = re.sub(r"\{\{[^{}]*\}\}", "", t)
    t = re.sub(r"\{\|.*?\|\}", "", t, flags=re.S)  # tabulky
    t = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", t, flags=re.S)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\[\[(?:Image|File|Category|[a-z\-]{2,12}):[^\]]*\]\]", "", t)
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", t)
    t = re.sub(r"\[https?://\S+ ?([^\]]*)\]", r"\1", t)
    t = re.sub(r"'{2,}", "", t)
    t = re.sub(r"^=+\s*(.*?)\s*=+\s*$", r"\1", t, flags=re.M)
    t = re.sub(r"^[*#:;]+\s*", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def read_wikipedia_history(path: str, cutoff=CUTOFF) -> Iterator[Document]:
    title = None
    best_ts = best_text = None  # poslední revize do cutoff
    for event, el in ET.iterparse(open_binary(path), events=("end",)):
        tag = _local(el.tag)
        if tag == "title":
            title = el.text or ""
        elif tag == "revision":
            ts = text = None
            for child in el:
                name = _local(child.tag)
                if name == "timestamp":
                    ts = child.text
                elif name == "text":
                    text = child.text or ""
            d = to_date(ts)
            if d is not None and d <= cutoff and (best_ts is None or ts > best_ts):
                best_ts, best_text = ts, text
            el.clear()
        elif tag == "page":
            if (
                title
                and best_text is not None
                and not title.startswith(SKIP_PREFIXES)
                and not best_text.lstrip().lower().startswith("#redirect")
            ):
                yield Document(
                    source="wikipedia",
                    source_id=title,
                    title=title,
                    date=to_date(best_ts).isoformat(),
                    text=f"{title}\n\n{clean_wikitext(best_text)}",
                    url="https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                )
            title = best_ts = best_text = None
            el.clear()
