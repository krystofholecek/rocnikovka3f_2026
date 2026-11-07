"""Abstrakty článků z arXiv.org (vědecké preprinty), jen ty z doby do konce roku 2005.

Zdroj: oficiální rozhraní OAI-PMH https://oaipmh.arxiv.org/oai (bez účtu).
Licence: metadata arXivu (název, abstrakt) jsou volně k použití (CC0).

Stažení = „sklizeň“ všech záznamů po stránkách (asi 2 000 dotazů, odhad ~4 h).
Na disk se uloží jen záznamy vytvořené do 31. 12. 2005 (malý soubor, odhad < 1 GB):
    python -m llm2005.download arxiv D:\\llm2005-raw\\arxiv
Import:
    python -m llm2005.ingest arxiv D:\\llm2005-raw\\arxiv\\arxiv-2005.jsonl

Datum: <created> = první verze článku, <updated> = poslední verze.
Abstrakt v metadatech je z POSLEDNÍ verze, proto bereme datum `updated`
(když chybí, tak `created`). Článek opravený po roce 2005 tak ingest odmítne (too_new)
– je to přísnější, ale jistější.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from ..document import Document
from ._io import open_text

OAI_URL = "https://oaipmh.arxiv.org/oai"
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "a": "http://arxiv.org/OAI/arXiv/"}
LAST_CREATED = "2005-12-31"


def _text(el, path: str) -> str:
    found = el.find(path, NS)
    return " ".join(found.text.split()) if found is not None and found.text else ""


def parse_page(xml: bytes) -> tuple[list[dict], str]:
    """Jedna stránka odpovědi OAI -> (záznamy, resumptionToken). Prázdný token = konec."""
    root = ET.fromstring(xml)
    records = []
    for rec in root.iterfind(".//oai:record", NS):
        meta = rec.find("oai:metadata/a:arXiv", NS)
        if meta is None:  # smazaný záznam
            continue
        records.append(
            {
                "id": _text(meta, "a:id"),
                "created": _text(meta, "a:created"),
                "updated": _text(meta, "a:updated"),
                "title": _text(meta, "a:title"),
                "abstract": _text(meta, "a:abstract"),
            }
        )
    token_el = root.find(".//oai:resumptionToken", NS)
    token = token_el.text.strip() if token_el is not None and token_el.text else ""
    return records, token


def _get(url: str, retries: int = 10) -> bytes:
    """Stáhne stránku; když server řekne 503 (moc rychle), počká, kolik chce (Retry-After)."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 503 or attempt == retries - 1:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 30))
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(60)
    raise RuntimeError("nedosažitelné")


def _cut_partial_line(path: Path) -> None:
    """Po přerušení může na konci souboru zůstat půlka řádku -> odřízneme ji."""
    if not path.exists():
        return
    data = path.read_bytes()
    end = data.rfind(b"\n") + 1
    if end != len(data):
        with open(path, "r+b") as f:
            f.truncate(end)


def download_arxiv(dest: str) -> None:
    """Sklidí všechna metadata a uloží jen záznamy s created <= 2005-12-31.

    Průběh (resumptionToken) se ukládá do souboru, takže po přerušení stačí
    příkaz spustit znovu a pokračuje se (token platí asi den). Záznamy, které se
    po navázání zapíšou dvakrát, vyřadí databáze jako duplikát.
    """
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / "arxiv-2005.jsonl"
    token_path = root / "resumption-token.txt"
    token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    if token == "DONE":
        print("arXiv: už je sklizeno", flush=True)
        return
    if not token:
        out_path.unlink(missing_ok=True)  # začínáme od začátku
    _cut_partial_line(out_path)
    pages = kept = seen = 0
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8", newline="\n") as out:
        while True:
            if token:
                q = {"verb": "ListRecords", "resumptionToken": token}
            else:
                q = {"verb": "ListRecords", "metadataPrefix": "arXiv"}
            records, token = parse_page(_get(OAI_URL + "?" + urllib.parse.urlencode(q)))
            for r in records:
                seen += 1
                if r["created"] and r["created"] <= LAST_CREATED:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                    kept += 1
            out.flush()
            token_path.write_text(token or "DONE", encoding="utf-8")
            pages += 1
            if pages % 20 == 0 or not token:
                print(f"{pages:>5} stránek, {seen:>8} záznamů, ponecháno {kept:>7}"
                      f"  ({time.time() - t0:.0f} s)", flush=True)
            if not token:
                break
            time.sleep(1)  # ohleduplnost k serveru arXivu


def read_arxiv(path: str) -> Iterator[Document]:
    with open_text(path) as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            title, abstract = r.get("title", ""), r.get("abstract", "")
            yield Document(
                source="arxiv",
                source_id=r["id"],
                title=title,
                date=r.get("updated") or r.get("created") or "",
                text=f"{title}\n\n{abstract}".strip(),
                url=f"https://arxiv.org/abs/{r['id']}",
            )
