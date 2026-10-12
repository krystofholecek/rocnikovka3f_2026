# Jak pracujeme

## Úkoly
- Každý úkol = GitHub issue. Než začneš, přiřaď si ho, ať se práce nekryje.
- Labely: `data` (zdroje, databáze), `model` (tokenizer, trénink, pokusy), `text-prace` (psaní samotné SOČ), `bug`, `enhancement`. Web (`backend`, `frontend`) přijde na řadu až po modelu.
- Úkoly jsou rozdělené do milníků podle fází projektu (Data → Malý model → Velký model → Vyhodnocení a práce).

## Větve
- Do `main` se nepushuje napřímo, je chráněná.
- Nová větev pro každý úkol: `feature/kratky-popis`, `fix/kratky-popis`, `docs/kratky-popis`.

```bash
git checkout main
git pull
git checkout -b feature/prihlaseni
```

## Pull requesty
- PR do `main`, v popisu odkaz na issue (`Closes #12`).
- Potřeba aspoň 1 schválení od někoho jiného.
- Před PR spusť testy: `pytest llm2005` (model) a `python manage.py test` (web).
- CI na GitHubu spouští testy automaticky.

## Commity
Krátce a výstižně, ideálně s prefixem: `feat:`, `fix:`, `docs:`, `chore:`.

## Data a modely
Data (`data/`) ani natrénované modely (`checkpoints/`) se necommitují, jsou na to moc velké.
Do repa patří jen kód a konfigurace. Výsledky pokusů (loss, rychlost, ukázky textu) zapisujte do issue.

## Tajné údaje
`.env` nikdy necommitovat. Nové proměnné přidej do `.env.example` bez skutečných hodnot.
