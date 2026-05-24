#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

PYTHON_VERSION="${PYTHON_VERSION:-3.9}"
CUDA_WHEEL_INDEX="${CUDA_WHEEL_INDEX:-https://download.pytorch.org/whl/cu121}"
TORCH_VERSION="${TORCH_VERSION:-2.2.0}"
TORCHVISION_VERSION="${TORCHVISION_VERSION:-0.17.0}"
TORCHTEXT_VERSION="${TORCHTEXT_VERSION:-0.17.0}"
RECREATE_ENV="${RECREATE_ENV:-0}"

usage() {
  cat <<'EOF'
Usage: scripts/leonardo/01_create_conda_env.sh

Creates the DPLM conda environment and installs Python dependencies while the
login node has internet access.

Useful overrides:
  DPLM_CONDA_ACTIVATE=/path/to/miniconda/bin/activate
  DPLM_CONDA_ENV=dplm
  PYTHON_VERSION=3.9
  RECREATE_ENV=1        Remove and recreate the conda env first.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v conda >/dev/null 2>&1; then
  [[ -f "${DPLM_CONDA_ACTIVATE}" ]] || fail "conda is not on PATH and activate script is missing: ${DPLM_CONDA_ACTIVATE}"
  set +u
  # shellcheck disable=SC1090
  source "${DPLM_CONDA_ACTIVATE}"
  set -u
fi

command -v conda >/dev/null 2>&1 || fail "conda is not on PATH; load your Leonardo conda/miniconda module first"

ensure_dirs
if [[ "${RECREATE_ENV}" == "1" ]]; then
  conda env remove -n "${DPLM_CONDA_ENV}" -y || true
fi

if conda env list | awk '{print $1}' | grep -qx "${DPLM_CONDA_ENV}"; then
  ok "conda environment '${DPLM_CONDA_ENV}' already exists"
else
  conda create -y -n "${DPLM_CONDA_ENV}" "python=${PYTHON_VERSION}" pip
  ok "created conda environment '${DPLM_CONDA_ENV}'"
fi

activate_dplm_env
python -m pip install --upgrade pip setuptools wheel
python -m pip install \
  "torch==${TORCH_VERSION}" \
  "torchvision==${TORCHVISION_VERSION}" \
  "torchtext==${TORCHTEXT_VERSION}" \
  --index-url "${CUDA_WHEEL_INDEX}"
python -m pip install -r "${ROOT_DIR}/requirements.txt"
python -m pip install "fairscale==0.4.6" "huggingface_hub[cli]"
ok "installed DPLM Python dependencies"
