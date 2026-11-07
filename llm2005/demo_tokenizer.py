"""Ukázka tokenizace na nejmenší části dat (e-maily Enron), s průběžným výpisem.

    python -m llm2005.demo_tokenizer --source enron --vocab 8000
"""
from __future__ import annotations

import argparse
import itertools
import time
from pathlib import Path

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

from .db import Database
from .tokenizer import EOT

SAMPLE = "Hi Kenneth, the meeting about the California energy contracts is moved to Friday at 10 am."


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/llm2005.sqlite")
    p.add_argument("--source", default="enron")
    p.add_argument("--vocab", type=int, default=8000)
    p.add_argument("--docs", type=int, default=100000, help="kolik dokumentů pro trénink")
    p.add_argument("--out", default="data/demo/tokenizer-demo.json")
    a = p.parse_args()
    db = Database(a.db)

    print(f"== 1) Trénink BPE tokenizeru ze zdroje '{a.source}' (slovník {a.vocab}) ==", flush=True)
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=a.vocab, special_tokens=[EOT], show_progress=True,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
    )
    t0 = time.time()
    tok.train_from_iterator(itertools.islice(db.iter_texts(a.source), a.docs), trainer=trainer)
    print(f"Hotovo za {time.time() - t0:.0f} s, slovník {tok.get_vocab_size()} tokenů", flush=True)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    tok.save(a.out)

    print("\n== 2) Co se model naučil: nejčastější slova a kousky ==", flush=True)
    vocab = sorted(tok.get_vocab().items(), key=lambda kv: kv[1])
    print("první naučené spojení (id 256+):", [t for t, _ in vocab[256:276]])
    print("poslední (vzácné) tokeny:", [t for t, _ in vocab[-12:]])

    print("\n== 3) Jak vypadá věta po tokenizaci ==", flush=True)
    enc = tok.encode(SAMPLE)
    print("věta:   ", SAMPLE)
    print("kousky: ", enc.tokens)
    print("čísla:  ", enc.ids)
    print(f"{len(SAMPLE)} znaků -> {len(enc.ids)} tokenů")
    print("zpět:   ", tok.decode(enc.ids))

    print("\n== 4) Tokenizace dávek s měřením rychlosti ==", flush=True)
    batch, n_tok, n_char, n_doc, t0 = [], 0, 0, 0, time.time()
    for text in itertools.islice(db.iter_texts(a.source), 20000):
        batch.append(text)
        if len(batch) == 1000:
            for e in tok.encode_batch(batch):
                n_tok += len(e.ids) + 1
            n_char += sum(map(len, batch))
            n_doc += len(batch)
            batch = []
            dt = time.time() - t0
            print(f"{n_doc:>6} dokumentů | {n_tok:>10,} tokenů | {n_char / n_tok:.2f} znaku/token | "
                  f"{n_tok / dt:,.0f} tokenů/s", flush=True)
    print("\nHotovo. Celá databáze: python -m llm2005.tokenizer --vocab 32000, pak python -m llm2005.prepare")


if __name__ == "__main__":
    main()
