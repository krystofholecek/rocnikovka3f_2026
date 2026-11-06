"""Soudní rozhodnutí USA z CourtListener (Free Law Project), bulk data bez účtu.

Zdroj: https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/list.html?prefix=bulk-data/
* opinion-clusters-*.csv.bz2 (~2,5 GB) – u každého případu datum `date_filed`
  a příznak `date_filed_is_approximate` (odhadnuté datum),
* opinions-*.csv.bz2 (~55 GB) – texty rozhodnutí; to je moc, proto stáhneme jen
  ZAČÁTEK souboru (OPINIONS_BYTES, ~7,5 GB) -> zdroj celkem ~10 GB. Useknutý konec
  archivu nevadí, čtení prostě skončí. Je to tedy jen omezený vzorek (formální text).

Stažení:
    python -m llm2005.download courtlistener D:\\llm2005-raw\\courtlistener
Import:
    python -m llm2005.ingest courtlistener D:\\llm2005-raw\\courtlistener

Pravidla data:
* datum = `date_filed` případu (clusteru), ke kterému rozhodnutí patří,
* chybějící nebo nečitelné datum -> bez data -> no_date,
* odhadnuté datum (date_filed_is_approximate = t) -> bez data -> no_date,
* po 31. 12. 2005 -> too_new,
* rozhodnutí před rokem MIN_YEAR (1980) vynecháváme úplně – chceme moderní jazyk,
  ne angličtinu 19. století (to už máme z knih PG-19).
"""
from __future__ import annotations

import bz2
import csv
import html
import io
import re
import sys
import urllib.request
from pathlib import Path
from typing import Iterator

from ..datecheck import CUTOFF
from ..document import Document
from ._io import USER_AGENT, fetch

BASE = "https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/bulk-data/"
SNAPSHOT = "2026-09-30"
CLUSTERS = f"opinion-clusters-{SNAPSHOT}.csv.bz2"
OPINIONS = f"opinions-{SNAPSHOT}.csv.bz2"
OPINIONS_BYTES = 7_500_000_000  # stáhneme jen začátek 55GB souboru
MIN_YEAR = 1980
# Sloupce s textem v pořadí, v jakém je zkoušíme (první neprázdný vyhraje)
TEXT_COLUMNS = ("plain_text", "html_with_citations", "html", "html_lawbox",
                "html_columbia", "html_anon_2020", "xml_harvard")

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _raise_field_limit() -> None:
    """Texty rozhodnutí jsou delší než výchozí limit modulu csv (128 kB) -> co nejvýš."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:  # na Windows je limit jen 32bitový C long
            limit //= 2


_raise_field_limit()


class CourtListenerDialect(csv.Dialect):
    """Formát bulk dumpů CourtListener (PostgreSQL COPY ... CSV, ESCAPE '\\').

    Každé pole je v uvozovkách, uvozovka UVNITŘ pole se píše jako \\" (ne "" jako
    v Excelu) a zpětné lomítko jako \\\\. Pole může obsahovat i konce řádků.
    S výchozím dialektem se \\" špatně přečte a sloupce se posunou.
    """
    delimiter = ","
    quotechar = '"'
    escapechar = "\\"
    doublequote = False
    quoting = csv.QUOTE_MINIMAL
    lineterminator = "\n"
    skipinitialspace = False
    strict = False


class RowStats:
    """Počítadlo přečtených a vadných (přeskočených) řádků CSV."""

    def __init__(self) -> None:
        self.rows = 0
        self.bad = 0

    def __str__(self) -> str:
        return f"{self.rows:,} řádků, vadných přeskočeno {self.bad:,}"


def _open_csv(path: Path):
    """Proudové čtení .csv.bz2 (i useknutého) jako slovníků podle hlavičky.

    newline="" je nutné, aby modul csv sám poznal konce řádků uvnitř polí.
    """
    text = io.TextIOWrapper(bz2.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return csv.DictReader(text, dialect=CourtListenerDialect)


def _rows(reader: csv.DictReader, stats: RowStats | None = None) -> Iterator[dict]:
    """Řádky CSV. Vadný řádek přeskočí a započítá, useknutý konec archivu čtení ukončí.

    Řádek je vadný, když ho csv nepřečte (csv.Error) nebo nemá stejný počet
    sloupců jako hlavička (DictReader pak dá klíč None nebo hodnotu None).
    """
    stats = stats if stats is not None else RowStats()
    while True:
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error as e:  # rozbitý řádek -> přeskočit, číst dál
            stats.bad += 1
            if stats.bad <= 10:
                print(f"vadný řádek CSV přeskočen ({e})", flush=True)
            continue
        except (EOFError, OSError) as e:  # useknutý archiv -> konec
            print(f"konec čtení CSV ({type(e).__name__}: {e})", flush=True)
            return
        stats.rows += 1
        if None in row or None in row.values():
            stats.bad += 1
            continue
        yield row


def _int(value: str | None) -> int | None:
    """Číslo z textu, nebo None, když to číslo není (vadný řádek)."""
    try:
        return int((value or "").strip())
    except ValueError:
        return None


def html_to_text(t: str) -> str:
    t = re.sub(r"<page-number[^>]*>.*?</page-number>", "", t, flags=re.S)  # čísla stran *232
    t = re.sub(r"</(p|div|blockquote|h\d|li|opinion|author)>|<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" ?\n ?", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def opinion_text(row: dict) -> str:
    for col in TEXT_COLUMNS:
        value = (row.get(col) or "").strip()
        if value:
            return value if col == "plain_text" else html_to_text(value)
    return ""


def load_clusters(path: Path, stats: RowStats | None = None) -> dict[int, str]:
    """cluster id -> "datum<TAB>název případu".

    * případ před rokem MIN_YEAR -> vůbec se neuloží (rozhodnutí se pak vynechá),
    * chybějící, nečitelné nebo odhadnuté datum -> "" (rozhodnutí půjde do rejected
      jako no_date),
    * po CUTOFF -> jen datum (rozhodnutí půjde do rejected jako too_new).
    Řádek bez platného číselného id je vadný -> přeskočí se a započítá do stats.bad.
    """
    stats = stats if stats is not None else RowStats()
    clusters: dict[int, str] = {}
    min_date = f"{MIN_YEAR}-01-01"
    for row in _rows(_open_csv(path), stats):
        cid = _int(row.get("id"))
        if cid is None:
            stats.bad += 1
            continue
        d = (row.get("date_filed") or "").strip()
        if not DATE_RE.fullmatch(d):
            clusters[cid] = ""  # chybějící/nečitelné datum -> odmítne se (no_date)
        elif d < min_date:
            continue  # starý případ -> nebereme vůbec
        elif row.get("date_filed_is_approximate") == "t":
            clusters[cid] = ""  # odhadnuté datum -> odmítne se (no_date)
        elif d <= CUTOFF.isoformat():
            name = " ".join((row.get("case_name") or "").split())[:200]
            clusters[cid] = f"{d}\t{name}"
        else:
            clusters[cid] = d  # pozdější -> stačí datum (odmítne se)
        if stats.rows % 1_000_000 == 0:
            print(f"clustery: {stats}, vybráno {len(clusters):,}", flush=True)
    return clusters


def read_courtlistener(path: str) -> Iterator[Document]:
    folder = Path(path)
    cstats = RowStats()
    clusters = load_clusters(folder / CLUSTERS, cstats)
    print(f"clustery načteny: {len(clusters):,} ({cstats})", flush=True)
    ostats = RowStats()
    for row in _rows(_open_csv(folder / OPINIONS), ostats):
        oid, cid = _int(row.get("id")), _int(row.get("cluster_id"))
        if oid is None or cid is None:
            ostats.bad += 1
            continue
        info = clusters.get(cid)
        if info is None:
            continue  # případ před rokem MIN_YEAR nebo neznámý -> nebereme
        date, _, title = info.partition("\t")
        allowed = bool(date) and date <= CUTOFF.isoformat()
        yield Document(
            source="courtlistener",
            source_id=str(oid),
            title=title,
            date=date,
            text=opinion_text(row) if allowed else "",
            url=f"https://www.courtlistener.com/opinion/{cid}/x/",
        )
    print(f"rozhodnutí: {ostats}", flush=True)


def fetch_prefix(url: str, target: Path, max_bytes: int, chunk: int = 1 << 20) -> int:
    """Stáhne jen prvních max_bytes souboru. Jde přerušit a znovu spustit (pokračuje)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    have = target.stat().st_size if target.exists() else 0
    if have >= max_bytes:
        return 0
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={have}-{max_bytes - 1}"}
    )
    got = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(target, "ab") as f:
        while data := r.read(chunk):
            f.write(data)
            got += len(data)
            if got % (500 * chunk) < chunk:
                print(f"  {(have + got) / 1e9:.1f} GB", flush=True)
    return got


def download_courtlistener(dest: str) -> None:
    folder = Path(dest)
    print(f"CourtListener: {CLUSTERS}", flush=True)
    fetch(BASE + CLUSTERS, folder / CLUSTERS, timeout=120)
    print(f"CourtListener: začátek {OPINIONS} ({OPINIONS_BYTES / 1e9:.1f} GB)", flush=True)
    for _ in range(20):  # při výpadku spojení pokračuje, kde skončilo
        try:
            fetch_prefix(BASE + OPINIONS, folder / OPINIONS, OPINIONS_BYTES)
            break
        except OSError as e:
            print(f"  chyba spojení ({e}), zkouším znovu", flush=True)
    print("hotovo", flush=True)
