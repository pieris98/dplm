#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

DOWNLOAD_PDB_SWISSPROT="${DOWNLOAD_PDB_SWISSPROT:-1}"
DOWNLOAD_EVAL_METADATA="${DOWNLOAD_EVAL_METADATA:-1}"
DOWNLOAD_CATH="${DOWNLOAD_CATH:-0}"
DOWNLOAD_UNIREF50="${DOWNLOAD_UNIREF50:-0}"

usage() {
  cat <<'EOF'
Usage: scripts/leonardo/04_download_training_data.sh

Downloads datasets while on the login node. PDB+SwissProt is enabled by default
because it is required for DPLM-2/DPLM-2 Bit training. CATH and UniRef50 are
optional because they are larger or for different recipes.

Useful overrides:
  DPLM_DATA_DIR=/shared/path/data-bin
  DOWNLOAD_PDB_SWISSPROT=1
  DOWNLOAD_EVAL_METADATA=1
  DOWNLOAD_CATH=1
  DOWNLOAD_UNIREF50=1
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

load_dplm_runtime_env
ensure_dirs
activate_dplm_env
cd "${ROOT_DIR}"

if [[ "${DPLM_DATA_DIR}" != "${ROOT_DIR}/data-bin" ]]; then
  mkdir -p "${DPLM_DATA_DIR}"
  if [[ ! -e "${ROOT_DIR}/data-bin" ]]; then
    ln -s "${DPLM_DATA_DIR}" "${ROOT_DIR}/data-bin"
    ok "linked ${ROOT_DIR}/data-bin -> ${DPLM_DATA_DIR}"
  elif [[ "$(cd "${ROOT_DIR}/data-bin" && pwd -P)" != "$(cd "${DPLM_DATA_DIR}" && pwd -P)" ]]; then
    warn "${ROOT_DIR}/data-bin already exists and is not ${DPLM_DATA_DIR}; downloads will use existing repo data-bin"
  fi
fi

if [[ "${DOWNLOAD_PDB_SWISSPROT}" == "1" ]]; then
  bash scripts/download_pdb_swissprot_hf.sh
fi

if [[ "${DOWNLOAD_EVAL_METADATA}" == "1" ]]; then
  bash scripts/reproduce/download_eval_data.sh --force
fi

if [[ "${DOWNLOAD_CATH}" == "1" ]]; then
  bash scripts/download_cath.sh
fi

if [[ "${DOWNLOAD_UNIREF50}" == "1" ]]; then
  bash scripts/download_uniref50_hf.sh
fi

ok "dataset download step finished"
