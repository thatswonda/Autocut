# One image for both the API and the worker (PRD 7: single deployable worker image).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    AUTOEDIT_MODELS_DIR=/opt/autoedit/models \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    AUTOEDIT_CHROMIUM_PATH=/usr/bin/chromium \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PATH="/app/.venv/bin:$PATH"

# ffmpeg for media, libEGL/GLES for MediaPipe's headless runtime, Chromium for motion-graphics frames,
# tini as PID 1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libegl1 libgles2 chromium tini ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# Dependency layer: cached unless the lockfile or a manifest changes.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/schemas/pyproject.toml packages/schemas/pyproject.toml
COPY packages/pipeline/pyproject.toml packages/pipeline/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN mkdir -p packages/schemas/src/autoedit_schemas packages/pipeline/src/autoedit_pipeline apps/api/src/autoedit_api \
 && touch packages/schemas/src/autoedit_schemas/__init__.py packages/pipeline/src/autoedit_pipeline/__init__.py apps/api/src/autoedit_api/__init__.py \
 && uv sync --frozen --no-dev --no-install-workspace


COPY packages ./packages
COPY apps/api ./apps/api
RUN uv sync --frozen --no-dev

# Bake on-device models into the image so workers never download at runtime.
RUN autoedit models download && autoedit models list

RUN useradd --system --uid 10001 --user-group --create-home autoedit \
 && mkdir -p /data && chown autoedit:autoedit /data
COPY infra/entrypoint.sh /usr/local/bin/autocut-entrypoint
ENV AUTOEDIT_DATA_DIR=/data

# PORT is set by hosts such as Render; 8000 otherwise.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD curl -fsS "http://localhost:${PORT:-8000}/healthz" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/autocut-entrypoint"]
CMD ["sh", "-c", "python -m autoedit_api.manage migrate && exec uvicorn autoedit_api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
