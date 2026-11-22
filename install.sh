#!/usr/bin/env bash
# =============================================================================
# codx-junior — one-line installer / upgrader (thin front door)
#
# This file only resolves the real installer for the requested branch and hands
# the arguments over to it. That installer is the single place that knows the
# deployment modes, the compose files and the settings:
#
#   codx-junior-installer/install.sh
#
#   curl -fsSL https://raw.githubusercontent.com/codx-junior/codx-junior/main/install.sh \
#     -o install.sh
#   sudo ./install.sh --mode server --domain codx.example.com \
#        --acme-email ops@example.com --provider deepseek --key sk-...
#
#   sudo ./install.sh --mode local --provider deepseek --key sk-...
#   sudo ./install.sh --mode slave --domain team.example.com \
#        --proxy-network traefik --provider openai --key sk-...
#   sudo ./install.sh --mode dind --dind-mode server --domain acme.example.com \
#        --acme-email ops@example.com --provider deepseek --key sk-...
#
# Every flag of the real installer works here; see:
#   ./codx-junior-installer/install.sh --help
#
# Settings can also come from the environment or --env-file. Re-running is an
# upgrade: projects and settings are kept.
# =============================================================================
set -euo pipefail

INSTALLER_VERSION="2.0.0"
DEFAULT_REPO="https://github.com/codx-junior/codx-junior.git"
DEFAULT_REF="main"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

short_help() {
    cat <<'USAGE'
codx-junior installer (front door)

Usage: install.sh [options]          install or update
       install.sh status|logs|down|upgrade|doctor|config

Modes:
  --mode local     laptop/CI: loopback port, no TLS, no proxy
  --mode server    public server: Traefik master :80/:443 + ACME
  --mode slave     behind a master proxy: Traefik slave :80 only
  --mode dind      Docker-in-Docker instance (--dind-mode local|server|slave)
  Default: server as root, local otherwise.

Common settings:
  --provider NAME            openai deepseek gemini openrouter mistral groq
                             custom
  --key KEY | --key-file P   provider API key
  --url URL                  OpenAI-compatible endpoint (custom provider)
  --no-ai                    configure the provider in the UI instead
  --domain DOMAIN            public domain (server/slave/dind)
  --acme-email EMAIL         Let's Encrypt account (server/master)
  --proxy-network NAME       master network (slave)
  --projects PATH            host projects folder
  --home PATH                installation root
  --instance NAME            instance slug
  --coworking                mount the Docker socket (browser/desktops)
  --build | --build-dind     build the app / DIND image from this checkout
  --systemd | --no-systemd   register a systemd unit
  --dry-run                  print what would run; change nothing
  --non-interactive          never read stdin
  -h, --help | -V, --version

Full reference: codx-junior-installer/install.sh --help
USAGE
}

REF="${CODX_JUNIOR_REF:-${DEFAULT_REF}}"
REPO="${CODX_JUNIOR_REPO:-${DEFAULT_REPO}}"
EXPLICIT_INSTALLER=""
FORWARD=()
HELP=0
VERSION=0

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    a="${args[$i]}"
    next="${args[$((i + 1))]:-}"
    case "$a" in
        --ref|--branch|-b)
            [[ -n "${next}" ]] || die "$a needs a value"
            REF="${next}"
            FORWARD+=("${a}" "${next}")
            i=$((i + 1)) ;;
        --repo)
            [[ -n "${next}" ]] || die "--repo needs a value"
            REPO="${next}"
            FORWARD+=("${a}" "${next}")
            i=$((i + 1)) ;;
        --installer)
            [[ -n "${next}" ]] || die "--installer needs a value"
            EXPLICIT_INSTALLER="${next}"
            i=$((i + 1)) ;;
        -h|--help)   HELP=1; FORWARD+=("${a}") ;;
        -V|--version) VERSION=1; FORWARD+=("${a}") ;;
        *)           FORWARD+=("${a}") ;;
    esac
done

if [[ "${VERSION}" == "1" ]]; then
    printf 'codx-junior install.sh %s\n' "${INSTALLER_VERSION}"
    exit 0
fi

# Running from a checkout: use its installer, no network needed.
LOCAL_INSTALLER=""
if [[ -n "${EXPLICIT_INSTALLER}" ]]; then
    [[ -f "${EXPLICIT_INSTALLER}" ]] || die "installer not found: ${EXPLICIT_INSTALLER}"
    LOCAL_INSTALLER="${EXPLICIT_INSTALLER}"
elif [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${SCRIPT_DIR}/codx-junior-installer/install.sh" ]]; then
        LOCAL_INSTALLER="${SCRIPT_DIR}/codx-junior-installer/install.sh"
    fi
fi

if [[ "${HELP}" == "1" && -z "${LOCAL_INSTALLER}" ]]; then
    short_help
    exit 0
fi

if [[ -n "${LOCAL_INSTALLER}" ]]; then
    log "Using ${LOCAL_INSTALLER}"
    exec bash "${LOCAL_INSTALLER}" "${FORWARD[@]+"${FORWARD[@]}"}"
fi

# No checkout: fetch the real installer for REF and let it clone the runtime.
raw_url() {
    local url="${REPO%.git}" owner repo
    url="${url#git@github.com:}"
    url="${url#ssh://git@github.com/}"
    url="${url#https://github.com/}"
    [[ "${REPO}" == *github.com* && "${url}" == */* ]] || return 1
    owner="${url%%/*}"
    repo="${url#*/}"
    printf 'https://raw.githubusercontent.com/%s/%s/%s/codx-junior-installer/install.sh' \
        "${owner}" "${repo}" "${REF}"
}

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codx-junior-install.XXXXXX" 2>/dev/null || mktemp -d)"
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

TARGET="${TMP_DIR}/install.sh"
FETCHED=0
URL="$(raw_url || true)"
if [[ -n "${URL}" ]]; then
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 60 "${URL}" -o "${TARGET}" 2>/dev/null && FETCHED=1
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${TARGET}" "${URL}" 2>/dev/null && FETCHED=1
    fi
fi

if [[ "${FETCHED}" != "1" ]]; then
    command -v git >/dev/null 2>&1 \
        || die "cannot download the installer and git is not installed (use --installer PATH)"
    log "Fetching ${REPO} @ ${REF} with git"
    git clone --depth 1 --branch "${REF}" "${REPO}" "${TMP_DIR}/src" >/dev/null 2>&1 \
        || die "cannot clone ${REPO} at ${REF} (does the branch exist?)"
    TARGET="${TMP_DIR}/src/codx-junior-installer/install.sh"
    [[ -f "${TARGET}" ]] || die "unexpected repository layout: ${TARGET} is missing"
fi

log "Running the codx-junior installer (${REF})"
bash "${TARGET}" "${FORWARD[@]+"${FORWARD[@]}"}"
