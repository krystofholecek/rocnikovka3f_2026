"""Testy zdrojů Enron, blogy a arXiv na malých umělých datech (vytvoří se v dočasné složce)."""
import io
import json
import tarfile
import zipfile

from llm2005.db import Database
from llm2005.ingest import ingest
from llm2005.sources import SOURCES, arxiv, blogs, enron

LONG = "This is a long enough sentence for the minimum length filter. " * 5

# ---------- Enron ----------

MAIL_1 = f"""Message-ID: <1.JavaMail.evans@thyme>
Date: Mon, 14 May 2001 16:39:00 -0700 (PDT)
From: phillip.allen@enron.com
Subject: Re: meeting

{LONG}

 -----Original Message-----
From: someone@enron.com
old quoted text that should be removed
"""
MAIL_NO_DATE = f"""Message-ID: <2.JavaMail.evans@thyme>
Subject: no date here

Another body. {LONG}
"""
MAIL_TOO_NEW = f"""Message-ID: <3.JavaMail.evans@thyme>
Date: Tue, 1 Jan 2044 10:00:00 -0800 (PST)
Subject: broken date

Body from the future. {LONG}
"""


def _make_tar(path, files: dict[str, str]):
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_enron_parse_date():
    assert enron.parse_date("Mon, 14 May 2001 16:39:00 -0700 (PDT)") == "2001-05-14"
    assert enron.parse_date("") == ""
    assert enron.parse_date("not a date") == ""


def test_enron_reads_tar_and_strips_quotes(tmp_path):
    archive = tmp_path / "enron.tar.gz"
    _make_tar(archive, {"maildir/allen-p/inbox/1.": MAIL_1})
    (doc,) = enron.read_enron(str(archive))
    assert doc.source == "enron" and doc.date == "2001-05-14"
    assert doc.title == "Re: meeting"
    assert doc.text.startswith("Subject: Re: meeting\n\nThis is a long")
    assert "quoted" not in doc.text and "Original Message" not in doc.text
    assert doc.url == "maildir/allen-p/inbox/1."


def test_enron_ingest_dedup_and_rejects(tmp_path):
    assert SOURCES["enron"] is enron.read_enron
    archive = tmp_path / "enron.tar.gz"
    _make_tar(archive, {
        "maildir/allen-p/inbox/1.": MAIL_1,
        "maildir/allen-p/all_documents/7.": MAIL_1,  # stejný e-mail v jiné složce
        "maildir/allen-p/inbox/2.": MAIL_NO_DATE,
        "maildir/allen-p/inbox/3.": MAIL_TOO_NEW,
    })
    db = Database(tmp_path / "t.sqlite")
    counts = ingest(enron.read_enron(str(archive)), db)
    assert counts == {"added": 1, "too_new": 1, "no_date": 1, "too_short": 0, "duplicate": 1}
    reasons = sorted(r for (r,) in db.conn.execute("SELECT reason FROM rejected"))
    assert reasons == ["no_date", "too_new"]


# ---------- Blogy ----------

BLOG_XML = f"""<Blog>

<date>14,May,2004</date>
<post>

       {LONG} urlLink http://example.com &amp; more

</post>

<date>02,juin,2004</date>
<post>
     Un texte en français. {LONG}
</post>

<date>05,Smarch,2004</date>
<post>
     Unknown month. {LONG}
</post>

<date>06,May,2004</date>
<post>
     too short
</post>

</Blog>
"""


def test_blogs_parse_date():
    assert blogs.parse_date("14,May,2004") == "2004-05-14"
    assert blogs.parse_date("02,juin,2004") == "2004-06-02"
    assert blogs.parse_date("05,Smarch,2004") == ""
    assert blogs.parse_date("garbage") == ""


def test_blogs_reads_zip_and_ingest(tmp_path):
    assert SOURCES["blogs"] is blogs.read_blogs
    archive = tmp_path / "blogs.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("blogs/1000331.female.37.indUnk.Leo.xml", BLOG_XML.encode("latin-1"))
    docs = list(blogs.read_blogs(str(archive)))
    assert [d.source_id for d in docs] == ["1000331-0", "1000331-1", "1000331-2", "1000331-3"]
    first = docs[0]
    assert first.date == "2004-05-14" and first.title == "1000331.female.37.indUnk.Leo"
    assert "urlLink" not in first.text and "& more" in first.text
    assert docs[1].text.startswith("Un texte en français.")  # latin-1 se přečte správně

    db = Database(tmp_path / "t.sqlite")
    counts = ingest(blogs.read_blogs(str(archive)), db)
    assert counts == {"added": 2, "too_new": 0, "no_date": 1, "too_short": 1, "duplicate": 0}


# ---------- arXiv ----------

OAI_PAGE = f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
<ListRecords>
<record><header><identifier>oai:arXiv.org:hep-th/9901001</identifier></header>
<metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
  <id>hep-th/9901001</id><created>1999-01-01</created>
  <title>Old
     paper</title><abstract>  {LONG} </abstract>
</arXiv></metadata></record>
<record><header><identifier>oai:arXiv.org:0501.0001</identifier></header>
<metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/">
  <id>0501.0001</id><created>2005-01-03</created><updated>2009-06-01</updated>
  <title>Revised later</title><abstract>{LONG}</abstract>
</arXiv></metadata></record>
<record><header status="deleted"><identifier>oai:arXiv.org:x</identifier></header></record>
<resumptionToken cursor="0">TOKEN123</resumptionToken>
</ListRecords>
</OAI-PMH>
""".encode("utf-8")


def test_arxiv_parse_page():
    records, token = arxiv.parse_page(OAI_PAGE)
    assert token == "TOKEN123"
    assert [r["id"] for r in records] == ["hep-th/9901001", "0501.0001"]
    assert records[0]["title"] == "Old paper" and records[0]["updated"] == ""


def test_arxiv_read_and_ingest(tmp_path):
    assert SOURCES["arxiv"] is arxiv.read_arxiv
    records, _ = arxiv.parse_page(OAI_PAGE)
    path = tmp_path / "arxiv-2005.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    docs = list(arxiv.read_arxiv(str(path)))
    assert docs[0].date == "1999-01-01" and docs[0].text.startswith("Old paper\n\n")
    assert docs[1].date == "2009-06-01"  # bere se poslední verze -> odmítne se
    db = Database(tmp_path / "t.sqlite")
    counts = ingest(arxiv.read_arxiv(str(path)), db)
    assert counts["added"] == 1 and counts["too_new"] == 1


def test_arxiv_cut_partial_line(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_bytes(b'{"id": 1}\n{"id": 2')
    arxiv._cut_partial_line(path)
    assert path.read_bytes() == b'{"id": 1}\n'
