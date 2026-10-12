"""Vlastní BPE tokenizer natrénovaný jen na naší databázi (žádný cizí slovník).

    python -m llm2005.tokenizer --vocab 32000
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from .db import Database

EOT = "<|endoftext|>"  # oddělovač dokumentů


def train_tokenizer(db: Database, vocab_size: int, out: str | Path) -> Tokenizer:
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size,
        special_tokens=[EOT],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
    )
    tok.train_from_iterator(db.iter_texts(), trainer=trainer)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    tok.save(str(out))
    return tok


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--vocab", type=int, default=32000)
    p.add_argument("--out", default="data/tokenizer.json")
    a = p.parse_args()
    tok = train_tokenizer(Database(a.db), a.vocab, a.out)
    print(f"Tokenizer uložen do {a.out}, slovník {tok.get_vocab_size()} tokenů")


if __name__ == "__main__":
    main()
