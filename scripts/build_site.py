"""Složí statický web chronosllm.com do složky dist/ (pro Cloudflare Workers Builds).

Použití (z kořene repa):  python scripts/build_site.py

Výsledek:
  dist/index.html         <- web/landing/index.html   (úvodní stránka)
  dist/postup/            <- web/postup/              (postup krok za krokem)
  dist/tokenizer/         <- web/tokenizer-lab/       (jen soubory potřebné za běhu)
  dist/version.json       <- commit, větev a čas buildu (ověření nasazené verze)

Používá jen standardní knihovnu Pythonu, nic se neinstaluje.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = ROOT / "dist"

# Z Tokenizer Labu bereme jen to, co prohlížeč opravdu načítá.
# Testy, node_modules, README a package*.json na web nepatří.
RUNTIME_SUFFIXES = {".html", ".css", ".js"}
SKIP_DIRS = {"tests", "node_modules"}


def copy_runtime(src: Path, dst: Path) -> None:
    """Zkopíruje z src do dst jen soubory .html/.css/.js mimo tests/ a node_modules/."""
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if not path.is_file() or path.suffix not in RUNTIME_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def git(*args: str) -> str | None:
    """Vrátí výstup příkazu git, nebo None, když git není k dispozici."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def version_info() -> dict:
    """Commit, větev a čas buildu.

    Na Cloudflare Workers Builds se berou proměnné WORKERS_CI_COMMIT_SHA
    a WORKERS_CI_BRANCH (https://developers.cloudflare.com/workers/ci-cd/builds/configuration/),
    lokálně se zjistí přes git.
    """
    on_ci = os.environ.get("WORKERS_CI") == "1"
    commit = os.environ.get("WORKERS_CI_COMMIT_SHA") or git("rev-parse", "HEAD")
    branch = os.environ.get("WORKERS_CI_BRANCH") or git("rev-parse", "--abbrev-ref", "HEAD")
    return {
        "commit": commit,
        "branch": branch,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "workers-builds" if on_ci else "local",
        "build_id": os.environ.get("WORKERS_CI_BUILD_UUID"),
    }


def build() -> list[Path]:
    """Smaže dist/, znovu ho složí a vrátí seznam vytvořených souborů."""
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()

    shutil.copy2(WEB / "landing" / "index.html", DIST / "index.html")
    copy_runtime(WEB / "postup", DIST / "postup")
    copy_runtime(WEB / "tokenizer-lab", DIST / "tokenizer")
    (DIST / "version.json").write_text(
        json.dumps(version_info(), indent=2) + "\n", encoding="utf-8"
    )

    # Kontrola: bez těchto stránek by web nefungoval, build má radši spadnout.
    for required in ("index.html", "postup/index.html", "tokenizer/index.html"):
        if not (DIST / required).is_file():
            raise SystemExit(f"Chybí {required} – build se nepovedl.")

    return sorted(p for p in DIST.rglob("*") if p.is_file())


def main() -> int:
    files = build()
    total = 0
    print(f"Hotovo: {DIST.relative_to(ROOT)}/")
    for path in files:
        size = path.stat().st_size
        total += size
        print(f"  {path.relative_to(DIST).as_posix():<32} {size:>8} B")
    print(f"Celkem {len(files)} souborů, {total / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
