#!/usr/bin/env bash
# =============================================================================
# codx-junior — UNIFIED installer
#
# One entry point for every supported deployment:
#
#   local    a laptop / CI box: loopback port, no domain, no proxy
#   server   a public server: Traefik primary on :80/:443 with ACME TLS
#   slave    legacy alias: a server behind an existing primary (prefer --traefik slave)
#   --dev    development option: DEBUG=1; host also gets Vite HMR + reload
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
#   sudo ./install.sh --mode dind --traefik primary --domain acme.example.com \
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
DEFAULT_DEV_IMAGE="codxjuniorai/codx-junior:dev"
DEFAULT_TRAEFIK_IMAGE="traefik:v3.3"
DEFAULT_PROXY_NETWORK="codx-proxy"
DEFAULT_TRUSTED_PROXIES="172.16.0.0/12,10.0.0.0/8,192.168.0.0/16,127.0.0.1/32"
DEFAULT_ACME_EMAIL=""

# ── 0.0.1 feature profile ────────────────────────────────────────────────────
# The application image always contains every feature, but only the ids in
# CODX_JUNIOR_ACTIVE_FEATURES are exposed. `core` is the 0.0.1 release profile:
# it hides the company OS (and its first-run wizard) so the instance boots in
# the plain project workspace. `all` is the full development/CI profile. A
# customer can also pass a comma-separated subset, e.g. core,mcp.
#
# These lists are the installer's offline copy of the public contract in
# docs/product/features-0.0.1.json (mirrored by api/codx/junior/features.json).
# Keep them in sync: an unknown id must fail the install instead of silently
# reappearing in a demo.
CORE_FEATURE_IDS="install onboarding auth workspace-shell projects chats agent-tools profiles knowledge vibe-coding file-explorer kanban git-review settings api mobile themes-i18n"
ALL_FEATURE_IDS="install onboarding auth workspace-shell projects chats agent-tools profiles knowledge vibe-coding file-explorer kanban git-review settings api mobile themes-i18n suggested-replies multi-user budget analytics workspaces coworking browser-use computer-use desktop mentions wiki company-business company-tickets company-assistant company-files company-security teams meetings calendar autonomous-agents autonomous-tickets channel-creator external-actors slack-whatsapp voice plugins mcp harness-dsh images logs infra self-update"

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGINAL_ARGC=$#

# Installer state: the last wizard/CLI selections, used as defaults when the
# installation is restarted. It is separate from the instance settings file:
# it exists even if the install never reached write_settings().
# Override with CODX_JUNIOR_INSTALLER_STATE.
STATE_FILE="${CODX_JUNIOR_INSTALLER_STATE:-${PWD}/installation.env}"
declare -A INSTALLER_STATE=()
if [[ -f "${STATE_FILE}" ]]; then
    while IFS='=' read -r key value; do
        value="${value%$'\r'}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        value="${value//\$\$/\$}"
        INSTALLER_STATE["${key}"]="${value}"
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${STATE_FILE}") || true
fi

C_RESET=$'\033[0m'
C_CYAN=$'\033[1;36m'
C_BOLD=$'\033[1m'
C_DIM=$'\033[2m'
BANNER_PRINTED=0
log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

print_banner() {
    [[ "${BANNER_PRINTED}" == "1" || "${CONTAINER}" == "1" ]] && return 0
    BANNER_PRINTED=1
    printf '%s' "${C_CYAN}" >&4
    cat >&4 <<'BANNER'
╔═══════════════════════════════════════════╗
║                                           ║
║    ██████╗ ██████╗ ██████╗ ██╗  ██╗       ║
║   ██╔════╝██╔═══██╗██╔══██╗╚██╗██╔╝       ║
║   ██║     ██║   ██║██║  ██║ ╚███╔╝        ║
║   ██║     ██║   ██║██║  ██║ ██╔██╗        ║
║   ╚██████╗╚██████╔╝██████╔╝██╔╝ ██╗       ║
║    ╚═════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝       ║
║                                           ║
║    j u n i o r     codx-junior installer  ║
║    AI engineering workspace · v1.0.0      ║
║                                           ║
╚═══════════════════════════════════════════╝
BANNER
    printf '%s\n' "${C_RESET}" >&4
}

section() {
    printf '\n%s── %s ──%s\n' "${C_CYAN}" "$*" "${C_RESET}" >&4
}

row() {
    printf '  %-10s: %s\n' "$1" "$2"
}

usage() {
    cat <<'USAGE'
codx-junior unified installer

Usage:
  ./install.sh [options] [install]      install or update (the default)
  ./install.sh [options] status|logs|down|up|restart|update|remove|doctor|config|url|env|version

Modes:
  --mode local              full machine dedicated to codx-junior (localhost by default)
  --mode server             public server: domain + Traefik primary :80/:443 + ACME
  --mode dind               isolate codx-junior in a Docker-in-Docker container
  --mode slave              legacy alias: behind an existing primary (use --traefik slave)
  --dind-mode MODE          legacy; the DIND inner installation is always server
  --dev                     development option: DEBUG=1 (host adds live checkout + Vite)
  --no-dev                  disable the development option
  --traefik MODE            Traefik setup: none|primary|slave (master is a
                            legacy alias for primary; the wizard asks for it)
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
  --proxy-network NAME      Docker network shared with the primary [codx-proxy]
  --create-network          create the slave proxy network when missing
  --trusted-proxy CIDR      primary CIDR allowed to set X-Forwarded-*; repeat
  --forwarded-insecure      trust X-Forwarded-* from any peer (slave only)
  --master-cert-resolver N  cert resolver name on the primary   [letsencrypt]
  --master-entrypoint NAME  primary HTTPS entrypoint           [websecure]
  --no-tls                  server mode: do not start Traefik (HTTP only)
  --tls none                alias of --no-tls (a proxy terminates TLS)

Features:
  --features PROFILE        core|all|id[,id...]                  [core]
                            core is the 0.0.1 release: workspace mode, company
                            OS hidden (no company wizard). all exposes everything.
  --full-featured           alias of --features all
  --core                    alias of --features core
  --coworking               mount /var/run/docker.sock (browser/desktops)
  --no-coworking            keep the socket out (default)
  --hidden                  hide codx-junior + child projects from the selector
  --no-hidden               show them (default)
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
  --update                  refresh source/image and recreate
  --delete-files            with remove: delete env/compose/installer files
  --confirm-delete          with remove: skip the interactive confirmation
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
        printf '\n' >/dev/tty
        printf '%s' "${label}" >/dev/tty
        [[ -n "${default}" ]] && printf ' [%s]' "${default}" >/dev/tty
        printf ': ' >/dev/tty
        IFS= read -r answer </dev/tty || answer=""
    else
        printf '\n' >&4
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
        printf '\n' >/dev/tty
        printf '%s: ' "${label}" >/dev/tty
        IFS= read -r -s answer </dev/tty || answer=""
        printf '\n' >/dev/tty
    else
        printf '\n' >&4
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

write_dind_instance_files() {
    local domain="${1:-}"
    local prompt="${2:-0}"
    local app_image="${IMAGE:-codxjuniorai/codx-junior:latest}"
    local dind_image="${DIND_IMAGE:-codxjuniorai/codx-junior:dind}"
    local storage="${CODX_DIND_STORAGE:-${INSTANCE}-dind}"
    local compose="${HOME_DIR}/docker-compose.yaml"
    local envfile="${ENV_FILE}"
    local env_ref="./.env"
    local rule="Host(\`${domain:-localhost}\`)"
    local debug="${CODX_JUNIOR_DEBUG:-$([[ "${DEV_MODE}" == "1" ]] && echo 1 || echo 0)}"
    local overwrite=0 write_env=0 write_compose=0 write_state=0

    if [[ "${envfile}" != "${HOME_DIR}/.env" ]]; then
        env_ref="${envfile}"
    fi

    mkdir -p "${HOME_DIR}" "${DATA_DIR}" "${PROJECTS}" "$(dirname "${envfile}")"

    if [[ -f "${envfile}" || -f "${compose}" || -f "${STATE_FILE}" ]]; then
        if [[ "${FORCE}" == "1" ]]; then
            overwrite=1
        elif [[ "${prompt}" == "1" ]]; then
            log "DIND instance files already exist:"
            log "  ${compose}"
            log "  ${envfile}"
            log "  ${STATE_FILE}"
            if [[ "$(ask_yesno "Overwrite them with the current selections?" 0)" == "1" ]]; then
                overwrite=1
            fi
        fi
    fi

    if [[ "${overwrite}" == "1" ]]; then
        write_env=1
        write_compose=1
        write_state=1
        rm -f "${envfile}" "${compose}" "${STATE_FILE}"
    else
        [[ -f "${envfile}" ]] || write_env=1
        [[ -f "${compose}" ]] || write_compose=1
        [[ -f "${STATE_FILE}" ]] || write_state=1
        if [[ "${write_env}" == "0" && "${write_compose}" == "0" && "${write_state}" == "0" ]]; then
            log "Keeping existing DIND instance files"
            return 0
        fi
    fi

    if [[ "${write_env}" == "1" ]]; then
        {
            printf '# codx-junior DIND instance settings\n'
            printf '# Edit before continuing. Unknown keys are preserved.\n'
        } > "${envfile}"
        chmod 600 "${envfile}" 2>/dev/null || true
        _dind_env() {
            local v="${2//\$/\$\$}"
            printf '%s=%s\n' "$1" "${v}" >> "${envfile}"
        }
        _dind_env CODX_JUNIOR_MODE dind
        _dind_env CODX_DIND_MODE server
        _dind_env CODX_JUNIOR_DEV "${DEV_MODE}"
        _dind_env CODX_JUNIOR_DEBUG "${debug}"
        _dind_env CODX_JUNIOR_DOMAIN "${domain}"
        _dind_env CODX_JUNIOR_TRAEFIK "${TRAEFIK_MODE}"
        _dind_env CODX_JUNIOR_INSTANCE "${INSTANCE}"
        _dind_env CODX_JUNIOR_HOME "${HOME_DIR}"
        _dind_env CODX_DATA_DIR "${DATA_DIR}"
        _dind_env CODX_JUNIOR_PROJECTS_PATH "${PROJECTS}"
        _dind_env CODX_JUNIOR_ENV_FILE "${envfile}"
        _dind_env CODX_JUNIOR_WEB_PORT "${WEB_PORT}"
        _dind_env CODX_BIND_ADDRESS "${BIND_ADDRESS}"
        _dind_env CODX_NO_TLS 0
        _dind_env CODX_PROXY_NETWORK "${PROXY_NETWORK}"
        _dind_env CODX_TRUSTED_PROXIES "${TRUSTED_PROXIES}"
        _dind_env CODX_FORWARDED_INSECURE "${FORWARDED_INSECURE}"
        _dind_env CODX_MASTER_CERT_RESOLVER "${MASTER_CERT_RESOLVER}"
        _dind_env CODX_MASTER_ENTRYPOINT "${MASTER_ENTRYPOINT}"
        _dind_env CODX_ACME_EMAIL "${ACME_EMAIL}"
        _dind_env CODX_ACME_CA "${ACME_CA}"
        _dind_env CODX_CERT_RESOLVER "${CERT_RESOLVER}"
        _dind_env CODX_JUNIOR_AI_PROVIDER "${PROVIDER}"
        _dind_env CODX_JUNIOR_AI_API_KEY "${API_KEY}"
        _dind_env CODX_JUNIOR_AI_API_KEY_FILE "${API_KEY_FILE}"
        _dind_env CODX_JUNIOR_AI_API_URL "${API_URL}"
        _dind_env CODX_JUNIOR_AI_MODEL "${MODEL}"
        _dind_env CODX_JUNIOR_NO_AI "${NO_AI}"
        _dind_env CODX_JUNIOR_COWORKING 1
        _dind_env CODX_JUNIOR_HIDDEN "${HIDDEN}"
        _dind_env CODX_JUNIOR_ACTIVE_FEATURES "${ACTIVE_FEATURES}"
        _dind_env CODX_DIND_SYSBOX "${SYSBOX}"
        _dind_env CODX_JUNIOR_KNOWLEDGE_BACKEND "${KNOWLEDGE_BACKEND}"
        _dind_env CODX_JUNIOR_IMAGE "${app_image}"
        _dind_env CODX_DIND_IMAGE "${dind_image}"
        _dind_env CODX_DIND_STORAGE "${storage}"
        unset -f _dind_env
        log "Created ${envfile}"
    else
        log "Keeping existing ${envfile}"
    fi

    if [[ "${write_state}" == "1" ]]; then
        if [[ "${STATE_FILE}" != "${envfile}" ]]; then
            cp "${envfile}" "${STATE_FILE}"
            chmod 600 "${STATE_FILE}" 2>/dev/null || true
        fi
        if ! grep -q '^CODX_JUNIOR_INSTALLER_STATE_VERSION=' "${STATE_FILE}" 2>/dev/null; then
            printf 'CODX_JUNIOR_INSTALLER_STATE_VERSION=1\n' >> "${STATE_FILE}"
        fi
        log "Created ${STATE_FILE}"
    else
        log "Keeping existing ${STATE_FILE}"
    fi

    if [[ "${write_compose}" == "0" ]]; then
        log "Keeping existing ${compose}"
        return 0
    fi

    {
        cat <<EOF
# =============================================================================
# codx-junior DIND instance — editable compose
#
# Generated before the install confirmation. Add folder mappings under
# services.dind.volumes, then continue the installer.
#
# Prefer mappings under /data/projects/<name> so the inner app sees them as
# projects. A path under /data that is not mounted into the inner container is
# only visible to the DIND wrapper.
# =============================================================================
name: ${INSTANCE}

services:
  dind:
    image: ${dind_image}
    container_name: ${INSTANCE}
EOF
        if [[ "${SYSBOX}" == "1" ]]; then
            cat <<EOF
    privileged: false
    runtime: sysbox-runc
EOF
        else
            cat <<EOF
    privileged: true
EOF
        fi
        cat <<EOF
    restart: unless-stopped
    stop_grace_period: 45s
    env_file:
      - "${env_ref}"
    environment:
      CODX_MODE: "server"
      CODX_TRAEFIK_MODE: "${TRAEFIK_MODE}"
      CODX_JUNIOR_DOMAIN: "${domain:-localhost}"
      CODX_JUNIOR_WEB_PORT: "${WEB_PORT}"
      CODX_JUNIOR_IMAGE: "${app_image}"
      CODX_JUNIOR_PROJECTS_PATH: /data/projects
      CODX_DATA_DIR: /data
      CODX_PROXY_NETWORK: codx-proxy
      CODX_TRUSTED_PROXIES: "${TRUSTED_PROXIES}"
      CODX_ACME_EMAIL: "${ACME_EMAIL}"
      CODX_ACME_CA: "${ACME_CA}"
      CODX_CERT_RESOLVER: "${CERT_RESOLVER}"
      CODX_JUNIOR_KNOWLEDGE_BACKEND: "${KNOWLEDGE_BACKEND}"
      CODX_JUNIOR_COWORKING: "1"
      CODX_JUNIOR_DEBUG: "${debug}"
      CODX_JUNIOR_DEV: "0"
      CODX_HEALTH_PORT: "80"
      DOCKER_TLS_CERTDIR: ""
      TINI_SUBREAPER: "1"
    volumes:
      - "${DATA_DIR}:/data"
      - "${PROJECTS}:/data/projects"
      - "dind-storage:/var/lib/docker"
      # Add host project folders here, for example:
      # - "/srv/other-project:/data/projects/other-project"
EOF
        if [[ "${TRAEFIK_MODE}" == "master" ]]; then
            cat <<EOF
    ports:
      - "80:80"
      - "443:443"
EOF
        else
            cat <<EOF
    networks:
      - proxy
    labels:
      - "traefik.enable=true"
      - "traefik.docker.network=${PROXY_NETWORK}"
      - "traefik.http.routers.${INSTANCE}-edge.rule=${rule}"
      - "traefik.http.routers.${INSTANCE}-edge.entrypoints=${MASTER_ENTRYPOINT:-websecure}"
      - "traefik.http.routers.${INSTANCE}-edge.tls=true"
      - "traefik.http.routers.${INSTANCE}-edge.tls.certresolver=${MASTER_CERT_RESOLVER:-letsencrypt}"
      - "traefik.http.routers.${INSTANCE}-edge.service=${INSTANCE}-edge"
      - "traefik.http.services.${INSTANCE}-edge.loadbalancer.server.port=80"
EOF
        fi
        cat <<EOF
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS -o /dev/null -H \"Host: \$\$CODX_JUNIOR_DOMAIN\" http://127.0.0.1:\$\$CODX_HEALTH_PORT/api/health"]
      interval: 30s
      timeout: 10s
      retries: 10
      start_period: 900s

volumes:
  dind-storage:
    name: ${storage}
EOF
        if [[ "${TRAEFIK_MODE}" != "master" ]]; then
            cat <<EOF

networks:
  proxy:
    name: ${PROXY_NETWORK}
    external: true
EOF
        fi
    } > "${compose}"
    chmod 600 "${compose}" 2>/dev/null || true
    log "Created ${compose}"
    log "Edit ${compose} now if you need extra folder mappings, then continue."
}

interactive_wizard() {
    print_banner
    section "Interactive setup"
    printf '  Press Enter to accept the value in [brackets].\n' >&4

    local original_mode="${MODE:-}"
    local original_instance="${INSTANCE:-}"
    local original_home="${HOME_DIR:-}"
    local mode_changed=0 instance_changed=0
    local mode_default="${MODE:-}"

    local target_default="${PWD}"
    if [[ "${HOME_DIR_EXPLICIT}" == "1" && -n "${HOME_DIR}" ]]; then
        target_default="${HOME_DIR}"
    fi
    local target=""
    target="$(ask "Target folder (install files, .env, data/projects)" "${target_default}")"
    [[ "${target}" = /* ]] || target="${PWD}/${target}"
    HOME_DIR="${target}"
    HOME_DIR_EXPLICIT=1
    if [[ -z "${CODX_JUNIOR_INSTALLER_STATE:-}" ]]; then
        STATE_FILE="${HOME_DIR}/installation.env"
    fi
    if [[ "${DATA_DIR_EXPLICIT}" != "1" ]]; then
        DATA_DIR="${HOME_DIR}/data"
    fi
    case "${mode_default}" in
        local|server|dind) ;;
        dev) mode_default="local" ;;
        slave) mode_default="server" ;;
        *) if [[ "${EUID}" -eq 0 ]]; then mode_default="server"; else mode_default="local"; fi ;;
    esac
    section "Deployment"
    cat >&4 <<'MODES'
  local    Full machine dedicated to codx-junior; localhost by default.
  server   Public server; domain + Traefik primary for HTTPS.
  dind     Isolate codx-junior in a Docker-in-Docker container.
MODES
    MODE="$(ask_choice "Deployment mode" "local server dind" "${mode_default}")"
    MODE_CLI=1

    if [[ "${MODE}" == "dind" ]]; then
        DIND_MODE="server"
    fi
    local effective="${MODE}"
    if [[ "${MODE}" == "dind" ]]; then
        effective="${DIND_MODE}"
    fi

    # `dev` is an option for host installs, not a deployment mode.
    section "Development option"
    if [[ "${MODE}" == "dind" ]]; then
        cat >&4 <<'DINDDEVMODE'
  DEBUG=1 for the inner scripts. The inner image is packaged, so there is no
  live checkout mount or Vite HMR inside DIND.
DINDDEVMODE
        DEV_MODE="$(ask_yesno "Run in development mode (DEBUG=1 inside DIND)" "${DEV_MODE:-0}")"
    else
        cat >&4 <<'DEVMODE'
  DEBUG=1 plus the live checkout, uvicorn --reload and Vite HMR.
DEVMODE
        DEV_MODE="$(ask_yesno "Run in development mode" "${DEV_MODE:-0}")"
    fi

    # The domain decides whether Traefik is needed at all. A blank domain is
    # localhost/plain HTTP; a domain must be resolved by a primary or a slave.
    resolve_traefik_mode
    local domain_default="${DOMAINS[0]:-}"
    if [[ "${domain_default}" == "localhost" ]]; then
        domain_default=""
    fi
    section "Domain and Traefik"
    cat >&4 <<'DOMAIN'
  A domain enables DNS and Traefik. Leave it blank for localhost and plain HTTP
  on the published port.
DOMAIN
    local domain="" required_domain=0
    if [[ "${MODE}" == "server" || "${MODE}" == "dind" ]]; then
        required_domain=1
    fi
    while true; do
        if [[ "${required_domain}" == "1" ]]; then
            domain="$(ask "Domain name (required for ${MODE}$([[ "${MODE}" == "dind" ]] && echo "/${DIND_MODE}"))" "${domain_default}")"
        else
            domain="$(ask "Domain name (optional)" "${domain_default}")"
        fi
        if [[ -z "${domain}" && "${required_domain}" == "0" ]]; then
            break
        fi
        if [[ -n "${domain}" && "${domain}" =~ ^[A-Za-z0-9.-]+$ ]]; then
            break
        fi
        if [[ "${required_domain}" == "1" ]]; then
            warn "a valid domain is required for ${MODE}$([[ "${MODE}" == "dind" ]] && echo "/${DIND_MODE}")"
        else
            warn "enter a valid domain, or leave it blank for localhost"
        fi
    done

    if [[ -z "${domain}" ]]; then
        TRAEFIK_MODE="none"
        NO_TLS=1
        log "No domain: Traefik disabled; using localhost / plain HTTP"
    else
        cat >&4 <<'TRAEFIK'
  primary  Resolve DNS and terminate TLS for this domain (ACME).
  slave    Sit behind an existing primary; receive X-Forwarded-* headers.
TRAEFIK
        local tr_default="primary"
        [[ "${TRAEFIK_MODE}" == "slave" ]] && tr_default="slave"
        while true; do
            local tr_choice=""
            tr_choice="$(ask_choice "Traefik mode" "primary slave" "${tr_default}")"
            case "${tr_choice}" in
                primary) TRAEFIK_MODE="master"; break ;;
                slave)   TRAEFIK_MODE="slave";  break ;;
            esac
        done
        NO_TLS=0
    fi

    if [[ "${TRAEFIK_MODE}" == "master" ]]; then
        while true; do
            local acme=""
            acme="$(ask "ACME email" "${ACME_EMAIL}")"
            if [[ -n "${acme}" ]]; then
                ACME_EMAIL="${acme}"
                break
            fi
            warn "the primary needs an ACME email"
        done
    fi

    if [[ "${TRAEFIK_MODE}" == "slave" ]]; then
        section "Traefik connection"
        local net_default="${PROXY_NETWORK:-traefik}"
        [[ "${net_default}" == "codx-proxy" ]] && net_default="traefik"
        PROXY_NETWORK="$(ask "Primary Traefik Docker network" "${net_default}")"
        MASTER_CERT_RESOLVER="$(ask "Primary certificate resolver" "${MASTER_CERT_RESOLVER:-letsencrypt}")"
        MASTER_ENTRYPOINT="$(ask "Primary HTTPS entrypoint" "${MASTER_ENTRYPOINT:-websecure}")"
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

    section "Instance and folders"
    local instance_default="${INSTANCE:-codx-junior}"
    if [[ -n "${domain}" && "${domain}" != "localhost" \
          && ( "${MODE}" == "dind" || "${MODE}" == "slave" || "${TRAEFIK_MODE}" == "slave" ) ]]; then
        instance_default="$(slugify "${domain}")"
    fi
    while true; do
        INSTANCE="$(ask "Instance name" "${instance_default}")"
        if [[ "${INSTANCE}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
            break
        fi
        warn "use lowercase letters, digits and '-' (max 32 chars)"
    done
    [[ "${MODE}" != "${original_mode}" ]] && mode_changed=1
    [[ "${INSTANCE}" != "${original_instance}" ]] && instance_changed=1
    if [[ "${instance_changed}" == "1" ]]; then
        CODX_DIND_STORAGE="${INSTANCE}-dind"
    fi

    # The target folder was chosen first. Data and projects default under it;
    # only explicit CLI/environment paths override them.
    if [[ "${DATA_DIR_EXPLICIT}" != "1" ]]; then
        DATA_DIR="${HOME_DIR}/data"
    fi

    local projects_default="${PROJECTS}"
    local projects_explicit=0
    [[ "${PROJECTS_CLI}" == "1" ]] && projects_explicit=1
    [[ -n "${CODX_JUNIOR_PROJECTS_PATH:-}" && "${PROJECTS_FROM_STATE:-0}" != "1" ]] && projects_explicit=1
    if [[ "${projects_explicit}" != "1" ]]; then
        case "${MODE}" in
            dind) projects_default="${DATA_DIR}/projects" ;;
            *)    projects_default="${HOME_DIR}/projects" ;;
        esac
    fi
    PROJECTS="$(ask "Projects folder" "${projects_default}")"
    PROJECTS_CLI=1

    section "Project selector"
    cat >&4 <<'HIDDENHELP'
  Hide codx-junior and its child projects from the project selector. Set this
  if you only want your own application projects in the UI.
HIDDENHELP
    HIDDEN="$(ask_yesno "Hide codx-junior and child projects" "${HIDDEN:-0}")"

    while true; do
        WEB_PORT="$(ask "Host web port" "${WEB_PORT}")"
        if [[ "${WEB_PORT}" =~ ^[0-9]+$ ]] && (( WEB_PORT >= 1 && WEB_PORT <= 65535 )); then
            break
        fi
        warn "enter a port between 1 and 65535"
    done

    section "AI provider"
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
    if [[ "${TRAEFIK_MODE}" != "none" && -n "${domain}" ]]; then
        DOMAINS=("${domain}")
    fi
    if [[ "${MODE}" == "dind" ]]; then
        # Never inherit another instance's .env. Only an explicit --env-file
        # may override the target-local .env.
        [[ -n "${PRESCAN_ENV:-}" ]] || ENV_FILE="${HOME_DIR}/.env"
    elif [[ "${ENV_FILE_EXPLICIT}" != "1" ]]; then
        ENV_FILE="${HOME_DIR}/config/${INSTANCE}.env"
    fi
    if [[ "${NO_TLS}" == "1" ]]; then
        NO_TLS_CLI=1
    fi

    printf '\n' >&2
    section "Configuration"
    row "mode" "${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)"
    row "dev" "$([[ "${DEV_MODE}" == "1" ]] && { [[ "${MODE}" == "dind" ]] && echo "enabled (DEBUG=1 inside DIND)" || echo "enabled (DEBUG=1, live checkout)"; } || echo "disabled")"
    row "traefik" "$(traefik_label)"
    row "domain" "${domain:-<none>}"
    row "instance" "${INSTANCE}"
    row "target" "${HOME_DIR}"
    row "projects" "${PROJECTS}"
    row "port" "${WEB_PORT}"
    row "provider" "${PROVIDER}$([[ "${NO_AI}" == "1" ]] && echo " (configure in UI)" || true)"
    row "state file" "${STATE_FILE}"
    if [[ "${MODE}" == "dind" ]]; then
        row "compose" "${HOME_DIR}/docker-compose.yaml"
        row "env file" "${ENV_FILE}"
    fi
    row "coworking" "${COWORKING}"
    row "features" "$(features_label)"
    row "hidden" "${HIDDEN}"
    if [[ "${MODE}" == "dind" ]]; then
        row "sysbox" "${SYSBOX}"
    fi
    if [[ "${TRAEFIK_MODE}" == "slave" ]]; then
        row "proxy" "${PROXY_NETWORK} (${MASTER_ENTRYPOINT}, resolver ${MASTER_CERT_RESOLVER})"
    fi

    if [[ "${MODE}" == "dind" && "${DRY_RUN}" != "1" ]]; then
        section "Instance files"
        # Generate/refresh the editable files at the end, after the summary and
        # before the final confirmation. Existing files prompt for overwrite.
        [[ "${HOME_DIR}" = /* ]] || HOME_DIR="${PWD}/${HOME_DIR}"
        [[ "${DATA_DIR}" = /* ]] || DATA_DIR="${PWD}/${DATA_DIR}"
        [[ "${PROJECTS}" = /* ]] || PROJECTS="${PWD}/${PROJECTS}"
        [[ "${ENV_FILE}" = /* ]] || ENV_FILE="${PWD}/${ENV_FILE}"
        write_dind_instance_files "${domain}" 1
    fi

    section "Confirm"
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
PRESCAN_DEV=0
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
        --dev)           PRESCAN_DEV=1 ;;
        --no-dev)        PRESCAN_DEV=0 ;;
    esac
done
[[ "${PRESCAN_MODE}" == "dev" ]] && PRESCAN_DEV=1

slugify() {
    local value="${1:-}"
    value="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
    value="$(printf '%s' "${value}" | sed -E 's#^[a-z]+://##; s#/.*$##; s#[^a-z0-9]+#-#g; s#^-+##; s#-+$##')"
    printf '%s' "${value:0:32}"
}

MODE_HINT="${PRESCAN_MODE:-${CODX_JUNIOR_MODE:-${INSTALLER_STATE[CODX_JUNIOR_MODE]:-}}}"
if [[ "${MODE_HINT}" == "dev" ]]; then
    MODE_HINT="local"
    PRESCAN_DEV=1
fi
DEV_HINT="${PRESCAN_DEV:-${CODX_JUNIOR_DEV:-${INSTALLER_STATE[CODX_JUNIOR_DEV]:-0}}}"
if [[ -z "${MODE_HINT}" ]]; then
    if [[ "${DEV_HINT}" == "1" ]]; then
        MODE_HINT="local"
    elif [[ "${EUID}" -eq 0 ]]; then
        MODE_HINT="server"
    else
        MODE_HINT="local"
    fi
fi
DOMAIN_HINT="${PRESCAN_DOMAIN:-${CODX_JUNIOR_DOMAIN:-${INSTALLER_STATE[CODX_JUNIOR_DOMAIN]:-}}}"
INSTANCE_HINT="${PRESCAN_INSTANCE:-${CODX_JUNIOR_INSTANCE:-${INSTALLER_STATE[CODX_JUNIOR_INSTANCE]:-}}}"
if [[ -z "${INSTANCE_HINT}" ]]; then
    case "${MODE_HINT}" in
        dind|slave) INSTANCE_HINT="$(slugify "${DOMAIN_HINT}")" ;;
    esac
    [[ -n "${INSTANCE_HINT}" ]] || INSTANCE_HINT="codx-junior"
fi
HOME_HINT="${PRESCAN_HOME:-${CODX_JUNIOR_HOME:-${INSTALLER_STATE[CODX_JUNIOR_HOME]:-}}}"
if [[ -z "${HOME_HINT}" ]]; then
    case "${MODE_HINT}" in
        local) HOME_HINT="${HOME}/.codx-junior" ;;
        dind)  HOME_HINT="/opt/codx-junior/instances/${INSTANCE_HINT}" ;;
        *)     HOME_HINT="/opt/codx-junior" ;;
    esac
fi
ENV_FILE="${PRESCAN_ENV:-${CODX_JUNIOR_ENV_FILE:-${INSTALLER_STATE[CODX_JUNIOR_ENV_FILE]:-${HOME_HINT}/config/${INSTANCE_HINT}.env}}}"

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

# The saved installer state is the fallback for anything the settings file and
# the environment did not provide. CLI flags are parsed later and still win.
HOME_FROM_STATE=0
DATA_DIR_FROM_STATE=0
PROJECTS_FROM_STATE=0
ENV_FILE_FROM_STATE=0
DIND_STORAGE_FROM_STATE=0
STATE_INSTANCE="${INSTALLER_STATE[CODX_JUNIOR_INSTANCE]:-}"
if (( ${#INSTALLER_STATE[@]} > 0 )); then
    for _key in "${!INSTALLER_STATE[@]}"; do
        if [[ -z "${!_key:-}" ]]; then
            case "${_key}" in
                CODX_JUNIOR_HOME)           HOME_FROM_STATE=1 ;;
                CODX_DATA_DIR)              DATA_DIR_FROM_STATE=1 ;;
                CODX_JUNIOR_PROJECTS_PATH)  PROJECTS_FROM_STATE=1 ;;
                CODX_JUNIOR_ENV_FILE)       ENV_FILE_FROM_STATE=1 ;;
                CODX_DIND_STORAGE)          DIND_STORAGE_FROM_STATE=1 ;;
            esac
            export "${_key}=${INSTALLER_STATE[$_key]}"
        fi
    done
    unset _key
fi

# --- configuration (env file + environment; flags below override) -----------
MODE="${CODX_JUNIOR_MODE:-${MODE_HINT}}"
DIND_MODE="${CODX_DIND_MODE:-server}"
DIND_MODE_CLI=0
INSTANCE="${CODX_JUNIOR_INSTANCE:-${INSTANCE_HINT}}"
HOME_DIR="${CODX_JUNIOR_HOME:-${HOME_HINT}}"
DATA_DIR="${CODX_DATA_DIR:-${HOME_DIR}/data}"
PROJECTS="${CODX_JUNIOR_PROJECTS_PATH:-${HOME_DIR}/projects}"
PROJECTS_CLI=0
HOME_DIR_EXPLICIT=0
DATA_DIR_EXPLICIT=0
ENV_FILE_EXPLICIT=0
if [[ -n "${PRESCAN_HOME:-}" ]] \
   || [[ -n "${CODX_JUNIOR_HOME:-}" && "${HOME_FROM_STATE:-0}" != "1" ]]; then
    HOME_DIR_EXPLICIT=1
fi
if [[ -n "${CODX_DATA_DIR:-}" && "${DATA_DIR_FROM_STATE:-0}" != "1" ]]; then
    DATA_DIR_EXPLICIT=1
fi
if [[ -n "${PRESCAN_ENV:-}" ]] \
   || [[ -n "${CODX_JUNIOR_ENV_FILE:-}" && "${ENV_FILE_FROM_STATE:-0}" != "1" ]]; then
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
TRAEFIK_MODE="${CODX_JUNIOR_TRAEFIK:-}"
DEV_MODE="${CODX_JUNIOR_DEV:-0}"
IMAGE_CLI=0
CODX_SOURCE_DIR="${CODX_JUNIOR_SOURCE:-}"
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
DELETE_FILES=0
DELETE_FILES_EXPLICIT=0
CONFIRM_DELETE=0
KNOWLEDGE_BACKEND="${CODX_JUNIOR_KNOWLEDGE_BACKEND:-lancedb}"
ACTIVE_FEATURES="${CODX_JUNIOR_ACTIVE_FEATURES:-core}"
ACTIVE_PROFILE="core"
ACTIVE_FEATURE_LIST="${CORE_FEATURE_IDS}"

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
        --dind-mode)            need "$@"; DIND_MODE="$2"; DIND_MODE_CLI=1; shift 2 ;;
        --dind-mode=*)          DIND_MODE="${1#*=}"; DIND_MODE_CLI=1; shift ;;
        --traefik|--proxy)      need "$@"; TRAEFIK_MODE="$2"; shift 2 ;;
        --traefik=*|--proxy=*)  TRAEFIK_MODE="${1#*=}"; shift ;;
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
        --data-dir)             need "$@"; DATA_DIR="$2"; DATA_DIR_EXPLICIT=1; shift 2 ;;
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
                                    *) die "only '--tls none' is supported; use --traefik slave behind an existing primary" ;;
                                esac
                                shift 2 ;;
        --no-docker-install)    INSTALL_DOCKER=0; shift ;;
        --open-firewall)        OPEN_FIREWALL=1; shift ;;
        --update)               UPGRADE=1; shift ;;
        --delete-files)         DELETE_FILES=1; DELETE_FILES_EXPLICIT=1; shift ;;
        --confirm-delete)       CONFIRM_DELETE=1; shift ;;
        --hidden)               HIDDEN=1; shift ;;
        --no-hidden|--show)     HIDDEN=0; shift ;;
        --features)             need "$@"; ACTIVE_FEATURES="$2"; shift 2 ;;
        --features=*)           ACTIVE_FEATURES="${1#*=}"; shift ;;
        --full-featured|--all-features|--full)
                                ACTIVE_FEATURES="all"; shift ;;
        --core|--no-full-featured)
                                ACTIVE_FEATURES="core"; shift ;;
        --coworking)            COWORKING=1; shift ;;
        --no-coworking)         COWORKING=0; shift ;;
        --allow-privileged)     ALLOW_PRIVILEGED=1; shift ;;
        --build)                DO_BUILD=1; shift ;;
        --build-dind)           DO_BUILD_DIND=1; shift ;;
        --image)                need "$@"; IMAGE="$2"; IMAGE_CLI=1; shift 2 ;;
        --image=*)              IMAGE="${1#*=}"; IMAGE_CLI=1; shift ;;
        --dind-image)           need "$@"; DIND_IMAGE="$2"; shift 2 ;;
        --dind-image=*)         DIND_IMAGE="${1#*=}"; shift ;;
        --sysbox)               SYSBOX=1; shift ;;
        --systemd)              SYSTEMD=1; shift ;;
        --no-cli)               CLI=0; shift ;;
        --no-systemd)           SYSTEMD=0; shift ;;
        --no-wait)              NO_WAIT=1; shift ;;
        --skip-tls-wait)        SKIP_TLS_WAIT=1; shift ;;
        --no-pull)              NO_PULL=1; shift ;;
        --dry-run)              DRY_RUN=1; shift ;;
        --force)                FORCE=1; shift ;;
        --container)            CONTAINER=1; shift ;;
        --interactive)          INTERACTIVE=1; shift ;;
        --dev)                  DEV_MODE=1; shift ;;
        --no-dev)               DEV_MODE=0; shift ;;
        --non-interactive|--yes|-y) NON_INTERACTIVE=1; shift ;;
        --ref|--branch|-b)      need "$@"; REF="$2"; shift 2 ;;
        --repo)                 need "$@"; REPO="$2"; shift 2 ;;
        -h|--help)              usage; exit 0 ;;
        -V|--version)           printf 'codx-junior installer %s\n' "${INSTALLER_VERSION}"; exit 0 ;;
        install|update|remove|status|logs|down|doctor|config|url|env|version|up|restart)
                                COMMAND="$1"; shift ;;
        "")                     shift ;;
        *)                      die "unknown option or command: $1 (see --help)" ;;
    esac
done

# --- feature profile --------------------------------------------------------
# Resolve and validate the install-time feature set. The app reads the same
# values from CODX_JUNIOR_ACTIVE_FEATURES; the installer never writes a typo
# that the runtime would have to guess about.
resolve_features() {
    local value="${ACTIVE_FEATURES:-core}"
    value="$(printf '%s' "${value}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
    [[ -n "${value}" ]] || value="core"

    case "${value}" in
        all)
            ACTIVE_PROFILE="all"
            ACTIVE_FEATURE_LIST="${ALL_FEATURE_IDS}" ;;
        core)
            ACTIVE_PROFILE="core"
            ACTIVE_FEATURE_LIST="${CORE_FEATURE_IDS}" ;;
        *)
            local -a requested=()
            local -a unknown=()
            local id
            local -a resolved=()
            local expanded_core=0 expanded_all=0
            IFS=',' read -r -a requested <<< "${value}"
            for id in "${requested[@]}"; do
                [[ -n "${id}" ]] || continue
                case "${id}" in
                    core)
                        expanded_core=1
                        local core_id
                        for core_id in ${CORE_FEATURE_IDS}; do
                            [[ " ${resolved[*]} " == *" ${core_id} "* ]] || resolved+=("${core_id}")
                        done
                        continue ;;
                    all)
                        expanded_all=1
                        local all_id
                        for all_id in ${ALL_FEATURE_IDS}; do
                            [[ " ${resolved[*]} " == *" ${all_id} "* ]] || resolved+=("${all_id}")
                        done
                        continue ;;
                esac
                if [[ " ${ALL_FEATURE_IDS} " != *" ${id} "* ]]; then
                    unknown+=("${id}")
                    continue
                fi
                [[ " ${resolved[*]} " == *" ${id} "* ]] || resolved+=("${id}")
            done
            if (( ${#unknown[@]} > 0 )); then
                die "invalid --features '${value}': unknown feature '$(IFS=','; printf '%s' "${unknown[*]}")' (valid profiles: core, all; valid ids: $(printf '%s' "${ALL_FEATURE_IDS}" | tr ' ' ','))"
            fi
            (( ${#resolved[@]} > 0 )) || die "invalid --features '${value}': the set is empty"
            ACTIVE_FEATURE_LIST="${resolved[0]}"
            for id in "${resolved[@]:1}"; do
                ACTIVE_FEATURE_LIST="${ACTIVE_FEATURE_LIST},${id}"
            done
            # Keep the flag the user typed (so config shows `core,mcp`), but
            # label a single expanded profile as that profile.
            if (( ${#requested[@]} == 1 )); then
                if [[ "${expanded_core}" == "1" ]]; then ACTIVE_PROFILE="core"
                elif [[ "${expanded_all}" == "1" ]]; then ACTIVE_PROFILE="all"
                else ACTIVE_PROFILE="custom"; fi
            else
                ACTIVE_PROFILE="custom"
            fi
            ACTIVE_FEATURES="${value}" ;;
    esac
}

# --- Traefik setup ----------------------------------------------------------
traefik_label() {
    if [[ "${TRAEFIK_MODE}" == "master" ]]; then
        printf 'primary'
    else
        printf '%s' "${TRAEFIK_MODE}"
    fi
}

features_label() {
    case "${ACTIVE_PROFILE}" in
        core)   printf 'core (workspace mode; company hidden)' ;;
        all)    printf 'all (full featured)' ;;
        *)      printf 'custom: %s' "${ACTIVE_FEATURES}" ;;
    esac
}
# MODE answers "where does the app run"; TRAEFIK_MODE answers "which proxy
# wraps it". Keeping them separate is what makes dind + local + slave possible.
resolve_traefik_mode() {
    [[ -n "${TRAEFIK_MODE}" ]] && return 0
    case "${MODE}" in
        local)
            TRAEFIK_MODE="none" ;;
        server)
            if [[ "${NO_TLS}" == "1" ]]; then TRAEFIK_MODE="none"; else TRAEFIK_MODE="master"; fi ;;
        slave)
            TRAEFIK_MODE="slave" ;;
        dind)
            TRAEFIK_MODE="master" ;;
    esac
}

# --- defaults after parsing -------------------------------------------------
[[ "${TRAEFIK_MODE}" == "primary" ]] && TRAEFIK_MODE="master"
# `dev` is an option, not a deployment mode. Keep the old spelling working.
if [[ "${MODE}" == "dev" ]]; then
    MODE="local"
    DEV_MODE=1
fi
case "${MODE}" in local|server|slave|dind) ;; *) die "invalid --mode '${MODE}' (local|server|slave|dind)" ;; esac
# `remove` must still work when an old env file holds a stale/unknown id.
[[ "${COMMAND}" == "remove" ]] || resolve_features
# DIND needs the nested Docker socket for coworking by design.
[[ "${MODE}" == "dind" ]] && COWORKING=1
if [[ "${COMMAND}" != "remove" && "${MODE}" == "dind" && "${DIND_MODE}" != "server" ]]; then
    if [[ "${DIND_MODE_CLI}" == "1" ]]; then
        die "the DIND inner installation is always server; --dind-mode is no longer needed"
    fi
    warn "ignoring saved --dind-mode '${DIND_MODE}': the DIND inner installation is always server"
    DIND_MODE="server"
fi
resolve_traefik_mode
case "${TRAEFIK_MODE}" in none|master|slave) ;; *) die "invalid --traefik '${TRAEFIK_MODE}' (none|master|slave)" ;; esac
EFFECTIVE_MODE="${MODE}"
if [[ "${MODE}" == "dind" ]]; then
    EFFECTIVE_MODE="${DIND_MODE}"
fi
# Backwards compatibility: `curl ... | sudo bash` with no mode/domain used to
# serve plain HTTP on the published port. Keep that default, but make it loud.
if [[ "${MODE}" == "server" && "${MODE_CLI}" == "0" && "${NO_TLS}" == "0" && "${NO_TLS_CLI}" == "0" && "${#DOMAINS[@]}" -eq 0 ]]; then
    NO_TLS=1
    warn "no --domain given: serving plain HTTP on port ${WEB_PORT} (use --domain for TLS)"
fi
[[ "${INSTANCE}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || die "invalid --instance '${INSTANCE}' (lowercase letters, digits and '-')"
# A default DIND storage volume is tied to the instance name. Do not reuse the
# previous instance's nested Docker metadata after an instance rename.
if [[ -n "${CODX_DIND_STORAGE:-}" && "${CODX_DIND_STORAGE}" == *-dind \
      && "${CODX_DIND_STORAGE}" != "${INSTANCE}-dind" ]]; then
    warn "using ${INSTANCE}-dind instead of the old nested storage volume ${CODX_DIND_STORAGE}"
    CODX_DIND_STORAGE="${INSTANCE}-dind"
fi
[[ "${WEB_PORT}" =~ ^[0-9]+$ ]] && ((WEB_PORT >= 1 && WEB_PORT <= 65535)) || die "invalid --port '${WEB_PORT}'"
[[ "${REF}" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid --ref '${REF}'"

if [[ "${COMMAND}" == "update" ]]; then UPGRADE=1; fi

# No arguments on a terminal: run the guided setup. --interactive forces it,
# --non-interactive (or --yes) always wins.
if [[ "${INTERACTIVE}" == "1" && "${NON_INTERACTIVE}" == "1" ]]; then
    die "--interactive and --non-interactive cannot be combined"
fi
if [[ "${INTERACTIVE}" != "1" && "${NON_INTERACTIVE}" != "1" \
      && "${ORIGINAL_ARGC}" -eq 0 && "${WIZARD_TTY}" == "1" ]]; then
    INTERACTIVE=1
fi
if [[ "${INTERACTIVE}" == "1" && "${COMMAND}" != "remove" ]]; then
    interactive_wizard
    EFFECTIVE_MODE="${MODE}"
    if [[ "${MODE}" == "dind" ]]; then
        EFFECTIVE_MODE="${DIND_MODE}"
        COWORKING=1
    fi
    resolve_traefik_mode
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
[[ -n "${CODX_JUNIOR_INSTALLER_STATE:-}" ]] || STATE_FILE="${HOME_DIR}/installation.env"
DATA_DIR="$(abs_dir "${DATA_DIR}")"
PROJECTS="$(abs_dir "${PROJECTS}")"
ENV_FILE="$(abs_dir "${ENV_FILE}")"
if [[ "${MODE}" == "dind" && -z "${PRESCAN_ENV:-}" ]]; then
    ENV_FILE="${HOME_DIR}/.env"
fi

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
if [[ "${DEV_MODE}" == "1" && "${MODE}" != "dind" && "${IMAGE_CLI}" == "0" ]] \
   && [[ -z "${IMAGE}" || "${IMAGE}" == *:latest || "${IMAGE}" == *:demo ]]; then
    IMAGE="${DEFAULT_DEV_IMAGE}"
elif [[ ( "${DEV_MODE}" != "1" || "${MODE}" == "dind" ) && "${IMAGE_CLI}" == "0" && "${IMAGE}" == *:dev ]]; then
    IMAGE="${DEFAULT_IMAGE}"
    warn "left dev mode: using ${DEFAULT_IMAGE} instead of the dev image"
elif [[ -z "${CODX_JUNIOR_IMAGE:-}" && -z "${IMAGE}" ]]; then
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

# An upgrade means "move to the current release" unless the caller explicitly
# pinned an image. Settings from the previous install may still contain an old
# tag/semver; keeping it silently turns --update into a no-op.
if [[ "${UPGRADE}" == "1" && "${IMAGE_CLI}" == "0" ]] \
   && [[ "${DEV_MODE}" != "1" || "${MODE}" == "dind" ]]; then
    IMAGE="${DEFAULT_IMAGE}"
fi

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
# A domain is only meaningful when a proxy is in front; local mode with no
# proxy ignores one inherited from the environment.
if [[ "${TRAEFIK_MODE}" == "none" && "${EFFECTIVE_MODE}" == "local" ]]; then
    DOMAINS=()
    PRIMARY_DOMAIN="localhost"
fi

if [[ "${COMMAND}" != "remove" && "${TRAEFIK_MODE}" != "none" ]] && [[ "${#DOMAINS[@]}" -eq 0 ]]; then
    die "Traefik '${TRAEFIK_MODE}' needs --domain (or CODX_JUNIOR_DOMAIN)"
fi
if [[ "${TRAEFIK_MODE}" == "master" && "${NO_TLS}" == "1" ]]; then
    die "--traefik primary cannot be combined with --no-tls"
fi
if [[ "${COMMAND}" != "remove" && "${TRAEFIK_MODE}" == "master" && -z "${ACME_EMAIL}" ]]; then
    die "--traefik primary needs --acme-email for Let's Encrypt"
fi
if [[ "${MODE}" == "dind" && "${DIND_MODE}" == "server" && "${NO_TLS}" == "1" ]]; then
    die "dind needs TLS; use --traefik primary or --traefik slave with a domain"
fi
if [[ "${MODE}" == "dind" && "${TRAEFIK_MODE}" == "none" ]]; then
    die "dind needs --traefik primary or --traefik slave"
fi
if [[ "${MODE}" == "slave" && "${TRAEFIK_MODE}" == "none" ]]; then
    die "--mode slave needs a Traefik setup (use --mode server --traefik none for plain HTTP)"
fi
if [[ "${DEV_MODE}" == "1" && "${CONTAINER}" == "1" ]]; then
    die "development mode inside the DIND image is controlled by CODX_JUNIOR_DEBUG"
fi
if [[ "${TRAEFIK_MODE}" == "slave" ]]; then
    PROXY_EXTERNAL="${PROXY_EXTERNAL:-true}"
else
    PROXY_EXTERNAL="${PROXY_EXTERNAL:-false}"
fi
# Inside the DIND wrapper the "proxy network" is a nested bridge that compose
# creates; only a host install joins an external master network.
if [[ "${CONTAINER}" == "1" && "${TRAEFIK_MODE}" == "slave" ]]; then
    PROXY_EXTERNAL="false"
fi
if [[ "${BIND_ADDRESS_CLI}" == "0" && -z "${CODX_BIND_ADDRESS:-}" ]]; then
    if [[ "${DEV_MODE}" == "1" ]]; then
        BIND_ADDRESS="127.0.0.1"
    elif [[ "${TRAEFIK_MODE}" == "none" && "${EFFECTIVE_MODE}" != "local" ]]; then
        BIND_ADDRESS="0.0.0.0"
    fi
fi
if [[ -z "${SYSTEMD}" ]]; then
    if [[ "${CONTAINER}" == "1" || ( "${DEV_MODE}" == "1" && "${MODE}" != "dind" ) ]]; then
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
if [[ "${CONTAINER}" == "1" || ( "${DEV_MODE}" == "1" && "${MODE}" != "dind" ) ]]; then
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
elif [[ "${TRAEFIK_MODE}" != "none" ]]; then
    PUBLIC_URL="https://${PRIMARY_DOMAIN}"
elif [[ "${DEV_MODE}" == "1" || "${EFFECTIVE_MODE}" == "local" ]]; then
    PUBLIC_URL="http://localhost:${WEB_PORT}"
elif [[ "${EFFECTIVE_MODE}" == "server" ]]; then
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
if [[ "${COMMAND}" != "remove" && "${NO_AI}" != "1" ]]; then
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
if [[ "${COWORKING}" == "1" && "${CONTAINER}" != "1" && "${MODE}" != "dind" \
      && "${EFFECTIVE_MODE}" != "local" && "${ALLOW_PRIVILEGED}" != "1" ]]; then
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
        if [[ -z "${CODX_SOURCE_DIR}" ]]; then
            CODX_SOURCE_DIR="${CODX_BUILD_CONTEXT}"
        fi
    else
        SRC_DIR="${HOME_DIR}/src"
        if [[ "${DRY_RUN}" == "1" ]]; then
            RUNTIME_DIR="${SRC_DIR}/codx-junior-installer/runtime"
            INSTALLER_DIR="${SRC_DIR}/codx-junior-installer"
            CODX_BUILD_CONTEXT="${SRC_DIR}"
            [[ -z "${CODX_SOURCE_DIR}" ]] && CODX_SOURCE_DIR="${SRC_DIR}"
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
        if [[ -z "${CODX_SOURCE_DIR}" ]]; then
            CODX_SOURCE_DIR="${SRC_DIR}"
        fi
    fi

    [[ -f "${source_dir}/runtime/docker-compose.yaml" ]] \
        || die "unexpected repository layout: ${source_dir}/runtime/docker-compose.yaml is missing"
    if [[ "${DEV_MODE}" == "1" && "${MODE}" != "dind" ]]; then
        [[ -n "${CODX_SOURCE_DIR}" && -f "${CODX_SOURCE_DIR}/client/package.json" \
           && -f "${CODX_SOURCE_DIR}/api/pyproject.toml" ]] \
            || die "development mode needs a full checkout (run from the repository; '${CODX_SOURCE_DIR:-<none>}' is not one)"
    fi
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
    if [[ "${DEV_MODE}" == "1" ]]; then
        COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dev.yaml")
    fi
    case "${TRAEFIK_MODE}" in
        none)
            [[ "${CONTAINER}" == "1" ]] \
                || COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.local.yaml") ;;
        master)
            COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.master.yaml") ;;
        slave)
            COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.slave.yaml")
            # Host slave: the edge labels are for the external primary. Inside
            # DIND the wrapper carries them, so the nested Traefik stays clean.
            if [[ "${CONTAINER}" != "1" ]]; then
                COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.slave-edge.yaml")
            fi ;;
    esac
    # Local access alongside a proxy: `local` + `slave` is the "dind local
    # slave" case. It publishes the app on 127.0.0.1 and still joins the master
    # network with a slave Traefik.
    if [[ "${CONTAINER}" != "1" && "${TRAEFIK_MODE}" != "none" ]] \
       && [[ "${EFFECTIVE_MODE}" == "local" || "${DEV_MODE}" == "1" ]]; then
        COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.local.yaml")
    fi
    if [[ "${COWORKING}" == "1" ]]; then
        COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.coworking.yaml")
    fi
    if [[ "${CONTAINER}" == "1" ]]; then
        case "${TRAEFIK_MODE}" in
            none)   COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-local.yaml") ;;
            master) COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-master.yaml") ;;
            slave)  COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-slave.yaml") ;;
        esac
        if [[ "${EFFECTIVE_MODE}" == "local" && "${TRAEFIK_MODE}" != "none" ]]; then
            COMPOSE_FILES+=(-f "${RUNTIME_DIR}/docker-compose.dind-local.yaml")
        fi
    fi
    if [[ "${DO_BUILD}" == "1" && "${CONTAINER}" != "1" && "${DEV_MODE}" != "1" ]]; then
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
        "CODX_SOURCE_DIR=${CODX_SOURCE_DIR}"
        "CODX_JUNIOR_DEV=${DEV_MODE}"
        "CODX_JUNIOR_DEBUG=${CODX_JUNIOR_DEBUG:-$([[ "${DEV_MODE}" == "1" ]] && echo 1 || echo 0)}"
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
        "CODX_TRAEFIK_MODE=${TRAEFIK_MODE}"
        "CODX_HEALTH_PORT=$([[ "${TRAEFIK_MODE}" == "none" ]] && echo "${WEB_PORT}" || echo 80)"
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

# Save the resolved selections before any slow work (Docker install, image build,
# pull). A failed/aborted install therefore still offers the same answers next
# time. This is deliberately separate from the instance settings file.
save_installer_state() {
    [[ "${DRY_RUN}" == "1" || "${CONTAINER}" == "1" ]] && return 0
    local domains_csv=""
    if (( ${#DOMAINS[@]} > 0 )); then
        domains_csv="$(IFS=,; printf '%s' "${DOMAINS[*]}")"
    fi
    if ! mkdir -p "$(dirname "${STATE_FILE}")" 2>/dev/null || ! : >> "${STATE_FILE}" 2>/dev/null; then
        warn "could not save installer selections to ${STATE_FILE}"
        return 0
    fi
    chmod 600 "${STATE_FILE}" 2>/dev/null || true

    set_env_var CODX_JUNIOR_INSTALLER_STATE_VERSION 1 "${STATE_FILE}"
    set_env_var CODX_JUNIOR_MODE "${MODE}" "${STATE_FILE}"
    set_env_var CODX_DIND_MODE "${DIND_MODE}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_DEV "${DEV_MODE}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_DOMAIN "${domains_csv}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_TRAEFIK "${TRAEFIK_MODE}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_INSTANCE "${INSTANCE}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_HOME "${HOME_DIR}" "${STATE_FILE}"
    set_env_var CODX_DATA_DIR "${DATA_DIR}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_ENV_FILE "${ENV_FILE}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_WEB_PORT "${WEB_PORT}" "${STATE_FILE}"
    set_env_var CODX_BIND_ADDRESS "${BIND_ADDRESS}" "${STATE_FILE}"
    set_env_var CODX_NO_TLS "${NO_TLS}" "${STATE_FILE}"
    set_env_var CODX_PROXY_NETWORK "${PROXY_NETWORK}" "${STATE_FILE}"
    set_env_var CODX_MASTER_CERT_RESOLVER "${MASTER_CERT_RESOLVER}" "${STATE_FILE}"
    set_env_var CODX_MASTER_ENTRYPOINT "${MASTER_ENTRYPOINT}" "${STATE_FILE}"
    set_env_var CODX_ACME_EMAIL "${ACME_EMAIL}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_AI_PROVIDER "${PROVIDER}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_AI_API_KEY "${API_KEY}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_AI_API_KEY_FILE "${API_KEY_FILE}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_AI_API_URL "${API_URL}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_AI_MODEL "${MODEL}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_NO_AI "${NO_AI}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_COWORKING "${COWORKING}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_HIDDEN "${HIDDEN}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_ACTIVE_FEATURES "${ACTIVE_FEATURES}" "${STATE_FILE}"
    set_env_var CODX_ALLOW_PRIVILEGED "${ALLOW_PRIVILEGED}" "${STATE_FILE}"
    set_env_var CODX_DIND_SYSBOX "${SYSBOX}" "${STATE_FILE}"
    set_env_var CODX_DIND_STORAGE "${CODX_DIND_STORAGE:-${INSTANCE}-dind}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_KNOWLEDGE_BACKEND "${KNOWLEDGE_BACKEND}" "${STATE_FILE}"
    set_env_var CODX_JUNIOR_IMAGE "${IMAGE}" "${STATE_FILE}"
    if [[ "${DEV_MODE}" == "1" ]]; then
        set_env_var CODX_JUNIOR_SOURCE "${CODX_SOURCE_DIR}" "${STATE_FILE}"
    else
        drop_env_var CODX_JUNIOR_SOURCE "${STATE_FILE}"
    fi
    log "Installer selections saved to ${STATE_FILE}"
}

write_settings() {
    mkdir -p "$(dirname "${ENV_FILE}")" "${DATA_DIR}" "${PROJECTS}" \
             "${DATA_DIR}/settings" "${DATA_DIR}/analytics"
    : >> "${ENV_FILE}"
    chmod 600 "${ENV_FILE}" 2>/dev/null || true

    set_env_var CODX_ENV_FILE "${ENV_FILE}" "${ENV_FILE}"
    set_env_var CODX_DATA_DIR "${DATA_DIR}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_MODE "${MODE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_TRAEFIK "${TRAEFIK_MODE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_DEV "${DEV_MODE}" "${ENV_FILE}"
    if [[ "${DEV_MODE}" == "1" ]]; then
        set_env_var CODX_JUNIOR_SOURCE "${CODX_SOURCE_DIR}" "${ENV_FILE}"
    else
        drop_env_var CODX_JUNIOR_SOURCE "${ENV_FILE}"
    fi
    set_env_var CODX_JUNIOR_INSTANCE "${INSTANCE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_DOMAIN "${PRIMARY_DOMAIN}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_PUBLIC_URL "${PUBLIC_URL}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_WEB_PORT "${WEB_PORT}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_PROJECTS_PATH "${PROJECTS}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_IMAGE "${IMAGE}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_KNOWLEDGE_BACKEND "${KNOWLEDGE_BACKEND}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_COWORKING "${COWORKING}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_HIDDEN "${HIDDEN}" "${ENV_FILE}"
    set_env_var CODX_JUNIOR_ACTIVE_FEATURES "${ACTIVE_FEATURES}" "${ENV_FILE}"
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
    write_update_descriptor
}

# --- self-update descriptor ------------------------------------------------
# The API only sees <data>/settings, never the installation home, so the facts
# an in-app update needs are published here. `GET/POST /api/system/update`
# reads it and runs `<cli> upgrade` from a helper container. Installations that
# predate this file are recovered from the compose labels instead.
_json_escape() {
    local value="${1:-}"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s' "${value}"
}

write_update_descriptor() {
    local settings_dir="${DATA_DIR}/settings"
    local cli="${HOME_DIR}/bin/codx-junior-${INSTANCE}"
    [[ "${INSTANCE}" == "codx-junior" ]] && cli="${HOME_DIR}/bin/codx-junior"
    local installer="${INSTALLER_DIR:-${HOME_DIR}/lib}/install.sh"
    mkdir -p "${settings_dir}" 2>/dev/null || return 0
    local tmp="${settings_dir}/.installer.json.$$"
    {
        printf '{\n'
        printf '  "source": "codx-junior-installer",\n'
        printf '  "installer_version": "%s",\n' "$(_json_escape "${INSTALLER_VERSION}")"
        printf '  "home": "%s",\n' "$(_json_escape "${HOME_DIR}")"
        printf '  "instance": "%s",\n' "$(_json_escape "${INSTANCE}")"
        printf '  "mode": "%s",\n' "$(_json_escape "${MODE}")"
        printf '  "container": %s,\n' "$([[ "${CONTAINER}" == "1" ]] && echo true || echo false)"
        printf '  "image": "%s",\n' "$(_json_escape "${IMAGE}")"
        printf '  "env_file": "%s",\n' "$(_json_escape "${ENV_FILE}")"
        printf '  "data_dir": "%s",\n' "$(_json_escape "${DATA_DIR}")"
        printf '  "cli": "%s",\n' "$(_json_escape "${cli}")"
        printf '  "installer": "%s",\n' "$(_json_escape "${installer}")"
        printf '  "updated_at": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '}\n'
    } > "${tmp}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
    # It carries host paths only (no secrets), but keep it private anyway.
    chmod 600 "${tmp}" 2>/dev/null || true
    mv "${tmp}" "${settings_dir}/installer.json" 2>/dev/null || rm -f "${tmp}"
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
        log "CLI: ${name} (status|logs|down|update|remove|doctor|config)"
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

    if [[ ( "${DEV_MODE}" == "1" && "${MODE}" != "dind" ) || "${EFFECTIVE_MODE}" == "local" ]]; then
        curl -fsS --max-time 5 "http://127.0.0.1:${WEB_PORT}/api/health" >/dev/null 2>&1 \
            || warn "the UI port did not answer /api/health yet (it may still be starting)"
    elif [[ "${TRAEFIK_MODE}" == "master" && "${NO_TLS}" == "0" && "${SKIP_TLS_WAIT}" != "1" ]]; then
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
    [[ "${TRAEFIK_MODE}" == "slave" ]] || return 0
    # Inside DIND the slave network is a nested Compose network that Docker
    # creates. Only a host install joins an external, pre-existing network.
    [[ "${PROXY_EXTERNAL}" == "true" ]] || return 0
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

# Keep the generated DIND wrapper's app image in sync with the installer
# selection. The wrapper passes CODX_JUNIOR_IMAGE to the inner installer as a
# literal, so `--update --image TAG` would otherwise keep pulling the old tag.
# Only that one line is rewritten: operators may have added volume mappings to
# the generated compose file.
sync_dind_wrapper_image() {
    local compose="$1" image="$2"
    [[ -f "${compose}" && -n "${image}" ]] || return 0
    [[ "${image}" =~ ^[A-Za-z0-9][A-Za-z0-9._/:@-]*$ ]] || {
        warn "ignoring an invalid image reference: ${image}"
        return 0
    }
    local current=""
    current="$(awk '
        /^[[:space:]]*CODX_JUNIOR_IMAGE:/ {
            line = $0
            sub(/^[[:space:]]*CODX_JUNIOR_IMAGE:[[:space:]]*/, "", line)
            gsub(/^"|"[[:space:]]*$/, "", line)
            print line
            exit
        }' "${compose}")"
    [[ -n "${current}" && "${current}" != "${image}" ]] || return 0
    local tmp="${compose}.tmp.$$"
    if ! awk -v image="${image}" '
        BEGIN { replaced = 0 }
        /^[[:space:]]*CODX_JUNIOR_IMAGE:[[:space:]]*/ && !replaced {
            match($0, /^[[:space:]]*/)
            print substr($0, 1, RLENGTH) "CODX_JUNIOR_IMAGE: \"" image "\""
            replaced = 1
            next
        }
        { print }' "${compose}" > "${tmp}"; then
        rm -f "${tmp}"
        warn "could not update the app image in ${compose}"
        return 0
    fi
    mv "${tmp}" "${compose}" 2>/dev/null || { rm -f "${tmp}"; return 0; }
    log "Updated the DIND wrapper image: ${current} -> ${image}"
}

install_dind() {
    if [[ -z "${RUNTIME_DIR}" ]]; then
        resolve_runtime
    fi

    local dind_dir=""
    if [[ "${DRY_RUN}" != "1" && ! -f "${HOME_DIR}/docker-compose.yaml" ]]; then
        write_dind_instance_files "${DOMAINS[0]:-}"
    fi
    if [[ -f "${HOME_DIR}/docker-compose.yaml" ]]; then
        DIND_COMPOSE=( -f "${HOME_DIR}/docker-compose.yaml" )
        dind_dir="${HOME_DIR}"
    else
        resolve_dind_dir
        dind_dir="${DIND_DIR}"
        DIND_COMPOSE=( -f "${dind_dir}/docker-compose.yaml" )
        case "${TRAEFIK_MODE}" in
            master) DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.master.yaml" ) ;;
            slave)  DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.slave.yaml" ) ;;
        esac
        [[ "${SYSBOX}" == "1" ]] && DIND_COMPOSE+=( -f "${dind_dir}/docker-compose.sysbox.yaml" )
    fi

    if [[ "${SYSBOX}" == "1" && "${DRY_RUN}" != "1" ]]; then
        docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"sysbox-runc"' \
            || die "the 'sysbox-runc' Docker runtime is not installed (see https://github.com/nestybox/sysbox)"
    fi
    if [[ "${TRAEFIK_MODE}" == "master" ]]; then
        check_ports
    fi
    ensure_proxy_network

    DIND_RECREATE=0
    local image_before="" image_after=""
    image_before="$(docker image inspect --format '{{.Id}}' "${DIND_IMAGE}" 2>/dev/null || true)"

    if [[ "${DO_BUILD_DIND}" == "1" ]]; then
        [[ -f "${INSTALLER_DIR}/dind/Dockerfile" ]] \
            || die "--build-dind needs ${INSTALLER_DIR}/dind/Dockerfile (run from a checkout)"
        log "Building ${DIND_IMAGE} (context: ${INSTALLER_DIR})"
        docker build -t "${DIND_IMAGE}" -f "${INSTALLER_DIR}/dind/Dockerfile" "${INSTALLER_DIR}"
        DIND_RECREATE=1
    elif [[ "${NO_PULL}" != "1" && "${DRY_RUN}" != "1" ]]; then
        log "Pulling latest ${DIND_IMAGE}"
        docker pull "${DIND_IMAGE}" \
            || die "could not pull ${DIND_IMAGE}; use --no-pull to use the local image or --build-dind to build it"
        image_after="$(docker image inspect --format '{{.Id}}' "${DIND_IMAGE}" 2>/dev/null || true)"
        if [[ -n "${image_after}" && "${image_after}" != "${image_before}" ]]; then
            DIND_RECREATE=1
            log "DIND image updated; the wrapper will be recreated"
        fi
    fi

    # `--update` must re-run the inner installer. The wrapper is the only
    # component that starts the nested daemon and runs `/opt/codx-junior/
    # install.sh` (its entrypoint), and the entrypoint only runs on container
    # start. Keying the recreate off the DIND image digest alone turned an
    # upgrade into a no-op whenever only the *app* image was republished —
    # which is the common case, because the app and the DIND image are
    # published separately.
    if [[ "${UPGRADE}" == "1" ]]; then
        if [[ "${DRY_RUN}" == "1" ]]; then
            warn "dry run: would recreate the DIND wrapper so the inner installer runs"
        else
            sync_dind_wrapper_image "${HOME_DIR}/docker-compose.yaml" "${IMAGE}"
            DIND_RECREATE=1
            log "Upgrade: recreating the DIND wrapper so the inner installer pulls ${IMAGE}"
        fi
    fi

    [[ "${DRY_RUN}" != "1" ]] && DIND_RECREATE=1
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

    log "Starting DIND instance ${INSTANCE} (app=${DIND_MODE}, traefik=${TRAEFIK_MODE})"
    if [[ "${DIND_RECREATE}" == "1" ]]; then
        compose up -d --remove-orphans --force-recreate
    else
        compose up -d --remove-orphans
    fi

    wait_health
    if [[ "${MODE}" == "dind" ]]; then
        log "Inner installer log: docker logs ${INSTANCE} (it also writes /var/log/inner-install.log inside the wrapper)"
    fi
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
    if [[ "${TRAEFIK_MODE}" == "master" ]]; then
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
    elif [[ "${TRAEFIK_MODE}" == "none" ]]; then
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
   dev      : $([[ "${DEV_MODE}" == "1" ]] && { [[ "${MODE}" == "dind" ]] && echo "enabled (DEBUG=1 inside DIND)" || echo "enabled (DEBUG=1, live checkout)"; } || echo "disabled")
   traefik  : $(traefik_label)
   features : $(features_label)
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
traefik_mode=$(traefik_label)
dev_mode=${DEV_MODE}
source=${CODX_SOURCE_DIR}
instance=${INSTANCE}
home=${HOME_DIR}
data=${DATA_DIR}
projects=${PROJECTS}
env_file=${ENV_FILE}
state_file=${STATE_FILE}
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
hidden=${HIDDEN}
features=${ACTIVE_FEATURES}
active_features=${ACTIVE_FEATURE_LIST// /,}
systemd=${SYSTEMD}
dind_image=${DIND_IMAGE}
sysbox=${SYSBOX}
CONFIG
}

run_install() {
    [[ "${BANNER_PRINTED}" == "1" ]] || print_banner
    log "codx-junior installer ${INSTALLER_VERSION}"
    log "  mode      : ${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)"
    log "  dev       : ${DEV_MODE}"
    log "  instance  : ${INSTANCE}"
    log "  home      : ${HOME_DIR}"
    log "  projects  : ${PROJECTS}"
    log "  image     : ${IMAGE}"
    log "  domain    : ${DOMAINS[*]:-<none>}"
    log "  traefik   : $([[ "${TRAEFIK_MODE}" == "master" ]] && echo "primary (${CERT_RESOLVER})" || { [[ "${TRAEFIK_MODE}" == "slave" ]] && echo "slave on ${PROXY_NETWORK}" || echo "none"; })"
    log "  coworking : ${COWORKING}"
    log "  features  : $(features_label)"

    if [[ "${DRY_RUN}" != "1" ]]; then
        ensure_docker
    fi
    if [[ "${MODE}" == "dind" ]]; then
        save_installer_state
        install_dind
        return 0
    fi
    resolve_runtime
    save_installer_state
    select_compose_files
    build_compose_env

    if [[ "${TRAEFIK_MODE}" == "master" ]]; then
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
    if [[ "${TRAEFIK_MODE}" == "master" ]]; then
        # Traefik refuses to start when acme.json is world-readable.
        [[ -f "${DATA_DIR}/traefik/acme.json" ]] || : > "${DATA_DIR}/traefik/acme.json"
        chmod 600 "${DATA_DIR}/traefik/acme.json" 2>/dev/null || true
    fi

    write_compose_wrapper
    select_compose_files

    if [[ "${DEV_MODE}" == "1" ]]; then
        if [[ "${DO_BUILD}" == "1" ]] || ! docker image inspect "${IMAGE}" >/dev/null 2>&1; then
            log "Building ${IMAGE} (dev target; first build can take several minutes)"
            compose build
        else
            log "Using the existing ${IMAGE} (source is mounted; use --build after dependency changes)"
        fi
    elif [[ "${DO_BUILD}" == "1" && "${CONTAINER}" != "1" ]]; then
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

run_remove() {
    [[ "${BANNER_PRINTED}" == "1" ]] || print_banner

    # The remove path must not need a complete install configuration. When no
    # settings file is present, ask for the minimum needed to identify the
    # installation and offer safe defaults.
    if [[ ! -f "${ENV_FILE}" && "${CONFIRM_DELETE}" != "1" ]]; then
        if [[ "${NON_INTERACTIVE}" == "1" ]] \
           || [[ "${WIZARD_TTY}" != "1" && "${INTERACTIVE}" != "1" ]]; then
            die "no settings file at ${ENV_FILE}; pass --home/--env-file or run interactively"
        fi
        section "Remove settings"
        local target_default="${HOME_DIR:-${PWD}}"
        if [[ -f "${target_default}/docker-compose.yaml" ]] \
           && grep -q 'codxjuniorai/codx-junior:dind' "${target_default}/docker-compose.yaml" 2>/dev/null; then
            MODE="dind"
        fi
        HOME_DIR="$(ask "Target folder" "${target_default}")"
        HOME_DIR="$(abs_dir "${HOME_DIR}")"
        MODE="$(ask_choice "Deployment mode" "local server dind" "${MODE:-local}")"
        INSTANCE="$(ask "Instance name" "${INSTANCE:-$(basename "${HOME_DIR}")}")"
        STATE_FILE="${HOME_DIR}/installation.env"
        if [[ "${MODE}" == "dind" ]]; then
            ENV_FILE="${HOME_DIR}/.env"
        else
            ENV_FILE="${HOME_DIR}/config/${INSTANCE}.env"
        fi
        DATA_DIR="${HOME_DIR}/data"
        PROJECTS="${DATA_DIR}/projects"
    fi
    if [[ "${DELETE_FILES_EXPLICIT}" != "1" && "${CONFIRM_DELETE}" != "1" \
          && "${DRY_RUN}" != "1" && "${NON_INTERACTIVE}" != "1" ]] \
       && { [[ "${WIZARD_TTY}" == "1" || "${INTERACTIVE}" == "1" ]]; }; then
        if [[ "$(ask_yesno "Keep installation files (env, compose, installation.env)?" 1)" == "1" ]]; then
            DELETE_FILES=0
        else
            DELETE_FILES=1
        fi
    fi

    local is_dind=0
    [[ "${MODE}" == "dind" ]] && is_dind=1

    local remove_files=()
    local project_dir="" base_runtime="" compose_exists=0
    if [[ "${is_dind}" == "1" ]]; then
        if [[ -f "${HOME_DIR}/docker-compose.yaml" ]]; then
            remove_files=(-f "${HOME_DIR}/docker-compose.yaml")
            project_dir="${HOME_DIR}"
            compose_exists=1
        else
            if [[ -f "${HOME_DIR}/lib/runtime/docker-compose.yaml" ]]; then
                base_runtime="${HOME_DIR}/lib/runtime"
            elif [[ -f "${SELF_DIR}/runtime/docker-compose.yaml" ]]; then
                base_runtime="${SELF_DIR}/runtime"
            fi
            if [[ -n "${base_runtime}" ]]; then
                local dind_dir="${SELF_DIR}/dind"
                if [[ -f "${HOME_DIR}/lib/dind/docker-compose.yaml" ]]; then
                    dind_dir="${HOME_DIR}/lib/dind"
                fi
                remove_files=(-f "${base_runtime}/docker-compose.yaml")
                case "${TRAEFIK_MODE}" in
                    master) remove_files+=(-f "${dind_dir}/docker-compose.master.yaml") ;;
                    slave)  remove_files+=(-f "${dind_dir}/docker-compose.slave.yaml") ;;
                esac
                [[ "${SYSBOX}" == "1" ]] && remove_files+=(-f "${dind_dir}/docker-compose.sysbox.yaml")
                project_dir="${base_runtime}"
                compose_exists=1
            fi
        fi
    else
        if [[ -f "${HOME_DIR}/lib/runtime/docker-compose.yaml" ]]; then
            base_runtime="${HOME_DIR}/lib/runtime"
        elif [[ -f "${SELF_DIR}/runtime/docker-compose.yaml" ]]; then
            base_runtime="${SELF_DIR}/runtime"
        fi
        if [[ -n "${base_runtime}" ]]; then
            remove_files=(-f "${base_runtime}/docker-compose.yaml")
            if [[ "${DEV_MODE}" == "1" ]]; then
                remove_files+=(-f "${base_runtime}/docker-compose.dev.yaml")
            fi
            case "${TRAEFIK_MODE}" in
                master) remove_files+=(-f "${base_runtime}/docker-compose.master.yaml") ;;
                slave)  remove_files+=(-f "${base_runtime}/docker-compose.slave.yaml") ;;
                *)      remove_files+=(-f "${base_runtime}/docker-compose.local.yaml") ;;
            esac
            project_dir="${base_runtime}"
            compose_exists=1
        fi
    fi

    local containers=("${INSTANCE}")
    [[ "${is_dind}" == "0" ]] && containers+=("${INSTANCE}-traefik")
    local networks=()
    [[ "${is_dind}" == "0" ]] && networks=("${INSTANCE}-internal")
    local volume="${CODX_DIND_STORAGE:-${INSTANCE}-dind}"
    local files=() seen=" "
    add_file() {
        [[ -n "$1" && "${seen}" != *"|$1|"* ]] || return 0
        seen="${seen}|$1|"
        files+=("$1")
    }
    add_file "${ENV_FILE}"
    add_file "${HOME_DIR}/docker-compose.yaml"
    add_file "${HOME_DIR}/installation.env"
    add_file "${HOME_DIR}/.env"
    add_file "${STATE_FILE}"
    add_file "${HOME_DIR}/lib"
    add_file "${HOME_DIR}/bin"
    add_file "${HOME_DIR}/config"
    local cli_name="codx-junior-${INSTANCE}"
    [[ "${INSTANCE}" == "codx-junior" ]] && cli_name="codx-junior"
    local unit_name="codx-junior-${INSTANCE}.service"
    [[ "${INSTANCE}" == "codx-junior" ]] && unit_name="codx-junior.service"
    add_file "/usr/local/bin/${cli_name}"
    add_file "/etc/systemd/system/${unit_name}"

    printf '\n%sRemoval summary%s\n' "${C_CYAN}" "${C_RESET}"
    printf '  instance   : %s\n' "${INSTANCE}"
    printf '  mode       : %s\n' "${MODE}$([[ "${MODE}" == "dind" ]] && echo " (${DIND_MODE})" || true)"
    printf '  target     : %s\n' "${HOME_DIR}"
    printf '  containers : %s\n' "${containers[*]}"
    if [[ "${#networks[@]}" -gt 0 ]]; then
        printf '  networks   : project/%s (external networks are kept)\n' "${networks[*]}"
    else
        printf '  networks   : nested networks (removed with the DIND volume)\n'
    fi
    if [[ "${is_dind}" == "1" ]]; then
        printf '  volume     : %s (nested Docker layers/images/containers)\n' "${volume}"
    fi
    printf '  kept       : projects, host data, settings inside data/, images, external networks\n'

    printf '\nFiles and folders to be removed:\n'
    local f existing_files=0
    if [[ "${DELETE_FILES}" != "1" ]]; then
        printf 'No files will be removed\n'
    else
        for f in "${files[@]}"; do
            [[ -n "${f}" && -e "${f}" ]] || continue
            existing_files=1
            if [[ -d "${f}" ]]; then
                printf '  %s/ (folder)\n' "${f}"
            else
                printf '  %s\n' "${f}"
            fi
        done
        [[ "${existing_files}" == "1" ]] || printf 'No files will be removed\n'
    fi

    printf '\n\033[1;31mWARNING: removing an installation is irreversible.\033[0m\n'

    if [[ "${DRY_RUN}" == "1" ]]; then
        log "dry run: nothing was removed"
        return 0
    fi
    if [[ "${CONFIRM_DELETE}" != "1" ]]; then
        if [[ "${NON_INTERACTIVE}" == "1" ]] \
           || [[ "${WIZARD_TTY}" != "1" && "${INTERACTIVE}" != "1" ]]; then
            die "refusing to remove without confirmation; re-run with --confirm-delete"
        fi
        if [[ "$(ask_yesno "Remove ${INSTANCE} and its Docker resources?" 0)" != "1" ]]; then
            log "cancelled"
            return 0
        fi
    fi

    if command -v docker >/dev/null 2>&1; then
        if [[ "${compose_exists}" == "1" ]]; then
            local args=(--project-directory "${project_dir}" --project-name "${INSTANCE}")
            [[ -f "${ENV_FILE}" ]] && args+=(--env-file "${ENV_FILE}")
            docker compose "${args[@]}" "${remove_files[@]}" down -v --remove-orphans \
                || warn "docker compose down failed; falling back to direct removal"
        fi
        local c
        for c in "${containers[@]}"; do docker rm -f "${c}" >/dev/null 2>&1 || true; done
        docker network rm "${networks[@]}" >/dev/null 2>&1 || true
        if [[ "${is_dind}" == "1" ]]; then
            docker volume rm "${volume}" >/dev/null 2>&1 || true
        fi
    else
        warn "docker is not available; only files can be removed"
    fi

    if [[ -f "/etc/systemd/system/${unit_name}" ]] && command -v systemctl >/dev/null 2>&1; then
        systemctl disable --now "${unit_name}" >/dev/null 2>&1 || true
        systemctl daemon-reload >/dev/null 2>&1 || true
    fi

    if [[ "${DELETE_FILES}" == "1" ]]; then
        for f in "${files[@]}"; do
            [[ -n "${f}" && "${f}" != "/" ]] || continue
            rm -rf -- "${f}" 2>/dev/null || warn "could not remove ${f}"
        done
        rmdir -- "${HOME_DIR}" 2>/dev/null || true
        log "Removed installation files"
    else
        log "Docker resources removed; generated files were kept"
    fi
}

run_update() {
    UPGRADE=1
    [[ "${BANNER_PRINTED}" == "1" ]] || print_banner
    if [[ "${DRY_RUN}" == "1" ]]; then
        log "dry run: would update ${INSTANCE} to ${IMAGE}"
        if [[ "${MODE}" != "dind" ]]; then
            select_compose_files
            build_compose_env
            run_config | sed 's/^/  /'
        fi
        return 0
    fi
    if [[ "${MODE}" == "dind" ]]; then
        ensure_docker
        save_installer_state
        resolve_runtime
        install_dind
        return 0
    fi
    resolve_runtime
    save_installer_state
    select_compose_files
    build_compose_env
    write_settings
    write_compose_wrapper
    if [[ "${DEV_MODE}" == "1" ]]; then
        log "Rebuilding the dev image (dependencies / Dockerfile)"
        compose build
    elif [[ "${DO_BUILD}" == "1" ]]; then
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
    if [[ -f "${HOME_DIR}/docker-compose.yaml" ]]; then
        DIND_COMPOSE=( -f "${HOME_DIR}/docker-compose.yaml" )
        RUNTIME_DIR="${HOME_DIR}"
    else
        resolve_dind_dir
        DIND_COMPOSE=( -f "${DIND_DIR}/docker-compose.yaml" )
        case "${TRAEFIK_MODE}" in
            master) DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.master.yaml" ) ;;
            slave)  DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.slave.yaml" ) ;;
        esac
        [[ "${SYSBOX}" == "1" ]] && DIND_COMPOSE+=( -f "${DIND_DIR}/docker-compose.sysbox.yaml" )
        RUNTIME_DIR="${DIND_DIR}"
    fi
    COMPOSE_FILES=("${DIND_COMPOSE[@]}")
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
    printf 'dev      : %s\n' "${DEV_MODE}"
    printf 'traefik  : %s\n' "$(traefik_label)"
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
    if docker network inspect "${PROXY_NETWORK}" >/dev/null 2>&1; then echo "${PROXY_NETWORK} exists"; else echo "${PROXY_NETWORK} missing"; [[ "${TRAEFIK_MODE}" == "slave" ]] && rc=1; fi
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
    update)  run_update ;;
    remove)  run_remove ;;
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
