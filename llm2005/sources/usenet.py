"""Usenet – archiv UTZOO (Henry Spencer, Univ. of Toronto), únor 1981 až červen 1991.

Zdroj: https://sourceforge.net/projects/utzoo/ (141 magnetických pásek, soubory
newsNNNfM.tar.bz2, celkem asi 2–3 GB). Kopie na archive.org byla v roce 2020 stažena.

Stažení:
    python -m llm2005.download utzoo D:\\llm2005-raw\\utzoo
Import (čte všechny .tar.bz2 ve složce, nic nerozbaluje):
    python -m llm2005.ingest usenet D:\\llm2005-raw\\utzoo

Každý soubor v archivu = jeden příspěvek ve formátu "B news" (hlavičky jako u e-mailu).
* Datum = hlavička `Date:` (např. "9 May 90 17:43:27 GMT"); bez ní -> no_date.
  (Páska 001 a Scavenged jsou ve starém formátu "A news" bez hlavičky Date:, ty se odmítnou.)
* Hlavičky se zahodí, nechá se jen předmět. Podpis za řádkem "-- " se odřízne,
  stejně tak citace (řádky začínající ">") a úvodní "In article ... writes:".
* Duplicity: stejný příspěvek je často na více páskách -> source_id = Message-ID
  (databáze má UNIQUE, ingest je počítá jako duplicate).
* Přeskočíme skupiny se zdrojovými kódy a binárkami (comp.sources.*, *.binaries)
  a řídicí zprávy (control).
"""
from __future__ import annotations

import email
import email.policy
import hashlib
import re
import tarfile
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from ..datecheck import is_placeholder
from ..document import Document
from ._io import fetch

USENET_START = "1980-01-01"  # dřívější datum v Usenetu nemůže být skutečné

FILES_PAGE = "https://sourceforge.net/projects/utzoo/files/"
RSS = "https://sourceforge.net/projects/utzoo/rss?path=/"
DOWNLOAD = "https://downloads.sourceforge.net/project/utzoo/"
USER_AGENT = "llm2005-school-project/0.1"

SKIP_GROUPS = re.compile(r"(^|[.,])(sources|binaries|control)([.,]|$)|^control$", re.I)
ATTRIBUTION = re.compile(r"^.{0,80}\b(writes|wrote|said|says):\s*$", re.I)
UUENCODE = re.compile(r"^begin [0-7]{3} \S+", re.M)


def parse_date(value: str | None) -> str:
    """'9 May 90 17:43:27 GMT' -> '1990-05-09'. Nejde-li přečíst -> ''.

    Podezřelé datum -> '' (ingest ho pak odmítne jako no_date):
    * 'Wed, 31-Dec-69 18:59:59 EDT' = unixový čas -1, news software datum neznal,
    * cokoli před rokem 1980 – Usenet vznikl až 1979/80 (archiv UTZOO začíná 1981),
      takže např. 1970-10-23 je počítač s nenastavenými hodinami.
    """
    if not value:
        return ""
    try:
        iso = parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return ""
    if is_placeholder(iso) or iso < USENET_START:
        return ""
    return iso


def clean_body(body: str) -> str:
    """Odřízne podpis, citace a řádky typu 'In article <...> x@y (Jméno) writes:'."""
    body = body.replace("\r\n", "\n")
    sig = re.search(r"^-- ?$", body, flags=re.M)  # podpis začíná řádkem "-- "
    if sig:
        body = body[: sig.start()]
    lines = []
    for line in body.split("\n"):
        s = line.lstrip()
        if s.startswith((">", "|>", ":>")):
            continue
        if s.startswith("In article <") or ATTRIBUTION.match(s):
            continue
        lines.append(line.rstrip())
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def parse_article(raw: bytes, member_name: str) -> Document | None:
    """Jeden příspěvek -> Document. None = přeskočit (kód, binárka, řídicí zpráva)."""
    msg = email.message_from_bytes(raw, policy=email.policy.compat32)
    groups = str(msg.get("Newsgroups", "") or "").strip()
    if msg.get("Control") or SKIP_GROUPS.search(groups):
        return None
    payload = msg.get_payload(decode=True)
    if not isinstance(payload, bytes):
        payload = raw.split(b"\n\n", 1)[-1]
    body = payload.decode("latin-1")  # 80. léta = ASCII, latin-1 nikdy nespadne
    if UUENCODE.search(body):
        return None
    body = clean_body(body)
    subject = " ".join(str(msg.get("Subject", "") or "").split())
    msg_id = str(msg.get("Message-ID", "") or "").strip()
    if not msg_id:
        msg_id = "sha1:" + hashlib.sha1(" ".join(body.lower().split()).encode()).hexdigest()[:20]
    return Document(
        source="usenet",
        source_id=msg_id,
        title=subject,
        date=parse_date(msg.get("Date")),
        text=f"Subject: {subject}\n\n{body}" if subject else body,
        url=f"{groups} {member_name}".strip(),
    )


def _read_tar(path: Path) -> Iterator[Document]:
    try:
        with tarfile.open(path, "r:*") as tar:
            for member in tar:  # postupně, bez rozbalování na disk
                if member.isfile() and member.size > 0:
                    f = tar.extractfile(member)
                    if f is not None:
                        doc = parse_article(f.read(), member.name)
                        if doc is not None:
                            yield doc
                tar.members = []  # nepamatovat si seznam statisíců souborů
    except (tarfile.ReadError, EOFError, OSError) as e:
        # některé pásky jsou na konci useknuté -> vezmeme, co jde přečíst
        print(f"{path.name}: konec čtení ({e})", flush=True)


def read_usenet(path: str) -> Iterator[Document]:
    """Složka s páskami (*.tar.bz2) nebo jeden archiv."""
    p = Path(path)
    files = sorted(p.glob("*.tar*")) if p.is_dir() else [p]
    for f in files:
        if not f.name.endswith(".part"):
            print(f"čtu {f.name}", flush=True)
            yield from _read_tar(f)


def list_tapes() -> list[str]:
    """Seznam souborů pásek ze SourceForge (stránka souborů + RSS, každé ukáže jen část)."""
    names: set[str] = set()
    for url in (FILES_PAGE, RSS):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        text = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
        names.update(re.findall(r"(news\d{3}f\d\.tar\.bz2|Scavenged\.tar\.bz2)", text))
    return sorted(names)


def download_utzoo(dest: str) -> None:
    names = list_tapes()
    print(f"UTZOO: {len(names)} souborů -> {dest}", flush=True)
    total = 0
    for name in names:
        n = fetch(DOWNLOAD + name, Path(dest) / name, timeout=120)
        total += n
        if n:
            print(f"  {name}: {n / 1e6:.1f} MB", flush=True)
    print(f"hotovo, nově staženo {total / 1e9:.2f} GB", flush=True)
