"""Čištění textu od „šumu“ – za běhu, databáze se nepřepisuje.

Proč: první tokenizér se naučil stovky zbytečných tokenů – 212 tokenů jen z mezer
(až 512 mezer za sebou), řady pomlček „-----“ a dlouhé řetězce „AAAA…“.
To jsou binární přílohy (uuencode, base64) v Usenetu a e-mailech, zarovnání tabulek
mezerami a ozdobné čáry. Model by se je zbytečně učil a plýtval na ně tokeny.

Použití: clean_text(text, source) volá výběr směsi (mix.py) – tedy tokenizér i prepare.
Databáze zůstává beze změny, takže pravidla jde kdykoli upravit a data jen znovu projít.

Pravidla (v tomto pořadí):
1. Konce řádků na \\n, řídicí znaky pryč (kromě \\n a \\t; \\f a \\v -> nový řádek).
2. Binární přílohy pryč ÚPLNĚ, bez náhradní značky:
   - uuencode: blok „begin 644 soubor … end“ a řady řádků „M…“ (61 znaků),
   - base64/MIME: aspoň 3 řádky jen ze znaků A–Z a–z 0–9 + / = (≥ 40 znaků bez mezery),
     i s hlavičkami Content-…: těsně před nimi; a samotná „slova“ base64 delší než 200 znaků,
   - jiné kódování (BinHex, btoa): aspoň 5 řádků po 56–80 znacích bez jediné mezery.
   Proč bez značky: značka typu „[binární příloha odstraněna]“ se v textech z roku 2005
   nikdy nevyskytla – model by se ji naučil a pak ji sám vymýšlel. Navíc je česky
   v anglických datech. Okolní věta („posílám v příloze…“) dává smysl i bez ní.
3. Mezery a tabulátory: odsazení na začátku řádku max 16 znaků, uvnitř řádku max 4
   za sebou, na konci řádku žádné. Běžné odsazení odstavců a kódu se tím nezmění;
   změní se jen zarovnání sloupců v textových tabulkách (sloupce se „sesunou“, slova zůstanou).
4. Opakovaný znak (---- ==== **** ____ ~~~~): max 8 za sebou; písmeno/číslice max 16
   (např. „aaaaaaaaaaaargh“ v blogu zůstane celé).
5. Víc než 2 prázdné řádky za sebou -> 2 prázdné řádky.
6. Mezery a prázdné řádky na začátku a konci dokumentu pryč.

Dokument, který je po čištění kratší než MIN_CHARS (stejně jako při importu), se do směsi nebere.
"""
from __future__ import annotations

import re

MIN_CHARS = 200  # stejné jako llm2005/ingest.py – kratší text nemá pro trénink cenu

MAX_LEAD = 16    # max. odsazení na začátku řádku (znaků)
MAX_INNER = 4    # max. mezer/tabulátorů za sebou uvnitř řádku
MAX_PUNCT = 8    # max. stejných ne-písmen za sebou (----, ====)
MAX_WORDCH = 16  # max. stejných písmen/číslic za sebou

# --- 1) řídicí znaky: C0 kromě \t \n, DEL a C1 (0x80–0x9f); \f \v nahradíme novým řádkem
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f-\x9f]")
_PAGEBREAK = re.compile(r"[\f\v]")

# --- 2) binární přílohy
# uuencode: řádek "begin 644 jmeno", pak řádky ze znaků 0x20–0x60, nakonec "end"
_UU_BLOCK = re.compile(
    r"^begin [0-7]{3,4} [^\n]*\n(?:[\x20-\x60]{1,64}\n)*?(?:`[ \t]*\n)?end[ \t]*\n", re.M)
# uuencode bez hlavičky (další díly vícedílné zprávy): aspoň 3 plné řádky "M" + 60 znaků
_UU_RUN = re.compile(r"(?:^M[\x20-\x60]{60}[ \t]*\n){3,}(?:^[\x21-\x60][\x20-\x60]{0,60}[ \t]*\n){0,2}", re.M)
# base64: aspoň 3 dlouhé řádky z abecedy base64, volitelně s MIME hlavičkami těsně před nimi
_B64_RUN = re.compile(
    r"(?:^Content-[\w-]+:[^\n]*\n(?:[ \t]+[^\n]*\n)*)*\n?"
    r"(?:^[A-Za-z0-9+/]{40,}={0,2}[ \t]*\n){3,}(?:^[A-Za-z0-9+/]{1,39}={0,2}[ \t]*\n)?", re.M)
_B64_WORD = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")
# BinHex a podobně: aspoň 5 řádků 56–80 viditelných znaků bez mezery a bez . / :
# (tečka, lomítko a dvojtečka v BinHexu nejsou – tím se nesmažou seznamy odkazů)
_OTHER_RUN = re.compile(r"(?:^[!-\-0-9;-~]{56,80}\n){5,}", re.M)
# rychlé předběžné hledání – drahé náhrady děláme jen u dokumentů, kde něco takového může být
_BINARY_HINT = re.compile(r"^begin [0-7]{3}|^M[\x20-\x60]{60}|[A-Za-z0-9+/]{40}|^[!-~]{56}", re.M)

# --- 3) mezery
_TRAILING = re.compile(r"[ \t]+$", re.M)
_LEAD = re.compile(r"^([ \t]{%d})[ \t]+" % MAX_LEAD, re.M)
_INNER = re.compile(r"(?<=[^ \t\n])([ \t]{%d})[ \t]+" % MAX_INNER)

# --- 4) opakované znaky
_REPEAT_PUNCT = re.compile(r"([^\w\s])\1{%d,}" % MAX_PUNCT)
_REPEAT_WORD = re.compile(r"(\w)\1{%d,}" % MAX_WORDCH)

# --- 5) prázdné řádky: 4 a víc konců řádku = 3 a víc prázdných řádků
_BLANKS = re.compile(r"\n{4,}")

RULES = ["uuencode", "base64", "binary_other", "spaces", "repeats", "blank_lines", "control"]


def _sub(pattern: re.Pattern, repl, text: str, stats: dict | None, key: str) -> str:
    """re.sub, který si navíc zapíše, kolik znaků pravidlo odstranilo."""
    if stats is None:
        return pattern.sub(repl, text)
    new, n = pattern.subn(repl, text)
    if n:
        stats[key] = stats.get(key, 0) + len(text) - len(new)
    return new


def clean_text(text: str, source: str = "", stats: dict | None = None) -> str:
    """Vrátí vyčištěný text. `source` = jméno zdroje (pravidla jsou zatím pro všechny zdroje stejná,
    parametr je připravený pro výjimky a statistiky).

    Když předáš slovník `stats`, přičte do něj, kolik znaků které pravidlo odstranilo
    (klíče viz RULES).
    """
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _sub(_PAGEBREAK, "\n", text, stats, "control")
    text = _sub(_CONTROL, "", text, stats, "control")

    text += "\n"  # regulární výrazy počítají s koncem řádku i u posledního řádku
    if _BINARY_HINT.search(text):
        text = _sub(_UU_BLOCK, "", text, stats, "uuencode")
        text = _sub(_UU_RUN, "", text, stats, "uuencode")
        text = _sub(_B64_RUN, "\n", text, stats, "base64")
        text = _sub(_B64_WORD, "", text, stats, "base64")
        text = _sub(_OTHER_RUN, "", text, stats, "binary_other")

    text = _sub(_TRAILING, "", text, stats, "spaces")
    text = _sub(_LEAD, r"\1", text, stats, "spaces")
    text = _sub(_INNER, r"\1", text, stats, "spaces")

    text = _sub(_REPEAT_PUNCT, lambda m: m.group(1) * MAX_PUNCT, text, stats, "repeats")
    text = _sub(_REPEAT_WORD, lambda m: m.group(1) * MAX_WORDCH, text, stats, "repeats")

    text = _sub(_BLANKS, "\n\n\n", text, stats, "blank_lines")
    return text.strip()


def is_long_enough(text: str) -> bool:
    return len(text) >= MIN_CHARS
