from pathlib import Path

from llm2005.db import Database
from llm2005.ingest import ingest
from llm2005.sources import SOURCES
from llm2005.sources.pg19 import clean_year, read_pg19

FIXTURE = str(Path(__file__).parent / "fixtures" / "pg19")


def test_pg19_reads_books_with_metadata():
    docs = {d.source_id: d for d in read_pg19(FIXTURE)}
    assert set(docs) == {"101", "102", "103", "104"}  # train + validation + test
    book = docs["101"]
    assert book.source == "pg19"
    assert book.date == "1890"
    assert book.title == "The Old Lighthouse by Jane Example"
    assert book.url.endswith("/101")
    assert book.text.startswith("THE OLD LIGHTHOUSE")
    assert docs["103"].title == "Letters, Notes and Sketches by A. Writer"  # čárka v názvu
    assert docs["102"].date == ""  # rok chybí
    assert docs["104"].date == ""  # kniha není v metadatech


def test_pg19_crlf_is_normalized(tmp_path):
    (tmp_path / "metadata.csv").write_text("7,T,1850,u\n", encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "7.txt").write_bytes(b"line one\r\nline two\r\n")
    (doc,) = read_pg19(str(tmp_path))
    assert doc.text == "line one\nline two"


def test_clean_year():
    assert clean_year("1899") == "1899"
    assert clean_year("") == ""
    assert clean_year("0") == ""
    assert clean_year("abc") == ""


def test_pg19_ingest_rejects_books_without_date(tmp_path):
    assert SOURCES["pg19"] is read_pg19
    db = Database(tmp_path / "t.sqlite")
    counts = ingest(read_pg19(FIXTURE), db)
    assert counts["added"] == 2 and counts["no_date"] == 2
    rejected = sorted(db.conn.execute("SELECT source_id, reason FROM rejected"))
    assert rejected == [("102", "no_date"), ("104", "no_date")]
    (source, n, chars, dmin, dmax), = db.stats()
    assert source == "pg19" and n == 2 and dmin == "1890" and dmax == "1905"
