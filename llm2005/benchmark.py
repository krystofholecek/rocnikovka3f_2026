"""Rychlý test hardwaru: změří rychlost tréninku a odhadne, jak dlouho poběží velký model.

    python -m llm2005.benchmark --config configs/base.json
"""
from __future__ import annotations

import argparse
import contextlib
import time

import torch

from .model import GPT, GPTConfig
from .train import load_config, pick_device


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/base.json")
    p.add_argument("--seconds", type=float, default=120)
    p.add_argument("--target-tokens", type=float, default=2.5e9)
    a = p.parse_args()

    cfg = load_config(a.config)
    m, t = cfg["model"], cfg["train"]
    device = pick_device()
    print("Zařízení:", device, torch.cuda.get_device_name(0) if device == "cuda" else "")
    model = GPT(GPTConfig(**m)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
    use_amp = device == "cuda"
    dtype = torch.bfloat16 if use_amp and torch.cuda.is_bf16_supported() else torch.float16
    ac = torch.autocast("cuda", dtype=dtype) if use_amp else contextlib.nullcontext()
    x = torch.randint(m["vocab_size"], (t["batch_size"], m["block_size"]), device=device)

    tokens, t0, warm = 0, None, 3
    while True:
        with ac:
            loss = model(x, x)[1]
        loss.backward()
        opt.step()
        opt.zero_grad(set_to_none=True)
        if device == "cuda":
            torch.cuda.synchronize()
        warm -= 1
        if warm == 0:
            t0 = time.time()
        elif warm < 0:
            tokens += x.numel()
            if time.time() - t0 > a.seconds:
                break
    tps = tokens / (time.time() - t0)
    hours = a.target_tokens / tps / 3600
    print(f"Model {model.num_params() / 1e6:.0f} M parametrů: {tps:,.0f} tokenů/s")
    print(f"Odhad pro {a.target_tokens / 1e9:.1f} mld. tokenů: {hours:.0f} h, tj. ~{hours / 8:.1f} nocí po 8 h")
    if device == "cuda":
        print(f"Max. paměť GPU: {torch.cuda.max_memory_allocated() / 1e9:.1f} GB")


if __name__ == "__main__":
    main()
