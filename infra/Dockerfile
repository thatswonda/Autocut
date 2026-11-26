# syntax=docker/dockerfile:1.7
# One image for both the API and the worker (PRD 7: single deployable worker image).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    AUTOEDIT_MODELS_DIR=/opt/autoedit/models \
    PATH="/app/.venv/bin:$PATH"

# ffmpeg for media, libEGL/GLES for MediaPipe's headless runtime, tini as PID 1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libegl1 libgles2 tini ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# Dependency layer: cached unless the lockfile or a manifest changes.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/schemas/pyproject.toml packages/schemas/pyproject.toml
COPY packages/pipeline/pyproject.toml packages/pipeline/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p packages/schemas/src/autoedit_schemas packages/pipeline/src/autoedit_pipeline apps/api/src/autoedit_api \
 && touch packages/schemas/src/autoedit_schemas/__init__.py packages/pipeline/src/autoedit_pipeline/__init__.py apps/api/src/autoedit_api/__init__.py \
 && uv sync --frozen --no-dev --no-install-workspace


COPY packages ./packages
COPY apps/api ./apps/api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Bake on-device models into the image so workers never download at runtime.
RUN autoedit models download && autoedit models list

RUN useradd --system --uid 10001 --create-home autoedit \
 && mkdir -p /data && chown autoedit /data
USER autoedit
ENV AUTOEDIT_DATA_DIR=/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://localhost:8000/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "python -m autoedit_api.manage migrate && uvicorn autoedit_api.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
