# Ročníková práce 3.F 2026

[![CI](https://github.com/krystofholecek/rocnikovka3f_2026/actions/workflows/ci.yml/badge.svg)](https://github.com/krystofholecek/rocnikovka3f_2026/actions/workflows/ci.yml)

Toto je ročníková práce na gymnáziu.
Ještě není jasné téma projektu, ale práce na něm už začala.
Na projektu budou pracovat Milý, Gregor a Holeček.

This is a year-long school project (ročníková práce) at the gymnasium.
The project topic is not yet decided, but work has already started.
The project is being developed by Milý, Gregor, and Holeček.

Jak pracujeme (větve, PR, review): viz [CONTRIBUTING.md](CONTRIBUTING.md).

## Spuštění / Getting started

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # then set DJANGO_SECRET_KEY
python manage.py migrate
python manage.py createsuperuser   # optional, for /admin
python manage.py runserver
```

Open http://127.0.0.1:8000/

## Struktura / Structure

- `config/` – Django project settings and root URLs
- `core/` – main app (views, models, app templates)
- `templates/` – shared templates (`base.html`)
- `static/` – CSS, JS, images

Run tests: `python manage.py test`
