"""Testy zdrojů s moderním jazykem: wiki diskuze a sesterské projekty, Usenet, CourtListener.

Všechno na malých umělých datech vytvořených v dočasné složce.
"""
import bz2
import csv
import io
import random
import tarfile

from llm2005.db import Database
from llm2005.ingest import ingest
from llm2005.sources import SOURCES, courtlistener, mediawiki, usenet

LONG = "This is a long enough sentence for the minimum length filter. " * 5

# ---------- MediaWiki (diskuze Wikipedie, Wikibooks, ...) ----------

WIKI_XML = f"""<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.3/">
<siteinfo><sitename>Wikipedia</sitename>
<namespaces>
  <namespace key="0" />
  <namespace key="1">Talk</namespace>
  <namespace key="2">User</namespace>
  <namespace key="3">User talk</namespace>
  <namespace key="4">Wikipedia</namespace>
  <namespace key="5">Wikipedia talk</namespace>
  <namespace key="10">Template</namespace>
</namespaces></siteinfo>
<page><title>Talk:Cats</title>
  <revision><timestamp>2004-03-01T10:00:00Z</timestamp><text>old</text></revision>
  <revision><timestamp>2005-06-02T10:00:00Z</timestamp><text>== Tail length ==
I think the article is wrong about tails. {LONG} --[[User:Bob|Bob]] 10:00, 2 June 2005 (UTC)
:No, it is fine. [[User:Ann|Ann]] ([[User talk:Ann|talk]]) 11:00, 2005 Jun 2 (UTC)
{{{{unsigned|Carl}}}}</text></revision>
</page>
<page><title>Talk:Dogs</title>
  <revision><timestamp>2005-01-01T00:00:00Z</timestamp><text>{LONG}</text></revision>
  <revision><timestamp>2006-02-01T00:00:00Z</timestamp><text>edited later {LONG}</text></revision>
</page>
<page><title>Cats</title>
  <revision><timestamp>2005-01-01T00:00:00Z</timestamp><text>Article text. {LONG}</text></revision>
</page>
<page><title>Template:Hello</title>
  <revision><timestamp>2005-01-01T00:00:00Z</timestamp><text>{LONG}</text></revision>
</page>
<page><title>User talk:Bob</title>
  <revision><timestamp>2005-05-05T00:00:00Z</timestamp><text>#REDIRECT [[User talk:Robert]]</text></revision>
</page>
<page><title>Wikipedia:Village pump</title>
  <revision><timestamp>2005-12-31T23:59:59Z</timestamp><text>Hello everyone. {LONG}</text></revision>
</page>
</mediawiki>
"""


def test_clean_talk_removes_signatures():
    t = mediawiki.clean_talk(
        "I agree. --[[User:Bob|Bob]] 10:00, 2 June 2005 (UTC)\n"
        ":Me too [[User:Ann|Ann]] <small>([[User talk:Ann|talk]])</small> 11:00, 2005 Jun 2 (UTC)\n"
        "Ask [[User:Carl|Carl]] about it. ~~~~"
    )
    assert t == "I agree.\nMe too\nAsk Carl about it."  # jméno uprostřed věty zůstane
    assert "UTC" not in t and "[[" not in t


def test_namespace_of():
    prefixes = {"Talk": 1, "User talk": 3, "Wikipedia": 4}
    assert mediawiki.namespace_of("Talk:Cats", prefixes) == 1
    assert mediawiki.namespace_of("Cats", prefixes) == 0
    assert mediawiki.namespace_of("Star Wars: Episode I", prefixes) == 0


def test_wikitalk_reads_only_talk_namespaces(tmp_path):
    assert SOURCES["wikitalk"] is mediawiki.read_wikitalk
    path = tmp_path / "dump.xml"
    path.write_text(WIKI_XML, encoding="utf-8")
    docs = {d.title: d for d in mediawiki.read_wikitalk(str(path))}
    # článek (ns 0), šablona a přesměrování se nevracejí
    assert set(docs) == {"Talk:Cats", "Talk:Dogs", "Wikipedia:Village pump"}
    cats = docs["Talk:Cats"]
    assert cats.source == "wikipedia-talk" and cats.date == "2005-06-02"
    assert cats.text.startswith("Talk:Cats\n\nTail length\nI think the article is wrong")
    assert "Bob" not in cats.text and "UTC" not in cats.text and "unsigned" not in cats.text
    assert docs["Talk:Dogs"].date == "2006-02-01"  # poslední revize je z roku 2006

    db = Database(tmp_path / "t.sqlite")
    counts = ingest(mediawiki.read_wikitalk(str(path)), db)
    assert counts == {"added": 2, "too_new": 1, "no_date": 0, "too_short": 0, "duplicate": 0}
    assert list(db.conn.execute("SELECT source_id, reason FROM rejected")) == [("Talk:Dogs", "too_new")]


def test_sister_projects_take_content_too(tmp_path):
    path = tmp_path / "dump.xml"
    path.write_text(WIKI_XML, encoding="utf-8")
    docs = list(SOURCES["wikibooks"](str(path)))
    assert {d.title for d in docs} == {"Cats", "Talk:Cats", "Talk:Dogs", "Wikipedia:Village pump"}
    assert all(d.source == "wikibooks" for d in docs)
    for name in ("wikinews", "wikiquote", "wikisource"):
        assert name in SOURCES


# ---------- Usenet (UTZOO) ----------

POST = f"""Path: utzoo!utgpu!news
From: someone@site.edu (Some One)
Newsgroups: comp.windows.ms
Subject: Re: WinQVT and file transfers
Message-ID: <1234@site.edu>
Date: 9 May 90 17:43:27 GMT
Lines: 12

In article <99@other.edu> other@other.edu (Other) writes:
>quoted line that should go away
I have the same problem. {LONG}

--
Some One   someone@site.edu   "my witty signature"
"""
POST_NO_DATE = f"""From: x@y
Newsgroups: net.general
Subject: old A news style
Message-ID: <5@y>

{LONG}
"""
POST_SOURCES = f"""Newsgroups: comp.sources.unix
Subject: v01i001: a program
Message-ID: <6@y>
Date: 1 Jan 88 00:00:00 GMT

{LONG}
"""


def _make_tar(path, files, cut_in=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in files.items():
            data = content.encode("latin-1")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    raw = buf.getvalue()
    if cut_in:  # useknout archiv uprostřed zadaného textu (jako poškozená páska)
        raw = raw[: raw.index(cut_in.encode()) + 5]
    path.write_bytes(bz2.compress(raw))


def test_usenet_parse_date():
    assert usenet.parse_date("9 May 90 17:43:27 GMT") == "1990-05-09"
    assert usenet.parse_date("Thu, 1 Jan 81 10:00:00 EST") == "1981-01-01"
    assert usenet.parse_date("") == "" and usenet.parse_date("garbage") == ""


def test_usenet_clean_and_dedup(tmp_path):
    assert SOURCES["usenet"] is usenet.read_usenet
    _make_tar(tmp_path / "news100f1.tar.bz2", {
        "b1/comp/windows/ms/1": POST,
        "b1/net/general/2": POST_NO_DATE,
        "b1/comp/sources/unix/3": POST_SOURCES,  # zdrojový kód -> přeskočí se
    })
    _make_tar(tmp_path / "news101f1.tar.bz2", {"b2/comp/windows/ms/1": POST})  # stejná zpráva znovu
    docs = list(usenet.read_usenet(str(tmp_path)))
    assert len(docs) == 3
    doc = docs[0]
    assert doc.date == "1990-05-09" and doc.source_id == "<1234@site.edu>"
    assert doc.text.startswith("Subject: Re: WinQVT and file transfers\n\nI have the same problem.")
    assert "quoted" not in doc.text and "writes:" not in doc.text
    assert "signature" not in doc.text and "Path:" not in doc.text

    db = Database(tmp_path / "t.sqlite")
    counts = ingest(usenet.read_usenet(str(tmp_path)), db)
    assert counts == {"added": 1, "too_new": 0, "no_date": 1, "too_short": 0, "duplicate": 1}


def test_usenet_truncated_tape(tmp_path):
    # useknutá páska: první zprávu přečteme, konec tiše skončí
    _make_tar(tmp_path / "news102f1.tar.bz2",
              {"a/1": POST, "a/2": POST.replace("<1234@", "<1235@")}, cut_in="<1235@")
    docs = list(usenet.read_usenet(str(tmp_path / "news102f1.tar.bz2")))
    assert [d.source_id for d in docs] == ["<1234@site.edu>"]


# ---------- CourtListener ----------

def _write_csv_bz2(path, header, rows, cut=0):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    data = bz2.compress(buf.getvalue().encode("utf-8"), compresslevel=1)  # bloky po 100 kB
    path.write_bytes(data[: len(data) - cut] if cut else data)


def test_courtlistener_read_and_ingest(tmp_path):
    assert SOURCES["courtlistener"] is courtlistener.read_courtlistener
    _write_csv_bz2(
        tmp_path / courtlistener.CLUSTERS,
        ["id", "date_filed", "date_filed_is_approximate", "case_name"],
        [
            ["1", "1995-03-04", "f", "Smith v. Jones"],
            ["2", "1890-01-01", "f", "Old v. Case"],      # před rokem 1980 -> vynechá se
            ["3", "2010-05-05", "f", "New v. Case"],      # too_new
            ["4", "2001-01-01", "t", "Approx v. Date"],   # odhadnuté datum -> no_date
        ],
    )
    _write_csv_bz2(
        tmp_path / courtlistener.OPINIONS,
        ["id", "plain_text", "html_with_citations", "cluster_id"],
        [
            ["10", "", f"<p>The court holds that {LONG}</p><page-number>*12</page-number><p>Affirmed.</p>", "1"],
            ["11", LONG, "", "2"],
            ["12", LONG, "", "3"],
            ["13", LONG, "", "4"],
        ],
    )
    docs = list(courtlistener.read_courtlistener(str(tmp_path)))
    assert [d.source_id for d in docs] == ["10", "12", "13"]
    first = docs[0]
    assert first.date == "1995-03-04" and first.title == "Smith v. Jones"
    assert first.text.startswith("The court holds that") and first.text.endswith("Affirmed.")
    assert "<p>" not in first.text and "*12" not in first.text

    db = Database(tmp_path / "t.sqlite")
    counts = ingest(courtlistener.read_courtlistener(str(tmp_path)), db)
    assert counts == {"added": 1, "too_new": 1, "no_date": 1, "too_short": 0, "duplicate": 0}


def test_courtlistener_truncated_opinions(tmp_path):
    _write_csv_bz2(tmp_path / courtlistener.CLUSTERS,
                   ["id", "date_filed", "date_filed_is_approximate", "case_name"],
                   [["1", "1995-03-04", "f", "A v. B"]])
    rnd = random.Random(0)  # náhodný text -> archiv má víc bloků a jde useknout uprostřed
    rows = [[str(i), LONG + "".join(rnd.choice("abcdefgh ") for _ in range(5000)), "", "1"]
            for i in range(50)]
    _write_csv_bz2(tmp_path / courtlistener.OPINIONS,
                   ["id", "plain_text", "html_with_citations", "cluster_id"], rows, cut=500)
    docs = list(courtlistener.read_courtlistener(str(tmp_path)))
    assert 0 < len(docs) < 50  # useknutý archiv: přečte se začátek, bez pádu
