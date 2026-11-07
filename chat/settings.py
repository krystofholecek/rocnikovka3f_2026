"""Nastavení Django pro backend chatu (jen API, žádné stránky, databáze ani přihlašování).

Všechno se nastavuje proměnnými prostředí (lokálně je lze dát do souboru .env, viz .env.example).
Výchozí hodnoty jsou bezpečné pro provoz: DEBUG je vypnutý a bez DJANGO_SECRET_KEY server nenastartuje.
"""

import os
from pathlib import Path

try:  # .env je jen pohodlí pro lokální vývoj, v Dockeru se proměnné předávají přímo
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("Chybí DJANGO_SECRET_KEY (nastav ji v prostředí nebo v .env).")
    SECRET_KEY = "django-insecure-jen-pro-lokalni-vyvoj"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# Žádné uživatelské účty, administrace ani databáze: API nic neukládá.
INSTALLED_APPS: list[str] = []
DATABASES: dict = {}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "chat.urls"
WSGI_APPLICATION = "chat.wsgi.application"
TEMPLATES: list = []

LANGUAGE_CODE = "cs"
TIME_ZONE = "Europe/Prague"
USE_I18N = False
USE_TZ = True

# Bezpečnostní hlavičky (za HTTPS proxy, např. Cloudflare Tunnel nebo Caddy).
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
X_FRAME_OPTIONS = "DENY"
if env_bool("DJANGO_BEHIND_HTTPS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True

# Velikost těla požadavku: chat posílá jen krátký text.
DATA_UPLOAD_MAX_MEMORY_SIZE = 64 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
