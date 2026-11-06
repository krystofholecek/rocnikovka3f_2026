"""Kontrola natrénovaného tokenizéru – co se naučil a jak dobře „balí“ text.

    python -m llm2005.tokenizer_report            # znovu vytvoří data/progress/tokenizer-report.json

Report obsahuje:
- skutečnou velikost slovníku,
- 30 nejdelších tokenů,
- rozdělení 5 ukázkových vět (e-mail, blog, zpráva, právní věta, Wikipedie),
- znaků na token celkem i podle zdroje (víc = lepší, text je kratší v tokenech),
- podíl bajtových tokenů (kolik tokenů je jen jeden bajt – „nouzové“ skládání po písmenech).

Měří se na začátku uloženého vzorku (data/tokenizer-sample/). Tokenizér se nepřeučuje jako model,
takže to stačí; čísla jsou jen orientační.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Callable

from tokenizers import Tokenizer

# Ukázkové věty ve stylu roku 2005 (vymyšlené, ne z dat).
SAMPLES = [
    ("e-mail", "Hi Mark, please find attached the revised Q3 forecast. Can we go over it on "
               "Monday's conference call at 10am? Thanks, Sarah"),
    ("blog", "OMG I finally got my new iPod nano today!! Spent the whole evening ripping CDs "
             "and updating my MySpace profile lol :)"),
    ("zpráva", "Hurricane Katrina made landfall near New Orleans on Monday, forcing more than "
               "one million residents of the Gulf Coast to evacuate."),
    ("právní věta", "The judgment of the district court is affirmed in part and reversed in part, "
                    "and the case is remanded for further proceedings consistent with this opinion."),
    ("Wikipedie", "Mozilla Firefox is a free, open source web browser descended from the Mozilla "
                  "Application Suite and managed by the Mozilla Foundation."),
]


def _bytes_to_unicode() -> dict[int, str]:
    """Stejná tabulka, jakou používá ByteLevel: každý bajt má svůj „viditelný“ znak."""
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


BYTE_TO_CHAR = _bytes_to_unicode()
CHAR_TO_BYTE = {c: b for b, c in BYTE_TO_CHAR.items()}


def readable(token: str) -> str:
    """Token ve tvaru ByteLevel (např. 'Ġthe') -> obyčejný text (' the')."""
    if any(c not in CHAR_TO_BYTE for c in token):
        return token  # speciální token
    return bytes(CHAR_TO_BYTE[c] for c in token).decode("utf-8", errors="replace")


def byte_token_ids(tok: Tokenizer) -> tuple[set[int], set[int]]:
    """Id tokenů, které jsou jen jeden bajt; a z nich ty, které nejsou ASCII (kousky ů, é, €…)."""
    single, nonascii = set(), set()
    for t, i in tok.get_vocab().items():
        if len(t) == 1 and t in CHAR_TO_BYTE:
            single.add(i)
            if CHAR_TO_BYTE[t] >= 0x80:
                nonascii.add(i)
    return single, nonascii


def _eval_texts(sample_dir: Path, source: str, eval_chars: int) -> list[str]:
    texts, n = [], 0
    f = sample_dir / f"{source}.jsonl"
    if not f.exists():
        return texts
    with open(f, encoding="utf-8") as fh:
        for line in fh:
            t = json.loads(line)
            texts.append(t[: eval_chars - n])  # dlouhé knihy ořízneme
            n += len(texts[-1])
            if n >= eval_chars:
                break
    return texts


def build_report(tok: Tokenizer, sample_dir: Path, manifest: dict | None, eval_chars: int = 2_000_000,
                 extra: dict | None = None, on_source: Callable[[str], None] | None = None) -> dict:
    vocab = tok.get_vocab()
    single, nonascii = byte_token_ids(tok)

    longest = sorted(vocab.items(), key=lambda kv: (-len(readable(kv[0])), kv[1]))[:30]

    samples = []
    for label, text in SAMPLES:
        enc = tok.encode(text)
        samples.append({
            "label": label, "text": text, "n_chars": len(text), "n_tokens": len(enc.ids),
            "chars_per_token": round(len(text) / len(enc.ids), 2),
            "tokens": [readable(t) for t in enc.tokens], "ids": enc.ids,
            "roundtrip_ok": tok.decode(enc.ids) == text,
        })

    per_source = []
    msources = (manifest or {}).get("sources", {})
    for s in sorted(msources):
        if on_source:
            on_source(s)
        texts = _eval_texts(sample_dir, s, eval_chars)
        if not texts:
            continue
        n_chars = n_tok = n_byte = n_nonascii = 0
        for i in range(0, len(texts), 256):
            chunk = texts[i:i + 256]
            for enc in tok.encode_batch(chunk):
                ids = enc.ids
                n_tok += len(ids)
                n_byte += sum(1 for x in ids if x in single)
                n_nonascii += sum(1 for x in ids if x in nonascii)
            n_chars += sum(map(len, chunk))
        per_source.append({
            "source": s, "eval_chars": n_chars, "eval_tokens": n_tok,
            "chars_per_token": round(n_chars / max(1, n_tok), 3),
            "byte_token_share": round(n_byte / max(1, n_tok), 5),
            "nonascii_byte_token_share": round(n_nonascii / max(1, n_tok), 5),
            "mix_chars": msources[s]["chars"],
        })

    tot_c = sum(p["eval_chars"] for p in per_source)
    tot_t = sum(p["eval_tokens"] for p in per_source)
    # Vážený průměr podle směsi: kolik tokenů by měl celý vzorek (zdroj po zdroji)
    mix_c = sum(p["mix_chars"] for p in per_source)
    mix_t = sum(p["mix_chars"] / p["chars_per_token"] for p in per_source if p["chars_per_token"])
    def weighted(key: str) -> float | None:
        if not mix_t:
            return None
        return sum(p[key] * p["mix_chars"] / p["chars_per_token"] for p in per_source if p["chars_per_token"]) / mix_t

    byte_share, nonascii_share = weighted("byte_token_share"), weighted("nonascii_byte_token_share")

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "vocab_size": tok.get_vocab_size(),
        "eot_id": tok.token_to_id("<|endoftext|>"),
        "byte_tokens_in_vocab": len(single),
        "longest_tokens": [{"id": i, "token": readable(t), "length": len(readable(t))} for t, i in longest],
        "samples": samples,
        "chars_per_token": {
            "mix_weighted": round(mix_c / mix_t, 3) if mix_t else None,
            "eval_sample": round(tot_c / tot_t, 3) if tot_t else None,
        },
        # bajtový token = token z jednoho bajtu (písmeno „a“, „I“ i kousek „é“); ne-ASCII = jen kousky
        # znaků mimo angličtinu (é, €, “) – to je skutečné „nouzové“ skládání po bajtech
        "byte_token_share": {
            "mix_weighted": None if byte_share is None else round(byte_share, 5),
            "nonascii_mix_weighted": None if nonascii_share is None else round(nonascii_share, 5),
        },
        "per_source": per_source,
        "eval_note": f"měřeno na prvních ~{eval_chars:,} znacích vzorku každého zdroje",
        "mix": {s: {"docs": v["docs"], "chars": v["chars"]} for s, v in msources.items()},
        **(extra or {}),
    }


def print_report(r: dict) -> None:
    print(f"\n== Kontrola tokenizéru ==\nSlovník: {r['vocab_size']:,} tokenů ({r['byte_tokens_in_vocab']} bajtových)")
    print(f"Znaků na token (vážený podle směsi): {r['chars_per_token']['mix_weighted']}")
    bs = r["byte_token_share"]["mix_weighted"]
    if bs is not None:
        na = r["byte_token_share"]["nonascii_mix_weighted"]
        print(f"Podíl bajtových tokenů v textu: {100 * bs:.2f} % (z toho ne-ASCII kousky znaků {100 * na:.2f} %)")
    print("\n30 nejdelších tokenů:")
    print("  " + " | ".join(repr(t["token"]) for t in r["longest_tokens"]))
    print("\nUkázkové věty:")
    for s in r["samples"]:
        print(f"  [{s['label']}] {s['n_chars']} znaků -> {s['n_tokens']} tokenů ({s['chars_per_token']} zn/tok)")
        print("    " + " | ".join(s["tokens"]))
    print("\nPodle zdroje:            znaků/token   bajtové tokeny")
    for p in r["per_source"]:
        print(f"  {p['source']:<18} {p['chars_per_token']:>8.2f}      {100 * p['byte_token_share']:>6.2f} %")


def main() -> None:
    p = argparse.ArgumentParser(description="Report natrénovaného tokenizéru")
    p.add_argument("--tokenizer", default="data/tokenizer.json")
    p.add_argument("--sample-dir", default="data/tokenizer-sample")
    p.add_argument("--out", default="data/progress/tokenizer-report.json")
    p.add_argument("--eval-chars", type=int, default=2_000_000)
    a = p.parse_args()
    tok = Tokenizer.from_file(a.tokenizer)
    sample_dir = Path(a.sample_dir)
    mf = sample_dir / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else None
    r = build_report(tok, sample_dir, manifest, a.eval_chars, extra={"tokenizer_path": a.tokenizer})
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print_report(r)
    print(f"\nUloženo: {a.out}")


if __name__ == "__main__":
    main()
