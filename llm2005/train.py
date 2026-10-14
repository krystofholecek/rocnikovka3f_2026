"""Trénink modelu od nuly s průběžným ukládáním.

    python -m llm2005.train --config configs/small.json            # nový trénink nebo pokračování
    python -m llm2005.train --config configs/small.json --hours 8  # skončí sám po 8 h (trénink přes noc)

Když checkpoint existuje, trénink automaticky pokračuje tam, kde skončil.
Ctrl+C trénink bezpečně ukončí a uloží stav.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
import torch

from .model import GPT, GPTConfig


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class Batches:
    def __init__(self, path: str, block: int, batch: int, device: str):
        self.data = np.memmap(path, dtype=np.uint16, mode="r")
        self.block, self.batch, self.device = block, batch, device

    def get(self):
        ix = torch.randint(len(self.data) - self.block - 1, (self.batch,))
        x = torch.stack([torch.from_numpy(self.data[i : i + self.block].astype(np.int64)) for i in ix])
        y = torch.stack([torch.from_numpy(self.data[i + 1 : i + 1 + self.block].astype(np.int64)) for i in ix])
        if self.device == "cuda":
            return x.pin_memory().to("cuda", non_blocking=True), y.pin_memory().to("cuda", non_blocking=True)
        return x.to(self.device), y.to(self.device)


def lr_at(step: int, t: dict) -> float:
    warm, total, lr, min_lr = t["warmup_steps"], t["max_steps"], t["lr"], t["lr"] / 10
    if step < warm:
        return lr * (step + 1) / warm
    progress = min(1.0, (step - warm) / max(1, total - warm))
    return min_lr + 0.5 * (1 + math.cos(math.pi * progress)) * (lr - min_lr)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--hours", type=float, default=None, help="po kolika hodinách skončit")
    p.add_argument("--data-dir", default="data")
    a = p.parse_args()

    cfg = load_config(a.config)
    t = cfg["train"]
    out = Path(cfg.get("out_dir", "checkpoints")) / cfg["name"]
    out.mkdir(parents=True, exist_ok=True)
    ckpt_path = out / "ckpt.pt"
    device = pick_device()
    torch.manual_seed(2005)

    use_amp = device == "cuda"
    amp_dtype = torch.bfloat16 if use_amp and torch.cuda.is_bf16_supported() else torch.float16
    autocast = torch.autocast("cuda", dtype=amp_dtype) if use_amp else contextlib.nullcontext()
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp and amp_dtype == torch.float16)
    if device == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = True

    model = GPT(GPTConfig(**cfg["model"])).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=t["lr"], betas=(0.9, 0.95), weight_decay=0.1)
    step, best_val = 0, float("inf")
    if ckpt_path.exists():
        ck = torch.load(ckpt_path, map_location=device)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["optimizer"])
        step, best_val = ck["step"], ck.get("best_val", best_val)
        print(f"Pokračuji z kroku {step}")
    print(f"Zařízení: {device}, parametrů: {model.num_params() / 1e6:.1f} M")

    block = cfg["model"]["block_size"]
    train = Batches(f"{a.data_dir}/train.bin", block, t["batch_size"], device)
    val = Batches(f"{a.data_dir}/val.bin", block, t["batch_size"], device)
    tokens_per_step = t["batch_size"] * block * t["grad_accum"]

    def save():
        torch.save(
            {"model": model.state_dict(), "optimizer": opt.state_dict(), "step": step,
             "best_val": best_val, "config": cfg},
            ckpt_path,
        )

    @torch.no_grad()
    def evaluate() -> float:
        model.eval()
        losses = []
        for _ in range(t["eval_batches"]):
            x, y = val.get()
            with autocast:
                losses.append(model(x, y)[1].item())
        model.train()
        return sum(losses) / len(losses)

    log = open(out / "log.csv", "a", newline="")
    writer = csv.writer(log)
    deadline = time.time() + a.hours * 3600 if a.hours else None
    t0, last = time.time(), time.time()
    try:
        while step < t["max_steps"]:
            for g in opt.param_groups:
                g["lr"] = lr_at(step, t)
            for _ in range(t["grad_accum"]):
                x, y = train.get()
                with autocast:
                    loss = model(x, y)[1] / t["grad_accum"]
                scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            opt.zero_grad(set_to_none=True)
            step += 1

            if step % t["log_every"] == 0:
                dt = time.time() - last
                last = time.time()
                tps = tokens_per_step * t["log_every"] / dt
                print(f"krok {step:>7}  loss {loss.item() * t['grad_accum']:.3f}  {tps:,.0f} tokenů/s", flush=True)
            if step % t["eval_every"] == 0 or step == t["max_steps"]:
                v = evaluate()
                best_val = min(best_val, v)
                writer.writerow([step, round(v, 4), round(time.time() - t0)])
                log.flush()
                save()
                print(f"krok {step:>7}  val loss {v:.3f}  (uloženo)", flush=True)
            if deadline and time.time() > deadline:
                print("Časový limit vypršel, ukládám a končím.")
                break
    except KeyboardInterrupt:
        print("Přerušeno, ukládám…")
    save()
    log.close()


if __name__ == "__main__":
    main()
