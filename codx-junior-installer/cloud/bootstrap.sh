#!/usr/bin/env bash
# =============================================================================
# codx-junior — cloud bootstrap (compatibility shim)
#
# The real installer is `codx-junior-installer/install.sh`. This file exists so
# cloud-init files, AWS user-data and GCP startup scripts that already point at
# `cloud/bootstrap.sh` keep working while they are migrated.
#
# Old behaviour: plain HTTP on the published port, no domain, no TLS. That is
# now expressed as `--mode server --no-tls`; if the caller passes a domain or an
# explicit mode, it is respected.
#
#   sudo ./bootstrap.sh --provider deepseek --key sk-...
#   sudo ./bootstrap.sh --env-file /etc/codx-junior/settings.env --domain x.example.com
#
# Prefer the repository-root `install.sh` for new deployments.
# =============================================================================
set -euo pipefail

DEFAULT_REPO="https://github.com/codx-junior/codx-junior.git"
DEFAULT_REF="main"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER_DIR="$(cd "${SELF_DIR}/.." && pwd)"

die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

REF="${CODX_JUNIOR_REF:-${DEFAULT_REF}}"
REPO="${CODX_JUNIOR_REPO:-${DEFAULT_REPO}}"
EXPLICIT=""
FORWARD=()
MODE_GIVEN=0
DOMAIN_GIVEN=0

args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
    a="${args[$i]}"
    next="${args[$((i + 1))]:-}"
    case "$a" in
        --ref|--branch|-b) REF="${next}"; FORWARD+=("${a}" "${next}"); i=$((i + 1)) ;;
        --repo)            REPO="${next}"; FORWARD+=("${a}" "${next}"); i=$((i + 1)) ;;
        --installer)       EXPLICIT="${next}"; i=$((i + 1)) ;;
        --mode|-m|--mode=*) MODE_GIVEN=1; FORWARD+=("${a}") ;;
        --domain|-d|--domain=*) DOMAIN_GIVEN=1; FORWARD+=("${a}") ;;
        *) FORWARD+=("${a}") ;;
    esac
done

if [[ "${MODE_GIVEN}" == "0" && "${DOMAIN_GIVEN}" == "0" && -z "${CODX_JUNIOR_DOMAIN:-}" ]]; then
    FORWARD=(--mode server --no-tls "${FORWARD[@]}")
fi

if [[ -n "${EXPLICIT}" ]]; then
    TARGET="${EXPLICIT}"
elif [[ -f "${INSTALLER_DIR}/install.sh" ]]; then
    TARGET="${INSTALLER_DIR}/install.sh"
else
    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/codx-junior-bootstrap.XXXXXX" 2>/dev/null || mktemp -d)"
    cleanup() { rm -rf "${TMP_DIR}"; }
    trap cleanup EXIT
    TARGET="${TMP_DIR}/install.sh"
    URL="https://raw.githubusercontent.com/codx-junior/codx-junior/${REF}/codx-junior-installer/install.sh"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 60 "${URL}" -o "${TARGET}" || TARGET=""
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "${TARGET}" "${URL}" || TARGET=""
    fi
    if [[ -z "${TARGET}" || ! -f "${TARGET}" ]]; then
        command -v git >/dev/null 2>&1 \
            || die "cannot download the installer and git is not installed"
        git clone --depth 1 --branch "${REF}" "${REPO}" "${TMP_DIR}/src" >/dev/null 2>&1 \
            || die "cannot clone ${REPO} at ${REF}"
        TARGET="${TMP_DIR}/src/codx-junior-installer/install.sh"
    fi
fi

[[ -f "${TARGET}" ]] || die "installer not found: ${TARGET}"
exec bash "${TARGET}" "${FORWARD[@]+"${FORWARD[@]}"}"
