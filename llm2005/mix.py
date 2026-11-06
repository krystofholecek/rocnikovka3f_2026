"""Vyvážená směs zdrojů – kolik textu vezmeme z kterého zdroje.

Proč: v databázi je nejvíc knih z 19. století (PG-19) a soudních rozhodnutí.
Kdybychom brali všechno, model (i tokenizér) by „mluvil“ jako stará kniha nebo právník.
Proto z každého zdroje bereme jen určitý podíl znaků (configs/mix.json).

Jak to funguje (jednoduše):
1. Malé zdroje ze seznamu "whole" bereme celé.
2. Zbytek rozpočtu (max_chars) rozdělíme podle vah ("weights").
3. Dokumenty každého zdroje zamícháme pevným seedem (2005) – výsledek je pokaždé stejný.
4. Bereme celé dokumenty v tomto pořadí, dokud zdroj nemá svůj podíl.
5. Když zdroj nemá dost textu, vezmeme ho celý a to, co chybí, rozdělíme
   poměrně mezi ostatní zdroje (pokračují ve svém zamíchaném pořadí).

    python -m llm2005.mix      # jen ukáže plán podle počtu dokumentů
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

DEFAULT_CONFIG = "configs/mix.json"
FETCH_BATCH = 256  # kolik dokumentů načteme z databáze najednou


@dataclass
class MixConfig:
    seed: int
    max_chars: int
    weights: dict[str, float]
    whole: list[str] = field(default_factory=list)


def load_mix_config(path: str | Path = DEFAULT_CONFIG) -> MixConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return MixConfig(
        seed=int(raw.get("seed", 2005)),
        max_chars=int(raw["max_chars"]),
        weights={k: float(v) for k, v in raw["weights"].items()},
        whole=list(raw.get("whole", [])),
    )


def allocate(budget: int, weights: dict[str, float], available: dict[str, int]) -> dict[str, int]:
    """Rozdělí rozpočet znaků podle vah; kdo nemá dost, dá všechno a zbytek jde ostatním.

    Čistá funkce bez databáze – stejné pravidlo jako iter_mix, jen se známými velikostmi.
    """
    quotas: dict[str, int] = {}
    active = {s: w for s, w in weights.items() if w > 0}
    remaining = budget
    while active:
        total_w = sum(active.values())
        short = [s for s, w in active.items() if available.get(s, 0) <= remaining * w / total_w]
        if not short:
            for s, w in active.items():
                quotas[s] = int(remaining * w / total_w)
            break
        for s in short:  # zdroj nestačí -> bereme celý
            quotas[s] = available.get(s, 0)
            remaining -= quotas[s]
            del active[s]
    return quotas


def shuffled_ids(conn: sqlite3.Connection, source: str, seed: int) -> list[int]:
    """Id dokumentů zdroje v pevně zamíchaném pořadí (stejný seed = stejné pořadí)."""
    ids = [r[0] for r in conn.execute("SELECT id FROM documents WHERE source = ? ORDER BY id", (source,))]
    random.Random(f"{seed}:{source}").shuffle(ids)
    return ids


class SourceCursor:
    """Prochází jeden zdroj v zamíchaném pořadí a bere celé dokumenty."""

    def __init__(self, conn: sqlite3.Connection, source: str, seed: int):
        self.conn, self.source = conn, source
        self.ids = shuffled_ids(conn, source, seed)
        self.pos = 0
        self.chars = 0
        self.docs = 0

    @property
    def exhausted(self) -> bool:
        return self.pos >= len(self.ids)

    def take(self, quota: float) -> Iterator[tuple[int, str]]:
        """Vrací (id, text) dalších dokumentů, dokud nemáme `quota` znaků (nebo dojdou)."""
        while self.chars < quota and not self.exhausted:
            batch = self.ids[self.pos:self.pos + FETCH_BATCH]
            marks = ",".join("?" * len(batch))
            texts = dict(self.conn.execute(f"SELECT id, text FROM documents WHERE id IN ({marks})", batch))
            for doc_id in batch:  # pořadí podle zamíchání, ne podle databáze
                if self.chars >= quota:
                    return
                self.pos += 1
                text = texts.get(doc_id, "")
                if not text:
                    continue
                self.chars += len(text)
                self.docs += 1
                yield doc_id, text


def iter_mix(
    conn: sqlite3.Connection,
    cfg: MixConfig,
    on_plan: Callable[[str, dict], None] | None = None,
) -> Iterator[tuple[str, int, str]]:
    """Projde směs a vrací (zdroj, id, text). Deterministické díky seedu.

    on_plan(zdroj, info) se zavolá vždy, když zdroj dostane (nový) cíl ve znacích.
    """
    present = {r[0] for r in conn.execute("SELECT DISTINCT source FROM documents")}
    cursors: dict[str, SourceCursor] = {}

    def cursor(source: str) -> SourceCursor:
        if source not in cursors:
            cursors[source] = SourceCursor(conn, source, cfg.seed)
        return cursors[source]

    # 1) malé zdroje celé
    for s in cfg.whole:
        if s not in present:
            continue
        c = cursor(s)
        if on_plan:
            on_plan(s, {"target_chars": None, "available_docs": len(c.ids), "whole": True})
        for doc_id, text in c.take(float("inf")):
            yield s, doc_id, text

    # 2) zbytek podle vah; když nějaký zdroj dojde, rozdělíme zbytek rozpočtu znovu
    budget = cfg.max_chars - sum(c.chars for c in cursors.values())
    weighted = {s: w for s, w in cfg.weights.items() if s in present and s not in cfg.whole and w > 0}
    while True:
        done = {s for s in weighted if cursor(s).exhausted}
        remaining = budget - sum(cursor(s).chars for s in done)
        active = {s: w for s, w in weighted.items() if s not in done}
        if not active or remaining <= 0:
            break
        total_w = sum(active.values())
        newly_exhausted = False
        for s, w in active.items():
            quota = remaining * w / total_w
            c = cursor(s)
            if on_plan:
                on_plan(s, {"target_chars": int(quota), "available_docs": len(c.ids), "whole": False})
            for doc_id, text in c.take(quota):
                yield s, doc_id, text
            if c.exhausted and c.chars < quota:
                newly_exhausted = True
        if not newly_exhausted:
            break


def plan_by_docs(conn: sqlite3.Connection, cfg: MixConfig) -> list[tuple[str, int, str]]:
    """Rychlý přehled: počty dokumentů a podíly (bez čtení textů)."""
    counts = dict(conn.execute("SELECT source, COUNT(*) FROM documents GROUP BY source"))
    total_w = sum(cfg.weights.values())
    rows = []
    for s in sorted(counts):
        if s in cfg.whole:
            rows.append((s, counts[s], "celý"))
        elif s in cfg.weights:
            rows.append((s, counts[s], f"{100 * cfg.weights[s] / total_w:.1f} % zbytku"))
        else:
            rows.append((s, counts[s], "nepoužit"))
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="Plán vyvážené směsi zdrojů")
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    a = p.parse_args()
    cfg = load_mix_config(a.config)
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    print(f"Seed {cfg.seed}, rozpočet {cfg.max_chars:,} znaků")
    for s, n, share in plan_by_docs(conn, cfg):
        print(f"{s:<16} {n:>10,} dokumentů   {share}")
    print("Vzorek se vytvoří při tréninku tokenizéru: python -m llm2005.tokenizer")


if __name__ == "__main__":
    main()
