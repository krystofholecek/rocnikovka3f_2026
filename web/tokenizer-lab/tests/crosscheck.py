"""Křížová kontrola JS implementace BPE proti Hugging Face `tokenizers` (stejné nastavení jako llm2005/tokenizer.py).

    <python s tokenizers> tests/crosscheck.py [--vocab 1000] [--corpus soubor.txt]

Natrénuje tokenizér v Pythonu i v JS (Node) na stejném textu a porovná:
  1. slovník (pořadí ID), 2. seznam spojení, 3. rozdělení testovacích vět,
  4. načtení JS slovníku přes `Tokenizer.from_str` (export je kompatibilní).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EOT = "<|endoftext|>"

SENTENCES = [
    "Hi Kenneth, the meeting about the California energy contracts is moved to Friday at 10 am.",
    "Dnes jsem konečně dočetl knihu, kterou mi půjčil děda.",
    "The the the. Then there were three thinkers thinking.",
    "Žluťoučký kůň úpěl ďábelské ódy 🐴   mezery\n\nnové řádky",
    f"první dokument{EOT}druhý dokument",
]


def load_sample_docs() -> list[str]:
    """Načte SAMPLE_DOCS z js/sample.js (přes Node, aby text byl přesně stejný)."""
    code = "import('./js/sample.js').then(m => process.stdout.write(JSON.stringify(m.SAMPLE_DOCS)))"
    out = subprocess.run(["node", "-e", code], cwd=ROOT, capture_output=True, check=True)
    return json.loads(out.stdout.decode("utf-8"))


def train_py(docs: list[str], vocab: int) -> Tokenizer:
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab, special_tokens=[EOT], show_progress=False,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
    )
    tok.train_from_iterator(docs, trainer=trainer)
    return tok


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--vocab", type=int, default=1000)
    p.add_argument("--corpus", help="textový soubor; dokumenty oddělené prázdným řádkem")
    a = p.parse_args()

    if a.corpus:
        docs = [d for d in re.split(r"\n\s*\n", Path(a.corpus).read_text("utf-8")) if d.strip()]
    else:
        docs = load_sample_docs()

    py = train_py(docs, a.vocab)
    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    py.save(str(out_dir / "py-tokenizer.json"))
    py_json = json.loads(py.to_str())
    py_vocab = sorted(py_json["model"]["vocab"].items(), key=lambda kv: kv[1])
    py_merges = [list(m) if isinstance(m, list) else m.split(" ", 1) for m in py_json["model"]["merges"]]

    payload = json.dumps({"docs": docs, "vocab": a.vocab, "sentences": SENTENCES}).encode("utf-8")
    res = subprocess.run(["node", str(HERE / "js_bpe_cli.mjs")], input=payload, capture_output=True, check=True)
    js = json.loads(res.stdout.decode("utf-8"))

    ok = True
    print(f"dokumentů: {len(docs)}, slovník: Python {len(py_vocab)} / JS {len(js['vocab'])}")

    same_vocab = [t for t, _ in py_vocab] == js["vocab"]
    print("1) slovník (pořadí ID):", "SHODA" if same_vocab else "ROZDÍL")
    ok &= same_vocab

    first_diff = next((i for i, (x, y) in enumerate(zip(py_merges, js["merges"])) if x != y), None)
    same_merges = first_diff is None and len(py_merges) == len(js["merges"])
    print(f"2) spojení ({len(py_merges)}):", "SHODA" if same_merges else f"ROZDÍL od pozice {first_diff}")
    if first_diff is not None:
        lo = max(0, first_diff - 2)
        for i in range(lo, min(first_diff + 3, len(py_merges))):
            print(f"   #{i}: py {py_merges[i]}  js {js['merges'][i]}  (js četnost {js['counts'][i]})")
    ok &= same_merges

    for s, js_ids in zip(SENTENCES, js["encoded"]):
        enc = py.encode(s)
        same = enc.ids == js_ids
        ok &= same
        print(f"3) {'SHODA' if same else 'ROZDÍL'}  {len(enc.ids):3d} tokenů  {enc.tokens[:12]}{' …' if len(enc.tokens) > 12 else ''}")
        if not same:
            print("   py:", enc.ids)
            print("   js:", js_ids)

    # 4) JS export → Python
    exp = subprocess.run(
        ["node", "-e",
         "import('./js/bpe.js').then(m=>{let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
         "const j=JSON.parse(s);process.stdout.write(m.toHfJson({idToToken:j.vocab,merges:j.merges}))})})"],
        cwd=ROOT, input=json.dumps(js).encode("utf-8"), capture_output=True, check=True,
    )
    loaded = Tokenizer.from_str(exp.stdout.decode("utf-8"))
    same_load = all(loaded.encode(s).ids == py.encode(s).ids for s in SENTENCES)
    print("4) JS tokenizer.json načtený v Pythonu:", "SHODA" if same_load else "ROZDÍL")
    ok &= same_load

    print("\nVÝSLEDEK:", "vše shodné ✔" if ok else "nalezeny rozdíly ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
