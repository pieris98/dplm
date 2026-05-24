#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/leonardo/07_run_all_login_setup.sh

Runs all login-node setup steps in order:
  1. write runtime env file
  2. create/install conda environment
  3. install this repo editable
  4. download model checkpoints
  5. download default DPLM-2 training/eval data
  6. run login-node smoke tests

Common overrides:
  DPLM_CONDA_ACTIVATE=/path/to/miniconda/bin/activate
  DPLM_CONDA_ENV=dplm
  DPLM_INSTALL_ROOT=/shared/scratch/$USER/dplm_reproduce
  DPLM_DATA_DIR=/shared/scratch/$USER/dplm_reproduce/data-bin
  DOWNLOAD_CATH=1 DOWNLOAD_UNIREF50=1
  RUN_MODEL_SMOKE=1
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

bash "${SCRIPT_DIR}/00_write_runtime_env.sh"
bash "${SCRIPT_DIR}/01_create_conda_env.sh"
bash "${SCRIPT_DIR}/02_install_repo_editable.sh"
bash "${SCRIPT_DIR}/03_download_model_checkpoints.sh"
bash "${SCRIPT_DIR}/04_download_training_data.sh"
bash "${SCRIPT_DIR}/05_login_node_smoke_test.sh"

printf '\nLogin-node setup finished.\n'
printf 'Before compute jobs, source:\n  source %s/env.sh\n' "${SCRIPT_DIR}"
printf 'Then run the offline compute check inside an allocation/job:\n  bash %s/06_compute_node_offline_test.sh\n' "${SCRIPT_DIR}"
