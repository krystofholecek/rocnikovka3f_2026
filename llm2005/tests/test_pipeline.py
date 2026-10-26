from pathlib import Path

from llm2005.datecheck import is_allowed, to_date
from llm2005.db import Database
from llm2005.ingest import ingest
from llm2005.sources.wikipedia import clean_wikitext, read_wikipedia_history

FIXTURE = str(Path(__file__).parent / "fixtures" / "history_sample.xml")


def test_cutoff():
    assert is_allowed("2005-12-31")
    assert is_allowed("2005-12-31T23:59:59Z")
    assert not is_allowed("2006-01-01")
    assert is_allowed("1899")
    assert not is_allowed(None)
    assert not is_allowed("neznámé")
    assert to_date("2005") is not None


def test_wikipedia_takes_last_revision_before_cutoff():
    docs = {d.title: d for d in read_wikipedia_history(FIXTURE)}
    assert set(docs) == {"Apple Computer"}  # Twitter až po 2005, redirect a Talk vynechány
    apple = docs["Apple Computer"]
    assert apple.date == "2005-11-20"
    assert "iPod music player" in apple.text
    assert "FUTURE TEXT" not in apple.text
    assert "[[" not in apple.text and "'''" not in apple.text


def test_clean_wikitext():
    assert clean_wikitext("A [[b|c]] {{x}} ''d''") == "A c  d"


def test_ingest_into_db(tmp_path):
    db = Database(tmp_path / "t.sqlite")
    counts = ingest(read_wikipedia_history(FIXTURE), db)
    assert counts["added"] == 1
    again = ingest(read_wikipedia_history(FIXTURE), db)
    assert again["duplicate"] == 1
    (source, n, chars, dmin, dmax), = db.stats()
    assert source == "wikipedia" and n == 1 and dmax <= "2005-12-31"
