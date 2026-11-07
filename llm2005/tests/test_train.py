"""Test tréninku v malém: stav pro dashboard, checkpoint, pokračování a zastavení přes STOP."""
import json
import sys

import numpy as np

from llm2005 import train


def _setup(tmp_path, max_steps=10):
    rng = np.random.default_rng(0)
    data = tmp_path / "data"
    data.mkdir()
    rng.integers(0, 100, 5000, dtype=np.uint16).tofile(data / "train.bin")
    rng.integers(0, 100, 1000, dtype=np.uint16).tofile(data / "val.bin")
    cfg = {"name": "unit", "model": {"vocab_size": 100, "block_size": 16, "n_layer": 1, "n_head": 2, "n_embd": 16},
           "train": {"batch_size": 4, "grad_accum": 2, "lr": 0.001, "warmup_steps": 2, "max_steps": max_steps,
                     "log_every": 2, "eval_every": 5, "eval_batches": 2}}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    return data


def _run(tmp_path, monkeypatch, *extra):
    args = ["train", "--config", str(tmp_path / "cfg.json"), "--data-dir", str(tmp_path / "data"),
            "--out-dir", str(tmp_path / "ck"), "--status", str(tmp_path / "status.json"),
            "--tokenizer", str(tmp_path / "chybi.json"), *extra]
    monkeypatch.setattr(sys, "argv", args)
    train.main()
    return json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))


def test_status_checkpoint_and_resume(tmp_path, monkeypatch):
    _setup(tmp_path)
    s = _run(tmp_path, monkeypatch)
    assert s["kind"] == "train" and s["state"] == "done" and s["step"] == 10
    assert s["train_loss"] > 0 and s["val_loss"] > 0 and s["lr"] > 0
    assert [p["step"] for p in s["history"]["val"]] == [5, 10]
    assert len(s["history"]["train"]) == 5
    assert s["checkpoint"]["step"] == 10 and (tmp_path / "ck" / "ckpt.pt").exists()
    assert 0 < s["epoch"] < 1 and s["tokens_seen"] == 10 * 4 * 16 * 2

    # pokračování: začne na kroku 10 a převezme historii grafů
    s = _run(tmp_path, monkeypatch, "--max-steps", "15")
    assert s["start_step"] == 10 and s["step"] == 15
    assert [p["step"] for p in s["history"]["val"]] == [5, 10, 15]


def test_stop_file(tmp_path, monkeypatch):
    _setup(tmp_path, max_steps=1000)
    (tmp_path / "ck").mkdir()
    (tmp_path / "ck" / "STOP").touch()
    s = _run(tmp_path, monkeypatch)
    assert s["state"] == "stopped" and s["step"] == 1
    assert not (tmp_path / "ck" / "STOP").exists()
    assert s["checkpoint"]["step"] == 1


def test_lr_schedule():
    t = {"warmup_steps": 10, "max_steps": 100, "lr": 1.0}
    assert train.lr_at(0, t) == 0.1
    assert abs(train.lr_at(10, t) - 1.0) < 1e-9
    assert abs(train.lr_at(100, t) - 0.1) < 1e-9
