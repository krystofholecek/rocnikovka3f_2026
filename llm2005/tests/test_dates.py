"""Nulová data (1969-12-31, 1979-12-31 …) se berou jako „bez data“ (no_date)."""
import sqlite3

from llm2005.datecheck import is_placeholder
from llm2005.db import Database
from llm2005.document import Document
from llm2005.fix_dates import find_suspicious, move_to_rejected
from llm2005.sources import enron, usenet


def test_placeholder_values():
    for d in ("1969-12-31", "1970-01-01", "1979-12-31", "1980-01-01"):
        assert is_placeholder(d)
    assert not is_placeholder("1990-05-09")


def test_usenet_parse_date():
    assert usenet.parse_date("9 May 90 17:43:27 GMT") == "1990-05-09"
    assert usenet.parse_date("Wed, 31-Dec-69 18:59:59 EDT") == ""  # unixový čas -1 ze skutečné hlavičky
    assert usenet.parse_date("Fri, 23 Oct 70 10:00:00 GMT") == ""  # před vznikem Usenetu
    assert usenet.parse_date("Thu, 1 Jan 70 00:00:00 GMT") == ""


def test_enron_parse_date():
    assert enron.parse_date("Mon, 14 May 2001 16:39:00 -0700 (PDT)") == "2001-05-14"
    assert enron.parse_date("Mon, 31 Dec 1979 16:00:00 -0800 (PST)") == ""  # nula Outlooku ze skutečné hlavičky


def test_fix_dates_moves_only_suspicious(tmp_path):
    path = tmp_path / "d.sqlite"
    db = Database(path)
    docs = [("usenet", "a", "1969-12-31"), ("usenet", "b", "1970-10-23"), ("usenet", "c", "1990-05-09"),
            ("enron", "d", "1979-12-31"), ("enron", "e", "2001-05-14"),
            ("courtlistener", "f", "1980-01-02"), ("pg19", "g", "1970")]
    for s, sid, d in docs:
        db.add(Document(source=s, source_id=sid, title=None, date=d, url=None, text="x" * 300))
    db.close()
    conn = sqlite3.connect(path)
    rows = find_suspicious(conn)
    assert sorted(r[2] for r in rows) == ["a", "b", "d"]
    move_to_rejected(conn, rows)
    left = sorted(r[0] for r in conn.execute("SELECT source_id FROM documents"))
    assert left == ["c", "e", "f", "g"]
    rej = conn.execute("SELECT source_id, reason FROM rejected ORDER BY source_id").fetchall()
    assert rej == [("a", "suspicious_date"), ("b", "suspicious_date"), ("d", "suspicious_date")]
    conn.close()
