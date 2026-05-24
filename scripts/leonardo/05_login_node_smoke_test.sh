#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

RUN_MODEL_SMOKE="${RUN_MODEL_SMOKE:-0}"
EXPECT_CUDA="${EXPECT_CUDA:-auto}"

load_dplm_runtime_env
ensure_dirs
activate_dplm_env
cd "${ROOT_DIR}"

EXPECT_CUDA="${EXPECT_CUDA}" RUN_MODEL_SMOKE="${RUN_MODEL_SMOKE}" bash scripts/reproduce/check_env.sh
python scripts/leonardo/offline_compute_check.py --mode online --skip-heavy-model-load

ok "login-node smoke test passed"
