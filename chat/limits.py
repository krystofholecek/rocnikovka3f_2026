"""Ochrana serveru: limit požadavků podle IP, počet souběžných generování a anonymní statistiky.

Nic se neukládá na disk. IP adresa se v paměti drží jen jako otisk (hash s náhodnou solí,
která se při každém startu změní) a jen po dobu limitního okna.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque

_SALT = secrets.token_bytes(16)


def anonymize(ip: str) -> str:
    return hashlib.sha256(_SALT + ip.encode()).hexdigest()[:16]


class RateLimiter:
    """Klouzavé okno: nejvýš `limit` požadavků za `window` sekund pro každý klíč."""

    def __init__(self, rules: list[tuple[int, float]]):
        self.rules = [(limit, window) for limit, window in rules if limit > 0]
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def hit(self, key: str) -> float:
        """Zaznamená požadavek. Vrací 0, když je povolený, jinak za kolik sekund to jde znovu."""
        if not self.rules:
            return 0.0
        now = time.monotonic()
        longest = max(window for _, window in self.rules)
        with self._lock:
            self._cleanup(now, longest)
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > longest:
                hits.popleft()
            for limit, window in self.rules:
                recent = [t for t in hits if now - t <= window]
                if len(recent) >= limit:
                    return max(1.0, window - (now - recent[0]))
            hits.append(now)
            return 0.0

    def _cleanup(self, now: float, longest: float) -> None:
        if now - self._last_cleanup < 60:
            return
        self._last_cleanup = now
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] > longest]:
            del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class Slots:
    """Kolik odpovědí se smí generovat najednou (model běží na CPU, víc by se jen zdržovalo)."""

    def __init__(self, size: int):
        self._sem = threading.BoundedSemaphore(max(1, size))

    def try_acquire(self) -> bool:
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        self._sem.release()


class Stats:
    """Jen anonymní počty (žádný obsah zpráv)."""

    FIELDS = ("requests", "completed", "tokens", "rate_limited", "busy", "rejected", "no_model", "errors", "timeouts")

    def __init__(self):
        self._lock = threading.Lock()
        self.started = time.time()
        self.counts = dict.fromkeys(self.FIELDS, 0)

    def add(self, field: str, n: int = 1) -> None:
        with self._lock:
            self.counts[field] += n

    def snapshot(self) -> dict:
        with self._lock:
            return {**self.counts, "uptime_s": int(time.time() - self.started)}
