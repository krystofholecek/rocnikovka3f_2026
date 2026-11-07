# Trénink

Všechny příkazy se spouštějí z kořene repa ve virtuálním prostředí (`pip install -r requirements/train.txt`,
PyTorch s CUDA podle [pytorch.org](https://pytorch.org/get-started/locally/)).
Čísla označená „odhad“ jsou odhady, skutečnou hodnotu ukáže až běh.

## 1. Tokenizér a data

```bash
python -m llm2005.tokenizer      # vzorek vyvážené směsi -> data/tokenizer.json (~15 min)
python -m llm2005.prepare        # směs -> data/train.bin a data/val.bin (hodiny, podle velikosti směsi)
```

První směs (~3 mld. znaků) dala 782 mil. tokenů pro trénink a 3,7 mil. pro kontrolu, ~3,8 znaku na token.

## 2. Trénink

```bash
python -m llm2005.train --config configs/tiny.json --max-steps 50   # rychlý test, že vše funguje
python -m llm2005.train --config configs/small.json --hours 8       # skončí sám po 8 hodinách
```

- Když `checkpoints/<name>/ckpt.pt` existuje, trénink **pokračuje** tam, kde skončil (stačí spustit znovu).
- Zastavení: Ctrl+C, nebo prázdný soubor `checkpoints/<name>/STOP`. Checkpoint se ukládá atomicky,
  takže ani tvrdé ukončení nic nerozbije.
- Průběh: `data/progress/train-status.json`, dashboard `python -m llm2005.progress_server` → http://localhost:8800.
- Rychlost a paměť grafiky: [trenink-rychlost.md](trenink-rychlost.md) (přibude s PR #20).

| Konfigurace | Kroků | Tokenů na krok | Celkem tokenů | Doba na RTX 3060 |
|---|---:|---:|---:|---|
| `small` (~23 M) | 28 000 | 65 536 | ~1,8 mld. | ~7 h (odhad, naměřeno ~75 tis. tokenů/s) |
| `base` (~125 M) | podle `configs/base.json` | | cíl ~2,5 mld. | ≈ 4 noci (odhad) |

## 3. Jak poznat, že se model učí

- **Loss** (chyba při hádání dalšího tokenu): na začátku ~10,4 (= ln 32 000, náhodné hádání), pak klesá.
- **val loss** se měří na datech, ze kterých se model neučí. Když train loss klesá a val loss roste,
  model se učí nazpaměť (overfitting) – je čas skončit.
- Při evaluaci model píše ukázky z promptů jako „The Internet is“. Postup bývá: nesmysly → slova → věty → smysl.

Stav k 7. 11. 2026: `small` na kroku ~6 350, val loss 3,83.

## 4. Vyzkoušet a připravit pro chat

```bash
python -m llm2005.sample --config configs/small.json --prompt "The Internet is"
python -m llm2005.export --config configs/small.json            # -> checkpoints/chat/model.pt + tokenizer.json
python -m llm2005.export --config configs/base.json --half      # menší soubor (poloviční přesnost vah)
```

Co dál s exportovaným modelem: [nasazeni.md](nasazeni.md#až-bude-model-hotový).

## Když něco nejde

| Problém | Co zkusit |
|---|---|
| `CUDA out of memory` nebo trénink zpomalí na zlomek | menší `batch_size` a větší `grad_accum` v configu (tokenů na krok zůstane stejně); zavřít jiné programy na GPU |
| loss neklesá nebo je `nan` | menší `lr`, zkontrolovat, že `train.bin` není prázdný a tokenizér sedí k datům |
| po pokračování jiné výsledky | pokračuje se z posledního checkpointu (kroky po něm se ztratí) – to je v pořádku |
