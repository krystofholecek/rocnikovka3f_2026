"""Trénink modelu od nuly s průběžným ukládáním.

    python -m llm2005.train --config configs/small.json            # nový trénink nebo pokračování
    python -m llm2005.train --config configs/small.json --hours 8  # skončí sám po 8 h (trénink přes noc)

Když checkpoint existuje, trénink automaticky pokračuje tam, kde skončil.
Zastavení kdykoli:
- Ctrl+C, nebo
- vytvořit prázdný soubor checkpoints/<name>/STOP (trénink uloží stav a skončí),
- i tvrdé ukončení procesu je v pořádku: checkpoint se ukládá atomicky,
  takže se přijde nejvýš o kroky od poslední evaluace.

Průběh se každých ~5 s zapisuje do data/progress/train-status.json
(dashboard: python -m llm2005.progress_server -> http://localhost:8800).
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import csv
import json
import math
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import torch

from .model import GPT, GPTConfig
from .progress import write_json_atomic

# Začátky vět, na kterých při každé evaluaci ukážeme, co model umí.
PROMPTS = ["The Internet is", "In 2005, the most popular", "Dear John,"]


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time()).astimezone().isoformat(timespec="seconds")


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
    """Learning rate: nejdřív lineární rozjezd (warmup), pak pokles po kosinusovce na 1/10."""
    warm, total, lr, min_lr = t["warmup_steps"], t["max_steps"], t["lr"], t["lr"] / 10
    if step < warm:
        return lr * (step + 1) / warm
    progress = min(1.0, (step - warm) / max(1, total - warm))
    return min_lr + 0.5 * (1 + math.cos(math.pi * progress)) * (lr - min_lr)


def gpu_memory() -> dict | None:
    """Kolik paměti GPU zabírá trénink (a kolik je obsazeno celkem i jinými programy)."""
    if not torch.cuda.is_available():
        return None
    free, total = torch.cuda.mem_get_info()
    gb = 2**30
    return {
        "allocated_gb": round(torch.cuda.memory_allocated() / gb, 2),  # právě použito tenzory
        "reserved_gb": round(torch.cuda.memory_reserved() / gb, 2),    # drží si PyTorch
        "peak_gb": round(torch.cuda.max_memory_allocated() / gb, 2),   # maximum od startu
        "used_gb": round((total - free) / gb, 2),                      # celé GPU (i ostatní programy)
        "total_gb": round(total / gb, 2),
    }


class TrainStatus:
    """Stav tréninku pro dashboard. Vlákno na pozadí ho každých `interval` s zapíše do JSON.

    Hlavní smyčka jen volá update()/add_point()/log(); rychlost, uplynulý čas a ETA
    se dopočítají při zápisu, takže jsou čerstvé i mezi logovacími kroky.
    """

    def __init__(self, path: str | Path | None, base: dict, interval: float = 5.0):
        self.path = Path(path) if path else None
        self.interval = interval
        self.lock = threading.Lock()
        self.data = base
        self.data.setdefault("history", {"train": [], "val": []})
        self.data.setdefault("samples", [])
        self.logs: deque[str] = deque(maxlen=15)
        self.started = time.time()
        self.deadline: float | None = None
        self.train_seconds_before = 0.0  # čistý čas tréninku z předchozích běhů
        self._rate_mark = (time.time(), base.get("step", 0))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="train-status", daemon=True)

    def start(self) -> "TrainStatus":
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.write()
            self._thread.start()
        return self

    def update(self, **fields) -> None:
        with self.lock:
            self.data.update(fields)

    def add_point(self, kind: str, point: dict, limit: int = 5000) -> None:
        with self.lock:
            h = self.data["history"].setdefault(kind, [])
            h.append(point)
            del h[:-limit]

    def add_samples(self, entry: dict, limit: int = 60) -> None:
        with self.lock:
            self.data["samples"].insert(0, entry)  # nejnovější první
            del self.data["samples"][limit:]

    def log(self, msg: str) -> None:
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        print(msg, flush=True)
        with self.lock:
            self.logs.append(line)

    def train_seconds(self) -> float:
        return self.train_seconds_before + (time.time() - self.started)

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            step = self.data.get("step", 0)
            t0, s0 = self._rate_mark
            if now - t0 >= 4 and self.data.get("state") == "running":
                # tokeny/s za posledních pár sekund (vyhlazeno, ať číslo neskáče)
                inst = (step - s0) * self.data["hparams"]["tokens_per_step"] / (now - t0)
                prev = self.data.get("tokens_per_s_live")
                self.data["tokens_per_s_live"] = round(inst if not prev else 0.5 * prev + 0.5 * inst)
                self._rate_mark = (now, step)
            d = copy.deepcopy(self.data)
            d["updated_at"] = _iso(now)
            d["elapsed_s"] = round(now - self.started)
            d["train_seconds_total"] = round(self.train_seconds())
            d["eta_deadline_s"] = round(max(0.0, self.deadline - now)) if self.deadline else None
            rate = d.get("tokens_per_s") or 0
            left = d["hparams"]["max_steps"] - step
            d["eta_max_steps_s"] = round(left * d["hparams"]["tokens_per_step"] / rate) if rate > 0 else None
            d["gpu"] = gpu_memory()
            d["log"] = list(self.logs)
            return d

    def write(self) -> None:
        if self.path:
            write_json_atomic(self.path, self.snapshot())

    def finish(self, state: str, error: str | None = None) -> None:
        self.update(state=state, error=error, finished_at=_iso())
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self.write()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.write()
            except Exception as e:  # přehled nesmí shodit trénink
                print(f"(train-status: zápis selhal: {e})", flush=True)


def previous_history(path: Path, name: str, step: int) -> tuple[dict, list]:
    """Při pokračování převezme historii grafů z minulého běhu (jen kroky, které jsou v checkpointu)."""
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"train": [], "val": []}, []
    if old.get("config") != name or step == 0:
        return {"train": [], "val": []}, []
    hist = {k: [p for p in v if p.get("step", 0) <= step] for k, v in (old.get("history") or {}).items()}
    samples = [s for s in old.get("samples") or [] if s.get("step", 0) <= step]
    return hist, samples


@torch.no_grad()
def generate_samples(model: GPT, tok, device: str, autocast, n_tokens: int = 40) -> list[dict]:
    """Pro každý prompt nechá model dopsat pár desítek tokenů (náhodně, teplota 0.8)."""
    model.eval()
    out = []
    for prompt in PROMPTS:
        ids = tok.encode(prompt).ids
        idx = torch.tensor([ids], device=device)
        with autocast:
            gen = model.generate(idx, n_tokens, temperature=0.8, top_k=50)
        out.append({"prompt": prompt, "completion": tok.decode(gen[0, len(ids):].tolist())})
    model.train()
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--hours", type=float, default=None, help="po kolika hodinách skončit")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--max-steps", type=int, default=None, help="přepíše max_steps z konfigurace")
    p.add_argument("--out-dir", default=None, help="kam ukládat checkpoint (jinak checkpoints/<name>)")
    p.add_argument("--tokenizer", default="data/tokenizer.json", help="jen pro ukázky textu při evaluaci")
    p.add_argument("--status", default="data/progress/train-status.json", help="stav pro dashboard")
    p.add_argument("--no-status", action="store_true", help="nezapisovat stav (krátké testy)")
    a = p.parse_args()

    cfg = load_config(a.config)
    t = cfg["train"]
    if a.max_steps:
        t["max_steps"] = a.max_steps
    out = Path(a.out_dir) if a.out_dir else Path(cfg.get("out_dir", "checkpoints")) / cfg["name"]
    out.mkdir(parents=True, exist_ok=True)
    ckpt_path = out / "ckpt.pt"
    stop_file = out / "STOP"
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
    step, best_val, train_seconds_before = 0, float("inf"), 0.0
    if ckpt_path.exists():
        ck = torch.load(ckpt_path, map_location=device)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["optimizer"])
        step, best_val = ck["step"], ck.get("best_val", best_val)
        train_seconds_before = ck.get("train_seconds", 0.0)
        print(f"Pokračuji z kroku {step}")
    print(f"Zařízení: {device}, parametrů: {model.num_params() / 1e6:.1f} M")

    tok = None
    if Path(a.tokenizer).exists():
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(a.tokenizer)

    block = cfg["model"]["block_size"]
    train = Batches(f"{a.data_dir}/train.bin", block, t["batch_size"], device)
    val = Batches(f"{a.data_dir}/val.bin", block, t["batch_size"], device)
    tokens_per_step = t["batch_size"] * block * t["grad_accum"]

    # --- stav pro dashboard ---
    status_path = None if a.no_status else Path(a.status)
    hist, samples = previous_history(status_path, cfg["name"], step) if status_path else ({}, [])
    status = TrainStatus(status_path, {
        "kind": "train", "title": f"Trénink modelu {cfg['name']}", "config": cfg["name"],
        "config_path": a.config, "state": "running", "error": None, "device": device,
        "gpu_name": torch.cuda.get_device_name(0) if device == "cuda" else None,
        "model": {**cfg["model"], "params_m": round(model.num_params() / 1e6, 1)},
        "hparams": {**{k: t[k] for k in ("batch_size", "grad_accum", "lr", "warmup_steps", "max_steps", "eval_every")},
                    "tokens_per_step": tokens_per_step},
        "train_tokens": len(train.data), "val_tokens": len(val.data),
        "started_at": _iso(), "hours_limit": a.hours, "start_step": step, "step": step,
        "tokens_seen": step * tokens_per_step, "epoch": step * tokens_per_step / len(train.data),
        "train_loss": None, "val_loss": None, "best_val": None if math.isinf(best_val) else best_val,
        "lr": lr_at(step, t), "tokens_per_s": None,
        "checkpoint": {"path": str(ckpt_path), "step": step, "saved_at": _iso(ckpt_path.stat().st_mtime)}
        if ckpt_path.exists() else None,
        "history": hist or {"train": [], "val": []}, "samples": samples,
    })
    status.train_seconds_before = train_seconds_before
    deadline = time.time() + a.hours * 3600 if a.hours else None
    status.deadline = deadline
    if deadline:
        status.update(deadline_at=_iso(deadline))
    status.start()

    def save():
        # Nejdřív do .tmp, pak přejmenovat: i když se proces tvrdě ukončí, starý checkpoint zůstane celý.
        tmp = ckpt_path.with_suffix(".pt.tmp")
        torch.save(
            {"model": model.state_dict(), "optimizer": opt.state_dict(), "step": step,
             "best_val": best_val, "config": cfg, "train_seconds": status.train_seconds()},
            tmp,
        )
        os.replace(tmp, ckpt_path)
        status.update(checkpoint={"path": str(ckpt_path), "step": step, "saved_at": _iso()})

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
    last, loss_sum, loss_n = time.time(), torch.zeros((), device=device), 0
    end_state = "done"
    try:
        while step < t["max_steps"]:
            lr = lr_at(step, t)
            for g in opt.param_groups:
                g["lr"] = lr
            for _ in range(t["grad_accum"]):
                x, y = train.get()
                with autocast:
                    loss = model(x, y)[1] / t["grad_accum"]
                scaler.scale(loss).backward()
                loss_sum += loss.detach().float()
            loss_n += 1
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            opt.zero_grad(set_to_none=True)
            step += 1
            status.update(step=step, lr=lr, tokens_seen=step * tokens_per_step,
                          epoch=step * tokens_per_step / len(train.data))

            if step % t["log_every"] == 0:
                dt = time.time() - last
                last = time.time()
                tps = tokens_per_step * loss_n / dt
                avg = loss_sum.item() / loss_n  # průměr loss za posledních log_every kroků (klouzavý)
                loss_sum.zero_()
                loss_n = 0
                status.update(train_loss=avg, tokens_per_s=tps)
                status.add_point("train", {"step": step, "loss": round(avg, 4), "lr": lr, "tps": round(tps),
                                           "t": round(status.train_seconds())})
                print(f"krok {step:>7}  loss {avg:.3f}  {tps:,.0f} tokenů/s", flush=True)
            if step % t["eval_every"] == 0 or step == t["max_steps"]:
                v = evaluate()
                best_val = min(best_val, v)
                writer.writerow([step, round(v, 4), round(status.train_seconds())])
                log.flush()
                save()
                status.update(val_loss=v, best_val=best_val)
                status.add_point("val", {"step": step, "loss": round(v, 4), "t": round(status.train_seconds())})
                status.log(f"krok {step:>7}  val loss {v:.3f}  (uloženo)")
                if tok is not None:
                    status.add_samples({"step": step, "val_loss": round(v, 4), "at": _iso(),
                                        "items": generate_samples(model, tok, device, autocast)})
                last = time.time()  # evaluaci nepočítat do rychlosti tréninku
            if stop_file.exists():
                status.log("Nalezen soubor STOP, ukládám a končím.")
                stop_file.unlink(missing_ok=True)
                end_state = "stopped"
                break
            if deadline and time.time() > deadline:
                status.log("Časový limit vypršel, ukládám a končím.")
                end_state = "stopped"
                break
    except KeyboardInterrupt:
        status.log("Přerušeno, ukládám…")
        end_state = "stopped"
    except Exception as e:
        status.finish("error", f"{type(e).__name__}: {e}")
        log.close()
        raise
    save()
    log.close()
    status.finish(end_state)


if __name__ == "__main__":
    main()
