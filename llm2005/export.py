"""Připraví natrénovaný model pro chat: menší soubor jen s vahami + tokenizér do jedné složky.

    python -m llm2005.export --config configs/base.json
    # -> checkpoints/chat/model.pt (váhy + konfigurace, bez stavu optimalizátoru) a checkpoints/chat/tokenizer.json

Checkpoint z tréninku obsahuje i stav optimalizátoru (asi 2× víc dat než samotné váhy). Pro chat stačí váhy,
volitelně v poloviční přesnosti (--half, další poloviční velikost; chat je při načtení převede zpět).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

import torch


def export(checkpoint: Path, tokenizer: Path, out_dir: Path, config: dict | None = None, half: bool = False) -> Path:
    ck = torch.load(checkpoint, map_location="cpu", weights_only=True)
    cfg = ck.get("config") or config
    if not cfg or "model" not in cfg:
        raise SystemExit("V checkpointu chybí konfigurace modelu – zadej --config configs/<velikost>.json.")
    state = {k.removeprefix("_orig_mod."): v for k, v in ck["model"].items()}
    if half:
        state = {k: v.half() if v.is_floating_point() else v for k, v in state.items()}
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "model.pt"
    tmp = out.with_suffix(".pt.tmp")
    torch.save({"model": state, "config": {"name": cfg.get("name"), "model": cfg["model"]},
                "step": ck.get("step"), "best_val": ck.get("best_val"), "format": "llm2005-chat-1"}, tmp)
    tmp.replace(out)
    shutil.copy2(tokenizer, out_dir / "tokenizer.json")
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    import json

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", required=True, help="configs/<velikost>.json (určí checkpoints/<name>/ckpt.pt)")
    p.add_argument("--checkpoint", help="jiný checkpoint než checkpoints/<name>/ckpt.pt")
    p.add_argument("--tokenizer", default="data/tokenizer.json")
    p.add_argument("--out-dir", default="checkpoints/chat")
    p.add_argument("--half", action="store_true", help="uložit váhy v poloviční přesnosti (menší soubor)")
    a = p.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    ckpt = Path(a.checkpoint) if a.checkpoint else Path(cfg.get("out_dir", "checkpoints")) / cfg["name"] / "ckpt.pt"
    out = export(ckpt, Path(a.tokenizer), Path(a.out_dir), cfg, a.half)
    size = out.stat().st_size / 1e6
    print(f"Hotovo: {out} ({size:.0f} MB), {Path(a.out_dir) / 'tokenizer.json'}")
    print(f"SHA-256 model.pt: {sha256(out)}")


if __name__ == "__main__":
    main()
