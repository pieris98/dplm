#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

DEVICE="${DEVICE:-auto}"
RUN_HEAVY_MODEL_LOAD="${RUN_HEAVY_MODEL_LOAD:-0}"
EXPECT_CUDA="${EXPECT_CUDA:-required}"

load_dplm_runtime_env
ensure_dirs
activate_dplm_env
cd "${ROOT_DIR}"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1

EXPECT_CUDA="${EXPECT_CUDA}" RUN_MODEL_SMOKE=0 bash scripts/reproduce/check_env.sh

args=(--mode offline --device "${DEVICE}")
if [[ "${RUN_HEAVY_MODEL_LOAD}" != "1" ]]; then
  args+=(--skip-heavy-model-load)
fi

python scripts/leonardo/offline_compute_check.py "${args[@]}"

ok "compute-node offline test passed"
