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
    AUTOEDIT_CHROMIUM_PATH=/opt/chrome-headless-shell/chrome-headless-shell \
    PATH="/app/.venv/bin:$PATH"

# ffmpeg for media, libEGL/GLES for MediaPipe's headless runtime, tini as PID 1, and the shared
# libraries Chrome's headless shell needs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libegl1 libgles2 tini ca-certificates curl unzip \
      libnss3 libatk-bridge2.0-0 libatk1.0-0 libcups2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
      libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libdrm2 libdbus-1-3 fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

# Motion-graphics frames come from Chrome for Testing's headless shell: ~100 MB lighter than full
# Chromium, which matters on a 512 MB server. Pinned for reproducible renders.
ARG CHROME_VERSION=158.0.8127.36
RUN curl -fsSL -o /tmp/chs.zip \
      "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-headless-shell-linux64.zip" \
 && unzip -q /tmp/chs.zip -d /opt \
 && mv /opt/chrome-headless-shell-linux64 /opt/chrome-headless-shell \
 && rm /tmp/chs.zip \
 && /opt/chrome-headless-shell/chrome-headless-shell --version

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# Dependency layer: cached unless the lockfile or a manifest changes.
COPY pyproject.toml uv.lock .python-version ./
COPY packages/schemas/pyproject.toml packages/schemas/pyproject.toml
COPY packages/pipeline/pyproject.toml packages/pipeline/pyproject.toml
COPY packages/brand/pyproject.toml packages/brand/pyproject.toml
COPY packages/motion/pyproject.toml packages/motion/pyproject.toml
COPY packages/render/pyproject.toml packages/render/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN for m in schemas pipeline brand motion render; do \
      mkdir -p packages/$m/src/autoedit_$m && touch packages/$m/src/autoedit_$m/__init__.py; done \
 && mkdir -p apps/api/src/autoedit_api && touch apps/api/src/autoedit_api/__init__.py \
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
