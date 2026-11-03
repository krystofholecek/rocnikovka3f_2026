from __future__ import annotations

import bz2
import gzip
import io
import sys
import urllib.request
from pathlib import Path


def open_binary(path: str):
    """Otevře soubor, i komprimovaný. '-' = standardní vstup (např. `7z x -so archiv.7z | ...`)."""
    if path == "-":
        return sys.stdin.buffer
    if path.endswith(".bz2"):
        return bz2.open(path, "rb")
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def open_text(path: str):
    return io.TextIOWrapper(open_binary(path), encoding="utf-8", errors="replace")


def fetch(url: str, target: Path, retries: int = 5, timeout: int = 60) -> int:
    """Stáhne jeden soubor (přes .part, takže přerušené stahování nezanechá půlku).

    Když soubor už existuje, nic nedělá -> stahování jde přerušit a znovu spustit.
    Vrací počet nově stažených bajtů.
    """
    if target.exists() and target.stat().st_size > 0:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    for attempt in range(retries):
        try:
            size = 0
            with urllib.request.urlopen(url, timeout=timeout) as r, open(part, "wb") as f:
                # po kouscích, ať se velký soubor nemusí celý vejít do paměti
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    size += len(chunk)
            part.replace(target)
            return size
        except OSError:
            if attempt == retries - 1:
                raise
    return 0
