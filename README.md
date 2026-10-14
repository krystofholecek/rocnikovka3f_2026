# Ročníková práce 3.F 2026 – LLM 2005

[![CI](https://github.com/krystofholecek/rocnikovka3f_2026/actions/workflows/ci.yml/badge.svg)](https://github.com/krystofholecek/rocnikovka3f_2026/actions/workflows/ci.yml)

Vlastní jazykový model (LLM) natrénovaný **úplně od nuly** jen na textech napsaných **do 31. 12. 2005**.
Nepoužíváme žádný hotový model (GPT, Llama…), tokenizer ani váhy. Všechno vzniká z našich dat.
Model tedy „žije v roce 2005“: neví nic o iPhonu, Bitcoinu, covidu ani ChatGPT.

Autoři: Holeček, Milý, Gregor · Ročníková práce na gymnáziu, 2026/2027

## Jak to funguje

```
NAJÍT ZDROJ  ->  KONTROLA DATA  ->  je ≤ 31. 12. 2005?  ->  DATABÁZE  ->  TOKENIZER  ->  TRÉNINK MODELU
                                         └─ ne -> zahodit (zapíše se do tabulky rejected)
```

Pravidlo: **datum uvedené u zdroje bereme jako pravdivé.** Dokument bez data nebereme.

| Krok | Příkaz | Výstup |
| --- | --- | --- |
| 1. Zdroj → kontrola data → databáze | `python -m llm2005.ingest wikipedia <soubor>` | `data/llm2005.sqlite` |
| 2. Přehled databáze | `python -m llm2005.ingest stats` | počty dokumentů a znaků podle zdroje |
| 3. Vlastní tokenizer | `python -m llm2005.tokenizer` | `data/tokenizer.json` |
| 4. Převod na tokeny | `python -m llm2005.prepare` | `data/train.bin`, `data/val.bin` |
| 5. Trénink | `python -m llm2005.train --config configs/small.json --hours 8` | `checkpoints/small/` |
| 6. Vyzkoušet model | `python -m llm2005.sample --config configs/small.json --prompt "The Internet is"` | vygenerovaný text |

Trénink se průběžně ukládá. Když ho spustíš znovu, pokračuje tam, kde skončil (ideální na trénink přes noc).

## Velikosti modelu (`configs/`)

| Konfigurace | Parametrů | K čemu |
| --- | --- | --- |
| `tiny.json` | ~4 M | test, že vše funguje; poběží i na notebooku bez GPU |
| `small.json` | ~25 M | pokusy, na GPU hodiny |
| `base.json` | ~125 M | hlavní model (velikost GPT-2 small), ~2,5 mld. tokenů |

Na jakém počítači budeme trénovat, zatím nevíme. Rychlost konkrétního PC změří:

```bash
python -m llm2005.benchmark --config configs/base.json
```

## Zdroje dat

| Zdroj | Stav | Jak se datuje |
| --- | --- | --- |
| Wikipedie (historie revizí ze 16. 8. 2006, [archive.org](https://archive.org/details/enwiki-20060816-pages-meta-history.xml.7z), 5,5 GB) | parser hotový | poslední revize článku do 31. 12. 2005 |
| Knihy Project Gutenberg | plán | datum vydání |
| Usenet 1981–1991 | plán | datum příspěvku |

Wikipedii zpracujeme bez rozbalování na disk:

```bash
7z x -so enwiki-20060816-pages-meta-history.xml.7z | python -m llm2005.ingest wikipedia -
```

Jiný zdroj stačí převést na JSON Lines (`source`, `source_id`, `date`, `text`) a nahrát přes `python -m llm2005.ingest jsonl soubor.jsonl`.

## Instalace

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
# PyTorch: s NVIDIA GPU podle https://pytorch.org/get-started/locally/ (verze s CUDA), bez GPU stačí:
pip install -r requirements-llm.txt
pytest llm2005                          # testy
```

## Struktura

- `llm2005/` – model a celý řetězec zpracování dat
  - `sources/` – čtení zdrojů (Wikipedie, JSONL); nový zdroj = nový soubor
  - `datecheck.py` – kontrola data (hranice 31. 12. 2005)
  - `db.py` – databáze dokumentů (SQLite)
  - `tokenizer.py`, `prepare.py` – vlastní tokenizer a převod na tokeny
  - `model.py`, `train.py`, `sample.py`, `benchmark.py` – model GPT, trénink, generování, test rychlosti
  - `tests/` – testy (`pytest llm2005`)
- `configs/` – velikosti modelu a nastavení tréninku
- `data/`, `checkpoints/` – data a natrénované modely (necommitují se, jsou velké)
- `config/`, `core/`, `templates/`, `static/` – kostra webu v Djangu (na později, až bude model hotový)

Jak pracujeme (větve, PR, review): viz [CONTRIBUTING.md](CONTRIBUTING.md). Plán projektu: [#6](https://github.com/krystofholecek/rocnikovka3f_2026/issues/6).
