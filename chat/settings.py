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
    "chat.middleware.ApiCorsMiddleware",
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
APPEND_SLASH = False


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# ---------------------------------------------------------------- chat
# Kde je model: složka s model.pt (export z `python -m llm2005.export`) a tokenizer.json.
# Lze zadat i přímo checkpoint z tréninku (checkpoints/base/ckpt.pt) a tokenizér (data/tokenizer.json).
CHAT_MODEL_DIR = Path(os.environ.get("CHAT_MODEL_DIR", BASE_DIR / "checkpoints" / "chat"))
CHAT_CHECKPOINT = os.environ.get("CHAT_CHECKPOINT", str(CHAT_MODEL_DIR / "model.pt"))
CHAT_TOKENIZER = os.environ.get("CHAT_TOKENIZER", str(CHAT_MODEL_DIR / "tokenizer.json"))
CHAT_CONFIG = os.environ.get("CHAT_CONFIG", "")  # configs/*.json, jen když checkpoint nemá konfiguraci v sobě
CHAT_DEVICE = os.environ.get("CHAT_DEVICE", "auto")  # auto | cpu | cuda
CHAT_TORCH_THREADS = env_int("CHAT_TORCH_THREADS", 0)  # 0 = nechat na PyTorchi

# Odkud smí prohlížeč volat API (CORS).
CHAT_ALLOWED_ORIGINS = env_list("CHAT_ALLOWED_ORIGINS", "https://chronosllm.com,https://www.chronosllm.com")
# Za Cloudflare (Tunnel/proxy) je skutečná IP návštěvníka v hlavičce CF-Connecting-IP.
# Nastav jen tehdy, když server není dostupný jinak než přes tuto proxy (jinak by šla hlavička podvrhnout).
CHAT_CLIENT_IP_HEADER = os.environ.get("CHAT_CLIENT_IP_HEADER", "")

# Limity (bezpečné výchozí hodnoty pro malý server bez GPU).
CHAT_MAX_INPUT_CHARS = env_int("CHAT_MAX_INPUT_CHARS", 1000)
CHAT_MAX_HISTORY_CHARS = env_int("CHAT_MAX_HISTORY_CHARS", 4000)
CHAT_MAX_MESSAGES = env_int("CHAT_MAX_MESSAGES", 12)
CHAT_DEFAULT_NEW_TOKENS = env_int("CHAT_DEFAULT_NEW_TOKENS", 150)
CHAT_MAX_NEW_TOKENS = env_int("CHAT_MAX_NEW_TOKENS", 256)
CHAT_TIMEOUT_SECONDS = float(os.environ.get("CHAT_TIMEOUT_SECONDS", 45))
CHAT_RATE_LIMIT_PER_MINUTE = env_int("CHAT_RATE_LIMIT_PER_MINUTE", 10)
CHAT_RATE_LIMIT_PER_DAY = env_int("CHAT_RATE_LIMIT_PER_DAY", 200)
CHAT_MAX_CONCURRENT = env_int("CHAT_MAX_CONCURRENT", 2)

if CHAT_TORCH_THREADS > 0:
    import torch

    torch.set_num_threads(CHAT_TORCH_THREADS)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}
