"""Vygeneruje text z natrénovaného modelu.

    python -m llm2005.sample --config configs/small.json --prompt "The Internet is"
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from tokenizers import Tokenizer

from .model import GPT, GPTConfig
from .train import load_config, pick_device


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--prompt", default="The")
    p.add_argument("--tokens", type=int, default=200)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--tokenizer", default="data/tokenizer.json")
    a = p.parse_args()

    cfg = load_config(a.config)
    device = pick_device()
    ck = torch.load(Path(cfg.get("out_dir", "checkpoints")) / cfg["name"] / "ckpt.pt", map_location=device)
    model = GPT(GPTConfig(**cfg["model"])).to(device).eval()
    model.load_state_dict(ck["model"])
    tok = Tokenizer.from_file(a.tokenizer)
    idx = torch.tensor([tok.encode(a.prompt).ids], device=device)
    out = model.generate(idx, a.tokens, temperature=a.temperature)
    print(tok.decode(out[0].tolist()))


if __name__ == "__main__":
    main()
