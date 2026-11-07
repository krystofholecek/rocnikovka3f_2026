"""Testy čištění textu (llm2005/clean.py).

Hlavní pravidlo: běžný text se NESMÍ změnit. Odstraňuje se jen šum (binární přílohy,
dlouhé řady mezer a stejných znaků, řídicí znaky).
"""
import base64
import binascii

from llm2005.clean import MIN_CHARS, clean_text, is_long_enough

# --- ukázky běžného textu (vymyšlené, ve stylu jednotlivých zdrojů) ---
EMAIL = """Subject: Re: Q3 forecast

Mark,

Please find the revised numbers below.  I moved the West desk figures into
the second table as you asked.

    Region  Q2    Q3
    West    12.4  13.1
    East    9.8   10.2

Let me know if Monday 10am works for the call.

Thanks,
Sarah"""

BLOG = """OMG!!! I finally got my iPod nano today :) Spent the whole evening ripping CDs...

Sooooo happy right now. Will post pics tomorrow -- my camera is dead lol.

*** UPDATE: pics are up! ***"""

WIKIPEDIA = """Mozilla Firefox is a free, open source web browser descended from the Mozilla Application Suite.

== History ==
The project began as an experimental branch of the Mozilla project by Dave Hyatt, Joe Hewitt and Blake Ross.

=== Release history ===
* Firefox 1.0 – November 9, 2004
* Firefox 1.5 – November 29, 2005"""

LEGAL = """UNITED STATES COURT OF APPEALS
FOR THE NINTH CIRCUIT

No. 03-15678

OPINION

    The judgment of the district court is AFFIRMED in part and REVERSED in part, and the case is
REMANDED for further proceedings consistent with this opinion. See 28 U.S.C. § 1291; Smith v. Jones,
123 F.3d 456, 459 (9th Cir. 1997) (\"[t]he standard of review is de novo\").

    ________________
    Circuit Judge"""

USENET_CODE = """Subject: Re: help with pointers in C

Here is the routine I use, it works with both cc and gcc:

#include <stdio.h>

int
strlen2(s)
char *s;
{
\tregister char *p = s;

\twhile (*p)
\t\tp++;
\treturn (p - s);\t/* length */
}

/*----------------------------------------------------------------*/

Hope this helps."""

NORMAL = {"email": EMAIL, "blog": BLOG, "wikipedia": WIKIPEDIA, "legal": LEGAL, "usenet": USENET_CODE}


def test_normal_text_unchanged():
    for source, text in NORMAL.items():
        if source == "usenet":
            continue  # komentářová čára v kódu se zkrátí, viz další test
        assert clean_text(text, source) == text, source


def test_usenet_code_only_separator_shortened():
    out = clean_text(USENET_CODE, "usenet")
    assert out == USENET_CODE.replace("-" * 64, "-" * 8)


def test_normal_text_no_stats():
    for source, text in NORMAL.items():
        if source == "usenet":
            continue
        st: dict = {}
        clean_text(text, source, st)
        assert st == {}, (source, st)


# --- šum ---

def _uuencode(data: bytes) -> str:
    lines = [binascii.b2a_uu(data[i:i + 45]).decode("ascii").rstrip("\n") for i in range(0, len(data), 45)]
    return "begin 644 picture.gif\n" + "\n".join(lines) + "\n`\nend\n"


def test_uuencode_block_removed():
    binary = bytes(range(256)) * 4
    text = "Here is the picture you wanted:\n\n" + _uuencode(binary) + "\nEnjoy!"
    st: dict = {}
    out = clean_text(text, "usenet", st)
    assert out == "Here is the picture you wanted:\n\n\nEnjoy!"
    assert st["uuencode"] > 1000


def test_uuencode_part_without_header_removed():
    lines = "\n".join("M" + "8" * 60 for _ in range(10))
    text = "part 2 of 3\n" + lines + "\nmore text follows here"
    assert clean_text(text, "usenet") == "part 2 of 3\nmore text follows here"


def test_base64_mime_removed():
    payload = base64.encodebytes(bytes(2000)).decode("ascii")  # řádky "AAAA…" po 76 znacích
    text = ("See the attached spreadsheet.\n\n"
            "Content-Type: application/octet-stream; name=\"q3.xls\"\n"
            "Content-Transfer-Encoding: base64\n"
            "Content-Disposition: attachment; filename=\"q3.xls\"\n\n"
            + payload + "\nRegards, Tom")
    st: dict = {}
    out = clean_text(text, "enron", st)
    assert "AAAA" not in out and "Content-Transfer" not in out
    assert out.startswith("See the attached spreadsheet.") and out.endswith("Regards, Tom")
    assert st["base64"] > 2000


def test_long_base64_word_removed():
    text = "key: " + "QUJD" * 100 + " end of message"
    assert clean_text(text) == "key:  end of message"


def test_space_runs_shortened():
    text = "Name" + " " * 40 + "Value\n" + " " * 100 + "deeply indented\nend   "
    out = clean_text(text)
    assert out == "Name    Value\n" + " " * 16 + "deeply indented\nend"


def test_wide_table_columns_compressed():
    # kompromis: široké zarovnání sloupců se zkrátí na 4 mezery, slova i čísla zůstanou
    text = "Region          Q2          Q3\nWest            12.4        13.1"
    assert clean_text(text) == "Region    Q2    Q3\nWest    12.4    13.1"


def test_tabs_runs_shortened():
    assert clean_text("a\t\t\t\t\t\t\tb") == "a\t\t\t\tb"


def test_repeated_chars_shortened():
    text = "Title\n" + "-" * 70 + "\n" + "=" * 30 + "\nzzz" + "z" * 40 + "\nso*" + "*" * 20
    out = clean_text(text)
    assert out == "Title\n--------\n========\n" + "z" * 16 + "\nso********"


def test_blank_lines_limited():
    assert clean_text("one\n\n\n\n\n\n\ntwo\n\n\nthree") == "one\n\n\ntwo\n\n\nthree"


def test_blank_lines_with_spaces():
    assert clean_text("one\n  \n\t\n   \n \n\ntwo") == "one\n\n\ntwo"


def test_control_chars_removed():
    text = "Hello\x00 wor\x1bld\x07\r\nnext\fpage\x85"
    assert clean_text(text) == "Hello world\nnext\npage"


def test_url_list_kept():
    urls = "\n".join(f"http://www.example{i}.com/very/long/path/to/some/page{i}.html" for i in range(8))
    assert clean_text(urls) == urls


def test_min_length():
    assert MIN_CHARS == 200
    assert not is_long_enough("x" * 199)
    assert is_long_enough("x" * 200)
