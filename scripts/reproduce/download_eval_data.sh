#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DATA_DIR="${DATA_DIR:-${ROOT_DIR}/data-bin}"
SKIP_DOWNLOAD="${SKIP_DOWNLOAD:-0}"
FORCE_DOWNLOAD="${FORCE_DOWNLOAD:-0}"

usage() {
  cat <<'EOF'
Usage: scripts/reproduce/download_eval_data.sh [--skip-download] [--force]

Downloads the released DPLM-2 evaluation metadata via scripts/download_metadata.sh
and verifies the forward-folding inputs used by the reproduction harness.

Environment:
  DATA_DIR          Data root to verify. Default: ./data-bin
  SKIP_DOWNLOAD    Set to 1 to only verify local files.
  FORCE_DOWNLOAD   Set to 1 to run the downloader even if required files exist.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-download)
      SKIP_DOWNLOAD=1
      shift
      ;;
    --force)
      FORCE_DOWNLOAD=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

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

required_files=(
  "${DATA_DIR}/cameo2022/aatype.fasta"
  "${DATA_DIR}/cameo2022/struct.fasta"
  "${DATA_DIR}/metadata/pdb_afdb_cameo.csv"
)

missing_required=0
for path in "${required_files[@]}"; do
  [[ -s "${path}" ]] || missing_required=1
done

if [[ "${SKIP_DOWNLOAD}" != "1" && ( "${FORCE_DOWNLOAD}" == "1" || "${missing_required}" == "1" ) ]]; then
  ok "running scripts/download_metadata.sh"
  (
    cd "${ROOT_DIR}"
    bash scripts/download_metadata.sh
  )
elif [[ "${SKIP_DOWNLOAD}" == "1" ]]; then
  ok "skipping download; verifying local files only"
else
  ok "required CAMEO metadata files already exist"
fi

for path in "${required_files[@]}"; do
  [[ -s "${path}" ]] || fail "required file is missing or empty: ${path}"
  ok "verified ${path#${ROOT_DIR}/}"
done

optional_pdb_date=(
  "${DATA_DIR}/PDB_date/aatype.fasta"
  "${DATA_DIR}/PDB_date/struct.fasta"
)

if [[ -d "${DATA_DIR}/PDB_date" ]]; then
  for path in "${optional_pdb_date[@]}"; do
    [[ -s "${path}" ]] || fail "PDB_date directory exists but file is missing or empty: ${path}"
    ok "verified ${path#${ROOT_DIR}/}"
  done
else
  warn "optional PDB_date split is not present under ${DATA_DIR}"
fi

ok "evaluation data are ready"
