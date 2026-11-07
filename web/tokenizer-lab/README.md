# Tokenizer Lab

> **Samostatný doprovodný projekt.** Tokenizer Lab je oddělený statický web, který jen vysvětluje a vizualizuje
> tokenizaci z hlavního projektu [LLM 2005](../../README.md). Nesdílí s ním žádný kód (je napsaný v JavaScriptu,
> Python ho nepotřebuje), má vlastní README, testy i nasazení (`chronosllm.com/tokenizer/`).
> Hlavní projekt na něm nijak nezávisí – složku `web/tokenizer-lab/` lze klidně smazat nebo přesunout jinam.

Statický web, na kterém je vidět, **jak probíhá BPE tokenizace**: nahrajete texty, v prohlížeči natrénujete
bytově-úrovňový BPE tokenizér a živě sledujete, jak se z bajtů skládají tokeny. Tokenizér je stejný jako v hlavním
projektu (`llm2005/tokenizer.py`, `llm2005/prepare.py`) a výsledky se s ním shodují – viz *Shoda s Pythonem*.

Vše běží jen v prohlížeči – žádný backend, žádné externí knihovny ani CDN, soubory se nikam neodesílají.

## Co umí

1. **Data** – drag & drop více souborů (`.txt`, `.md`, `.jsonl`, `.csv`; pole s textem nastavitelné, výchozí `text`),
   přehled velikosti/znaků/slov/dokumentů, limit 200 MB, čtení po 4MB blocích ve Workeru. Tlačítko *Použít ukázková data*.
2. **Trénink BPE** ve Web Workeru – ByteLevel (256 bajtů + `<|endoftext|>`), slovník 500–32 000, volitelný vzorek v MB.
   Živě: průběh, rychlost, tabulka posledních spojení, graf znaků/token, histogram délek, nejdelší tokeny.
   Start / Pauza / Pokračovat / Reset a **Krok** (jedno spojení se zvýrazněním dvojice ve vzorové větě).
   Export `tokenizer.json` (formát Hugging Face) a CSV; import hotového `tokenizer.json` (např. ostrý 32k).
3. **Prohlížeč tokenizace** – barevné tokeny s detailem (ID, bajty), ID tokenů, poměr znaků/token, dekódování zpět
   s kontrolou shody, posuvník počtu spojení (0 / 100 / 500 / 2 000 / vše) a srovnávací tabulka.
4. **Pipeline „prepare“** – dokumenty → tokenizace → `<|endoftext|>` → train/val (0,5 %) → velikost `.bin` (uint16),
   náhled proudu tokenů. Jen statistika, binární soubory se negenerují.

## Spuštění lokálně

```bash
cd web/tokenizer-lab
python -m http.server 8765
# otevřít http://localhost:8765/
```

(Moduly a Web Workery nefungují z `file://`, proto je potřeba jakýkoli statický server.)

## Nasazení (Cloudflare Pages / Workers static assets)

Nahrát obsah složky jako statické soubory – stačí `index.html`, `css/`, `js/`. Složky `tests/` a `node_modules/` nejsou potřeba.
Všechny cesty jsou relativní, takže web běží samostatně i v podsložce, např. `chronosllm.com/tokenizer/`
(soubory nahrát do `tokenizer/`; adresa musí končit lomítkem nebo mít přesměrování `/tokenizer` → `/tokenizer/`).

## Shoda s Pythonem

Trénink napodobuje `tokenizers.trainers.BpeTrainer`: stejné pořadí ID (speciální tokeny, pak 256 bajtů seřazených
podle kódu), stejný GPT-2 regex předtokenizace a stejné řešení shod – při rovné četnosti vyhrává dvojice
s menšími ID. Výsledek je proto **identický** se Pythonem (slovník, pořadí spojení i rozdělení vět).

Drobné rozdíly, které mohou nastat:
- regex v JS (`\p{L}`, `\p{N}`) a v Rustu (Oniguruma) se mohou lišit u velmi vzácných Unicode znaků;
- rozdělení train/val je náhodné se seedem 2005 jako v `prepare.py`, ale jiný generátor → jiný výběr dokumentů;
- velké `.txt` soubory se čtou po blocích a řežou na mezerách, což tokenizaci nemění.

## Testy

```bash
cd web/tokenizer-lab
npm test                       # unit testy BPE (node --test)
python tests/crosscheck.py     # křížová kontrola proti HF tokenizers z venv hlavního projektu (--vocab, --corpus)
npm i && node tests/e2e.mjs    # Playwright + nainstalovaný Chrome: screenshoty do tests/out/, test 50 MB souboru
```

## Struktura

| Soubor | Co dělá |
|---|---|
| `js/bytelevel.js` | mapování bajtů na znaky, GPT-2 předtokenizace |
| `js/bpe.js` | `BpeTrainer`, `BpeTokenizer`, import/export `tokenizer.json`, CSV |
| `js/train.worker.js` | trénink ve Workeru, souhrn max. ~10× za sekundu |
| `js/data.worker.js` | statistika souborů, výpočet pipeline |
| `js/docs.js` | čtení `.txt/.md/.jsonl/.csv` po částech |
| `js/charts.js` | grafy na canvasu |
| `js/app.js` | UI |
