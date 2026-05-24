#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

load_dplm_runtime_env
ensure_dirs
activate_dplm_env

cd "${ROOT_DIR}"
python -m pip install -e .

python - <<'PY'
import byprot
import openfold
print("OK: byprot importable")
print("OK: openfold importable")
PY

ok "installed repository in editable mode"
