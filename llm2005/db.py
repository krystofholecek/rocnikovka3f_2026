"""Databáze dokumentů (jeden soubor SQLite).

Každý dokument má uložený zdroj a datum, takže se dá vždy dohledat, odkud je.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from .document import Document

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id        INTEGER PRIMARY KEY,
    source    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title     TEXT,
    date      TEXT NOT NULL,
    url       TEXT,
    text      TEXT NOT NULL,
    n_chars   INTEGER NOT NULL,
    added_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS rejected (
    source    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    date      TEXT,
    reason    TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def add(self, doc: Document) -> bool:
        """Uloží dokument. Vrací False, pokud už v databázi je."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO documents (source, source_id, title, date, url, text, n_chars)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc.source, doc.source_id, doc.title, doc.date, doc.url, doc.text, len(doc.text)),
        )
        return cur.rowcount == 1

    def reject(self, doc: Document, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO rejected (source, source_id, date, reason) VALUES (?, ?, ?, ?)",
            (doc.source, doc.source_id, doc.date, reason),
        )

    def commit(self) -> None:
        self.conn.commit()

    def iter_texts(self, source: str | None = None) -> Iterator[str]:
        q, args = "SELECT text FROM documents", ()
        if source:
            q, args = q + " WHERE source = ?", (source,)
        for (text,) in self.conn.execute(q, args):
            yield text

    def stats(self) -> list[tuple]:
        """Počet dokumentů, znaků a nejnovější datum podle zdroje."""
        return list(
            self.conn.execute(
                "SELECT source, COUNT(*), SUM(n_chars), MIN(date), MAX(date)"
                " FROM documents GROUP BY source ORDER BY source"
            )
        )

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
