"""Obecné čtení dumpů MediaWiki (Wikipedie a sesterské projekty) s přísným pravidlem data.

Používá se pro:
* diskuze anglické Wikipedie (jmenné prostory Talk, User talk, Wikipedia, Wikipedia talk)
  ze zálohy enwiki-20061104-pages-meta-history.xml.7z -> source "wikipedia-talk",
* Wikibooks, Wikinews, Wikiquote a Wikisource ze záloh z prosince 2006
  (https://dumps.wikimedia.org/archive/2006/2006-12/) -> source "wikibooks" atd.

Pravidlo data (přísné): datum dokumentu = datum POSLEDNÍ revize stránky v dumpu.
Když stránku někdo upravil až po 31. 12. 2005, celá stránka se odmítne (too_new),
protože text v té podobě v roce 2005 ještě neexistoval. (Články Wikipedie se dřív
importovaly jinak – jako stav k 31. 12. 2005, viz wikipedia.py.)

Jmenné prostory poznáme podle předpony názvu (např. "Talk:"). Seznam předpon
a jejich čísel je přímo v dumpu v části <siteinfo><namespaces>.

Import (archiv se nerozbaluje, čte se proudem):
    7z x -so "D:\\llm2005-raw\\enwiki-20061104-pages-meta-history.xml.7z" | python -m llm2005.ingest wikitalk -
    7z x -so "D:\\llm2005-raw\\wikimedia\\enwikibooks-20061223-pages-meta-history.xml.7z" | python -m llm2005.ingest wikibooks -
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from ..datecheck import CUTOFF, to_date
from ..document import Document
from ._io import fetch, open_binary
from .wikipedia import clean_wikitext

# Diskuze na Wikipedii: 1 = Talk, 3 = User talk, 4 = Wikipedia (projekt), 5 = Wikipedia talk
TALK_NAMESPACES = {1, 3, 4, 5}
# U sesterských projektů bereme i samotný obsah (0 = hlavní jmenný prostor)
CONTENT_AND_TALK = {0, 1, 3, 4, 5}

# Zálohy z prosince 2006 (nejstarší dostupné s celou historií revizí)
DUMPS_2006 = {
    "wikibooks": "2006-12/enwikibooks/20061223/enwikibooks-20061223-pages-meta-history.xml.7z",
    "wikinews": "2006-12/enwikinews/20061223/enwikinews-20061223-pages-meta-history.xml.7z",
    "wikiquote": "2006-12/enwikiquote/20061223/enwikiquote-20061223-pages-meta-history.xml.7z",
    "wikisource": "2006-12/enwikisource/20061224/enwikisource-20061224-pages-meta-history.xml.7z",
}
DUMPS_BASE = "https://dumps.wikimedia.org/archive/2006/"
SITE_URL = {
    "wikipedia-talk": "https://en.wikipedia.org/wiki/",
    "wikibooks": "https://en.wikibooks.org/wiki/",
    "wikinews": "https://en.wikinews.org/wiki/",
    "wikiquote": "https://en.wikiquote.org/wiki/",
    "wikisource": "https://en.wikisource.org/wiki/",
}

# Časové razítko podpisu: "12:34, 5 May 2005 (UTC)" nebo starší tvar "12:34, 2005 May 5 (UTC)"
TIMESTAMP = r"\d{1,2}:\d{2},?\s+(?:\d{1,2}\s+[A-Z][a-z]+\.?\s+\d{4}|\d{4}\s+[A-Z][a-z]+\.?\s+\d{1,2})\s*\((?:UTC|GMT)\)"
# Podpis = odkaz na uživatele (+ případně odkaz na jeho diskuzi, značky, "talk") a za ním čas,
# nebo pomlčka a odkaz na uživatele bez času ("--[[User:Bob|Bob]]").
# Odkaz na uživatele uprostřed věty ("zeptej se [[User:Bob|Boba]]") zůstane jako jméno.
_USER_LINK = r"\[\[(?:User|User talk|Special:Contributions)[^\]]*\]\]"
_SIG_TAIL = r"(?:[ \t]|\[\[[^\]]*\]\]|<[^>\n]{0,40}>|[()|·•/,.:\-–—]|talk|contribs|email){0,12}"
SIGNATURE = re.compile(
    r"(?:-{1,2}|—|–)?[ \t]*" + _USER_LINK + _SIG_TAIL + TIMESTAMP
    + r"|(?:-{1,2}|—|–)[ \t]*" + _USER_LINK + _SIG_TAIL
)
LONE_TIMESTAMP = re.compile(r"(?:-{1,2}|—|–)?[ \t]*" + TIMESTAMP)


def clean_talk(t: str) -> str:
    """Odstraní podpisy (uživatel + čas) a pak běžné wiki značky. Text diskuze zůstane."""
    t = html.unescape(t)
    t = SIGNATURE.sub("", t)
    t = LONE_TIMESTAMP.sub("", t)
    t = re.sub(r"~{3,5}", "", t)  # nevyplněný podpis
    t = clean_wikitext(t)
    t = re.sub(r"[ \t]*(?:--|—)[ \t]*$", "", t, flags=re.M)  # zbylé pomlčky na konci řádku
    t = re.sub(r"[ \t]+$", "", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def namespace_of(title: str, prefixes: dict[str, int]) -> int:
    """Číslo jmenného prostoru podle předpony názvu ('Talk:Foo' -> 1). Bez předpony = 0."""
    if ":" in title:
        prefix = title.split(":", 1)[0]
        if prefix in prefixes:
            return prefixes[prefix]
    return 0


def read_mediawiki(path: str, source: str, namespaces: set[int], cutoff=CUTOFF) -> Iterator[Document]:
    """Projde dump MediaWiki a vrací stránky z vybraných jmenných prostorů.

    Datum = datum poslední revize stránky (u pozdějších stránek pak ingest napíše too_new).
    """
    prefixes: dict[str, int] = {}
    title = None
    last_ts = last_text = None
    base_url = SITE_URL.get(source, "")
    for _, el in ET.iterparse(open_binary(path), events=("end",)):
        tag = _local(el.tag)
        if tag == "namespace":
            key = int(el.get("key", "0"))
            if el.text:
                prefixes[el.text] = key
        elif tag == "title":
            title = el.text or ""
        elif tag == "revision":
            ts = text = None
            for child in el:
                name = _local(child.tag)
                if name == "timestamp":
                    ts = child.text
                elif name == "text":
                    text = child.text or ""
            if ts and (last_ts is None or ts > last_ts):
                last_ts, last_text = ts, text or ""
            el.clear()
        elif tag == "page":
            if title and last_ts and namespace_of(title, prefixes) in namespaces:
                if not last_text.lstrip().lower().startswith("#redirect"):
                    d = to_date(last_ts)
                    allowed = d is not None and d <= cutoff
                    # text čistíme jen u stránek, které projdou kontrolou data (šetří čas)
                    body = clean_talk(last_text) if allowed else ""
                    yield Document(
                        source=source,
                        source_id=title,
                        title=title,
                        date=d.isoformat() if d else "",
                        text=f"{title}\n\n{body}" if allowed else "",
                        url=base_url + title.replace(" ", "_"),
                    )
            title = last_ts = last_text = None
            el.clear()


def read_wikitalk(path: str) -> Iterator[Document]:
    return read_mediawiki(path, "wikipedia-talk", TALK_NAMESPACES)


def read_wikibooks(path: str) -> Iterator[Document]:
    return read_mediawiki(path, "wikibooks", CONTENT_AND_TALK)


def read_wikinews(path: str) -> Iterator[Document]:
    return read_mediawiki(path, "wikinews", CONTENT_AND_TALK)


def read_wikiquote(path: str) -> Iterator[Document]:
    return read_mediawiki(path, "wikiquote", CONTENT_AND_TALK)


def read_wikisource(path: str) -> Iterator[Document]:
    return read_mediawiki(path, "wikisource", CONTENT_AND_TALK)


def download_wikimedia(dest: str) -> None:
    """Stáhne zálohy Wikibooks, Wikinews, Wikiquote a Wikisource z prosince 2006 (~360 MB)."""
    for name, rel in DUMPS_2006.items():
        url = DUMPS_BASE + rel
        target = Path(dest) / rel.rsplit("/", 1)[1]
        print(f"{name}: {url} -> {target}", flush=True)
        n = fetch(url, target, timeout=120)
        print(f"  nově staženo {n / 1e6:.0f} MB", flush=True)
