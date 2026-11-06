"""Malý server jen pro čtení: živý přehled průběhu na http://localhost:8800

    python -m llm2005.progress_server            # běží, dokud ho nezastavíš (Ctrl+C)

Bezpečnost:
- poslouchá jen na 127.0.0.1 (z jiného počítače se k němu nikdo nepřipojí),
- umí jen GET/HEAD a vrací jen dvě věci:
    /                       -> tools/progress/index.html
    /progress/<soubor>.json -> data/progress/<soubor>.json
  nic jiného (žádné procházení složek, žádné ../).
Běží nezávisle na tréninku – stránka funguje i před ním a po něm.
"""
from __future__ import annotations

import argparse
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"  # schválně napevno – nikdy ne 0.0.0.0
ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "tools" / "progress" / "index.html"
DATA = ROOT / "data" / "progress"
JSON_NAME = re.compile(r"^/progress/([A-Za-z0-9_-]+\.json)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "LLM2005-progress"
    index_cache: bytes = b""
    index_path: Path = INDEX
    data_dir: Path = DATA

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _resolve(self) -> tuple[int, bytes, str]:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                type(self).index_cache = self.index_path.read_bytes()
            except OSError:
                pass  # např. přepnutá větev – použijeme kopii z paměti
            if self.index_cache:
                return 200, self.index_cache, "text/html; charset=utf-8"
        m = JSON_NAME.match(path)
        if m:
            f = self.data_dir / m.group(1)
            try:
                return 200, f.read_bytes(), "application/json; charset=utf-8"
            except FileNotFoundError:
                return 404, b'{"missing": true}', "application/json; charset=utf-8"
            except OSError:  # soubor se právě přepisuje
                return 503, b'{"busy": true}', "application/json; charset=utf-8"
        return 404, b"Not found", "text/plain; charset=utf-8"

    def do_GET(self) -> None:  # noqa: N802
        self._send(*self._resolve())

    def do_HEAD(self) -> None:  # noqa: N802
        self._send(*self._resolve())

    def log_message(self, fmt: str, *args) -> None:  # nezahlcovat log každým dotazem
        pass


def make_server(port: int, index: Path = INDEX, data_dir: Path = DATA) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"index_path": index, "data_dir": data_dir, "index_cache": b""})
    try:
        handler.index_cache = index.read_bytes()
    except OSError:
        pass
    return ThreadingHTTPServer((HOST, port), handler)


def main() -> None:
    p = argparse.ArgumentParser(description="Živý přehled průběhu (jen pro čtení, jen localhost)")
    p.add_argument("--port", type=int, default=8800)
    a = p.parse_args()
    srv = make_server(a.port)
    print(f"Přehled běží na http://localhost:{a.port}  (jen tento počítač; zastavení Ctrl+C)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
