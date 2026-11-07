# Backend chatu ChronosLLM pro server bez GPU (CPU).
#   docker compose up -d --build        (viz chat/README.md)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# PyTorch jen pro CPU (o několik GB menší než verze s CUDA)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements/chat.txt requirements/chat.txt
RUN pip install -r requirements/chat.txt

# Jen kód potřebný pro chat: Django API a definice modelu (žádná data, žádné checkpointy)
COPY manage.py ./
COPY chat/ chat/
COPY llm2005/__init__.py llm2005/model.py llm2005/export.py llm2005/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app && mkdir -p /models
USER app

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT} \
    CHAT_MODEL_DIR=/models \
    CHAT_DEVICE=cpu

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# Jeden proces = model v paměti jen jednou; vlákna obslouží víc spojení najednou.
# Přístupový log je vypnutý (neukládáme ani IP adresy).
CMD ["gunicorn", "chat.wsgi", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "120"]
