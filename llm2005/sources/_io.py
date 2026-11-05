from __future__ import annotations

import bz2
import gzip
import io
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# obecné jméno klienta (bez osobních údajů); některé servery odmítají výchozí "Python-urllib"
USER_AGENT = "llm2005-school-project/0.1"


def open_binary(path: str):
    """Otevře soubor, i komprimovaný. '-' = standardní vstup (např. `7z x -so archiv.7z | ...`)."""
    if path == "-":
        return sys.stdin.buffer
    if path.endswith(".7z"):
        # .7z umí rozbalit jen program 7-Zip -> čteme jeho výstup proudem (nic se neukládá)
        exe = shutil.which("7z") or r"C:\Program Files\7-Zip\7z.exe"
        proc = subprocess.Popen([exe, "x", "-so", path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        return proc.stdout
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
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
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
