# Basic School Project

Toto je školní projekt gymnázia.
Ještě není jasné téma projektu, ale práce na něm už začala.
Na projektu budou pracovat Milý, Gregor a Holeček.

This is a school project for the gymnasium.
The project topic is not yet decided, but work has already started.
The project is being developed by Milý, Gregor, and Holeček.

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
