from __future__ import annotations

import bz2
import gzip
import io
import sys


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
