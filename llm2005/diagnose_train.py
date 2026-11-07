"""Diagnostika rychlosti tréninku: změří zvlášť každou část jednoho kroku.

    python -m llm2005.diagnose_train --config configs/small.json
    python -m llm2005.diagnose_train --config configs/small.json --batch-sizes 8 16 32

Pro každou velikost dávky (batch) vypíše:
- načtení dat (náhodné okno z train.bin -> tensor -> GPU),
- forward + backward (výpočet chyby a gradientů),
- krok optimizeru (úprava vah),
- špičkovou paměť GPU a celkové obsazení GPU podle nvidia-smi.

Když součet (paměť tréninku + ostatní programy) přeleze fyzickou VRAM, Windows začne
data odkládat do "sdílené paměti GPU" (běžná RAM přes PCIe) a všechno se dramaticky zpomalí.
"""
from __future__ import annotations

import argparse
import time

import torch

from .gpu_info import nvidia_smi
from .model import GPT, GPTConfig
from .train import Batches, load_config, make_autocast, pick_device


def timed(fn, device: str, n: int) -> float:
    """Průměrná doba jednoho volání fn() v sekundách (s čekáním na GPU)."""
    if device == "cuda":
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    if device == "cuda":
        torch.cuda.synchronize()
    return (time.perf_counter() - t0) / n


def measure(cfg: dict, batch: int, data_dir: str, device: str, reps: int) -> dict:
    m = cfg["model"]
    model = GPT(GPTConfig(**m)).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
    autocast, _ = make_autocast(device)
    data = Batches(f"{data_dir}/train.bin", m["block_size"], batch, device)
    if device == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    x, y = data.get()

    def fwd_bwd():
        with autocast:
            loss = model(x, y)[1]
        loss.backward()

    # zahřátí (první kroky alokují paměť a ladí CUDA kernely)
    for _ in range(3):
        fwd_bwd()
        opt.step()

    t_data = timed(data.get, device, reps * 4)
    t_fb = timed(fwd_bwd, device, reps)
    t_opt = timed(opt.step, device, reps)
    smi = nvidia_smi()
    tokens = batch * m["block_size"]
    res = {
        "batch": batch,
        "data_ms": t_data * 1000, "fwd_bwd_ms": t_fb * 1000, "opt_ms": t_opt * 1000,
        "tokens_per_s": tokens / (t_data + t_fb),  # optimizer běží jen 1× za grad_accum mikrokroků
        "peak_gb": torch.cuda.max_memory_allocated() / 2**30 if device == "cuda" else 0,
        "reserved_gb": torch.cuda.memory_reserved() / 2**30 if device == "cuda" else 0,
        "smi": smi,
    }
    del model, opt, x, y
    if device == "cuda":
        torch.cuda.empty_cache()
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/small.json")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--batch-sizes", type=int, nargs="*", default=None)
    p.add_argument("--reps", type=int, default=10)
    p.add_argument("--threads", type=int, default=None, help="torch.set_num_threads")
    a = p.parse_args()

    if a.threads:
        torch.set_num_threads(a.threads)
    cfg = load_config(a.config)
    device = pick_device()
    torch.backends.cuda.matmul.allow_tf32 = True
    print(f"Zařízení: {device}, torch {torch.__version__}, CPU vláken: {torch.get_num_threads()}")
    print("GPU před startem:", nvidia_smi())
    for b in a.batch_sizes or [cfg["train"]["batch_size"]]:
        r = measure(cfg, b, a.data_dir, device, a.reps)
        s = r["smi"] or {}
        print(
            f"batch {b:>3}: data {r['data_ms']:6.1f} ms | fwd+bwd {r['fwd_bwd_ms']:8.1f} ms | "
            f"optimizer {r['opt_ms']:6.1f} ms | {r['tokens_per_s']:>8,.0f} tok/s | "
            f"špička PyTorch {r['peak_gb']:.2f} GB (rezerv. {r['reserved_gb']:.2f}) | "
            f"GPU celkem {s.get('mem_used_gb', '?')}/{s.get('mem_total_gb', '?')} GB, "
            f"vytížení {s.get('util_pct', '?')} %",
            flush=True,
        )


if __name__ == "__main__":
    main()
