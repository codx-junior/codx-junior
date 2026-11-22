#!/usr/bin/env bash
# =============================================================================
# codx-junior — UNIFIED installer
#
# One entry point for every supported deployment:
#
#   local    a laptop / CI box: loopback port, no domain, no proxy
#   server   a public server: Traefik master on :80/:443 with ACME TLS
#   slave    a server behind an existing master proxy: Traefik slave on :80,
#            trusted X-Forwarded-* headers, no ACME, no published port
#   dind     a Docker-in-Docker instance: one isolated nested stack per
#            container, for running several customers on one host
#
# The same application image runs in every mode. Optional features are not
# separate services: knowledge is embedded LanceDB, and coworking/browser
# sessions are enabled by mounting the Docker socket (--coworking).
#
#   sudo ./install.sh --mode server --domain codx.example.com \
#        --acme-email ops@example.com --provider deepseek --key sk-...
#
#   ./install.sh --mode local --provider deepseek --key sk-...
#
#   sudo ./install.sh --mode slave --domain team.example.com \
#        --proxy-network traefik --provider openai --key sk-...
#
#   sudo ./install.sh --mode dind --domain acme.example.com \
#        --acme-email ops@example.com --provider deepseek --key sk-...
#
# Run twice to upgrade: settings and projects are kept, the image is refreshed.
# Every value can also be supplied from the settings file or the environment;
# precedence is: default < env file < environment < flags.
#
# Exit code 0 means the instance answered its health check (unless --no-wait).
# =============================================================================
set -Eeuo pipefail
# Every file created here may hold the provider key or the admin password.
umask 077

# Keep an install log where the operator expects it (cloud-init troubleshooting).
# Never fatal: a read-only /var/log or a non-root local install just skips it.
if [[ -z "${CODX_JUNIOR_LOG:-}" ]]; then
    if [[ "${EUID}" -eq 0 ]]; then
        CODX_JUNIOR_LOG="/var/log/codx-junior-install.log"
    else
        CODX_JUNIOR_LOG="${HOME}/.codx-junior-install.log"
    fi
fi
if mkdir -p "$(dirname "${CODX_JUNIOR_LOG}")" 2>/dev/null && touch "${CODX_JUNIOR_LOG}" 2>/dev/null; then
    exec > >(tee -a "${CODX_JUNIOR_LOG}") 2>&1
fi

INSTALLER_VERSION="1.0.0"
DEFAULT_REPO="https://github.com/codx-junior/codx-junior.git"
DEFAULT_REF="main"
DEFAULT_PORT="19981"
DEFAULT_IMAGE="codxjuniorai/codx-junior:latest"
DEFAULT_DIND_IMAGE="codxjuniorai/codx-junior:dind"
DEFAULT_TRAEFIK_IMAGE="traefik:v3.3"
DEFAULT_PROXY_NETWORK="codx-proxy"
DEFAULT_TRUSTED_PROXIES="172.16.0.0/12,10.0.0.0/8,192.168.0.0/16,127.0.0.1/32"
DEFAULT_ACME_EMAIL=""

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGINAL_ARGC=$#

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
codx-junior unified installer

Usage:
  ./install.sh [options] [install]      install or update (the default)
  ./install.sh [options] status|logs|down|up|restart|upgrade|doctor|config|url|env|version

Modes:
  --mode local              laptop/CI: 127.0.0.1:<port>, no TLS, no proxy
  --mode server             public server: Traefik master :80/:443 + ACME
  --mode slave              behind a master proxy: Traefik slave :80 only
  --mode dind               Docker-in-Docker instance (one per customer)
  --dind-mode MODE          inner mode for dind: local|server|slave [local]
  Default: server as root, local otherwise.

Settings:
  --provider NAME           openai deepseek gemini openrouter mistral groq
                            custom                              [deepseek]
  --key KEY                 provider API key (or --key-file)
  --key-file PATH           read the key from a file
  --url URL                 OpenAI-compatible endpoint (only for custom)
  --model ID                override the preset chat model
  --no-ai                   do not configure a provider (set it in the UI)
  --admin-user USER         first account (created on first boot)
  --admin-password PASS     its password; generated when omitted
  --admin-email EMAIL       optional e-mail of the first account
  --port PORT               web UI port (local mode)          [19981]
  --bind-address ADDR       local bind address             [127.0.0.1]
  --projects PATH           host projects folder       [<home>/projects]
  --home PATH               installation root    [/opt/codx-junior]
  --data-dir PATH           settings/analytics root [<home>/data]
  --instance NAME           instance slug (containers/volumes/router names)
  --env-file PATH           settings file [<home>/config/<instance>.env]

Proxy / TLS:
  --domain DOMAIN           public domain; repeat or comma-separate
  --acme-email EMAIL        Let's Encrypt account (server mode)
  --acme-ca staging|prod    ACME directory (staging avoids rate limits)
  --proxy-network NAME      Docker network shared with a master [codx-proxy]
  --create-network          create the slave proxy network when missing
  --trusted-proxy CIDR      master CIDR allowed to set X-Forwarded-*; repeat
  --forwarded-insecure      trust X-Forwarded-* from any peer (slave only)
  --master-cert-resolver N  cert resolver name on the master    [letsencrypt]
  --master-entrypoint NAME  master HTTPS entrypoint            [websecure]
  --no-tls                  server mode: do not start Traefik (HTTP only)
  --tls none                alias of --no-tls (a proxy terminates TLS)

Features:
  --coworking               mount /var/run/docker.sock (browser/desktops)
  --no-coworking            keep the socket out (default)
  --allow-privileged        allow --coworking on a non-isolated host
  --build                   build the application image from this checkout
  --build-dind              build the DIND image from this checkout
  --image IMAGE             app image [codxjuniorai/codx-junior:latest]
  --dind-image IMAGE        DIND image [codxjuniorai/codx-junior:dind]
  --sysbox                  DIND: runtime sysbox-runc, privileged=false

Lifecycle:
  --systemd / --no-systemd  register/do not register a systemd unit
  --no-cli                  do not install /usr/local/bin/codx-junior-<instance>
  --no-wait                 do not wait for the health check
  --skip-tls-wait           wait for the app, not for the public certificate
  --no-pull                 do not pull images (air-gapped / local image)
  --no-docker-install       fail instead of installing Docker
  --open-firewall           ufw: allow ssh + the published ports
  --update                  alias of --upgrade
  --upgrade                 refresh source/image and recreate
  --dry-run                 print what would run; change nothing
  --force                   continue when an old installation is detected
  --container               internal: running inside the DIND image
  --interactive             ask for every setting, using the current values as defaults
  --non-interactive         never read stdin; fail instead of prompting
  -h, --help | -V, --version

Environment: prefix every setting with CODX_JUNIOR_ (CODX_JUNIOR_MODE,
CODX_JUNIOR_DOMAIN, CODX_JUNIOR_AI_PROVIDER, CODX_JUNIOR_AI_API_KEY,
CODX_JUNIOR_WEB_PORT, CODX_JUNIOR_HOME, CODX_JUNIOR_PROJECTS_PATH, ...).
USAGE
}

need() {
    [[ $# -ge 2 && -n "${2:-}" ]] || die "option $1 needs a value (see --help)"
}

# --- interactive prompts ----------------------------------------------------
# The wizard reads from the controlling terminal when there is one, so it also
# works from `curl ... | sudo bash`. In a forced interactive run without a TTY
# (tests/CI) it reads stdin instead.
WIZARD_TTY=0
# Prompts go to a dedicated duplicate of stderr: command substitution captures
# fd 1 only, so the prompt must not be swallowed by `$(ask ...)`.
exec 4>&2
if [[ -t 0 || -t 1 ]]; then
    WIZARD_TTY=1
fi

ask() {
    local label="$1" default="${2:-}" answer=""
    if [[ "${WIZARD_TTY}" == "1" ]]; then
        printf '%s' "${label}" >/dev/tty
        [[ -n "${default}" ]] && printf ' [%s]' "${default}" >/dev/tty
        printf ': ' >/dev/tty
        IFS= read -r answer </dev/tty || answer=""
    else
        printf '%s' "${label}" >&4
        [[ -n "${default}" ]] && printf ' [%s]' "${default}" >&4
        printf ': ' >&4
        IFS= read -r answer || answer=""
    fi
    [[ -n "${answer}" ]] || answer="${default}"
    printf '%s' "${answer}"
}

ask_secret() {
    local label="$1" answer=""
    if [[ "${WIZARD_TTY}" == "1" ]]; then
        printf '%s: ' "${label}" >/dev/tty
        IFS= read -r -s answer </dev/tty || answer=""
        printf '\n' >/dev/tty
    else
        printf '%s: ' "${label}" >&4
        IFS= read -r -s answer || answer=""
        printf '\n' >&4
    fi
    printf '%s' "${answer}"
}

ask_choice() {
    local label="$1" choices="$2" default="$3" answer=""
    while true; do
        answer="$(ask "${label} (${choices// /|})" "${default}")"
        case " ${choices} " in
            *" ${answer} "*) printf '%s' "${answer}"; return 0 ;;
        esac
        warn "choose one of: ${choices// /, }"
    done
}

ask_yesno() {
    local label="$1" default="${2:-0}" default_text="n" answer=""
    [[ "${default}" == "1" ]] && default_text="y"
    while true; do
        answer="$(ask "${label} (y/n)" "${default_text}")"
        case "${answer,,}" in
            y|yes) printf '1'; return 0 ;;
            n|no)  printf '0'; return 0 ;;
        esac
        warn "answer y or n"
    done
}

interactive_wizard() {
    printf '\n' >&4
    log "Interactive setup — press Enter to accept the value in [brackets]"

    local mode_default="${MODE:-}"
    case "${mode_default}" in local|server|slave|dind) ;; *)
        if [[ "${EUID}" -eq 0 ]]; then mode_default="server"; else mode_default="local"; fi ;;
    esac
    MODE="$(ask_choice "Deployment mode" "local server slave dind" "${mode_default}")"
    MODE_CLI=1

    if [[ "${MODE}" == "dind" ]]; then
        DIND_MODE="$(ask_choice "DIND inner mode" "local server slave" "${DIND_MODE:-local}")"
    fi
    local effective="${MODE}"
    if [[ "${MODE}" == "dind" ]]; then
        effective="${DIND_MODE}"
    fi

    local domain_default="${DOMAINS[0]:-}"
    if [[ "${domain_default}" == "localhost" ]]; then
        domain_default=""
    fi
    local domain=""
    if [[ "${effective}" == "local" ]]; then
        domain="localhost"
    else
        while true; do
            domain="$(ask "Domain (blank = plain HTTP on the web port)" "${domain_default}")"
            if [[ -z "${domain}" ]]; then
                if [[ "${effective}" == "server" && "${MODE}" != "dind" ]]; then
                    break
                fi
                warn "a domain is required for ${effective} mode"
                continue
            fi
            if [[ "${domain}" =~ ^[A-Za-z0-9.-]+$ ]]; then
                break
            fi
            warn "invalid domain: ${domain}"
        done
    fi

    NO_TLS=0
    if [[ "${effective}" == "server" ]]; then
        if [[ -z "${domain}" ]]; then
            NO_TLS=1
        else
            while true; do
                local acme=""
                acme="$(ask "ACME email (blank = plain HTTP)" "${ACME_EMAIL}")"
                if [[ -n "${acme}" ]]; then
                    ACME_EMAIL="${acme}"
                    break
                fi
                if [[ "${MODE}" == "dind" ]]; then
                    warn "dind/server needs TLS: enter an ACME email"
                    continue
                fi
                NO_TLS=1
                break
            done
        fi
    fi

    if [[ "${effective}" == "slave" ]]; then
        local net_default="${PROXY_NETWORK:-traefik}"
        [[ "${net_default}" == "codx-proxy" ]] && net_default="traefik"
        PROXY_NETWORK="$(ask "Master Traefik Docker network" "${net_default}")"
        MASTER_CERT_RESOLVER="$(ask "Master certificate resolver" "${MASTER_CERT_RESOLVER:-letsencrypt}")"
        MASTER_ENTRYPOINT="$(ask "Master HTTPS entrypoint" "${MASTER_ENTRYPOINT:-websecure}")"
    fi

    if [[ "${MODE}" == "dind" ]]; then
        local sysbox_default=0
        if [[ "${SYSBOX}" == "1" ]]; then
            sysbox_default=1
        elif command -v docker >/dev/null 2>&1 \
            && timeout 5 docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"sysbox-runc"'; then
            sysbox_default=1
        fi
        SYSBOX="$(ask_yesno "Use Sysbox runtime (recommended)" "${sysbox_default}")"
        if [[ "${SYSBOX}" != "1" ]]; then
            warn "DIND will run privileged (root-equivalent on this host)"
        fi
    fi

    local instance_default="${INSTANCE:-codx-junior}"
    if [[ ( "${MODE}" == "dind" || "${MODE}" == "slave" ) && -n "${domain}" && "${domain}" != "localhost" ]]; then
        instance_default="$(slugify "${domain}")"
    fi
    while true; do
        INSTANCE="$(ask "Instance name" "${instance_default}")"
        if [[ "${INSTANCE}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
            break
        fi
        warn "use lowercase letters, digits and '-' (max 32 chars)"
    done

    local home_default="${HOME_DIR}"
    if [[ "${HOME_DIR_EXPLICIT}" != "1" ]]; then
        case "${MODE}" in
            local) home_default="${HOME}/.codx-junior" ;;
            dind)  home_default="/opt/codx-junior/instances/${INSTANCE}" ;;
            *)     home_default="/opt/codx-junior" ;;
        esac
    fi
    HOME_DIR="$(ask "Installation root" "${home_default}")"
    if [[ -z "${CODX_DATA_DIR:-}" ]]; then
        DATA_DIR="${HOME_DIR}/data"
    fi

    local projects_default="${PROJECTS}"
    if [[ "${PROJECTS_CLI}" != "1" && -z "${CODX_JUNIOR_PROJECTS_PATH:-}" ]]; then
        case "${MODE}" in
            dind) projects_default="${DATA_DIR}/projects" ;;
            *)    projects_default="${HOME_DIR}/projects" ;;
        esac
    fi
    PROJECTS="$(ask "Projects folder" "${projects_default}")"
    PROJECTS_CLI=1

    while true; do
        WEB_PORT="$(ask "Host web port" "${WEB_PORT}")"
        if [[ "${WEB_PORT}" =~ ^[0-9]+$ ]] && (( WEB_PORT >= 1 && WEB_PORT <= 65535 )); then
            break
        fi
        warn "enter a port between 1 and 65535"
    done

    local provider_default="${PROVIDER:-deepseek}"
    case "${provider_default}" in
        openai|deepseek|gemini|openrouter|mistral|groq|custom|none) ;;
        *) provider_default="deepseek" ;;
    esac
    if [[ "${NO_AI}" == "1" ]]; then
        provider_default="none"
    fi
    PROVIDER="$(ask_choice "AI provider" "openai deepseek gemini openrouter mistral groq custom none" "${provider_default}")"
    if [[ "${PROVIDER}" == "none" ]]; then
        NO_AI=1
        API_KEY=""
        API_URL=""
        MODEL=""
    else
        NO_AI=0
        if [[ "${PROVIDER}" == "custom" ]]; then
            while true; do
                API_URL="$(ask "OpenAI-compatible base URL (ending in /v1)" "${API_URL}")"
                [[ -n "${API_URL}" ]] && break
                warn "a custom provider needs its base URL"
            done
        fi
        if [[ -n "${API_KEY_FILE:-}" && -z "${API_KEY}" ]]; then
            log "Using the existing --key-file"
        elif [[ -n "${API_KEY}" ]]; then
            local replacement=""
            replacement="$(ask_secret "API key (Enter keeps the current key)")"
            [[ -n "${replacement}" ]] && API_KEY="${replacement}"
        else
            while true; do
                API_KEY="$(ask_secret "API key")"
                [[ -n "${API_KEY}" ]] && break
                warn "an API key is required (choose 'none' to configure it in the UI)"
            done
        fi
        MODEL="$(ask "Model (blank = provider preset)" "${MODEL}")"
    fi

    if [[ "${MODE}" != "dind" ]]; then
        COWORKING="$(ask_yesno "Enable coworking/browser/desktops (mounts Docker socket)" "${COWORKING}")"
        if [[ "${COWORKING}" == "1" && "${effective}" != "local" ]]; then
            ALLOW_PRIVILEGED=1
            warn "coworking on a server mounts /var/run/docker.sock (root-equivalent)"
        fi
    fi

    DOMAINS=()
    if [[ "${effective}" != "local" && -n "${domain}" ]]; then
        DOMAINS=("${domain}")
    fi
    if [[ "${ENV_FILE_EXPLICIT}" != "1" ]]; then
        ENV_FILE="${HOME_DIR}/config/${INSTANCE}.env"
    fi
    if [[ "${NO_TLS}" == "1" ]]; then
        NO_TLS_CLI=1
    fi

    printf '\n' >&2
    log "Configuration"
    log "  mode      : ${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)"
    log "  domain    : ${domain:-<none>}"
    log "  instance  : ${INSTANCE}"
    log "  home      : ${HOME_DIR}"
    log "  projects  : ${PROJECTS}"
    log "  port      : ${WEB_PORT}"
    log "  provider  : ${PROVIDER}$([[ "${NO_AI}" == "1" ]] && echo " (configure in UI)" || true)"
    log "  coworking : ${COWORKING}"
    if [[ "${MODE}" == "dind" ]]; then
        log "  sysbox    : ${SYSBOX}"
    fi
    if [[ "${effective}" == "slave" ]]; then
        log "  proxy     : ${PROXY_NETWORK} (${MASTER_ENTRYPOINT}, resolver ${MASTER_CERT_RESOLVER})"
    fi

    if [[ "$(ask_yesno "Proceed with this configuration?" 1)" != "1" ]]; then
        log "cancelled"
        exit 0
    fi
}

# --- prescan: decide the settings file before parsing everything ------------
PRESCAN_MODE=""
PRESCAN_DOMAIN=""
PRESCAN_INSTANCE=""
PRESCAN_HOME=""
PRESCAN_ENV=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    a="${args[$i]}"
    next="${args[$((i + 1))]:-}"
    case "$a" in
        --mode)          PRESCAN_MODE="$next"; i=$((i + 1)) ;;
        --mode=*)        PRESCAN_MODE="${a#*=}" ;;
        --domain)        [[ -n "${PRESCAN_DOMAIN}" ]] || PRESCAN_DOMAIN="$next"; i=$((i + 1)) ;;
        --domain=*)      [[ -n "${PRESCAN_DOMAIN}" ]] || PRESCAN_DOMAIN="${a#*=}" ;;
        --instance)      PRESCAN_INSTANCE="$next"; i=$((i + 1)) ;;
        --instance=*)    PRESCAN_INSTANCE="${a#*=}" ;;
        --home)          PRESCAN_HOME="$next"; i=$((i + 1)) ;;
        --home=*)        PRESCAN_HOME="${a#*=}" ;;
        --env-file)      PRESCAN_ENV="$next"; i=$((i + 1)) ;;
        --env-file=*)    PRESCAN_ENV="${a#*=}" ;;
    esac
done

slugify() {
    local value="${1:-}"
    value="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
    value="$(printf '%s' "${value}" | sed -E 's#^[a-z]+://##; s#/.*$##; s#[^a-z0-9]+#-#g; s#^-+##; s#-+$##')"
    printf '%s' "${value:0:32}"
}

MODE_HINT="${PRESCAN_MODE:-${CODX_JUNIOR_MODE:-}}"
if [[ -z "${MODE_HINT}" ]]; then
    if [[ "${EUID}" -eq 0 ]]; then MODE_HINT="server"; else MODE_HINT="local"; fi
fi
DOMAIN_HINT="${PRESCAN_DOMAIN:-${CODX_JUNIOR_DOMAIN:-}}"
INSTANCE_HINT="${PRESCAN_INSTANCE:-${CODX_JUNIOR_INSTANCE:-}}"
if [[ -z "${INSTANCE_HINT}" ]]; then
    case "${MODE_HINT}" in
        dind|slave) INSTANCE_HINT="$(slugify "${DOMAIN_HINT}")" ;;
    esac
    [[ -n "${INSTANCE_HINT}" ]] || INSTANCE_HINT="codx-junior"
fi
HOME_HINT="${PRESCAN_HOME:-${CODX_JUNIOR_HOME:-}}"
if [[ -z "${HOME_HINT}" ]]; then
    case "${MODE_HINT}" in
        local) HOME_HINT="${HOME}/.codx-junior" ;;
        dind)  HOME_HINT="/opt/codx-junior/instances/${INSTANCE_HINT}" ;;
        *)     HOME_HINT="/opt/codx-junior" ;;
    esac
fi
ENV_FILE="${PRESCAN_ENV:-${CODX_JUNIOR_ENV_FILE:-${HOME_HINT}/config/${INSTANCE_HINT}.env}}"

# --- settings file: parsed as data, never sourced ---------------------------
if [[ -f "${ENV_FILE}" ]]; then
    while IFS='=' read -r key value; do
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        # Compose's literal-dollar escape; undo it for our own variables.
        value="${value//\$\$/\$}"
        # Environment wins over the file, like the cloud installer always did.
        if [[ -z "${!key:-}" ]]; then
            export "${key}=${value}"
        fi
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ENV_FILE}") || true
    log "Loaded settings from ${ENV_FILE}"
fi

# --- configuration (env file + environment; flags below override) -----------
MODE="${CODX_JUNIOR_MODE:-${MODE_HINT}}"
DIND_MODE="${CODX_DIND_MODE:-local}"
INSTANCE="${CODX_JUNIOR_INSTANCE:-${INSTANCE_HINT}}"
HOME_DIR="${CODX_JUNIOR_HOME:-${HOME_HINT}}"
DATA_DIR="${CODX_DATA_DIR:-${HOME_DIR}/data}"
PROJECTS="${CODX_JUNIOR_PROJECTS_PATH:-${HOME_DIR}/projects}"
PROJECTS_CLI=0
HOME_DIR_EXPLICIT=0
ENV_FILE_EXPLICIT=0
if [[ -n "${CODX_JUNIOR_HOME:-}${PRESCAN_HOME:-}" ]]; then
    HOME_DIR_EXPLICIT=1
fi
if [[ -n "${CODX_JUNIOR_ENV_FILE:-}${PRESCAN_ENV:-}" ]]; then
    ENV_FILE_EXPLICIT=1
fi
WEB_PORT="${CODX_JUNIOR_WEB_PORT:-${DEFAULT_PORT}}"
BIND_ADDRESS="${CODX_BIND_ADDRESS:-127.0.0.1}"
BIND_ADDRESS_CLI=0
IMAGE="${CODX_JUNIOR_IMAGE:-}"
REF="${CODX_JUNIOR_REF:-${DEFAULT_REF}}"
REPO="${CODX_JUNIOR_REPO:-${DEFAULT_REPO}}"
DO_BUILD="${CODX_JUNIOR_BUILD:-0}"
DO_BUILD_DIND="${CODX_JUNIOR_BUILD_DIND:-0}"
DIND_IMAGE="${CODX_DIND_IMAGE:-${DEFAULT_DIND_IMAGE}}"
SYSBOX="${CODX_DIND_SYSBOX:-0}"
TRAEFIK_IMAGE="${CODX_TRAEFIK_IMAGE:-${DEFAULT_TRAEFIK_IMAGE}}"
PROXY_NETWORK="${CODX_PROXY_NETWORK:-${DEFAULT_PROXY_NETWORK}}"
PROXY_EXTERNAL="${CODX_PROXY_EXTERNAL:-}"
TRUSTED_PROXIES="${CODX_TRUSTED_PROXIES:-${DEFAULT_TRUSTED_PROXIES}}"
FORWARDED_INSECURE="${CODX_FORWARDED_INSECURE:-0}"
ACME_EMAIL="${CODX_ACME_EMAIL:-${DEFAULT_ACME_EMAIL}}"
ACME_CA="${CODX_ACME_CA:-prod}"
MASTER_CERT_RESOLVER="${CODX_MASTER_CERT_RESOLVER:-letsencrypt}"
MASTER_ENTRYPOINT="${CODX_MASTER_ENTRYPOINT:-websecure}"
CERT_RESOLVER="${CODX_CERT_RESOLVER:-letsencrypt}"
NO_TLS="${CODX_NO_TLS:-0}"
MODE_CLI=0
NO_TLS_CLI=0
COWORKING="${CODX_JUNIOR_COWORKING:-0}"
ALLOW_PRIVILEGED="${CODX_ALLOW_PRIVILEGED:-0}"
SYSTEMD="${CODX_SYSTEMD:-}"
NO_WAIT="${CODX_NO_WAIT:-0}"
SKIP_TLS_WAIT="${CODX_SKIP_TLS_WAIT:-0}"
NO_PULL="${CODX_NO_PULL:-0}"
UPGRADE="${CODX_UPGRADE:-0}"
DRY_RUN=0
FORCE=0
CONTAINER="${CODX_CONTAINER:-0}"
NON_INTERACTIVE="${CODX_JUNIOR_NON_INTERACTIVE:-0}"
INTERACTIVE="${CODX_JUNIOR_INTERACTIVE:-0}"
COMMAND="install"

PROVIDER="${CODX_JUNIOR_AI_PROVIDER:-deepseek}"
API_KEY="${CODX_JUNIOR_AI_API_KEY:-}"
API_KEY_FILE="${CODX_JUNIOR_AI_API_KEY_FILE:-}"
API_URL="${CODX_JUNIOR_AI_API_URL:-}"
MODEL="${CODX_JUNIOR_AI_MODEL:-}"
NO_AI="${CODX_JUNIOR_NO_AI:-0}"
INSTALL_DOCKER="${CODX_JUNIOR_INSTALL_DOCKER:-1}"
OPEN_FIREWALL="${CODX_JUNIOR_OPEN_FIREWALL:-0}"
CLI=1
ADMIN_USER="${CODX_JUNIOR_ADMIN_USER:-}"
ADMIN_PASSWORD="${CODX_JUNIOR_ADMIN_PASSWORD:-}"
ADMIN_EMAIL="${CODX_JUNIOR_ADMIN_EMAIL:-}"
HIDDEN="${CODX_JUNIOR_HIDDEN:-0}"
KNOWLEDGE_BACKEND="${CODX_JUNIOR_KNOWLEDGE_BACKEND:-lancedb}"

DOMAINS=()
DOMAIN_CLI=0
if [[ -n "${CODX_JUNIOR_DOMAIN:-}" ]]; then
    IFS=',' read -r -a _env_domains <<< "${CODX_JUNIOR_DOMAIN}"
    for d in "${_env_domains[@]}"; do
        [[ -n "${d// /}" ]] && DOMAINS+=("${d// /}")
    done
    unset _env_domains
fi

# --- command line -----------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode|-m)              need "$@"; MODE="$2"; MODE_CLI=1; shift 2 ;;
        --mode=*)               MODE="${1#*=}"; MODE_CLI=1; shift ;;
        --dind-mode)            need "$@"; DIND_MODE="$2"; shift 2 ;;
        --dind-mode=*)          DIND_MODE="${1#*=}"; shift ;;
        --provider|--ai-provider|-p) need "$@"; PROVIDER="$2"; shift 2 ;;
        --provider=*)           PROVIDER="${1#*=}"; shift ;;
        --key|-k)               need "$@"; API_KEY="$2"; shift 2 ;;
        --key=*)                API_KEY="${1#*=}"; shift ;;
        --key-file)             need "$@"; API_KEY_FILE="$2"; shift 2 ;;
        --key-file=*)           API_KEY_FILE="${1#*=}"; shift ;;
        --url|-u)               need "$@"; API_URL="$2"; shift 2 ;;
        --url=*)                API_URL="${1#*=}"; shift ;;
        --model)                need "$@"; MODEL="$2"; shift 2 ;;
        --model=*)              MODEL="${1#*=}"; shift ;;
        --no-ai)                NO_AI=1; shift ;;
        --admin-user)           need "$@"; ADMIN_USER="$2"; shift 2 ;;
        --admin-password)       need "$@"; ADMIN_PASSWORD="$2"; shift 2 ;;
        --admin-email)          need "$@"; ADMIN_EMAIL="$2"; shift 2 ;;
        --port|-P)              need "$@"; WEB_PORT="$2"; shift 2 ;;
        --port=*)               WEB_PORT="${1#*=}"; shift ;;
        --bind-address)         need "$@"; BIND_ADDRESS="$2"; BIND_ADDRESS_CLI=1; shift 2 ;;
        --projects)             need "$@"; PROJECTS="$2"; PROJECTS_CLI=1; shift 2 ;;
        --home)                 need "$@"; HOME_DIR="$2"; HOME_DIR_EXPLICIT=1; shift 2 ;;
        --data-dir)             need "$@"; DATA_DIR="$2"; shift 2 ;;
        --instance)             need "$@"; INSTANCE="$2"; shift 2 ;;
        --env-file)             need "$@"; ENV_FILE="$2"; ENV_FILE_EXPLICIT=1; shift 2 ;;
        --domain|-d)            need "$@"
                                [[ "${DOMAIN_CLI}" == "1" ]] || { DOMAINS=(); DOMAIN_CLI=1; }
                                IFS=',' read -r -a _parts <<< "$2"
                                for d in "${_parts[@]}"; do [[ -n "${d// /}" ]] && DOMAINS+=("${d// /}"); done
                                unset _parts; shift 2 ;;
        --domain=*)             [[ "${DOMAIN_CLI}" == "1" ]] || { DOMAINS=(); DOMAIN_CLI=1; }
                                IFS=',' read -r -a _parts <<< "${1#*=}"
                                for d in "${_parts[@]}"; do [[ -n "${d// /}" ]] && DOMAINS+=("${d// /}"); done
                                unset _parts; shift ;;
        --acme-email)           need "$@"; ACME_EMAIL="$2"; shift 2 ;;
        --acme-email=*)         ACME_EMAIL="${1#*=}"; shift ;;
        --acme-ca)              need "$@"; ACME_CA="$2"; shift 2 ;;
        --acme-ca=*)            ACME_CA="${1#*=}"; shift ;;
        --proxy-network)        need "$@"; PROXY_NETWORK="$2"; shift 2 ;;
        --proxy-network=*)      PROXY_NETWORK="${1#*=}"; shift ;;
        --create-network)       CODX_CREATE_NETWORK=1; shift ;;
        --trusted-proxy)        need "$@"; TRUSTED_PROXIES="$2"; shift 2 ;;
        --trusted-proxy=*)      TRUSTED_PROXIES="${1#*=}"; shift ;;
        --forwarded-insecure)   FORWARDED_INSECURE=1; shift ;;
        --master-cert-resolver) need "$@"; MASTER_CERT_RESOLVER="$2"; shift 2 ;;
        --master-entrypoint)    need "$@"; MASTER_ENTRYPOINT="$2"; shift 2 ;;
        --cert-resolver)        need "$@"; CERT_RESOLVER="$2"; shift 2 ;;
        --no-tls)               NO_TLS=1; NO_TLS_CLI=1; shift ;;
        --tls)                  need "$@"
                                case "${2:-}" in
                                    none) NO_TLS=1; NO_TLS_CLI=1 ;;
                                    *) die "only '--tls none' is supported; use --mode slave behind a master" ;;
                                esac
                                shift 2 ;;
        --no-docker-install)    INSTALL_DOCKER=0; shift ;;
        --open-firewall)        OPEN_FIREWALL=1; shift ;;
        --update)               UPGRADE=1; shift ;;
        --coworking)            COWORKING=1; shift ;;
        --no-coworking)         COWORKING=0; shift ;;
        --allow-privileged)     ALLOW_PRIVILEGED=1; shift ;;
        --build)                DO_BUILD=1; shift ;;
        --build-dind)           DO_BUILD_DIND=1; shift ;;
        --image)                need "$@"; IMAGE="$2"; shift 2 ;;
        --image=*)              IMAGE="${1#*=}"; shift ;;
        --dind-image)           need "$@"; DIND_IMAGE="$2"; shift 2 ;;
        --dind-image=*)         DIND_IMAGE="${1#*=}"; shift ;;
        --sysbox)               SYSBOX=1; shift ;;
        --systemd)              SYSTEMD=1; shift ;;
        --no-cli)               CLI=0; shift ;;
        --no-systemd)           SYSTEMD=0; shift ;;
        --no-wait)              NO_WAIT=1; shift ;;
        --skip-tls-wait)        SKIP_TLS_WAIT=1; shift ;;
        --no-pull)              NO_PULL=1; shift ;;
        --upgrade)              UPGRADE=1; shift ;;
        --dry-run)              DRY_RUN=1; shift ;;
        --force)                FORCE=1; shift ;;
        --container)            CONTAINER=1; shift ;;
        --interactive)          INTERACTIVE=1; shift ;;
        --non-interactive|--yes|-y) NON_INTERACTIVE=1; shift ;;
        --ref|--branch|-b)      need "$@"; REF="$2"; shift 2 ;;
        --repo)                 need "$@"; REPO="$2"; shift 2 ;;
        -h|--help)              usage; exit 0 ;;
        -V|--version)           printf 'codx-junior installer %s\n' "${INSTALLER_VERSION}"; exit 0 ;;
        install|upgrade|status|logs|down|doctor|config|url|env|version|up|restart)
                                COMMAND="$1"; shift ;;
        "")                     shift ;;
        *)                      die "unknown option or command: $1 (see --help)" ;;
    esac
done

# --- defaults after parsing -------------------------------------------------
case "${MODE}" in local|server|slave|dind) ;; *) die "invalid --mode '${MODE}' (local|server|slave|dind)" ;; esac
case "${DIND_MODE}" in local|server|slave) ;; *) die "invalid --dind-mode '${DIND_MODE}' (local|server|slave)" ;; esac
EFFECTIVE_MODE="${MODE}"
[[ "${MODE}" == "dind" ]] && EFFECTIVE_MODE="${DIND_MODE}"
# Backwards compatibility: `curl ... | sudo bash` with no mode/domain used to
# serve plain HTTP on the published port. Keep that default, but make it loud.
if [[ "${MODE}" == "server" && "${MODE_CLI}" == "0" && "${NO_TLS}" == "0" && "${NO_TLS_CLI}" == "0" && "${#DOMAINS[@]}" -eq 0 ]]; then
    NO_TLS=1
    warn "no --domain given: serving plain HTTP on port ${WEB_PORT} (use --domain for TLS)"
fi
[[ "${INSTANCE}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || die "invalid --instance '${INSTANCE}' (lowercase letters, digits and '-')"
[[ "${WEB_PORT}" =~ ^[0-9]+$ ]] && ((WEB_PORT >= 1 && WEB_PORT <= 65535)) || die "invalid --port '${WEB_PORT}'"
[[ "${REF}" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid --ref '${REF}'"

if [[ "${COMMAND}" == "upgrade" ]]; then UPGRADE=1; fi

# No arguments on a terminal: run the guided setup. --interactive forces it,
# --non-interactive (or --yes) always wins.
if [[ "${INTERACTIVE}" == "1" && "${NON_INTERACTIVE}" == "1" ]]; then
    die "--interactive and --non-interactive cannot be combined"
fi
if [[ "${INTERACTIVE}" != "1" && "${NON_INTERACTIVE}" != "1" \
      && "${ORIGINAL_ARGC}" -eq 0 && "${WIZARD_TTY}" == "1" ]]; then
    INTERACTIVE=1
fi
if [[ "${INTERACTIVE}" == "1" ]]; then
    interactive_wizard
    EFFECTIVE_MODE="${MODE}"
    if [[ "${MODE}" == "dind" ]]; then
        EFFECTIVE_MODE="${DIND_MODE}"
    fi
fi

# An unattended caller (cloud-init) must never block on a prompt. Closing stdin
# makes any library that asks get EOF and fail loudly instead.
if [[ "${NON_INTERACTIVE}" == "1" ]]; then
    exec </dev/null
    export DEBIAN_FRONTEND=noninteractive
    export NEEDRESTART_MODE=a
    export GIT_TERMINAL_PROMPT=0
    export COMPOSE_INTERACTIVE_NO_CLI=1
fi

# Relative paths are resolved from the invocation directory.
abs_dir() {
    local p="$1"
    [[ "$p" = /* ]] || p="${PWD}/${p}"
    printf '%s' "$p"
}
HOME_DIR="$(abs_dir "${HOME_DIR}")"
DATA_DIR="$(abs_dir "${DATA_DIR}")"
PROJECTS="$(abs_dir "${PROJECTS}")"
ENV_FILE="$(abs_dir "${ENV_FILE}")"

# The DIND wrapper mounts <data> at /data; projects must be inside it unless
# the caller explicitly mounted another host path (the outer compose mounts
# both, with the project path taking precedence over /data/projects).
if [[ "${MODE}" == "dind" && "${PROJECTS_CLI}" == "0" && -z "${CODX_JUNIOR_PROJECTS_PATH:-}" ]]; then
    PROJECTS="${DATA_DIR}/projects"
fi

# Refuse a home that would make the installer delete or populate a system
# directory. `persist_runtime` removes <home>/lib/runtime, so this is a guard,
# not a style preference.
case "${HOME_DIR}" in
    /|/usr|/usr/local|/etc|/var|/bin|/sbin|/lib|/lib64|/boot|/dev|/proc|/sys|/root|/home)
        die "refusing to use ${HOME_DIR} as --home; choose a dedicated folder" ;;
esac
[[ "${HOME_DIR}" == /* && "${#HOME_DIR}" -ge 4 ]] || die "invalid --home: ${HOME_DIR}"
case "${DATA_DIR}" in
    /|/usr|/etc|/var|/bin|/sbin|/lib|/boot|/dev|/proc|/sys)
        die "refusing to use ${DATA_DIR} as --data-dir" ;;
esac

# `dind` keeps one root per instance; a host install has one root.
if [[ -z "${CODX_JUNIOR_IMAGE:-}" && -z "${IMAGE}" ]]; then
    if [[ "${REF}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]]; then
        IMAGE="codxjuniorai/codx-junior:${REF}"
    else
        IMAGE="${DEFAULT_IMAGE}"
        if [[ "${REF}" != "main" ]]; then
            warn "no published image is derived from '${REF}': using ${DEFAULT_IMAGE}. Pass --image or --build to pin."
        fi
    fi
fi
IMAGE="${IMAGE:-${DEFAULT_IMAGE}}"

# Domains: strip scheme/path, lower-case, de-duplicate.
PRIMARY_DOMAIN=""
NORMALIZED_DOMAINS=()
for d in "${DOMAINS[@]}"; do
    d="$(printf '%s' "$d" | tr '[:upper:]' '[:lower:]' | sed -E 's#^[a-z]+://##; s#/.*$##; s#\.$##')"
    [[ -n "$d" ]] || continue
    [[ "$d" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "invalid domain '${d}'"
    local_seen=0
    for existing in "${NORMALIZED_DOMAINS[@]}"; do
        [[ "$existing" == "$d" ]] && local_seen=1
    done
    [[ "$local_seen" == "1" ]] || NORMALIZED_DOMAINS+=("$d")
done
unset local_seen
DOMAINS=("${NORMALIZED_DOMAINS[@]+"${NORMALIZED_DOMAINS[@]}"}")
PRIMARY_DOMAIN="${DOMAINS[0]:-localhost}"
# local mode never uses a domain; ignore one inherited from the environment.
if [[ "${EFFECTIVE_MODE}" == "local" ]]; then
    DOMAINS=()
    PRIMARY_DOMAIN="localhost"
fi

if [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" ]] && [[ "${#DOMAINS[@]}" -eq 0 ]]; then
    die "--mode server needs --domain (or CODX_JUNIOR_DOMAIN); use --no-tls for plain HTTP"
fi
if [[ "${EFFECTIVE_MODE}" == "slave" ]] && [[ "${#DOMAINS[@]}" -eq 0 ]]; then
    die "--mode slave needs --domain (or CODX_JUNIOR_DOMAIN)"
fi
if [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" && -z "${ACME_EMAIL}" ]]; then
    die "--mode server needs --acme-email for Let's Encrypt (or --no-tls)"
fi
if [[ "${MODE}" == "dind" && "${DIND_MODE}" == "server" && "${NO_TLS}" == "1" ]]; then
    die "dind/server needs TLS; use --dind-mode local for a plain-HTTP DIND instance"
fi
if [[ "${EFFECTIVE_MODE}" == "slave" ]]; then
    PROXY_EXTERNAL="${PROXY_EXTERNAL:-true}"
else
    PROXY_EXTERNAL="${PROXY_EXTERNAL:-false}"
fi
# Inside the DIND wrapper the "proxy network" is a nested bridge that compose
# creates; only a host install joins an external master network.
if [[ "${CONTAINER}" == "1" && "${MODE}" == "slave" ]]; then
    PROXY_EXTERNAL="false"
fi
if [[ "${EFFECTIVE_MODE}" != "local" && "${BIND_ADDRESS_CLI}" == "0" && -z "${CODX_BIND_ADDRESS:-}" ]]; then
    BIND_ADDRESS="0.0.0.0"
fi
if [[ -z "${SYSTEMD}" ]]; then
    if [[ "${CONTAINER}" == "1" ]]; then
        SYSTEMD=0
    elif [[ "${MODE}" == "dind" ]]; then
        # The wrapper container must come back after a host reboot even when
        # its inner mode is localhost.
        SYSTEMD=1
    elif [[ "${EFFECTIVE_MODE}" == "local" ]]; then
        SYSTEMD=0
    else
        SYSTEMD=1
    fi
fi
if [[ "${CONTAINER}" == "1" ]]; then
    CLI=0
elif [[ "${MODE}" != "dind" && "${EFFECTIVE_MODE}" == "local" ]]; then
    CLI=0
fi

DOMAIN_RULE=""
for d in "${DOMAINS[@]+"${DOMAINS[@]}"}"; do
    [[ -n "${DOMAIN_RULE}" ]] && DOMAIN_RULE+=" || "
    DOMAIN_RULE+="Host(\`${d}\`)"
done
[[ -n "${DOMAIN_RULE}" ]] || DOMAIN_RULE="Host(\`localhost\`)"

detect_public_ip() {
    local ip=""
    ip="$(curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || true)"
    if [[ -z "${ip}" ]]; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi
    printf '%s' "${ip:-localhost}"
}

if [[ -n "${CODX_JUNIOR_PUBLIC_URL:-}" ]]; then
    PUBLIC_URL="${CODX_JUNIOR_PUBLIC_URL}"
elif [[ "${EFFECTIVE_MODE}" == "local" ]]; then
    PUBLIC_URL="http://localhost:${WEB_PORT}"
elif [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "1" ]]; then
    if [[ "${PRIMARY_DOMAIN}" != "localhost" ]]; then
        if [[ "${WEB_PORT}" == "80" ]]; then
            PUBLIC_URL="http://${PRIMARY_DOMAIN}"
        else
            PUBLIC_URL="http://${PRIMARY_DOMAIN}:${WEB_PORT}"
        fi
    else
        PUBLIC_URL="http://$(detect_public_ip):${WEB_PORT}"
    fi
else
    PUBLIC_URL="https://${PRIMARY_DOMAIN}"
fi

if [[ -z "${API_KEY}" && -n "${API_KEY_FILE}" ]]; then
    [[ -f "${API_KEY_FILE}" ]] || die "key file not found: ${API_KEY_FILE}"
    IFS= read -r API_KEY < "${API_KEY_FILE}" || true
    API_KEY="${API_KEY%%$'\r'}"
fi
if [[ "${NO_AI}" != "1" ]]; then
    if [[ -z "${API_KEY}" ]]; then
        die "an AI provider key is required (--key/--key-file, or --no-ai to configure it later)"
    fi
    if [[ "${PROVIDER}" == "custom" && -z "${API_URL}" ]]; then
        die "provider 'custom' needs --url (an OpenAI-compatible endpoint ending in /v1)"
    fi
fi
if [[ -n "${ADMIN_USER}" && -z "${ADMIN_PASSWORD}" ]]; then
    ADMIN_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -d '\n=+/' | cut -c1-24)"
    ADMIN_PASSWORD_GENERATED=1
else
    ADMIN_PASSWORD_GENERATED=0
fi

# `--coworking` mounts a root-equivalent socket. Require an explicit opt-in
# when the instance is not a nested daemon and not a single-user local install.
if [[ "${COWORKING}" == "1" && "${CONTAINER}" != "1" && "${EFFECTIVE_MODE}" != "local" && "${ALLOW_PRIVILEGED}" != "1" ]]; then
    warn "--coworking mounts /var/run/docker.sock (root on this host)."
    warn "Re-run with --allow-privileged to confirm, or use --mode dind for isolation."
    die "refusing to mount the Docker socket without --allow-privileged"
fi

# --- compose file set -------------------------------------------------------
COMPOSE_FILES=()
INSTALLER_DIR="${SELF_DIR}"
RUNTIME_DIR=""
SRC_DIR=""
COMPOSE_READY=0

# Copy the installer assets into the installation root. systemd and the
# generated wrapper must never depend on a checkout the operator may delete.
persist_runtime() {
    local source_dir="$1"
    local lib="${HOME_DIR}/lib"
    [[ "${source_dir}" == "${lib}" ]] && return 0
    mkdir -p "${lib}"
    if [[ -d "${lib}/runtime" && ! -f "${lib}/.codx-junior-runtime" ]]; then
        [[ "${FORCE}" == "1" ]] \
            || die "refusing to replace ${lib}/runtime (it was not created by this installer; use --force)"
        warn "replacing ${lib}/runtime (--force)"
    fi
    : > "${lib}/.codx-junior-runtime"
    cp -R "${source_dir}/install.sh" "${lib}/install.sh"
    rm -rf "${lib}/runtime" "${lib}/dind"
    cp -R "${source_dir}/runtime" "${lib}/runtime"
    if [[ -d "${source_dir}/dind" ]]; then
        cp -R "${source_dir}/dind" "${lib}/dind"
    fi
    chmod 755 "${lib}/install.sh" 2>/dev/null || true
    log "Installed runtime assets in ${lib}"
}

resolve_runtime() {
    # Inside the DIND image the assets are already baked at /opt/codx-junior.
    if [[ "${CONTAINER}" == "1" ]]; then
        RUNTIME_DIR="${SELF_DIR}/runtime"
        INSTALLER_DIR="${SELF_DIR}"
        COMPOSE_READY=1
        return 0
    fi

    local source_dir=""
    if [[ -f "${SELF_DIR}/runtime/docker-compose.yaml" ]]; then
        source_dir="${SELF_DIR}"
        CODX_BUILD_CONTEXT="$(cd "${SELF_DIR}/.." && pwd)"
    else
        SRC_DIR="${HOME_DIR}/src"
        if [[ "${DRY_RUN}" == "1" ]]; then
            RUNTIME_DIR="${SRC_DIR}/codx-junior-installer/runtime"
            INSTALLER_DIR="${SRC_DIR}/codx-junior-installer"
            CODX_BUILD_CONTEXT="${SRC_DIR}"
            warn "dry run: would download ${REPO} @ ${REF} to ${SRC_DIR}"
            return 0
        fi

        if [[ -e "${SRC_DIR}" && ! -d "${SRC_DIR}/.git" ]]; then
            die "${SRC_DIR} exists and is not a git checkout"
        fi
        if [[ ! -d "${SRC_DIR}/.git" ]]; then
            command -v git >/dev/null 2>&1 || die "git is required to download the installer"
            log "Downloading ${REPO} @ ${REF} to ${SRC_DIR}"
            mkdir -p "${HOME_DIR}"
            git clone --depth 1 --branch "${REF}" "${REPO}" "${SRC_DIR}" >/dev/null 2>&1 \
                || die "cannot clone ${REPO} at ${REF}"
        elif [[ "${UPGRADE}" == "1" ]]; then
            log "Refreshing ${SRC_DIR}"
            git -C "${SRC_DIR}" fetch --depth 1 origin "${REF}" >/dev/null 2>&1 \
                || die "cannot fetch ${REF} from ${REPO}"
            # Settings/projects are outside the checkout; reset only the source.
            git -C "${SRC_DIR}" reset --hard FETCH_HEAD --quiet
        fi
        source_dir="${SRC_DIR}/codx-junior-installer"
        CODX_BUILD_CONTEXT="${SRC_DIR}"
    fi

    [[ -f "${source_dir}/runtime/docker-compose.yaml" ]] \
        || die "unexpected repository layout: ${source_dir}/runtime/docker-compose.yaml is missing"
    if [[ "${DRY_RUN}" == "1" ]]; then
        RUNTIME_DIR="${source_dir}/runtime"
        INSTALLER_DIR="${source_dir}"
        COMPOSE_READY=1
        return 0
    fi
    persist_runtime "${source_dir}"
    RUNTIME_DIR="${HOME_DIR}/lib/runtime"
    INSTALLER_DIR="${HOME_DIR}/lib"
    COMPOSE_READY=1
}

select_compose_files() {
    COMPOSE_FILES=(-f "${RUNTIME_DIR}/docker-compose.yaml")
    case "${MODE}" in
        local)
            COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.local.yaml") ;;
        server)
            if [[ "${NO_TLS}" == "0" ]]; then
                COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.master.yaml")
            else
                COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.local.yaml")
            fi ;;
        slave)
            COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.slave.yaml") ;;
    esac
    if [[ "${COWORKING}" == "1" ]]; then
        COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.coworking.yaml")
    fi
    if [[ "${CONTAINER}" == "1" ]]; then
        case "${MODE}" in
            local)  COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-local.yaml") ;;
            server) COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-master.yaml") ;;
            slave)  COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-slave.yaml") ;;
        esac
    fi
    if [[ "${DO_BUILD}" == "1" && "${CONTAINER}" != "1" ]]; then
        COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.build.yaml")
    fi
}

# --- environment passed to docker compose ----------------------------------
COMPOSE_ENV=()
build_compose_env() {
    COMPOSE_ENV=(
        "CODX_ENV_FILE=${ENV_FILE}"
        "CODX_DATA_DIR=${DATA_DIR}"
        "CODX_INSTANCE=${INSTANCE}"
        "CODX_JUNIOR_PROJECTS_PATH=${PROJECTS}"
        "CODX_JUNIOR_IMAGE=${IMAGE}"
        "CODX_JUNIOR_DOMAIN=${PRIMARY_DOMAIN}"
        "CODX_JUNIOR_WEB_PORT=${WEB_PORT}"
        "CODX_BIND_ADDRESS=${BIND_ADDRESS}"
        "CODX_DOMAIN_RULE=${DOMAIN_RULE}"
        "CODX_ACME_EMAIL=${ACME_EMAIL}"
        "CODX_CERT_RESOLVER=${CERT_RESOLVER}"
        "CODX_MASTER_CERT_RESOLVER=${MASTER_CERT_RESOLVER}"
        "CODX_MASTER_ENTRYPOINT=${MASTER_ENTRYPOINT}"
        "CODX_ACME_CA=${ACME_CA}"
        "CODX_ACME_CA_URL=$(case "${ACME_CA}" in staging) echo https://acme-staging-v02.api.letsencrypt.org/directory ;; prod) echo https://acme-v02.api.letsencrypt.org/directory ;; *) echo "${ACME_CA}" ;; esac)"
        "CODX_TRAEFIK_IMAGE=${TRAEFIK_IMAGE}"
        "CODX_PROXY_NETWORK=${PROXY_NETWORK}"
        "CODX_PROXY_EXTERNAL=${PROXY_EXTERNAL}"
        "CODX_TRUSTED_PROXIES=${TRUSTED_PROXIES}"
        "CODX_FORWARDED_INSECURE=$([[ "${FORWARDED_INSECURE}" == "1" ]] && echo true || echo false)"
        "CODX_JUNIOR_KNOWLEDGE_BACKEND=${KNOWLEDGE_BACKEND}"
        "CODX_DIND_IMAGE=${DIND_IMAGE}"
        "CODX_DIND_PRIVILEGED=$([[ "${SYSBOX}" == "1" ]] && echo false || echo true)"
        "CODX_MODE=${EFFECTIVE_MODE}"
        "CODX_HEALTH_PORT=$([[ "${EFFECTIVE_MODE}" == "local" ]] && echo "${WEB_PORT}" || echo 80)"
        "CODX_DIND_SYSBOX=${SYSBOX}"
        "CODX_BUILD_CONTEXT=${CODX_BUILD_CONTEXT:-../..}"
    )
}

export_compose_env() {
    local kv
    for kv in "${COMPOSE_ENV[@]+"${COMPOSE_ENV[@]}"}"; do
        export "${kv}"
    done
}

compose() {
    export_compose_env
    docker compose \
        --project-directory "${RUNTIME_DIR}" \
        --project-name "${INSTANCE}" \
        --env-file "${ENV_FILE}" \
        "${COMPOSE_FILES[@]}" "$@"
}

print_compose_command() {
    printf 'docker compose --project-directory %q --project-name %q --env-file %q' \
        "${RUNTIME_DIR}" "${INSTANCE}" "${ENV_FILE}"
    printf ' %q' "${COMPOSE_FILES[@]}"
    printf '\n'
}

# --- settings file writer ---------------------------------------------------
set_env_var() {
    local key="$1" value="$2" file="$3"
    # Compose interpolates `$` in env files; write the literal escape so a key
    # or password containing `$` survives.
    value="${value//\$/\$\$}"
    mkdir -p "$(dirname "${file}")"
    [[ -f "${file}" ]] || { : > "${file}"; chmod 600 "${file}" 2>/dev/null || true; }
    if grep -Eq "^#?[[:space:]]*${key}=" "${file}"; then
        awk -v k="${key}" -v v="${value}" '
            $0 ~ "^#?[[:space:]]*" k "=" && !done { print k "=" v; done=1; next }
            { print }
        ' "${file}" > "${file}.tmp"
        mv "${file}.tmp" "${file}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
    chmod 600 "${file}" 2>/dev/null || true
}

drop_env_var() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    grep -vE "^#?[[:space:]]*${key}=" "${file}" > "${file}.tmp" || true
    mv "${file}.tmp" "${file}"
    chmod 600 "${file}" 2>/dev/null || true
}

write_settings() {
    mkdir -p "$(dirname "${ENV_FILE}")" "${DATA_DIR}" "${PROJECTS}" \
             "${DATA_DIR}/settings" "${DATA_DIR}/analytics"
    : >> "${ENV_FILE}"
    chmod 600 "${ENV_FILE}" 2>/dev/null || true

    set_env_var CODX_ENV_FILE "${ENV_FILE}" "${ENV_FILE}"
    set_env_var CODX_DATA_DIR "${DATA_DIR}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_MODE "${MODE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_INSTANCE "${INSTANCE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_DOMAIN "${PRIMARY_DOMAIN}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_PUBLIC_URL "${PUBLIC_URL}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_WEB_PORT "${WEB_PORT}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_IMAGE "${IMAGE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_KNOWLEDGE_BACKEND "${KNOWLEDGE_BACKEND}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_COWORKING "${COWORKING}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_HIDDEN "${HIDDEN}" "${ENV_FILE}"
    set_env_var CODX_PROXY_NETWORK "${PROXY_NETWORK}" "${ENV_FILE}"
    set_env_var CODX_TRUSTED_PROXIES "${TRUSTED_PROXIES}" "${ENV_FILE}"
    set_env_var CODX_ACME_EMAIL "${ACME_EMAIL}" "${ENV_FILE}"
    # Persist the deployment choices so a later `upgrade` with no flags cannot
    # silently switch mode, resolver or DIND variant.
    set_env_var CODX_DIND_MODE "${DIND_MODE}" "${ENV_FILE}"
    set_env_var CODX_ACME_CA "${ACME_CA}" "${ENV_FILE}"
    set_env_var CODX_CERT_RESOLVER "${CERT_RESOLVER}" "${ENV_FILE}"
    set_env_var CODX_MASTER_CERT_RESOLVER "${MASTER_CERT_RESOLVER}" "${ENV_FILE}"
    set_env_var CODX_MASTER_ENTRYPOINT "${MASTER_ENTRYPOINT}" "${ENV_FILE}"
    set_env_var CODX_TRAEFIK_IMAGE "${TRAEFIK_IMAGE}" "${ENV_FILE}"
    set_env_var CODX_DIND_IMAGE "${DIND_IMAGE}" "${ENV_FILE}"
    set_env_var CODX_DIND_SYSBOX "${SYSBOX}" "${ENV_FILE}"
    set_env_var CODX_NO_TLS "${NO_TLS}" "${ENV_FILE}"
    set_env_var CODX_BIND_ADDRESS "${BIND_ADDRESS}" "${ENV_FILE}"
    if [[ -n "${DIND_STORAGE:-}" ]]; then
        set_env_var CODX_DIND_STORAGE "${DIND_STORAGE}" "${ENV_FILE}"
    fi

    if [[ "${NO_AI}" == "1" ]]; then
        drop_env_var CODX_JUNIOR_AI_PROVIDER "${ENV_FILE}"
        drop_env_var CODX_JUNIOR_AI_API_KEY "${ENV_FILE}"
    else
        set_env_var CODX_JUNIOR_AI_PROVIDER "${PROVIDER}" "${ENV_FILE}"
        set_env_var CODX_JUNIOR_AI_API_KEY "${API_KEY}" "${ENV_FILE}"
    fi
    if [[ -n "${API_URL}" ]]; then
        set_env_var CODX_JUNIOR_AI_API_URL "${API_URL}" "${ENV_FILE}"
    else
        drop_env_var CODX_JUNIOR_AI_API_URL "${ENV_FILE}"
    fi
    if [[ -n "${MODEL}" ]]; then
        set_env_var CODX_JUNIOR_AI_MODEL "${MODEL}" "${ENV_FILE}"
    else
        drop_env_var CODX_JUNIOR_AI_MODEL "${ENV_FILE}"
    fi
    if [[ -n "${ADMIN_USER}" ]]; then
        set_env_var CODX_JUNIOR_ADMIN_USER "${ADMIN_USER}" "${ENV_FILE}"
        set_env_var CODX_JUNIOR_ADMIN_PASSWORD "${ADMIN_PASSWORD}" "${ENV_FILE}"
        [[ -n "${ADMIN_EMAIL}" ]] && set_env_var CODX_JUNIOR_ADMIN_EMAIL "${ADMIN_EMAIL}" "${ENV_FILE}"
    fi
    if ! chmod 600 "${ENV_FILE}" 2>/dev/null; then
        warn "could not restrict ${ENV_FILE} to the service user (the filesystem ignores chmod)"
    fi
}

write_compose_wrapper() {
    local bin_dir="${HOME_DIR}/bin"
    local wrapper="${bin_dir}/codx-junior-compose"
    mkdir -p "${bin_dir}"
    {
        printf '#!/usr/bin/env bash\n'
        printf '# Generated by codx-junior-installer/install.sh — do not edit.\n'
        printf 'set -euo pipefail\n'
        printf 'export'
        local first=1
        for kv in "${COMPOSE_ENV[@]}"; do
            if [[ "${first}" == "1" ]]; then printf ' %q' "${kv}"; first=0; else printf ' \\\n       %q' "${kv}"; fi
        done
        printf '\nexec docker compose --project-directory %q --project-name %q --env-file %q' \
            "${RUNTIME_DIR}" "${INSTANCE}" "${ENV_FILE}"
        for f in "${COMPOSE_FILES[@]}"; do printf ' %q' "${f}"; done
        printf ' "$@"\n'
    } > "${wrapper}"
    chmod 755 "${wrapper}" 2>/dev/null || warn "could not mark ${wrapper} executable"
}

# --- operations CLI ---------------------------------------------------------
install_cli() {
    [[ "${CLI}" == "1" ]] || return 0
    [[ "${EUID}" -eq 0 ]] || { warn "not root: skipping /usr/local/bin"; return 0; }
    local name="codx-junior-${INSTANCE}"
    [[ "${INSTANCE}" == "codx-junior" ]] && name="codx-junior"
    local local_bin="${HOME_DIR}/bin/${name}"
    local installer="${INSTALLER_DIR}/install.sh"
    [[ -f "${installer}" ]] || installer="${SELF_DIR}/install.sh"
    mkdir -p "${HOME_DIR}/bin"
    cat > "${local_bin}" <<CLI
#!/usr/bin/env bash
# Generated by codx-junior-installer/install.sh — do not edit.
exec bash $(printf '%q' "${installer}") --home $(printf '%q' "${HOME_DIR}") --instance $(printf '%q' "${INSTANCE}") --env-file $(printf '%q' "${ENV_FILE}") "\$@"
CLI
    chmod 755 "${local_bin}" 2>/dev/null || true
    if install -m 0755 "${local_bin}" "/usr/local/bin/${name}" 2>/dev/null; then
        log "CLI: ${name} (status|logs|down|upgrade|doctor|config)"
    else
        warn "could not install /usr/local/bin/${name}; use ${local_bin}"
    fi
}

# --- systemd ----------------------------------------------------------------
install_systemd() {
    [[ "${SYSTEMD}" == "1" ]] || return 0
    [[ "${EUID}" -eq 0 ]] || { warn "not root: skipping the systemd unit"; return 0; }
    command -v systemctl >/dev/null 2>&1 || { warn "systemctl not found: skipping the unit"; return 0; }
    [[ -d /run/systemd/system ]] || { warn "systemd is not PID 1: skipping the unit"; return 0; }

    local unit_name
    if [[ "${INSTANCE}" == "codx-junior" ]]; then
        unit_name="codx-junior.service"
    else
        unit_name="codx-junior-${INSTANCE}.service"
    fi
    local wrapper="${HOME_DIR}/bin/codx-junior-compose"
    local unit="/etc/systemd/system/${unit_name}"
    cat > "${unit}" <<UNIT
[Unit]
Description=codx-junior (${INSTANCE})
Documentation=https://github.com/codx-junior/codx-junior
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory="${RUNTIME_DIR}"
EnvironmentFile=-${ENV_FILE}
ExecStart=/bin/bash "${wrapper}" up -d --remove-orphans
ExecStop=/bin/bash "${wrapper}" down
ExecReload=/bin/bash "${wrapper}" up -d --remove-orphans
TimeoutStartSec=0
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable "${unit_name}" >/dev/null 2>&1 || warn "could not enable ${unit_name}"
    log "systemd unit: ${unit_name} (enable --now on next boot; already running)"
}

# --- docker -----------------------------------------------------------------
install_compose_plugin() {
    docker compose version >/dev/null 2>&1 && return 0
    log "Installing the docker compose plugin"
    case "$(. /etc/os-release 2>/dev/null; printf '%s' "${ID:-unknown}")" in
        debian|ubuntu|raspbian|linuxmint)
            apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || true ;;
        amzn|rhel|centos|fedora|rocky|almalinux|ol)
            dnf install -y -q docker-compose-plugin >/dev/null 2>&1 || true ;;
    esac
    docker compose version >/dev/null 2>&1 && return 0
    local arch tag="v2.29.7"
    case "$(uname -m)" in
        x86_64|amd64) arch="x86_64" ;;
        aarch64|arm64) arch="aarch64" ;;
        *) die "unsupported architecture $(uname -m) for the compose plugin" ;;
    esac
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -fsSL "https://github.com/docker/compose/releases/download/${tag}/docker-compose-linux-${arch}" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    docker compose version >/dev/null 2>&1 || die "could not install the docker compose plugin"
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        install_compose_plugin
        return 0
    fi
    if [[ "${CONTAINER}" == "1" ]]; then
        die "the nested Docker daemon is not ready"
    fi
    if [[ "${INSTALL_DOCKER}" != "1" ]]; then
        die "Docker is not installed and --no-docker-install was given"
    fi
    command -v docker >/dev/null 2>&1 \
        || [[ "${EUID}" -eq 0 ]] \
        || die "Docker is not installed. Install it, or run this installer as root."
    if [[ "${EUID}" -ne 0 ]]; then
        die "the Docker daemon is not reachable by ${USER} (add the user to the docker group)"
    fi
    log "Installing Docker Engine"
    case "$(. /etc/os-release 2>/dev/null; printf '%s' "${ID:-unknown}")" in
        debian|ubuntu|raspbian|linuxmint)
            apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io >/dev/null 2>&1 || true ;;
        amzn|rhel|centos|fedora|rocky|almalinux|ol)
            dnf install -y -q docker >/dev/null 2>&1 || true ;;
    esac
    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com -o /tmp/get-docker.sh || die "cannot download the Docker installer"
        sh /tmp/get-docker.sh >/dev/null 2>&1 || die "the Docker installer failed"
    fi
    systemctl enable --now docker >/dev/null 2>&1 || service docker start >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || die "the Docker daemon did not start"
    install_compose_plugin
}

# --- health -----------------------------------------------------------------
container_health() {
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo missing
}

wait_health() {
    [[ "${NO_WAIT}" == "1" ]] && return 0
    local status="starting" i attempts=120 delay=3
    if [[ "${MODE}" == "dind" ]]; then
        # The nested daemon pulls images and starts the inner stack; the DIND
        # healthcheck has a 15 min start period.
        attempts=240
        delay=5
    fi
    log "Waiting for ${INSTANCE} to become healthy (first start can take a minute)"
    for i in $(seq 1 "${attempts}"); do
        status="$(container_health "${INSTANCE}")"
        case "${status}" in
            healthy) break ;;
            unhealthy|exited|dead)
                docker logs --tail 80 "${INSTANCE}" 2>&1 || true
                die "container ${INSTANCE} is ${status}" ;;
        esac
        sleep "${delay}"
    done
    if [[ "${status}" != "healthy" ]]; then
        docker logs --tail 80 "${INSTANCE}" 2>&1 || true
        die "health check timed out for ${INSTANCE}"
    fi
    log "Container health: ${status}"

    if [[ "${EFFECTIVE_MODE}" == "local" ]]; then
        curl -fsS --max-time 5 "http://127.0.0.1:${WEB_PORT}/api/health" >/dev/null 2>&1 \
            || warn "the UI port did not answer /api/health yet (it may still be starting)"
    elif [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" && "${SKIP_TLS_WAIT}" != "1" ]]; then
        log "Waiting for the certificate of ${PRIMARY_DOMAIN} (max 5 min; needs its A record and :80)"
        local ok=0
        for i in $(seq 1 60); do
            if curl -fsS --max-time 5 --resolve "${PRIMARY_DOMAIN}:443:127.0.0.1" \
                    "https://${PRIMARY_DOMAIN}/api/health" >/dev/null 2>&1; then
                ok=1; break
            fi
            sleep 5
        done
        if [[ "${ok}" != "1" ]]; then
            docker logs --tail 40 "${INSTANCE}-traefik" 2>&1 || true
            die "https://${PRIMARY_DOMAIN} did not answer with a valid certificate.
       Point the A record at this machine and open :80/:443, then re-run.
       Traefik keeps retrying in the background: docker logs -f ${INSTANCE}-traefik"
        fi
        log "Certificate ready: https://${PRIMARY_DOMAIN}"
    fi
}

apply_bootstrap() {
    [[ "${NO_AI}" == "1" ]] && return 0
    [[ "${MODE}" == "dind" ]] && return 0
    docker exec "${INSTANCE}" curl -fsS -X POST http://127.0.0.1:19980/api/bootstrap/apply >/dev/null 2>&1 \
        || warn "could not apply the AI provider automatically; configure it in the UI"
}

# --- network / port checks --------------------------------------------------
ensure_proxy_network() {
    [[ "${EFFECTIVE_MODE}" == "slave" ]] || return 0
    if docker network inspect "${PROXY_NETWORK}" >/dev/null 2>&1; then
        return 0
    fi
    if [[ "${CODX_CREATE_NETWORK:-0}" == "1" ]]; then
        if [[ "${DRY_RUN}" == "1" ]]; then
            warn "dry run: would create Docker network ${PROXY_NETWORK}"
            return 0
        fi
        log "Creating Docker network ${PROXY_NETWORK}"
        docker network create "${PROXY_NETWORK}" >/dev/null
        return 0
    fi
    if [[ "${DRY_RUN}" == "1" ]]; then
        warn "dry run: Docker network '${PROXY_NETWORK}' is missing and must be created"
        return 0
    fi
    die "Docker network '${PROXY_NETWORK}' does not exist.
       Create it with: docker network create ${PROXY_NETWORK}
       or pass --create-network / --proxy-network <existing>."
}

check_ports() {
    [[ "${EUID}" -eq 0 ]] || return 0
    command -v ss >/dev/null 2>&1 || return 0
    local p
    for p in 80 443; do
        if ss -ltn "sport = :${p}" 2>/dev/null | grep -q LISTEN; then
            if [[ "${DRY_RUN}" == "1" ]]; then
                warn "dry run: port ${p} is already in use"
            elif [[ "${FORCE}" == "1" ]]; then
                warn "port ${p} is already in use; continuing because of --force"
            else
                die "port ${p} is already in use; stop the other proxy/server or use --mode slave"
            fi
        fi
    done
}

# --- dind -------------------------------------------------------------------
resolve_dind_dir() {
    if [[ -f "${INSTALLER_DIR}/dind/docker-compose.yaml" ]]; then
        DIND_DIR="${INSTALLER_DIR}/dind"
        return 0
    fi
    if [[ "${DRY_RUN}" == "1" ]]; then
        DIND_DIR="${INSTALLER_DIR}/dind"
        warn "dry run: would download the DIND templates into ${DIND_DIR}"
        return 0
    fi
    die "DIND templates not found under ${INSTALLER_DIR}/dind"
}

install_dind() {
    if [[ -z "${RUNTIME_DIR}" ]]; then
        resolve_runtime
    fi
    resolve_dind_dir
    local dind_dir="${DIND_DIR}"

    DIND_COMPOSE=( -f "${dind_dir}/docker-compose.yaml" )
    case "${DIND_MODE}" in
        local)  DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.local.yaml" ) ;;
        server) DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.master.yaml" ) ;;
        slave)  DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.slave.yaml" ) ;;
    esac
    [[ "${SYSBOX}" == "1" ]] && DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.sysbox.yaml" )

    if [[ "${SYSBOX}" == "1" && "${DRY_RUN}" != "1" ]]; then
        docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"sysbox-runc"' \
            || die "the 'sysbox-runc' Docker runtime is not installed (see https://github.com/nestybox/sysbox)"
    fi
    if [[ "${DIND_MODE}" == "server" ]]; then
        check_ports
    fi
    ensure_proxy_network

    if [[ "${DO_BUILD_DIND}" == "1" ]]; then
        [[ -f "${INSTALLER_DIR}/dind/Dockerfile" ]] \
            || die "--build-dind needs ${INSTALLER_DIR}/dind/Dockerfile (run from a checkout)"
        log "Building ${DIND_IMAGE} (context: ${INSTALLER_DIR})"
        docker build -t "${DIND_IMAGE}" -f "${INSTALLER_DIR}/dind/Dockerfile" "${INSTALLER_DIR}"
    elif [[ "${NO_PULL}" != "1" && "${DRY_RUN}" != "1" ]]; then
        log "Pulling ${DIND_IMAGE}"
        docker pull "${DIND_IMAGE}" >/dev/null 2>&1 \
            || warn "could not pull ${DIND_IMAGE}: use --build-dind until it is published"
    fi

    DIND_STORAGE="${CODX_DIND_STORAGE:-${INSTANCE}-dind}"
    COMPOSE_FILES=("${DIND_COMPOSE[@]}")
    RUNTIME_DIR="${dind_dir}"
    build_compose_env
    COMPOSE_ENV+=("CODX_DIND_STORAGE=${DIND_STORAGE}")

    if [[ "${DRY_RUN}" == "1" ]]; then
        log "dry run: nothing was changed"
        printf '\nSettings file: %s\n' "${ENV_FILE}"
        run_config | sed 's/^/  /'
        printf '\nCommand:\n  '
        print_compose_command
        printf '\nFiles:\n'
        printf '  %s\n' "${COMPOSE_FILES[@]}"
        return 0
    fi

    write_settings
    write_compose_wrapper

    log "Starting DIND instance ${INSTANCE} (dind/${DIND_MODE})"
    compose up -d --remove-orphans

    wait_health
    install_systemd
    install_cli
    open_firewall
    summary
}

# --- firewall (opt-in) ------------------------------------------------------
open_firewall() {
    [[ "${OPEN_FIREWALL}" == "1" ]] || return 0
    [[ "${EUID}" -eq 0 ]] || { warn "--open-firewall needs root; skipped"; return 0; }
    if ! command -v ufw >/dev/null 2>&1; then
        case "$(. /etc/os-release 2>/dev/null; printf '%s' "${ID:-unknown}")" in
            debian|ubuntu|raspbian|linuxmint)
                apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw >/dev/null 2>&1 || true ;;
            amzn|rhel|centos|fedora|rocky|almalinux|ol)
                dnf install -y -q ufw >/dev/null 2>&1 || true ;;
        esac
    fi
    command -v ufw >/dev/null 2>&1 || { warn "ufw is not available; open the ports in the provider firewall"; return 0; }
    ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1 || true
    if [[ "${MODE}" == "local" ]]; then
        ufw allow "${WEB_PORT}/tcp" >/dev/null 2>&1 || true
    elif [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" ]]; then
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
    elif [[ "${EFFECTIVE_MODE}" == "server" || "${EFFECTIVE_MODE}" == "local" ]]; then
        ufw allow "${WEB_PORT}/tcp" >/dev/null 2>&1 || true
    fi
    ufw --force enable >/dev/null 2>&1 || true
    log "ufw: ssh + the published ports are allowed"
}

# --- summary ----------------------------------------------------------------
summary() {
    local settings="${ENV_FILE}"
    cat <<DONE

=============================================================================
 codx-junior is running

   mode     : ${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)
   URL      : ${PUBLIC_URL}
   projects : ${PROJECTS}
   settings : ${settings} (mode 600)
   instance : ${INSTANCE}
   compose  : ${RUNTIME_DIR}
   systemd  : $([[ "${SYSTEMD}" == "1" ]] && { [[ "${INSTANCE}" == "codx-junior" ]] && echo "codx-junior.service" || echo "codx-junior-${INSTANCE}.service"; } || echo "disabled")

 Manage
   ${HOME_DIR}/bin/codx-junior-compose ps
   ${HOME_DIR}/bin/codx-junior-compose logs -f
   ${HOME_DIR}/bin/codx-junior-compose down

${ADMIN_USER:+   First account: ${ADMIN_USER}${ADMIN_PASSWORD_GENERATED:+ (password generated below)}}
DONE
    if [[ "${ADMIN_PASSWORD_GENERATED}" == "1" ]]; then
        printf '\n   Admin password: %s\n   (change it after the first login)\n' "${ADMIN_PASSWORD}"
    fi
    cat <<'DONE'
=============================================================================
DONE
    printf 'CODX_JUNIOR_READY %s\n' "${PUBLIC_URL}"
}

# --- commands ---------------------------------------------------------------
run_config() {
    cat <<CONFIG
mode=${MODE}
dind_mode=${DIND_MODE}
instance=${INSTANCE}
home=${HOME_DIR}
data=${DATA_DIR}
projects=${PROJECTS}
env_file=${ENV_FILE}
image=${IMAGE}
ref=${REF}
repo=${REPO}
domains=${DOMAINS[*]:-}
primary_domain=${PRIMARY_DOMAIN}
public_url=${PUBLIC_URL}
web_port=${WEB_PORT}
proxy_network=${PROXY_NETWORK}
proxy_external=${PROXY_EXTERNAL}
trusted_proxies=${TRUSTED_PROXIES}
acme_email=${ACME_EMAIL}
coworking=${COWORKING}
systemd=${SYSTEMD}
dind_image=${DIND_IMAGE}
sysbox=${SYSBOX}
CONFIG
}

run_install() {
    log "codx-junior installer ${INSTALLER_VERSION}"
    log "  mode      : ${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)"
    log "  instance  : ${INSTANCE}"
    log "  home      : ${HOME_DIR}"
    log "  projects  : ${PROJECTS}"
    log "  image     : ${IMAGE}"
    log "  domain    : ${DOMAINS[*]:-<none>}"
    log "  proxy     : $([[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" ]] && echo "traefik master (${CERT_RESOLVER})" || { [[ "${EFFECTIVE_MODE}" == "slave" ]] && echo "traefik slave on ${PROXY_NETWORK}" || echo "none"; })"
    log "  coworking : ${COWORKING}"

    if [[ "${DRY_RUN}" != "1" ]]; then
        ensure_docker
    fi
    if [[ "${MODE}" == "dind" ]]; then
        install_dind
        return 0
    fi
    resolve_runtime
    select_compose_files
    build_compose_env

    if [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" ]]; then
        check_ports
    fi
    ensure_proxy_network

    if [[ "${DRY_RUN}" == "1" ]]; then
        write_settings_plan=1
        log "dry run: nothing was changed"
        printf '\nSettings file: %s\n' "${ENV_FILE}"
        run_config | sed 's/^/  /'
        printf '\nCommand:\n  '
        print_compose_command
        printf '\nFiles:\n'
        printf '  %s\n' "${COMPOSE_FILES[@]}"
        printf '\nHealth check: %s\n' "${PUBLIC_URL}/api/health"
        return 0
    fi

    write_settings
    # Re-build after write: the settings file may add values used by compose.
    build_compose_env

    mkdir -p "${DATA_DIR}/traefik"
    chmod 700 "${DATA_DIR}/traefik" 2>/dev/null || true
    if [[ "${EFFECTIVE_MODE}" == "server" && "${NO_TLS}" == "0" ]]; then
        # Traefik refuses to start when acme.json is world-readable.
        [[ -f "${DATA_DIR}/traefik/acme.json" ]] || : > "${DATA_DIR}/traefik/acme.json"
        chmod 600 "${DATA_DIR}/traefik/acme.json" 2>/dev/null || true
    fi

    write_compose_wrapper
    select_compose_files

    if [[ "${DO_BUILD}" == "1" && "${CONTAINER}" != "1" ]]; then
        log "Building ${IMAGE} (first build: several minutes)"
        compose build
    elif [[ "${NO_PULL}" != "1" && "${MODE}" != "dind" ]]; then
        log "Pulling ${IMAGE}"
        compose pull --quiet codx-junior >/dev/null 2>&1 \
            || warn "could not pull ${IMAGE}: starting the local image (use --build if missing)"
    fi

    log "Starting ${INSTANCE} (${MODE})"
    compose up -d --remove-orphans

    wait_health
    apply_bootstrap
    install_systemd
    install_cli
    open_firewall
    summary
}

run_upgrade() {
    UPGRADE=1
    if [[ "${MODE}" == "dind" ]]; then
        ensure_docker
        resolve_runtime
        install_dind
        return 0
    fi
    resolve_runtime
    select_compose_files
    build_compose_env
    write_settings
    write_compose_wrapper
    if [[ "${DO_BUILD}" == "1" ]]; then
        compose build
    elif [[ "${NO_PULL}" != "1" ]]; then
        compose pull --quiet codx-junior || warn "image pull failed; keeping the local image"
    fi
    compose up -d --remove-orphans
    wait_health
    apply_bootstrap
    summary
}

select_dind_files() {
    resolve_dind_dir
    DIND_COMPOSE=( -f "${DIND_DIR}/docker-compose.yaml" )
    case "${MODE}" in
        local)  DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.local.yaml" ) ;;
        server) DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.master.yaml" ) ;;
        slave)  DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.slave.yaml" ) ;;
    esac
    [[ "${SYSBOX}" == "1" ]] && DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.sysbox.yaml" )
    COMPOSE_FILES=("${DIND_COMPOSE[@]}")
    RUNTIME_DIR="${DIND_DIR}"
    DIND_STORAGE="${CODX_DIND_STORAGE:-${INSTANCE}-dind}"
    build_compose_env
    COMPOSE_ENV+=("CODX_DIND_STORAGE=${DIND_STORAGE}")
}

run_status() {
    resolve_runtime
    if [[ "${MODE}" == "dind" ]]; then
        select_dind_files
    else
        select_compose_files
        build_compose_env
    fi
    printf 'instance : %s\n' "${INSTANCE}"
    printf 'mode     : %s\n' "${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)"
    printf 'url      : %s\n' "${PUBLIC_URL}"
    printf 'settings : %s\n' "${ENV_FILE}"
    printf 'health   : %s\n' "$(container_health "${INSTANCE}")"
    compose ps
}

run_logs() {
    resolve_runtime
    if [[ "${MODE}" == "dind" ]]; then
        select_dind_files
    else
        select_compose_files
        build_compose_env
    fi
    compose logs --tail 100 -f
}

run_down() {
    resolve_runtime
    if [[ "${MODE}" == "dind" ]]; then
        select_dind_files
    else
        select_compose_files
        build_compose_env
    fi
    compose down --remove-orphans
    if [[ "${SYSTEMD}" == "1" && "${EUID}" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
        local unit_name="codx-junior-${INSTANCE}.service"
        [[ "${INSTANCE}" == "codx-junior" ]] && unit_name="codx-junior.service"
        systemctl disable "${unit_name}" >/dev/null 2>&1 || true
    fi
    log "Stopped ${INSTANCE}; projects and settings were kept"
}

run_url() {
    printf '%s\n' "${PUBLIC_URL}"
}

run_env() {
    if [[ ! -f "${ENV_FILE}" ]]; then
        die "settings file not found: ${ENV_FILE}"
    fi
    sed -E \
        -e 's/^(CODX_JUNIOR_AI_API_KEY)=.*/\1=***masked***/' \
        -e 's/^(CODX_JUNIOR_ADMIN_PASSWORD)=.*/\1=***masked***/' \
        "${ENV_FILE}"
}

run_version() {
    printf 'codx-junior installer %s (%s)\n' "${INSTALLER_VERSION}" "${MODE}"
}

run_up() {
    resolve_runtime
    if [[ "${MODE}" == "dind" ]]; then select_dind_files; else select_compose_files; build_compose_env; fi
    compose up -d --remove-orphans
}

run_restart() {
    resolve_runtime
    if [[ "${MODE}" == "dind" ]]; then select_dind_files; else select_compose_files; build_compose_env; fi
    compose restart
}

run_doctor() {
    local rc=0
    printf 'docker        : '
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then echo "ok ($(docker --version | awk '{print $3}' | tr -d ,))"; else echo "MISSING"; rc=1; fi
    printf 'compose       : '
    if docker compose version >/dev/null 2>&1; then echo "ok ($(docker compose version --short 2>/dev/null || echo v2))"; else echo "MISSING"; rc=1; fi
    printf 'git           : '
    if command -v git >/dev/null 2>&1; then echo "ok"; else echo "missing (only needed to download the source)"; fi
    printf 'systemd       : '
    if [[ -d /run/systemd/system ]]; then echo "ok"; else echo "not PID 1 (the unit is skipped)"; fi
    printf 'proxy network : '
    if docker network inspect "${PROXY_NETWORK}" >/dev/null 2>&1; then echo "${PROXY_NETWORK} exists"; else echo "${PROXY_NETWORK} missing"; [[ "${EFFECTIVE_MODE}" == "slave" ]] && rc=1; fi
    printf 'port 80/443   : '
    if command -v ss >/dev/null 2>&1 && ss -ltn 'sport = :80' 2>/dev/null | grep -q LISTEN; then echo "80 in use"; else echo "free"; fi
    printf 'docker socket : '
    if [[ -S /var/run/docker.sock ]]; then echo "present"; else echo "missing"; fi
    printf 'memory        : '
    free -h 2>/dev/null | awk '/^Mem:/ {print $2 " total, " $7 " available"}' || echo "unknown"
    printf 'disk          : '
    df -h "${HOME_DIR}" 2>/dev/null | awk 'NR==2 {print $4 " free on " $6}' || echo "unknown"
    if [[ "${COWORKING}" == "1" ]]; then
        printf 'coworking     : enabled (docker socket is root-equivalent)\n'
    fi
    return "${rc}"
}

case "${COMMAND}" in
    install) run_install ;;
    upgrade) run_upgrade ;;
    status)  run_status ;;
    logs)    run_logs ;;
    down)    run_down ;;
    doctor)  run_doctor ;;
    config)  run_config ;;
    url)     run_url ;;
    env)     run_env ;;
    version) run_version ;;
    up)      run_up ;;
    restart) run_restart ;;
    *)       die "unknown command '${COMMAND}'" ;;
esac
