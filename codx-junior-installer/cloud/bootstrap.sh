#!/usr/bin/env bash
# =============================================================================
# codx-junior — UNATTENDED installer for cloud VMs
#
# Takes a fresh Ubuntu/Debian or RHEL-family machine (or a shell with root) to a
# running codx-junior and prints the URL. No questions, no interactive prompt,
# safe to run twice.
#
# It is deliberately thin: it installs Docker, downloads the repository, writes
# the settings and then delegates the start to `demo/up.sh` — the same script a
# human runs — so there is exactly ONE place that knows how the stack starts.
#
#   sudo ./bootstrap.sh --provider deepseek --key sk-xxxxxxxx
#   sudo ./bootstrap.sh --env-file /etc/codx-junior/settings.env
#   curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/codx-junior-installer/cloud/bootstrap.sh \
#     | sudo bash -s -- --provider openai --key sk-xxxxxxxx
#
# Settings (lowest to highest precedence):
#   default  <  /etc/codx-junior/settings.env  <  environment  <  flags
#
#   --provider NAME      CODX_JUNIOR_AI_PROVIDER    default: deepseek
#   --key KEY            CODX_JUNIOR_AI_API_KEY     required (or --key-file)
#   --key-file PATH      CODX_JUNIOR_AI_API_KEY_FILE  read the key from a file
#   --url URL            CODX_JUNIOR_AI_API_URL     only for `custom` providers
#   --model ID           CODX_JUNIOR_AI_MODEL       default: the preset's model
#   --port PORT          CODX_JUNIOR_WEB_PORT       default: 19981
#   --projects PATH      CODX_JUNIOR_PROJECTS_PATH  default: $HOME/projects
#   --home PATH          CODX_JUNIOR_HOME           default: /opt/codx-junior
#   --repo URL           CODX_JUNIOR_REPO           default: the public repo
#   --ref BRANCH|TAG     CODX_JUNIOR_REF            default: main
#   --branch BRANCH|TAG  alias of --ref
#   --env-file PATH      CODX_JUNIOR_ENV_FILE       default: /etc/codx-junior/settings.env
#   --build              build the image locally instead of pulling it
#   --no-docker-install  fail instead of installing Docker
#   --open-firewall      ufw: allow ssh + the web port (OFF by default)
#   -h, --help
#
# Exit code 0 means the instance answered its health check from the host.
# =============================================================================
set -euo pipefail

DEFAULT_HOME="/opt/codx-junior"
DEFAULT_REPO="https://github.com/codx-junior/codx-junior.git"
DEFAULT_REF="main"
DEFAULT_PORT="19981"
DEFAULT_PROVIDER="deepseek"
DEFAULT_ENV_FILE="/etc/codx-junior/settings.env"
CONTAINER="codx-junior-demo"

# --- logging ---------------------------------------------------------------
LOG_FILE="${CODX_JUNIOR_LOG:-/var/log/codx-junior-install.log}"
if ! mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || ! touch "${LOG_FILE}" 2>/dev/null; then
    LOG_FILE="/tmp/codx-junior-install.log"
fi
exec > >(tee -a "${LOG_FILE}") 2>&1

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    # The header comment block, not a fixed line range, so the help cannot drift
    # from the options. Works when this script is piped into bash too.
    if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
        awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
    else
        printf 'codx-junior unattended installer — see install.sh --help for the options\n'
    fi
    exit 0
}

# --- generic helpers -------------------------------------------------------
os_id() {
    local id="unknown"
    if [[ -r /etc/os-release ]]; then
        id="$(. /etc/os-release; printf '%s' "${ID:-unknown}")"
    fi
    printf '%s' "${id}"
}

pkg_install() {
    case "$(os_id)" in
        debian|ubuntu|raspbian|linuxmint)
            apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
        amzn|rhel|centos|fedora|rocky|almalinux|ol)
            dnf install -y -q "$@" ;;
        *) die "unsupported distribution '$(os_id)': install git and docker manually" ;;
    esac
}

# --- 1. settings -----------------------------------------------------------
# The settings file is scanned first so that every value below can come from it.
ENV_FILE_SETTING="${CODX_JUNIOR_ENV_FILE:-${DEFAULT_ENV_FILE}}"
CLI_ARGS=("$@")
for ((i = 0; i < ${#CLI_ARGS[@]}; i++)); do
    if [[ "${CLI_ARGS[$i]}" == "--env-file" && $((i + 1)) -lt ${#CLI_ARGS[@]} ]]; then
        ENV_FILE_SETTING="${CLI_ARGS[$((i + 1))]}"
    fi
done

if [[ -f "${ENV_FILE_SETTING}" ]]; then
    # Parsed, not sourced: a settings file is data, not shell code. A value the
    # caller already exported wins, so `CODX_JUNIOR_REF=v1 sudo ./bootstrap.sh`
    # still behaves as documented (default < file < environment < flags).
    while IFS='=' read -r key value; do
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        if [[ -z "${!key:-}" ]]; then
            export "${key}=${value}"
        fi
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ENV_FILE_SETTING}") || true
    log "Loaded settings from ${ENV_FILE_SETTING}"
fi

HOME_DIR="${CODX_JUNIOR_HOME:-${DEFAULT_HOME}}"
REPO="${CODX_JUNIOR_REPO:-${DEFAULT_REPO}}"
REF="${CODX_JUNIOR_REF:-${DEFAULT_REF}}"
PROJECTS="${CODX_JUNIOR_PROJECTS_PATH:-${HOME_DIR}/projects}"
WEB_PORT="${CODX_JUNIOR_WEB_PORT:-${DEFAULT_PORT}}"
PROVIDER="${CODX_JUNIOR_AI_PROVIDER:-${DEFAULT_PROVIDER}}"
API_KEY="${CODX_JUNIOR_AI_API_KEY:-}"
API_KEY_FILE="${CODX_JUNIOR_AI_API_KEY_FILE:-}"
API_URL="${CODX_JUNIOR_AI_API_URL:-}"
MODEL="${CODX_JUNIOR_AI_MODEL:-}"
DO_BUILD="${CODX_JUNIOR_BUILD:-0}"
INSTALL_DOCKER="${CODX_JUNIOR_INSTALL_DOCKER:-1}"
OPEN_FIREWALL="${CODX_JUNIOR_OPEN_FIREWALL:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider)          PROVIDER="${2:-}"; shift 2 ;;
        --key)               API_KEY="${2:-}"; shift 2 ;;
        --key-file)          API_KEY_FILE="${2:-}"; shift 2 ;;
        --url)               API_URL="${2:-}"; shift 2 ;;
        --model)             MODEL="${2:-}"; shift 2 ;;
        --port)              WEB_PORT="${2:-}"; shift 2 ;;
        --projects)          PROJECTS="${2:-}"; shift 2 ;;
        --home)              HOME_DIR="${2:-}"; shift 2 ;;
        --repo)              REPO="${2:-}"; shift 2 ;;
        --ref|--branch|-branch|-b) REF="${2:-}"; shift 2 ;;
        --env-file)          shift 2 ;;
        --build)             DO_BUILD=1; shift ;;
        --no-docker-install) INSTALL_DOCKER=0; shift ;;
        --open-firewall)     OPEN_FIREWALL=1; shift ;;
        -h|--help)           usage ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

if [[ -z "${API_KEY}" && -n "${API_KEY_FILE}" ]]; then
    [[ -f "${API_KEY_FILE}" ]] || die "key file not found: ${API_KEY_FILE}"
    API_KEY="$(head -n1 "${API_KEY_FILE}" | tr -d '[:space:]')"
fi
if [[ -z "${API_KEY}" && "${PROVIDER}" == "ollama" ]]; then
    # A local Ollama endpoint needs no key at all.
    API_KEY="ollama"
fi
if [[ -z "${API_KEY}" ]]; then
    cat >&2 <<MSG
ERROR: an AI provider key is required — codx-junior cannot chat without one.

  $0 --provider deepseek --key sk-xxxxxxxx
  $0 --provider openai   --key sk-xxxxxxxx
  $0 --env-file /etc/codx-junior/settings.env     # CODX_JUNIOR_AI_API_PROVIDER + _KEY

Providers: openai, deepseek, gemini, openrouter, mistral, groq, ollama, custom.
MSG
    exit 1
fi
if [[ "${PROVIDER}" == "custom" && -z "${API_URL}" ]]; then
    die "provider 'custom' needs --url (an OpenAI-compatible endpoint ending in /v1)"
fi

INSTALL_DIR="${HOME_DIR}/src"

log "codx-junior unattended install"
log "  home     : ${HOME_DIR}"
log "  provider : ${PROVIDER}${MODEL:+ (model ${MODEL})}"
log "  port     : ${WEB_PORT}"
log "  projects : ${PROJECTS}"
log "  source   : ${REPO} @ ${REF}"
log "  log      : ${LOG_FILE}"

# --- 2. host dependencies ---------------------------------------------------
ensure_git() {
    command -v git >/dev/null 2>&1 && return 0
    log "Installing git"
    pkg_install git
}

install_compose_plugin() {
    docker compose version >/dev/null 2>&1 && return 0
    log "Installing the docker compose plugin"
    pkg_install docker-compose-plugin >/dev/null 2>&1 || true
    docker compose version >/dev/null 2>&1 && return 0

    local arch tag="v2.29.7"
    case "$(uname -m)" in
        x86_64|amd64) arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        *) die "unsupported architecture '$(uname -m)' for the compose plugin" ;;
    esac
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL "https://github.com/docker/compose/releases/download/${tag}/docker-compose-linux-${arch}" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    docker compose version >/dev/null 2>&1 || die "could not install the docker compose plugin"
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1; then
        if ! docker info >/dev/null 2>&1; then
            log "Starting the docker daemon"
            systemctl enable --now docker >/dev/null 2>&1 || service docker start >/dev/null 2>&1 || true
        fi
        if docker info >/dev/null 2>&1; then
            install_compose_plugin
            log "Docker $(docker --version | awk '{print $3}' | tr -d ,) is ready"
            return 0
        fi
    fi

    if [[ "${INSTALL_DOCKER}" != "1" ]]; then
        die "docker is not installed and CODX_JUNIOR_INSTALL_DOCKER=0"
    fi

    log "Installing Docker Engine (the only package this host needs besides git)"
    if ! command -v curl >/dev/null 2>&1; then
        pkg_install curl || true
    fi
    if pkg_install docker.io >/dev/null 2>&1 && docker --version >/dev/null 2>&1; then
        :
    else
        # Official convenience script: correct repo, latest engine + compose.
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh || die "cannot download the docker installer"
        sh /tmp/get-docker.sh >/dev/null 2>&1 || die "the docker installer failed (see ${LOG_FILE})"
    fi
    systemctl enable --now docker >/dev/null 2>&1 || service docker start >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "the docker daemon did not start"
    install_compose_plugin
    log "Docker $(docker --version | awk '{print $3}' | tr -d ,) installed"
}

# Checked only after the settings are valid, so a bad invocation fails on its
# own error instead of "run as root".
[[ "${EUID}" -eq 0 ]] || die "run as root (sudo $0 ...), or use demo/up.sh directly as a normal user"

ensure_git
ensure_docker

# --- 3. source --------------------------------------------------------------
if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "Updating ${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" fetch --depth 1 origin "${REF}"
    # Untracked files (projects, .env) are untouched: no git clean here.
    git -C "${INSTALL_DIR}" reset --hard FETCH_HEAD --quiet
else
    [[ -e "${INSTALL_DIR}" ]] && die "${INSTALL_DIR} exists and is not a git checkout"
    log "Downloading the repository (shallow clone of ${REF})"
    mkdir -p "${HOME_DIR}"
    git clone --depth 1 --branch "${REF}" "${REPO}" "${INSTALL_DIR}"
fi

DEMO_DIR="${INSTALL_DIR}/codx-junior-installer/demo"
[[ -f "${DEMO_DIR}/up.sh" ]] || die "unexpected repository layout: ${DEMO_DIR}/up.sh is missing"

# --- 4. operations CLI ------------------------------------------------------
if [[ -f "${INSTALL_DIR}/codx-junior-installer/cloud/codx-junior" ]]; then
    install -m 0755 "${INSTALL_DIR}/codx-junior-installer/cloud/codx-junior" /usr/local/bin/codx-junior
    mkdir -p /etc/default
    cat > /etc/default/codx-junior <<DEFAULTS
# Written by codx-junior-installer/cloud/bootstrap.sh
CODX_JUNIOR_HOME=${HOME_DIR}
CODX_JUNIOR_WEB_PORT=${WEB_PORT}
DEFAULTS
fi

# --- 5. install -------------------------------------------------------------
mkdir -p "${PROJECTS}"

export CODX_JUNIOR_AI_PROVIDER="${PROVIDER}"
export CODX_JUNIOR_AI_API_KEY="${API_KEY}"
export CODX_JUNIOR_PROJECTS_PATH="${PROJECTS}"
export CODX_JUNIOR_WEB_PORT="${WEB_PORT}"
if [[ -n "${API_URL}" ]]; then export CODX_JUNIOR_AI_API_URL="${API_URL}"; fi
if [[ -n "${MODEL}" ]]; then export CODX_JUNIOR_AI_MODEL="${MODEL}"; fi

BUILD_ARGS=()
if [[ "${DO_BUILD}" == "1" ]]; then
    BUILD_ARGS+=(--build)
fi

log "Installing codx-junior (this pulls or builds the image: 1-2 min pulled, 5-10 min built)"
cd "${DEMO_DIR}"
bash ./up.sh "${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}"

# --- 6. verify from the host (outside the container) ------------------------
log "Verifying the published port from the host"
for _ in $(seq 1 30); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
curl -fsS --max-time 3 "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1 \
    || die "the web UI did not answer on port ${WEB_PORT} (docker logs ${CONTAINER})"

BOOTSTRAP_STATE="$(docker exec "${CONTAINER}" curl -fsS http://127.0.0.1:19980/api/bootstrap 2>/dev/null || true)"
if [[ -n "${BOOTSTRAP_STATE}" ]]; then
    log "AI configuration: ${BOOTSTRAP_STATE}"
else
    warn "could not read /api/bootstrap; configure the provider from the UI"
fi

# --- 7. firewall (opt-in) ---------------------------------------------------
if [[ "${OPEN_FIREWALL}" == "1" ]]; then
    if ! command -v ufw >/dev/null 2>&1; then
        pkg_install ufw >/dev/null 2>&1 || true
    fi
    if command -v ufw >/dev/null 2>&1; then
        ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1 || true
        ufw allow "${WEB_PORT}/tcp" >/dev/null 2>&1 || true
        ufw --force enable >/dev/null 2>&1 || true
        log "ufw: ssh + ${WEB_PORT}/tcp allowed"
    else
        warn "ufw is not available: open port ${WEB_PORT} in your provider's firewall"
    fi
fi

# --- 8. summary -------------------------------------------------------------
PUBLIC_URL=""
detect_url() {
    if [[ -n "${CODX_JUNIOR_PUBLIC_URL:-}" ]]; then
        printf '%s' "${CODX_JUNIOR_PUBLIC_URL}"
        return
    fi
    local ip
    ip="$(curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || true)"
    if [[ -z "${ip}" ]]; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    printf 'http://%s:%s' "${ip:-localhost}" "${WEB_PORT}"
}
PUBLIC_URL="$(detect_url)"

cat <<DONE

=============================================================================
 codx-junior is installed and running

   URL      : ${PUBLIC_URL}
   projects : ${PROJECTS}
   source   : ${INSTALL_DIR} (git, ${REF})
   settings : ${DEMO_DIR}/.env          (chmod 600, holds the API key)
   install  : ${LOG_FILE}

 Manage this instance
   codx-junior status | url | logs | restart | upgrade | down
   codx-junior upgrade          # git pull + recreate, keeps projects/settings

 Open the URL and pick (or create) a project: the AI provider is already
 configured, so the chat works immediately.
=============================================================================
CODX_JUNIOR_READY ${PUBLIC_URL}
DONE
