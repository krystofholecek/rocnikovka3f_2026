"""Přesune dokumenty s podezřelým „nulovým“ datem z tabulky documents do rejected.

    python -m llm2005.fix_dates            # jen ukáže, co by se přesunulo
    python -m llm2005.fix_dates --apply    # opravdu přesune (předtím zálohuj databázi!)

Proč: parsery Usenetu a Enronu dřív braly výchozí datum z hlavičky jako skutečné.
Ověřeno v surových datech (2026-11-06):
* Usenet: "Date: Wed, 31-Dec-69 18:59:59 EDT" = unixový čas -1 (news software datum neznal),
  pár zpráv s rokem 1970 = počítač s nenastavenými hodinami (Usenet vznikl až 1979/80).
* Enron: "Date: Mon, 31 Dec 1979 16:00:00 -0800 (PST)" = nula Outlooku (1. 1. 1980 0:00 UTC),
  přitom jde o e-maily z let 2000–2001.
Opravené parsery (usenet.py, enron.py) už taková data vrací jako prázdná -> no_date.
Tady opravujeme dokumenty, které se do databáze dostaly dřív. Důvod v rejected: suspicious_date.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from .datecheck import PLACEHOLDER_DATES
from .sources.usenet import USENET_START

REASON = "suspicious_date"


def find_suspicious(conn: sqlite3.Connection) -> list[tuple[int, str, str, str]]:
    """(id, source, source_id, date) dokumentů s podezřelým datem (stejná pravidla jako parsery)."""
    ph = sorted(PLACEHOLDER_DATES)
    marks = ",".join("?" * len(ph))
    q = (f"SELECT id, source, source_id, date FROM documents WHERE "
         f"(source = 'enron' AND substr(date, 1, 10) IN ({marks})) OR "
         f"(source = 'usenet' AND (substr(date, 1, 10) IN ({marks}) OR date < ?))")
    return list(conn.execute(q, (*ph, *ph, USENET_START)))


def move_to_rejected(conn: sqlite3.Connection, rows: list[tuple[int, str, str, str]]) -> None:
    """V jedné transakci: zapíše do rejected a smaže z documents."""
    with conn:
        conn.executemany("INSERT INTO rejected (source, source_id, date, reason) VALUES (?, ?, ?, ?)",
                         [(s, sid, d, REASON) for _, s, sid, d in rows])
        conn.executemany("DELETE FROM documents WHERE id = ?", [(i,) for i, *_ in rows])


def summarize(rows: list[tuple[int, str, str, str]]) -> dict:
    by: dict[str, dict[str, int]] = {}
    for _, s, _, d in rows:
        by.setdefault(s, {})
        by[s][d] = by[s].get(d, 0) + 1
    return {s: {"total": sum(v.values()), "by_date": dict(sorted(v.items()))} for s, v in sorted(by.items())}


def main() -> None:
    p = argparse.ArgumentParser(description="Dokumenty s nulovým datem -> rejected (suspicious_date)")
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--apply", action="store_true", help="opravdu přesunout (jinak jen výpis)")
    p.add_argument("--out", default="data/progress/date-fix.json")
    a = p.parse_args()
    conn = sqlite3.connect(a.db)
    t0 = time.time()
    rows = find_suspicious(conn)
    summary = summarize(rows)
    print(f"Nalezeno {len(rows)} dokumentů s podezřelým datem ({time.time() - t0:.0f} s):")
    for s, v in summary.items():
        print(f"  {s:<10} {v['total']:>6}   {v['by_date']}")
    if a.apply and rows:
        move_to_rejected(conn, rows)
        print(f"Přesunuto do rejected s důvodem '{REASON}'.")
    conn.close()
    result = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "applied": bool(a.apply),
              "reason": REASON, "moved": len(rows) if a.apply else 0, "found": len(rows),
              "placeholder_dates": sorted(PLACEHOLDER_DATES), "usenet_start": USENET_START,
              "by_source": summary}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
