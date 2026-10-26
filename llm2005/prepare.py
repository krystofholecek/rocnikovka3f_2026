"""Převede databázi na tokeny pro trénink: data/train.bin a data/val.bin.

    python -m llm2005.prepare
"""
from __future__ import annotations

import argparse
import random

import numpy as np
from tokenizers import Tokenizer

from .db import Database
from .tokenizer import EOT


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--tokenizer", default="data/tokenizer.json")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--val-fraction", type=float, default=0.005)
    p.add_argument("--batch", type=int, default=1000)
    a = p.parse_args()

    tok = Tokenizer.from_file(a.tokenizer)
    assert tok.get_vocab_size() < 2**16, "uint16 stačí jen pro slovník < 65 536"
    eot = tok.token_to_id(EOT)
    rng = random.Random(2005)
    files = {s: open(f"{a.out_dir}/{s}.bin", "wb") for s in ("train", "val")}
    counts = {"train": 0, "val": 0}

    def flush(texts):
        for enc in tok.encode_batch(texts):
            split = "val" if rng.random() < a.val_fraction else "train"
            arr = np.array(enc.ids + [eot], dtype=np.uint16)
            arr.tofile(files[split])
            counts[split] += len(arr)

    buf = []
    for text in Database(a.db).iter_texts():
        buf.append(text)
        if len(buf) >= a.batch:
            flush(buf)
            buf = []
    if buf:
        flush(buf)
    for f in files.values():
        f.close()
    print(f"train: {counts['train']:,} tokenů, val: {counts['val']:,} tokenů")


if __name__ == "__main__":
    main()
