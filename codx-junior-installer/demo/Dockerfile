# syntax=docker/dockerfile:1
# =============================================================================
# codx-junior — DEMO image (single container, ~1 GB)
#
# What this image is NOT: it does not install TeX, Firefox, Tesseract, Pandoc,
# torch, vLLM, Playwright or Node. Those are runtime dependencies of optional
# features (document conversion, browser sessions, local embeddings) that are
# only needed by the full/server deployment.
#
#   client  -> compiled in a `node` builder stage (never shipped)
#   api     -> python:3.11-slim + the base dependencies of api/pyproject.toml
#   web     -> nginx-light serving the compiled SPA and proxying /api
#   process -> supervisord (api + background worker + nginx)
#
# Build context is the REPOSITORY ROOT (see demo/docker-compose.yaml):
#   docker build -f codx-junior-installer/demo/Dockerfile .
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

# Only what the API actually uses at runtime. `nginx` serves the SPA,
# `supervisor` keeps the three processes alive, `tini` reaps zombies.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        nginx \
        procps \
        supervisor \
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

# The venv is created at build time: restarting the container must not install
# anything (see CODX_JUNIOR_SKIP_INSTALL in scripts/run_api.sh).
RUN python -m venv "${CODX_JUNIOR_API_VENV}" \
    && "${CODX_JUNIOR_API_VENV}/bin/pip" install --upgrade pip \
    && "${CODX_JUNIOR_API_VENV}/bin/pip" install ./api

# Web client + web server + process manager
COPY --from=client /build/client/dist /usr/share/codx-junior/client
COPY codx-junior-installer/demo/nginx.conf /etc/nginx/nginx.conf
COPY codx-junior-installer/demo/supervisord.conf /etc/supervisord.conf
COPY codx-junior-installer/demo/entrypoint.sh /usr/local/bin/codx-junior-entrypoint.sh
RUN chmod +x /usr/local/bin/codx-junior-entrypoint.sh

# 19981: web UI (nginx) — the only published port
# 19980: API (internal, proxied by nginx)
EXPOSE 19981

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
    CMD curl -fsS http://127.0.0.1:19980/api/health || exit 1

# Runs as root on purpose: the demo bind-mounts a host folder for the projects
# and the API (git, editors, project scripts) must be able to write there
# whatever the host UID is. The full deployment keeps the 1001:1001 user.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/codx-junior-entrypoint.sh"]
