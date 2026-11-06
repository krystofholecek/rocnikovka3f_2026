"""Porovnání dvou tokenizérů (starý data/tokenizer-v1.json vs. nový data/tokenizer.json).

    python -m llm2005.tokenizer_compare     # -> data/progress/tokenizer-compare.json

Co se porovnává:
- kolik tokenů ve slovníku je „odpad“: jen mezery/tabulátory/nové řádky, jen jeden opakovaný
  znak (-----, ====, AAAA) nebo kus podobný base64,
- znaků na token na STEJNÉM textu (vyčištěný vzorek, stejné kusy pro oba tokenizéry),
- ukázkové věty (z tokenizer_report.SAMPLES).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from tokenizers import Tokenizer

from .tokenizer_report import SAMPLES, _eval_texts, readable

_B64ISH = re.compile(r"^[A-Za-z0-9+/]{12,}$")


def _is_base64_like(s: str) -> bool:
    s = s.strip()
    return bool(_B64ISH.match(s)) and any(c.isdigit() for c in s) and any(c.isupper() for c in s) \
        and any(c.islower() for c in s)


def junk_tokens(tok: Tokenizer) -> dict:
    """Spočítá tokeny, které jsou jen šum. Vrací počty a ukázky."""
    space, repeat, b64 = [], [], []
    for t in tok.get_vocab():
        s = readable(t)
        if len(s) >= 2 and not s.strip():
            space.append(s)
        elif len(s.strip()) >= 3 and len(set(s.strip())) == 1:
            repeat.append(s)
        elif _is_base64_like(s):
            b64.append(s)

    def top(xs: list[str]) -> list[str]:
        return sorted(xs, key=lambda x: (-len(x), x))[:15]

    return {"whitespace_only": len(space), "repeated_char": len(repeat), "base64_like": len(b64),
            # krátké (..., !!!, ---) jsou běžný text; dlouhé (víc než 8) po čištění v datech nejsou
            "whitespace_long": sum(1 for x in space if len(x) > 4),
            "repeated_char_long": sum(1 for x in repeat if len(x.strip()) > 8),
            "total": len(space) + len(repeat) + len(b64),
            "longest_whitespace": max((len(x) for x in space), default=0),
            "examples_repeated": top(repeat), "examples_base64": top(b64)}


def chars_per_token(tok: Tokenizer, texts_by_source: dict[str, list[str]]) -> dict:
    out = {}
    tot_c = tot_t = 0
    for s, texts in texts_by_source.items():
        n_c = sum(map(len, texts))
        n_t = sum(len(e.ids) for e in tok.encode_batch(texts))
        out[s] = round(n_c / max(1, n_t), 3)
        tot_c, tot_t = tot_c + n_c, tot_t + n_t
    out["_all"] = round(tot_c / max(1, tot_t), 3)
    return out


def compare(old: Tokenizer, new: Tokenizer, sample_dir: Path, sources: list[str], eval_chars: int = 500_000,
            names: tuple[str, str] = ("v1 (bez čištění)", "v2 (s čištěním)")) -> dict:
    texts = {}
    for s in sources:
        t = _eval_texts(sample_dir, s, eval_chars)
        if t:
            texts[s] = t
    cpt_old, cpt_new = chars_per_token(old, texts), chars_per_token(new, texts)
    samples = []
    for label, text in SAMPLES:
        eo, en = old.encode(text), new.encode(text)
        samples.append({"label": label, "text": text,
                        "old": {"n_tokens": len(eo.ids), "tokens": [readable(t) for t in eo.tokens]},
                        "new": {"n_tokens": len(en.ids), "tokens": [readable(t) for t in en.tokens]}})
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "names": {"old": names[0], "new": names[1]},
        "vocab_size": {"old": old.get_vocab_size(), "new": new.get_vocab_size()},
        "junk_tokens": {"old": junk_tokens(old), "new": junk_tokens(new)},
        "chars_per_token": {"old": cpt_old, "new": cpt_new},
        "eval_note": f"stejný vyčištěný text pro oba: prvních ~{eval_chars:,} znaků vzorku každého zdroje",
        "samples": samples,
    }


def print_compare(c: dict) -> None:
    jo, jn = c["junk_tokens"]["old"], c["junk_tokens"]["new"]
    print(f"\n== Porovnání tokenizérů: {c['names']['old']} -> {c['names']['new']} ==")
    print(f"Tokeny jen z mezer:          {jo['whitespace_only']:>5} -> {jn['whitespace_only']:>5}"
          f"   (nejdelší {jo['longest_whitespace']} -> {jn['longest_whitespace']} znaků)")
    print(f"Tokeny z opakovaného znaku:  {jo['repeated_char']:>5} -> {jn['repeated_char']:>5}"
          f"   (z toho delší než 8: {jo['repeated_char_long']} -> {jn['repeated_char_long']})")
    print(f"Tokeny podobné base64:       {jo['base64_like']:>5} -> {jn['base64_like']:>5}")
    co, cn = c["chars_per_token"]["old"], c["chars_per_token"]["new"]
    print(f"Znaků na token (celkem):     {co['_all']:>5} -> {cn['_all']:>5}")
    for s in sorted(k for k in co if k != "_all"):
        print(f"   {s:<18} {co[s]:>6} -> {cn[s]:>6}")
    for s in c["samples"]:
        print(f"  [{s['label']}] {s['old']['n_tokens']} -> {s['new']['n_tokens']} tokenů")


def main() -> None:
    p = argparse.ArgumentParser(description="Porovnání starého a nového tokenizéru")
    p.add_argument("--old", default="data/tokenizer-v1.json")
    p.add_argument("--new", default="data/tokenizer.json")
    p.add_argument("--sample-dir", default="data/tokenizer-sample")
    p.add_argument("--eval-chars", type=int, default=500_000)
    p.add_argument("--out", default="data/progress/tokenizer-compare.json")
    a = p.parse_args()
    sd = Path(a.sample_dir)
    sources = sorted(f.stem for f in sd.glob("*.jsonl"))
    c = compare(Tokenizer.from_file(a.old), Tokenizer.from_file(a.new), sd, sources, a.eval_chars)
    Path(a.out).write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    print_compare(c)
    print(f"\nUloženo: {a.out}")


if __name__ == "__main__":
    main()
