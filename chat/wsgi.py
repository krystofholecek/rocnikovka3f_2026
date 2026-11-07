"""Vstupní bod pro WSGI server (gunicorn chat.wsgi)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "chat.settings")

application = get_wsgi_application()
