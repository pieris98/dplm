#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DPLM_CONDA_ACTIVATE="${DPLM_CONDA_ACTIVATE:-${CONDA_PREFIX:-}/bin/activate}"
DPLM_CONDA_ENV="${DPLM_CONDA_ENV:-dplm}"
DPLM_INSTALL_ROOT="${DPLM_INSTALL_ROOT:-${SCRATCH:-${WORK:-${HOME}}}/dplm_reproduce}"
DPLM_CACHE_ROOT="${DPLM_CACHE_ROOT:-${DPLM_INSTALL_ROOT}/cache}"
DPLM_DATA_DIR="${DPLM_DATA_DIR:-${ROOT_DIR}/data-bin}"
DPLM_LOG_DIR="${DPLM_LOG_DIR:-${ROOT_DIR}/logs}"
DPLM_ENV_FILE="${DPLM_ENV_FILE:-${ROOT_DIR}/scripts/leonardo/env.sh}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

ok() {
  printf 'OK: %s\n' "$*"
}

activate_dplm_env() {
  if [[ -f "${DPLM_CONDA_ACTIVATE}" ]]; then
    set +u
    # shellcheck disable=SC1090
    source "${DPLM_CONDA_ACTIVATE}" "${DPLM_CONDA_ENV}"
    set -u
    ok "activated conda environment '${DPLM_CONDA_ENV}'"
  else
    fail "conda activate script not found: ${DPLM_CONDA_ACTIVATE}"
  fi
}

load_dplm_runtime_env() {
  if [[ -f "${DPLM_ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${DPLM_ENV_FILE}"
    ok "loaded runtime env file ${DPLM_ENV_FILE}"
  else
    warn "runtime env file not found: ${DPLM_ENV_FILE}; using process environment"
  fi
}

ensure_dirs() {
  mkdir -p \
    "${DPLM_INSTALL_ROOT}" \
    "${DPLM_CACHE_ROOT}" \
    "${DPLM_DATA_DIR}" \
    "${DPLM_LOG_DIR}" \
    "${DPLM_CACHE_ROOT}/huggingface" \
    "${DPLM_CACHE_ROOT}/torch" \
    "${DPLM_CACHE_ROOT}/triton" \
    "${DPLM_CACHE_ROOT}/matplotlib" \
    "${DPLM_CACHE_ROOT}/xdg"
}

print_context() {
  printf 'ROOT_DIR=%s\n' "${ROOT_DIR}"
  printf 'DPLM_CONDA_ACTIVATE=%s\n' "${DPLM_CONDA_ACTIVATE}"
  printf 'DPLM_CONDA_ENV=%s\n' "${DPLM_CONDA_ENV}"
  printf 'DPLM_INSTALL_ROOT=%s\n' "${DPLM_INSTALL_ROOT}"
  printf 'DPLM_CACHE_ROOT=%s\n' "${DPLM_CACHE_ROOT}"
  printf 'DPLM_DATA_DIR=%s\n' "${DPLM_DATA_DIR}"
  printf 'DPLM_ENV_FILE=%s\n' "${DPLM_ENV_FILE}"
}
