#!/usr/bin/env bash
# =============================================================================
# codx-junior — one-line installer / upgrader
#
# Root is required (it installs Docker); when it is not root it prints the exact
# command to run instead of escalating on its own.
#
#   curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh -o install.sh
#   sudo ./install.sh --provider deepseek --key sk-xxxxxxxx
#
# It installs on a fresh machine and UPDATES an
# existing installation (the repository is moved to the requested branch, your
# projects and settings are kept).
#
# This file is deliberately small: it resolves the real installer of the
# requested branch (`codx-junior-installer/cloud/bootstrap.sh`) and hands the
# settings over to it, so there is exactly ONE place that knows how to install.
#
#   --branch, -branch, --ref, -b BRANCH   branch or tag to install/update (default: main)
#   --provider, -p NAME   AI provider preset: openai deepseek gemini
#                         openrouter mistral groq ollama custom.
#                         Default: keep the settings file value, else deepseek.
#   --key, -k KEY         provider API key (or --key-file)
#   --key-file PATH       read the key from a file
#   --url URL             OpenAI-compatible endpoint (only for provider `custom`)
#   --model ID            override the preset chat model
#   --port, -P PORT       web port                                default: 19981
#   --projects PATH       folder holding your projects            default: <home>/projects
#   --home PATH           installation root                       default: /opt/codx-junior
#   --repo URL            git repository to install from
#   --env-file PATH       settings file                       default: /etc/codx-junior/settings.env
#   --installer PATH      use this bootstrap.sh instead of the one of the branch
#   --build               build the image from source instead of pulling it
#   --no-docker-install   fail instead of installing Docker
#   --open-firewall       ufw: allow ssh + the web port (off by default)
#   --update              update an existing installation (the default)
#   --dry-run             show what would run; change nothing
#   -h, --help            this help
#   -V, --version         print the version of this installer script
#
# Every value can also come from the environment (CODX_JUNIOR_REF,
# CODX_JUNIOR_AI_PROVIDER, CODX_JUNIOR_AI_API_KEY, CODX_JUNIOR_WEB_PORT, …) and
# from --env-file. Precedence: default < env file < environment < flags.
#
# Exit code 0 means codx-junior answered its health check.
# =============================================================================
set -euo pipefail

INSTALLER_VERSION="1.0.0"
DEFAULT_REPO="https://github.com/codx-junior/codx-junior.git"
DEFAULT_REF="main"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    # Printed from here (not from the file header) because when this script is
    # piped into bash, $BASH_SOURCE is empty and there is nothing to sed.
    cat <<'USAGE'
codx-junior installer — one line, installs or updates

  curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
    -o install.sh
  sudo ./install.sh --provider deepseek --key sk-xxxxxxxx

  ./install.sh --provider openai --key sk-xxxxxxxx          # from a checkout
  ./install.sh --branch feature-x --provider gemini --key AIza...

Settings (flag | environment | --env-file), lowest precedence first:
  --branch, -branch, --ref, -b  BRANCH   branch or tag to install/update   [main]
  --provider, -p                NAME     openai deepseek gemini openrouter
                                         mistral groq ollama custom
                                         [settings file, else deepseek]
  --key, -k                     KEY      provider API key (or --key-file)
  --key-file                    PATH     read the key from a file
  --url                         URL      endpoint, only for provider `custom`
  --model                       ID       override the preset chat model
  --port, -P                    PORT     web port                       [19981]
  --projects                    PATH     folder for your projects  [<home>/projects]
  --home                        PATH     installation root          [/opt/codx-junior]
  --repo                        URL      git repository to install from
  --env-file                    PATH     settings file  [/etc/codx-junior/settings.env]
  --installer                   PATH     use this bootstrap.sh for the install
  --build                                build the image instead of pulling it
  --no-docker-install                    fail instead of installing Docker
  --open-firewall                        ufw: allow ssh + the web port
  --update                               update an existing install (default)
  --dry-run                              show what would run; change nothing
  -h, --help | -V, --version

Environment equivalent of every flag: CODX_JUNIOR_REF, CODX_JUNIOR_AI_PROVIDER,
CODX_JUNIOR_AI_API_KEY, CODX_JUNIOR_AI_API_URL, CODX_JUNIOR_AI_MODEL,
CODX_JUNIOR_WEB_PORT, CODX_JUNIOR_PROJECTS_PATH, CODX_JUNIOR_HOME,
CODX_JUNIOR_REPO, CODX_JUNIOR_ENV_FILE, CODX_JUNIOR_INSTALLER,
CODX_JUNIOR_BUILD, CODX_JUNIOR_INSTALL_DOCKER, CODX_JUNIOR_OPEN_FIREWALL.

From a checkout: ./install.sh …   As root, or with sudo in front of it.
USAGE
}

need() {
    [[ $# -ge 2 && -n "${2:-}" ]] || die "option $1 needs a value (see --help)"
}

# --- settings: environment first, flags afterwards --------------------------
REF="${CODX_JUNIOR_REF:-${DEFAULT_REF}}"
REPO="${CODX_JUNIOR_REPO:-${DEFAULT_REPO}}"
PROVIDER="${CODX_JUNIOR_AI_PROVIDER:-}"
API_KEY="${CODX_JUNIOR_AI_API_KEY:-}"
API_KEY_FILE="${CODX_JUNIOR_AI_API_KEY_FILE:-}"
API_URL="${CODX_JUNIOR_AI_API_URL:-}"
MODEL="${CODX_JUNIOR_AI_MODEL:-}"
PORT="${CODX_JUNIOR_WEB_PORT:-}"
PROJECTS="${CODX_JUNIOR_PROJECTS_PATH:-}"
HOME_DIR="${CODX_JUNIOR_HOME:-}"
ENV_FILE="${CODX_JUNIOR_ENV_FILE:-}"
INSTALLER="${CODX_JUNIOR_INSTALLER:-}"
BUILD="${CODX_JUNIOR_BUILD:-0}"
INSTALL_DOCKER="${CODX_JUNIOR_INSTALL_DOCKER:-1}"
OPEN_FIREWALL="${CODX_JUNIOR_OPEN_FIREWALL:-0}"
DRY_RUN=0
UPDATE_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch|--ref|-branch|-ref|-b|-r)  need "$@"; REF="${2:-}"; shift 2 ;;
        --provider|-provider|-p)            need "$@"; PROVIDER="${2:-}"; shift 2 ;;
        --key|-key|-k)                      need "$@"; API_KEY="${2:-}"; shift 2 ;;
        --key-file|-key-file)               need "$@"; API_KEY_FILE="${2:-}"; shift 2 ;;
        --url|-url|-u)                      need "$@"; API_URL="${2:-}"; shift 2 ;;
        --model|-model|-m)                  need "$@"; MODEL="${2:-}"; shift 2 ;;
        --port|-port|-P)                    need "$@"; PORT="${2:-}"; shift 2 ;;
        --projects|-projects)               need "$@"; PROJECTS="${2:-}"; shift 2 ;;
        --home|-home)                       need "$@"; HOME_DIR="${2:-}"; shift 2 ;;
        --repo|-repo)                       need "$@"; REPO="${2:-}"; shift 2 ;;
        --env-file|-env-file)               need "$@"; ENV_FILE="${2:-}"; shift 2 ;;
        --installer|-installer)             need "$@"; INSTALLER="${2:-}"; shift 2 ;;
        --build|-build)                     BUILD=1; shift ;;
        --no-docker-install)                INSTALL_DOCKER=0; shift ;;
        --open-firewall)                    OPEN_FIREWALL=1; shift ;;
        --update|-update)                   UPDATE_ONLY=1; shift ;;
        --dry-run|--dry|--check)            DRY_RUN=1; shift ;;
        -h|--help|-help)                    usage; exit 0 ;;
        -V|--version|-version)              printf 'codx-junior install.sh %s\n' "${INSTALLER_VERSION}"; exit 0 ;;
        "")                                 shift ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

# The branch lands in a URL and in a git ref: keep it boring on purpose.
[[ "${REF}" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid branch name: ${REF}"
[[ "${PORT}" =~ ^[0-9]+$ || -z "${PORT}" ]] || die "invalid port: ${PORT}"

if [[ -z "${API_KEY}" && -n "${API_KEY_FILE}" ]]; then
    [[ -f "${API_KEY_FILE}" ]] || die "key file not found: ${API_KEY_FILE}"
    # `read` stops at the newline and needs no command substitution, so the
    # value never reaches the process list.
    IFS= read -r API_KEY < "${API_KEY_FILE}" || true
    API_KEY=${API_KEY%%$'\r'}
fi

# --- resolve the real installer for the requested branch --------------------
REAL_INSTALLER=""
RESOLVED_HOW=""

LOCAL_INSTALLER=""
if [[ -n "${INSTALLER}" ]]; then
    LOCAL_INSTALLER="${INSTALLER}"
elif [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    # Running from a file: prefer the checkout this script belongs to.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${SCRIPT_DIR}/codx-junior-installer/cloud/bootstrap.sh" ]]; then
        LOCAL_INSTALLER="${SCRIPT_DIR}/codx-junior-installer/cloud/bootstrap.sh"
    fi
fi

if [[ -n "${LOCAL_INSTALLER}" ]]; then
    [[ -f "${LOCAL_INSTALLER}" ]] || die "installer not found: ${LOCAL_INSTALLER}"
    REAL_INSTALLER="${LOCAL_INSTALLER}"
    RESOLVED_HOW="local"
fi

# raw.githubusercontent needs no git and no clone, which matters on a bare VM.
raw_url() {
    local url="${REPO%.git}" owner repo
    url="${url#git@github.com:}"
    url="${url#ssh://git@github.com/}"
    url="${url#https://github.com/}"
    [[ "${url}" == */* ]] || return 1
    owner="${url%%/*}"
    repo="${url#*/}"
    [[ "${REPO}" == *github.com* ]] || return 1
    printf 'https://raw.githubusercontent.com/%s/%s/%s/codx-junior-installer/cloud/bootstrap.sh' \
        "${owner}" "${repo}" "${REF}"
}

TMP_INSTALLER=""
cleanup() {
    [[ -n "${TMP_INSTALLER}" && -f "${TMP_INSTALLER}" ]] && rm -f "${TMP_INSTALLER}" || true
}
trap cleanup EXIT

if [[ -z "${REAL_INSTALLER}" ]]; then
    TMP_INSTALLER="$(mktemp "${TMPDIR:-/tmp}/codx-junior-bootstrap.XXXXXX.sh")"
    URL="$(raw_url || true)"
    FETCHED=0
    if [[ -n "${URL}" ]]; then
        if command -v curl >/dev/null 2>&1; then
            curl -fsSL --max-time 60 "${URL}" -o "${TMP_INSTALLER}" 2>/dev/null && FETCHED=1
        elif command -v wget >/dev/null 2>&1; then
            wget -qO "${TMP_INSTALLER}" "${URL}" 2>/dev/null && FETCHED=1
        fi
        if [[ "${FETCHED}" == "1" ]]; then
            RESOLVED_HOW="downloaded (${REF})"
        fi
    fi

    if [[ "${FETCHED}" != "1" ]]; then
        # Unknown host or no raw endpoint: get the branch with git instead.
        command -v git >/dev/null 2>&1 \
            || die "cannot download the installer and git is not installed (see --installer)"
        CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codx-junior-src.XXXXXX")"
        log "Fetching the installer with git (${REPO} @ ${REF})"
        git clone --depth 1 --branch "${REF}" "${REPO}" "${CLONE_DIR}" >/dev/null 2>&1 \
            || die "cannot clone ${REPO} at ${REF} (does the branch exist?)"
        REAL_INSTALLER="${CLONE_DIR}/codx-junior-installer/cloud/bootstrap.sh"
        [[ -f "${REAL_INSTALLER}" ]] \
            || die "unexpected repository layout: ${REAL_INSTALLER} is missing"
        RESOLVED_HOW="cloned (${REF})"
    else
        REAL_INSTALLER="${TMP_INSTALLER}"
    fi
fi

# --- forward everything explicitly (flags survive sudo, env vars do not) ----
FORWARD=(--ref "${REF}")
[[ -n "${PROVIDER}" ]]      && FORWARD+=(--provider "${PROVIDER}")
[[ -n "${API_KEY}" ]]       && FORWARD+=(--key "${API_KEY}")
[[ -n "${API_KEY_FILE}" ]]  && FORWARD+=(--key-file "${API_KEY_FILE}")
[[ -n "${API_URL}" ]]       && FORWARD+=(--url "${API_URL}")
[[ -n "${MODEL}" ]]         && FORWARD+=(--model "${MODEL}")
[[ -n "${PORT}" ]]          && FORWARD+=(--port "${PORT}")
[[ -n "${PROJECTS}" ]]      && FORWARD+=(--projects "${PROJECTS}")
[[ -n "${HOME_DIR}" ]]      && FORWARD+=(--home "${HOME_DIR}")
[[ -n "${ENV_FILE}" ]]      && FORWARD+=(--env-file "${ENV_FILE}")
[[ -n "${REPO}" ]]          && FORWARD+=(--repo "${REPO}")
[[ "${BUILD}" == "1" ]]             && FORWARD+=(--build)
[[ "${INSTALL_DOCKER}" == "0" ]]    && FORWARD+=(--no-docker-install)
[[ "${OPEN_FIREWALL}" == "1" ]]     && FORWARD+=(--open-firewall)

# --- announce --------------------------------------------------------------
log "codx-junior install.sh ${INSTALLER_VERSION}"
log "  branch    : ${REF}"
log "  provider  : ${PROVIDER:-<from ${ENV_FILE:-the settings file}>}${MODEL:+ (model ${MODEL})}"
log "  port      : ${PORT:-<from the settings file, else 19981>}"
log "  installer : ${REAL_INSTALLER}"
log "  resolved  : ${RESOLVED_HOW}"
if [[ "${UPDATE_ONLY}" == "1" ]]; then
    log "  mode      : update (existing projects and settings are kept)"
fi

if [[ "${DRY_RUN}" == "1" ]]; then
    RUNAS="bash"
    [[ "${EUID}" -eq 0 ]] || RUNAS="sudo"
    log "dry run — nothing was changed. This is the command:"
    printf '\n  %s %s' "${RUNAS}" "${REAL_INSTALLER}"
    printf ' %q' "${FORWARD[@]}"
    printf '\n\n'
    exit 0
fi

# --- hand over to the real installer (root is required from here on) --------
if [[ "${EUID}" -eq 0 ]]; then
    exec bash "${REAL_INSTALLER}" "${FORWARD[@]}"
fi

# No silent escalation: installing Docker needs root, so say what to run instead
# of asking for a password in the middle of a pipe.
chmod +x "${REAL_INSTALLER}" 2>/dev/null || true
warn "Root is required to install Docker and start the stack."
printf '\n  Re-run it as root:\n\n    sudo %s' "${REAL_INSTALLER}"
printf ' %q' "${FORWARD[@]}"
printf '\n\n'
exit 2
