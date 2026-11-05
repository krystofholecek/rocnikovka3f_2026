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

csv.field_size_limit(2**31 - 1)  # texty rozhodnutí jsou delší než výchozí limit


def _open_csv(path: Path):
    """Proudové čtení .csv.bz2 (i useknutého) jako slovníků podle hlavičky."""
    text = io.TextIOWrapper(bz2.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return csv.DictReader(text)


def _rows(reader) -> Iterator[dict]:
    """Řádky CSV; useknutý konec archivu (EOFError) nebo rozbitý řádek čtení ukončí."""
    try:
        yield from reader
    except (EOFError, OSError, csv.Error) as e:
        print(f"konec čtení CSV ({type(e).__name__}: {e})", flush=True)


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


def load_clusters(path: Path) -> dict[int, str]:
    """cluster id -> "datum<TAB>název případu" (jen od MIN_YEAR; odhadnuté datum -> "")."""
    clusters: dict[int, str] = {}
    min_date = f"{MIN_YEAR}-01-01"
    for i, row in enumerate(_rows(_open_csv(path)), 1):
        d = (row.get("date_filed") or "").strip()
        if d < min_date:
            continue
        if row.get("date_filed_is_approximate") == "t":
            clusters[int(row["id"])] = ""
        elif d <= CUTOFF.isoformat():
            name = " ".join((row.get("case_name") or "").split())[:200]
            clusters[int(row["id"])] = f"{d}\t{name}"
        else:
            clusters[int(row["id"])] = d  # pozdější -> stačí datum (odmítne se)
        if i % 1_000_000 == 0:
            print(f"clustery: {i:,} řádků, vybráno {len(clusters):,}", flush=True)
    return clusters


def read_courtlistener(path: str) -> Iterator[Document]:
    folder = Path(path)
    clusters = load_clusters(folder / CLUSTERS)
    print(f"clustery načteny: {len(clusters):,}", flush=True)
    for row in _rows(_open_csv(folder / OPINIONS)):
        try:
            info = clusters.get(int(row.get("cluster_id") or 0))
        except ValueError:
            continue
        if info is None:
            continue  # případ před rokem MIN_YEAR nebo neznámý -> nebereme
        date, _, title = info.partition("\t")
        allowed = bool(date) and date <= CUTOFF.isoformat()
        yield Document(
            source="courtlistener",
            source_id=row["id"],
            title=title,
            date=date,
            text=opinion_text(row) if allowed else "",
            url=f"https://www.courtlistener.com/opinion/{row['cluster_id']}/x/",
        )


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
