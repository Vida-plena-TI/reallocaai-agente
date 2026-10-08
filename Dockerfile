# syntax=docker/dockerfile:1

# ---- Build: instala só as dependências de produção a partir do uv.lock ----
FROM python:3.14.8-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# Usa o Python da imagem (o mesmo da etapa final), nunca um baixado pelo uv.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---- Final: só o venv e o código ----
FROM python:3.14.8-slim-trixie

RUN groupadd --system --gid 10001 realocai \
    && useradd --system --uid 10001 --gid realocai --no-create-home \
       --home-dir /nonexistent --shell /usr/sbin/nologin realocai

# FORWARDED_ALLOW_IPS="*": o container só recebe tráfego do Traefik (rede interna
# do Easypanel), então o uvicorn confia nos X-Forwarded-* de qualquer origem.
# Não publique a porta do container diretamente na internet.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    APP_ENV=production \
    PORT=8000 \
    FORWARDED_ALLOW_IPS="*"

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)"]

# Formato exec: o Python é o PID 1 e recebe o SIGTERM do `docker stop` direto.
# `python -m app` valida a configuração e sobe o uvicorn com 1 worker (sessões
# e cache em memória; ver app/__main__.py), escutando em 0.0.0.0:$PORT.
CMD ["python", "-m", "app"]
