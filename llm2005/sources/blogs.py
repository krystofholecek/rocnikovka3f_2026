"""Blog Authorship Corpus: 681 288 příspěvků 19 320 blogerů z blogger.com (sebráno v srpnu 2004).

Zdroj: Koppel a kol., Bar-Ilan University (https://u.cs.biu.ac.il/~koppel/BlogCorpus.htm).
Soubor blogs.zip (asi 313 MB) je na Hugging Face v repozitáři univerzity:
https://huggingface.co/datasets/barilan/blog_authorship_corpus
Licence: volně pro nekomerční výzkum.

Stažení:
    python -m llm2005.download blogs D:\\llm2005-raw\\blogs
Import (zip se nerozbaluje):
    python -m llm2005.ingest blogs D:\\llm2005-raw\\blogs\\blogs.zip

Jeden soubor = jeden bloger, např. `1000331.female.37.indUnk.Leo.xml`:
    <Blog>
    <date>14,May,2004</date>
    <post> text příspěvku </post>
    ...
Jeden dokument = jeden příspěvek, datum = jeho <date>. Měsíc bývá i v jiném jazyce
(např. "mai", "juin"); neznámý měsíc -> prázdné datum -> ingest odmítne (no_date).
"""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Iterator

from ..document import Document
from ._io import fetch

URL = "https://huggingface.co/datasets/barilan/blog_authorship_corpus/resolve/main/data/blogs.zip"

# názvy měsíců v jazycích, které se v korpusu objevují (malými písmeny)
MONTHS = {}
for number, names in enumerate(
    [
        "january janvier januar enero gennaio janeiro januari styczen tammikuu",
        "february fevrier février februar febrero febbraio fevereiro februari luty helmikuu",
        "march mars märz marz maerz marzo março marco maart marzec maaliskuu",
        "april avril abril aprile kwiecien huhtikuu",
        "may mai mayo maggio maio mei maj toukokuu",
        "june juin juni junio giugno junho czerwiec kesäkuu kesakuu",
        "july juillet juli julio luglio julho lipiec heinäkuu heinakuu",
        "august aout août agosto augustus sierpien elokuu",
        "september septembre septiembre settembre setembro wrzesien syyskuu",
        "october octobre oktober octubre ottobre outubro pazdziernik lokakuu",
        "november novembre noviembre novembro listopad marraskuu",
        "december decembre décembre dezember diciembre dicembre dezembro grudzien joulukuu",
    ],
    start=1,
):
    for name in names.split():
        MONTHS[name] = number

POST_RE = re.compile(r"<date>(.*?)</date>\s*<post>(.*?)</post>", re.DOTALL | re.IGNORECASE)


def parse_date(value: str) -> str:
    """'14,May,2004' -> '2004-05-14'. Neznámý formát/měsíc -> ''."""
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
        return ""
    day, month, year = int(parts[0]), MONTHS.get(parts[1].lower()), int(parts[2])
    if month is None or not 1 <= day <= 31 or not 1990 <= year <= 2100:
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}"


def clean_post(text: str) -> str:
    """Odstraní značku 'urlLink' (místo odkazu), HTML entity a zbytečné mezery na řádcích."""
    text = html.unescape(text.replace("urlLink", ""))
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # starší soubory nejsou v UTF-8


def parse_blog(raw: bytes, filename: str) -> Iterator[Document]:
    blogger = Path(filename).name.removesuffix(".xml")
    blogger_id = blogger.split(".")[0]
    for i, (date, post) in enumerate(POST_RE.findall(decode(raw))):
        yield Document(
            source="blogs",
            source_id=f"{blogger_id}-{i}",
            title=blogger,  # id.pohlaví.věk.obor.znamení
            date=parse_date(date),
            text=clean_post(post),
        )


def read_blogs(path: str) -> Iterator[Document]:
    """Čte blogs.zip, nebo rozbalenou složku s .xml soubory."""
    p = Path(path)
    if p.is_dir():
        for file in sorted(p.rglob("*.xml")):
            yield from parse_blog(file.read_bytes(), file.name)
        return
    with zipfile.ZipFile(p) as z:
        for name in sorted(z.namelist()):
            if name.endswith(".xml"):
                yield from parse_blog(z.read(name), name)


def download_blogs(dest: str) -> None:
    target = Path(dest) / "blogs.zip"
    print(f"Blogy: stahuji {URL} -> {target}", flush=True)
    n = fetch(URL, target, timeout=120)
    print(f"hotovo, nově staženo {n / 1e6:.0f} MB", flush=True)
