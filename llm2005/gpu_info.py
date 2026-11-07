"""Údaje o GPU z nvidia-smi (vytížení, VRAM, teplota) a snížení priority procesu.

nvidia-smi vidí celé GPU, tedy i paměť, kterou zabírají jiné programy (prohlížeč, plocha).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

FIELDS = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"


def parse_smi(line: str) -> dict | None:
    """Rozebere jeden řádek z `nvidia-smi --format=csv,noheader,nounits`."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 5:
        return None

    def num(s: str) -> float | None:
        try:
            return float(s)
        except ValueError:  # např. "[N/A]"
            return None

    util, used, total, temp, power = (num(p) for p in parts[:5])
    return {
        "util_pct": util,
        "mem_used_gb": round(used / 1024, 2) if used is not None else None,
        "mem_total_gb": round(total / 1024, 2) if total is not None else None,
        "temp_c": temp,
        "power_w": power,
    }


def nvidia_smi(timeout: float = 5.0) -> dict | None:
    """Aktuální stav GPU 0, nebo None, když nvidia-smi chybí nebo selže."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # na Windows bez blikajícího okna
    try:
        out = subprocess.run([exe, f"--query-gpu={FIELDS}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return parse_smi(out.stdout.strip().splitlines()[0])


def lower_priority() -> str:
    """Sníží prioritu vlastního procesu, aby počítač při tréninku zůstal použitelný.

    Windows: třída BelowNormal (platí pro všechna vlákna procesu, i načítání dat).
    Linux/macOS: nice +10. Vrací popis pro log.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # typy musí sedět, jinak se 64bitový "handle" procesu ořízne a volání selže
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        ok = k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        return "BelowNormal" if ok else f"nepodařilo se snížit (chyba {ctypes.get_last_error()})"
    try:
        os.nice(10)
        return "nice +10"
    except OSError:
        return "nepodařilo se snížit"
