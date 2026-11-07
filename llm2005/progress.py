"""Průběh dlouhé úlohy pro živý přehled v prohlížeči.

Úloha volá jen jednoduché metody (phase, add, log ...). Vlákno na pozadí
každé ~2 s zapíše stav do JSON souboru (např. data/progress/tokenizer-status.json),
který si stránka tools/progress/index.html načítá.
"""
from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def process_memory_gb() -> float | None:
    """Kolik paměti RAM zabírá tento proces (jen Windows, jinak None)."""
    if os.name != "nt":
        return None
    try:
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
                (n, ctypes.c_size_t) for n in
                ("Peak", "WorkingSet", "QPPeak", "QP", "QNPPeak", "QNP", "Pagefile", "PeakPagefile")
            ]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSet / 2**30, 2)
    except Exception:
        pass
    return None


def write_json_atomic(path: Path, data: dict) -> None:
    """Zapíše JSON nejdřív do .tmp a pak ho přejmenuje – čtenář nikdy neuvidí půlku souboru."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for _ in range(10):  # na Windows může soubor chvíli držet čtenář (server)
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1)


class Progress:
    """Stav úlohy rozdělené na fáze. Každá fáze má váhu (podíl na celkových procentech)."""

    def __init__(self, path: str | Path, title: str, phases: list[tuple[str, str, float]],
                 interval: float = 2.0, log_lines: int = 15, unit: str = "znaků/s"):
        self.path = Path(path)
        self.title = title
        self.interval = interval
        self.unit = unit
        self.started = time.time()
        self.lock = threading.RLock()
        self.phases = [
            {"id": pid, "label": label, "weight": w, "state": "pending", "fraction": 0.0,
             "started": None, "ended": None, "eta_s": None, "note": "", "estimate": False}
            for pid, label, w in phases
        ]
        self.current: dict | None = None
        self.state = "running"
        self.error: str | None = None
        self.sources: dict[str, dict] = {}
        self.extra: dict = {}
        self.logs: deque[str] = deque(maxlen=log_lines)
        self.counter = 0  # práce hotová v aktuální fázi (např. znaky) – z ní se počítá rychlost
        self._last = (time.time(), 0)
        self.rate = 0.0
        self.rate_history: deque[list] = deque(maxlen=900)  # ~30 min po 2 s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="progress-writer", daemon=True)

    # --- volá úloha ---
    def start(self) -> "Progress":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write()
        self._thread.start()
        return self

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(line, flush=True)
        with self.lock:
            self.logs.append(line)

    def phase(self, pid: str, note: str = "") -> None:
        """Ukončí aktuální fázi a začne fázi `pid`."""
        with self.lock:
            if self.current and self.current["state"] == "running":
                self._close(self.current, "done")
            ph = self._get(pid)
            ph.update(state="running", started=time.time(), fraction=0.0, note=note)
            self.current = ph
            self.counter = 0
            self._last = (time.time(), 0)
            self.rate = 0.0
        self.log(f"Fáze: {ph['label']}" + (f" – {note}" if note else ""))

    def skip(self, pid: str, note: str) -> None:
        """Fáze se nedělá (např. vzorek už existuje)."""
        with self.lock:
            self._get(pid).update(state="skipped", fraction=1.0, note=note)

    def set_fraction(self, fraction: float, eta_s: float | None = None, estimate: bool = False,
                     note: str | None = None) -> None:
        with self.lock:
            if self.current:
                self.current["fraction"] = max(0.0, min(1.0, fraction))
                self.current["eta_s"] = eta_s
                self.current["estimate"] = estimate
                if note is not None:
                    self.current["note"] = note

    def add(self, amount: int) -> None:
        """Přičte hotovou práci (např. znaky) v aktuální fázi – z toho je rychlost."""
        with self.lock:
            self.counter += amount

    def phase_rate(self) -> float:
        """Průměrná rychlost aktuální fáze (práce / s od začátku fáze)."""
        with self.lock:
            if not self.current or not self.current["started"]:
                return 0.0
            dt = time.time() - self.current["started"]
            return self.counter / dt if dt > 0 else 0.0

    def source(self, name: str, **fields) -> None:
        with self.lock:
            self.sources.setdefault(name, {"source": name}).update(fields)

    def source_add(self, name: str, **fields: int) -> None:
        with self.lock:
            s = self.sources.setdefault(name, {"source": name})
            for k, v in fields.items():
                s[k] = s.get(k, 0) + v

    def finish(self, state: str = "done", error: str | None = None) -> None:
        with self.lock:
            if self.current and self.current["state"] == "running":
                self._close(self.current, "done" if state == "done" else "error")
            self.state, self.error = state, error
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        self.write()

    # --- vnitřek ---
    def _get(self, pid: str) -> dict:
        for ph in self.phases:
            if ph["id"] == pid:
                return ph
        raise KeyError(pid)

    def _close(self, ph: dict, state: str) -> None:
        ph["state"] = state
        ph["ended"] = time.time()
        if state == "done":
            ph["fraction"] = 1.0
        ph["eta_s"] = 0 if state == "done" else None

    def _update_rate(self) -> None:
        t, c = time.time(), self.counter
        t0, c0 = self._last
        if t - t0 >= 0.5:
            inst = (c - c0) / (t - t0)
            self.rate = inst if self.rate == 0 else 0.6 * self.rate + 0.4 * inst  # vyhlazení
            self._last = (t, c)
            self.rate_history.append([round(t - self.started, 1), round(self.rate, 1),
                                      self.current["id"] if self.current else None])

    def overall_fraction(self) -> float:
        total = sum(p["weight"] for p in self.phases) or 1
        return sum(p["weight"] * p["fraction"] for p in self.phases) / total

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            elapsed = now - self.started
            frac = 1.0 if self.state == "done" else self.overall_fraction()
            # Celkové ETA = součet ETA aktuální fáze a odhadu zbylých fází podle jejich vah.
            eta = None
            cur = self.current
            if self.state == "running" and cur and cur["eta_s"] is not None and cur["started"]:
                cur_elapsed = now - cur["started"]
                cur_total = cur_elapsed + cur["eta_s"]
                later = sum(p["weight"] for p in self.phases if p["state"] == "pending")
                per_weight = cur_total / cur["weight"] if cur["weight"] else 0
                eta = cur["eta_s"] + later * per_weight
            phases = []
            for p in self.phases:
                start = p["started"]
                end = p["ended"] or (now if start else None)
                phases.append({
                    "id": p["id"], "label": p["label"], "state": p["state"],
                    "percent": round(100 * p["fraction"], 1),
                    "elapsed_s": round(end - start, 1) if start else None,
                    "eta_s": None if p["eta_s"] is None else round(p["eta_s"]),
                    "eta_is_estimate": p["estimate"], "note": p["note"],
                })
            return {
                "title": self.title,
                "state": self.state,
                "error": self.error,
                "started_at": datetime.fromtimestamp(self.started).astimezone().isoformat(timespec="seconds"),
                "updated_at": _now_iso(),
                "elapsed_s": round(elapsed, 1),
                "percent": round(100 * frac, 2),
                "eta_s": None if eta is None else round(eta),
                "eta_is_estimate": True,
                "phase": cur["id"] if cur else None,
                "phases": phases,
                "rate": {"value": round(self.rate, 1), "unit": self.unit},
                "rate_history": list(self.rate_history),
                "sources": sorted(self.sources.values(), key=lambda s: s["source"]),
                "memory_gb": process_memory_gb(),
                "log": list(self.logs),
                **self.extra,
            }

    def write(self) -> None:
        with self.lock:
            if self.state == "running":
                self._update_rate()
            data = self.snapshot()
        write_json_atomic(self.path, data)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.write()
            except Exception as e:  # přehled nesmí shodit trénink
                print(f"(progress: zápis selhal: {e})", flush=True)
