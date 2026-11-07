"""E-maily firmy Enron (asi 517 000 zpráv, hlavně 1999–2002).

Zdroj: https://www.cs.cmu.edu/~enron/ (verze z 7. 5. 2015, asi 443 MB).
Data zveřejnil americký úřad FERC při vyšetřování; CMU je šíří pro výzkum.

Stažení:
    python -m llm2005.download enron D:\\llm2005-raw\\enron
Import (archiv se nerozbaluje, čte se přímo z .tar.gz):
    python -m llm2005.ingest enron D:\\llm2005-raw\\enron\\enron_mail_20150507.tar.gz

Datum = hlavička `Date:` každého e-mailu. Když chybí nebo nejde přečíst,
dokument dostane prázdné datum a ingest ho odmítne (no_date).
Některé e-maily mají nesmyslné datum (např. rok 2020+) -> ingest je odmítne (too_new).

Stejný e-mail bývá ve více složkách (inbox, all_documents, …). Jako source_id
proto bereme otisk (hash) textu -> duplikáty zachytí databáze (UNIQUE) a ingest
je počítá jako `duplicate`.
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import re
import tarfile
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from ..datecheck import is_placeholder
from ..document import Document
from ._io import fetch

URL = "https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz"

# Od těchto řádků začíná citace předchozí zprávy -> zbytek zahodíme (jinak by se texty opakovaly).
QUOTE_MARKERS = re.compile(
    r"^\s*(-{2,}\s*Original Message\s*-{2,}|-{5,}\s*Forwarded by|From:\s.*\n\s*Sent:\s)",
    re.IGNORECASE | re.MULTILINE,
)


def parse_date(value: str | None) -> str:
    """'Mon, 14 May 2001 16:39:00 -0700 (PDT)' -> '2001-05-14'. Nejde-li přečíst -> ''.

    'Mon, 31 Dec 1979 16:00:00 -0800 (PST)' je nulové datum Outlooku (1. 1. 1980 0:00 UTC),
    skutečné datum e-mail nemá -> '' (ingest ho odmítne jako no_date).
    """
    if not value:
        return ""
    try:
        iso = parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""
    return "" if is_placeholder(iso) else iso


def clean_body(body: str) -> str:
    """Odřízne citovanou předchozí zprávu a srovná konce řádků."""
    body = body.replace("\r\n", "\n")
    m = QUOTE_MARKERS.search(body)
    if m:
        body = body[: m.start()]
    return body.strip()


def text_id(text: str) -> str:
    """Otisk textu (bez ohledu na mezery a velikost písmen) – stejné texty = stejné ID."""
    norm = " ".join(text.lower().split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:20]


def parse_message(raw: bytes, member_name: str) -> Document:
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    payload = None
    for part in msg.walk():  # u jednoduché zprávy je to jen ona sama
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            break
    body = clean_body((payload or b"").decode("utf-8", errors="replace"))
    subject = " ".join(str(msg.get("Subject", "") or "").split())
    text = f"Subject: {subject}\n\n{body}" if subject else body
    return Document(
        source="enron",
        source_id=text_id(body),
        title=subject,
        date=parse_date(msg.get("Date")),
        text=text,
        url=member_name,  # cesta v archivu, např. maildir/allen-p/inbox/1.
    )


def read_enron(path: str) -> Iterator[Document]:
    """Projde archiv .tar.gz (nebo rozbalenou složku maildir) a vrací e-maily."""
    p = Path(path)
    if p.is_dir():
        for file in sorted(f for f in p.rglob("*") if f.is_file()):
            yield parse_message(file.read_bytes(), file.relative_to(p).as_posix())
        return
    with tarfile.open(p, "r:*") as tar:
        for member in tar:  # postupně, bez rozbalování na disk
            if not member.isfile():
                continue
            f = tar.extractfile(member)
            if f is not None:
                yield parse_message(f.read(), member.name)
            tar.members = []  # nepamatovat si seznam 500 000 souborů


def download_enron(dest: str) -> None:
    target = Path(dest) / URL.rsplit("/", 1)[1]
    print(f"Enron: stahuji {URL} -> {target}", flush=True)
    n = fetch(URL, target, timeout=120)
    print(f"hotovo, nově staženo {n / 1e6:.0f} MB", flush=True)
