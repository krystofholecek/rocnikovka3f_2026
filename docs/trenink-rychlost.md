# Proč byl trénink `small` pomalý a jak jsme to opravili

## Co se stalo (7. 11. 2026)

Trénink `configs/small.json` (model 22,9 M parametrů) běžel jen **~2 000 tokenů/s**
(50 kroků za 28 minut) a celý počítač se sekal.

## Příčina: přetečení paměti grafiky do „sdílené paměti GPU“

Původní nastavení bylo `batch_size 32 × 512 tokenů`. Měřením (`python -m llm2005.diagnose_train`) vyšlo:

| batch | data | forward+backward | optimizer | tokenů/s | PyTorch si rezervuje | celé GPU (nvidia-smi) |
|---|---|---|---|---|---|---|
| 8  | 3 ms  | 50 ms  | 6 ms | ~77 000 | 2,5 GB | 4,2 GB |
| 16 | 6 ms  | 94 ms  | 6 ms | ~82 000 | 4,7 GB | 6,5 GB |
| 32 | 10 ms | 184 ms | 6 ms | ~84 000 | 9,3 GB | **11,0 GB z 12 GB** |

Batch 32 tedy zabral skoro celou grafiku. Největší část paměti spolkne poslední vrstva modelu:
pro každý z 32 × 512 tokenů počítá skóre všech 32 000 slov slovníku (to jsou stovky milionů čísel).
K tomu si ~1,7 GB bere plocha Windows, prohlížeče, Discord atd.

Jakmile jiné programy vzaly o kousek víc, součet přelezl 12 GB. Windows v takové chvíli
nevyhodí chybu, ale začne část dat odkládat do běžné RAM („sdílená paměť GPU“) a grafika
na ně čeká přes sběrnici PCIe. Trénink se zpomalí desítkykrát, a protože grafiku potřebuje
i plocha Windows, seká se celý počítač.

Ostatní části jsme ověřili a příčinou nebyly:
- načítání dat z `train.bin` (memmap, náhodné okno, převod na tensor, přesun na GPU) trvá ~5 ms z ~100 ms,
- AMP (bf16) se používá, krok optimizeru trvá ~6 ms,
- evaluace a ukázky textu běží jen každých 500 kroků, `train-status.json` se zapisuje každých 5 s ve vlákně na pozadí,
- `torch.compile` nepoužíváme (na Windows chybí knihovna Triton).

## Oprava

- `batch_size 16`, `grad_accum 8`: do modelu jde **stejně tokenů na krok (65 536)**, jen ve dvou menších dávkách.
  Výsledek učení je stejný, paměť poloviční.
- `max_vram_gb 8`: strop paměti pro PyTorch. Kdyby trénink chtěl víc, skončí chybou „out of memory“
  místo tichého přetečení do RAM.
- Proces tréninku běží s prioritou **BelowNormal** (`--priority low`, výchozí) a s nejvýš 4 CPU vlákny
  (`cpu_threads`), takže ostatní programy mají přednost.
- Do `train-status.json` se zapisuje stav z `nvidia-smi` (vytížení GPU, VRAM celé karty, teplota, příkon)
  a dashboard ho ukazuje v dlaždici „GPU (nvidia-smi)“. Když je na GPU volno méně než 0,5 GB,
  dashboard i log ukážou varování.

## Naměřená rychlost

| | tokenů/s | VRAM celé GPU |
|---|---|---|
| před opravou (běh 111, s přetečením) | ~2 000 | přes 12 GB (přetékalo) |
| batch 32, krátký test bez dalších programů | ~91 000 | 11,2 GB z 12 GB (na hraně) |
| **po opravě: batch 16 × 8, BelowNormal, 4 vlákna** | **~85 000** | **6,7 GB z 12 GB** |

Odhad: 8 h × ~85 000 tokenů/s ≈ 2,4 mld. tokenů, takže celých 28 000 kroků (1,8 mld. tokenů) by mělo být hotových za ~6 h.
