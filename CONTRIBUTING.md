# Jak pracujeme

## Úkoly
- Každý úkol = GitHub issue. Než začneš, přiřaď si ho, ať se práce nekryje.
- Labely: `backend`, `frontend`, `text-prace` (psaní samotné ročníkové práce), `bug`, `enhancement`.

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
- Před PR spusť testy: `python manage.py test`.
- CI na GitHubu spouští testy automaticky.

## Commity
Krátce a výstižně, ideálně s prefixem: `feat:`, `fix:`, `docs:`, `chore:`.

## Tajné údaje
`.env` nikdy necommitovat. Nové proměnné přidej do `.env.example` bez skutečných hodnot.
