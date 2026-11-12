#!/bin/bash
# =============================================================================
# codx-junior demo — container entrypoint
#
# Keeps the boot dead simple and idempotent:
#   1. create the folders the API expects (they may be bind mounts)
#   2. warn loudly about the settings that would leave a half working app
#   3. hand over to supervisord (api + background worker + nginx)
#
# Nothing is installed here. Anything that needs installing must happen at
# build time or it belongs to the full deployment.
# =============================================================================
set -e

export CODX_SUPERVISOR_LOG_FOLDER="${CODX_SUPERVISOR_LOG_FOLDER:-/var/log/codx-junior-supervisor}"
export CODX_JUNIOR_PROJECTS_PATH="${CODX_JUNIOR_PROJECTS_PATH:-/home/codx-junior-projects}"
export CODX_JUNIOR_CONFIG_FOLDER="${CODX_JUNIOR_CONFIG_FOLDER:-/home/codx-junior/.codx-junior}"
export CODX_JUNIOR_API_ANALYTICS_DATA_PATH="${CODX_JUNIOR_API_ANALYTICS_DATA_PATH:-/home/codx-junior/analytics}"
export CODX_JUNIOR_STATIC_FOLDER="${CODX_JUNIOR_STATIC_FOLDER:-/home/codx-junior/static}"
export CODX_JUNIOR_PATH="${CODX_JUNIOR_PATH:-/home/codx-junior/codx-junior}"

mkdir -p \
    "${CODX_SUPERVISOR_LOG_FOLDER}" \
    "${CODX_JUNIOR_PROJECTS_PATH}" \
    "${CODX_JUNIOR_CONFIG_FOLDER}" \
    "${CODX_JUNIOR_API_ANALYTICS_DATA_PATH}/chats" \
    "${CODX_JUNIOR_STATIC_FOLDER}/uploads" \
    "${HOME}/.config"

cat <<BANNER
=============================================================================
 codx-junior (demo)  -  http://localhost:${CODX_JUNIOR_WEB_PORT:-19981}
-----------------------------------------------------------------------------
 projects : ${CODX_JUNIOR_PROJECTS_PATH}
 settings : ${CODX_JUNIOR_CONFIG_FOLDER}
 ai       : provider=${CODX_JUNIOR_AI_PROVIDER:-auto} model=${CODX_JUNIOR_AI_MODEL:-preset}
=============================================================================
BANNER

if [ -z "${CODX_JUNIOR_AI_PROVIDER}${CODX_JUNIOR_AI_API_KEY}${OPENAI_API_KEY}${DEEPSEEK_API_KEY}${GEMINI_API_KEY}" ]; then
    echo "WARNING: no AI provider configured. The UI will load but chats cannot"
    echo "         run until you set CODX_JUNIOR_AI_PROVIDER and"
    echo "         CODX_JUNIOR_AI_API_KEY (see demo/README.md) and restart."
fi

exec /usr/bin/supervisord -c /etc/supervisord.conf
