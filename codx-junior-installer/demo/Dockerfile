# syntax=docker/dockerfile:1
# =============================================================================
# codx-junior — COMPLETE application image (single container)
#
# One image, no slim variant. It contains everything the product can use:
#
#   client    -> compiled in a `node` builder stage (never shipped)
#   api       -> FastAPI + background worker + embedded LanceDB
#   tools     -> pandoc, TeX Live, tesseract, ffmpeg, Playwright/Chromium
#   embeddings-> torch (CPU) + sentence-transformers
#   web       -> nginx serving the compiled SPA and proxying /api
#   process   -> supervisord (api + background worker + nginx)
#
# Build context is the REPOSITORY ROOT:
#   docker build -f codx-junior-installer/demo/Dockerfile .
#
# The image is large (TeX Live, torch and Chromium). That is intentional: no
# feature may fail because the released image is a "slim" build.
# =============================================================================

ARG NODE_VERSION=22

# -----------------------------------------------------------------------------
# 1. Web client (Node only lives in this stage)
# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-bookworm-slim AS client

WORKDIR /build/client
COPY client/package.json client/package-lock.json ./
RUN npm ci --no-audit --no-fund --prefer-offline

COPY client ./
# `build-only` = `vite build`. `npm run build` would also run `vue-tsc`, which
# is a type check: slow and useless in a release image.
RUN npm run build-only

# -----------------------------------------------------------------------------
# 2. Runtime
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    HOME=/home/codx-junior \
    CODX_JUNIOR_PATH=/home/codx-junior/codx-junior \
    CODX_JUNIOR_PROJECTS_PATH=/home/codx-junior-projects \
    CODX_JUNIOR_CONFIG_FOLDER=/home/codx-junior/.codx-junior \
    CODX_JUNIOR_API_VENV=/home/codx-junior/.venv \
    CODX_JUNIOR_API_ANALYTICS_DATA_PATH=/home/codx-junior/analytics \
    CODX_JUNIOR_AI_RAW_LOG_PATH=/home/codx-junior/analytics/chats \
    CODX_JUNIOR_STATIC_FOLDER=/home/codx-junior/static \
    CODX_SUPERVISOR_LOG_FOLDER=/var/log/codx-junior-supervisor \
    CODX_JUNIOR_SKIP_INSTALL=1 \
    CODX_JUNIOR_APPS=api

# Runtime tools used by document conversion, OCR, audio and browser rendering.
# TeX Live is intentionally the full metapackage: xelatex alone is not enough
# for every template the document converter accepts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        fonts-dejavu \
        git \
        lmodern \
        nginx \
        pandoc \
        procps \
        supervisor \
        tesseract-ocr \
        texlive \
        texlive-latex-extra \
        texlive-xetex \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /home/codx-junior-projects /home/codx-junior/.codx-junior \
               /home/codx-junior/analytics /home/codx-junior/static \
               ${CODX_SUPERVISOR_LOG_FOLDER}

WORKDIR ${CODX_JUNIOR_PATH}

COPY api ./api
COPY scripts ./scripts
COPY codx-junior ./codx-junior
COPY set_env.sh .
COPY supervisor.conf .

# Venv and all optional runtime features are baked at build time: restarting the
# container must not install anything (see CODX_JUNIOR_SKIP_INSTALL in
# scripts/run_api.sh). Torch is installed from the CPU index first so pip does
# not pull the CUDA build; the `local-models` extra then sees the requirement
# satisfied.
RUN python -m venv "${CODX_JUNIOR_API_VENV}" \
    && "${CODX_JUNIOR_API_VENV}/bin/pip" install --upgrade pip \
    && "${CODX_JUNIOR_API_VENV}/bin/pip" install \
        --index-url https://download.pytorch.org/whl/cpu torch \
    && "${CODX_JUNIOR_API_VENV}/bin/pip" install "./api[local-models,browser]" \
    && "${CODX_JUNIOR_API_VENV}/bin/python" -m playwright install --with-deps chromium

# -----------------------------------------------------------------------------
# Build provenance, declared LAST on purpose. Declared before the apt-get and
# venv layers these three ARGs would invalidate them on every tag: a release
# rebuild would re-download the whole product to change one string.
#
# `GET /api/health` reports them so an unattended install can assert WHICH build
# answered instead of "something returned HTML" (api/codx/junior/version.py).
# `.github/workflows/release.yml` passes the git tag; a local build leaves them
# `unknown`.
# -----------------------------------------------------------------------------
ARG CODX_JUNIOR_BUILD_VERSION=unknown
ARG CODX_JUNIOR_BUILD_REF=unknown
ARG CODX_JUNIOR_BUILD_COMMIT=unknown
ENV CODX_JUNIOR_BUILD_VERSION=${CODX_JUNIOR_BUILD_VERSION} \
    CODX_JUNIOR_BUILD_REF=${CODX_JUNIOR_BUILD_REF} \
    CODX_JUNIOR_BUILD_COMMIT=${CODX_JUNIOR_BUILD_COMMIT}

# Also written to disk: the env above only survives while the container keeps
# its build-time environment, the file survives `docker commit`/re-tagging.
RUN mkdir -p /etc/codx-junior \
    && printf '{"version":"%s","ref":"%s","commit":"%s"}\n' \
        "${CODX_JUNIOR_BUILD_VERSION}" "${CODX_JUNIOR_BUILD_REF}" "${CODX_JUNIOR_BUILD_COMMIT}" \
        > /etc/codx-junior/build-info.json

# Web client + web server + process manager
COPY --from=client /build/client/dist /usr/share/codx-junior/client
COPY codx-junior-installer/demo/nginx.conf /etc/nginx/nginx.conf
COPY codx-junior-installer/demo/supervisord.conf /etc/supervisord.conf
COPY codx-junior-installer/demo/entrypoint.sh /usr/local/bin/codx-junior-entrypoint.sh
RUN chmod +x /usr/local/bin/codx-junior-entrypoint.sh

# 19981: web UI (nginx) — the only published port
# 19980: API (internal, proxied by nginx)
EXPOSE 19981

HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=20 \
    CMD curl -fsS http://127.0.0.1:19980/api/health || exit 1

# Runs as root on purpose: the API bind-mounts a host folder for the projects
# and (git, editors, project scripts) must be able to write there whatever the
# host UID is. Coworking containers are created by the API through the socket.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/codx-junior-entrypoint.sh"]
