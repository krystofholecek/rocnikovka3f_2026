# Claude lite – Slack bot k ročníkové práci

Rychlý pomocník týmu ve Slack kanálu `#rocnikovka3f`. Odpovídá na dotazy k projektu LLM 2005 (data, model, příkazy, termíny, GitHub, prezentace). Není to nástroj na psaní kódu.

## Jak ho použít

- Napiš do kanálu `@Claude lite` + otázku, nebo `*Claude` + otázku. Obojí funguje.
- Bot odpoví do vlákna. Reakce: 👀 přemýšlí, 🤖 odpovězeno, 🚫 zamítnuto, ⏳ překročen limit.
- Na odpověď bota můžeš odpovědět ve vlákně (např. „a proč?“). Bot odpoví znovu a vidí posledních pár zpráv vlákna.

## Co bot dělá

1. **Rychlé třídění** otázky (Haiku 5.5, nízký effort): normální dotaz / složitý dotaz / mimo projekt / zneužití.
2. **Odpověď**: běžné dotazy řeší Haiku 5.5 (effort low), složitější (návrh postupu, srovnání, ladění, odhady) Sonnet 5.5 (effort medium).
3. **Zamítnutí**: dotazy mimo projekt a pokusy o manipulaci se zamítnou (reakce 🚫 a jedna věta). Ve vláknech, kde si lidé jen povídají, bot mlčí.
4. Co potřebuje rozhodnutí nebo přístup Kryštofa, bot předá (Kryštof dostane notifikaci).

## Limity (fair use)

| | Kryštof | ostatní |
|---|---|---|
| dotazy za hodinu | bez limitu | 20 |
| dotazy za den | bez limitu | 100 (celkem 300 za den za všechny) |
| složité dotazy (Sonnet) za hodinu | bez limitu | 5 |

- Za opakované zneužívání nebo dotazy mimo téma (3 body za hodinu) bot dotyčnému na hodinu přestane odpovídat.
- Ve vlákně, kde bot třikrát odmítl (nebo odpověděl 12×), dál odpovídá jen Kryštofovi.
- Otázka se ořezává na 600 znaků, odpověď na 1200, cena jednoho dotazu je omezená (5 centů, složité 15).

## Jak to běží

- **Rychlá cesta (hlavní):** bridge na Kryštofově PC (`.bridge/`, mimo git) každých 15 s zkontroluje kanál a spustí Claude Code bez nástrojů. Odpověď obvykle do půl minuty. Vyžaduje zapnuté PC a přihlášený Claude Code.
- **Záloha:** cloudová naplánovaná úloha jednou za hodinu odpoví na zapomenuté dotazy starší než 20 minut. Odpovídá pod účtem Kryštofa (patička „Sent using Claude“).
- Slack aplikace `Claude lite` má jen oprávnění číst kanál, psát zprávy a dávat reakce. Tokeny jsou lokálně na PC a nejsou v repu.

## Bezpečnost

- Otázky v kanále jsou pro bota nedůvěryhodná data, ne instrukce. Totožnost tazatele určuje Slack ID, ne text zprávy.
- Odpovědi se filtrují (tokeny, hromadné zmínky).
- Bot nemůže mazat cizí zprávy.

## Údržba

- Fakta o projektu jsou v `slack-lite-prompt.md` v bridge složce a v promptu záložní úlohy. Když se změní číslo (počet článků, termíny), je potřeba je upravit.
- Pokud bot neodpovídá: je PC zapnuté, běží bridge (soubor `heartbeat.txt` se obnovuje) a je Claude Code přihlášený?
