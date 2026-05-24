#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

MODELS="${MODELS:-airkingbd/dplm_650m airkingbd/dplm2_650m airkingbd/dplm2_bit_650m airkingbd/struct_tokenizer}"
LOCAL_MODEL_ROOT="${LOCAL_MODEL_ROOT:-${DPLM_INSTALL_ROOT}/models}"

usage() {
  cat <<'EOF'
Usage: scripts/leonardo/03_download_model_checkpoints.sh

Downloads Hugging Face model snapshots needed for DPLM-2 reproduction into the
shared HF cache and mirrors them under $DPLM_INSTALL_ROOT/models for auditability.

Useful overrides:
  MODELS="airkingbd/dplm_650m airkingbd/struct_tokenizer"
  HF_TOKEN=...          If the hub ever requires authentication.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

load_dplm_runtime_env
ensure_dirs
activate_dplm_env
mkdir -p "${LOCAL_MODEL_ROOT}"

python - "${LOCAL_MODEL_ROOT}" ${MODELS} <<'PY'
import sys
from pathlib import Path
from huggingface_hub import snapshot_download

local_root = Path(sys.argv[1])
models = sys.argv[2:]
if not models:
    raise SystemExit("ERROR: no model ids provided")

for repo_id in models:
    local_dir = local_root / repo_id.replace("/", "__")
    print(f"Caching {repo_id} in HF_HOME")
    snapshot_download(repo_id=repo_id, repo_type="model")
    print(f"Mirroring {repo_id} -> {local_dir}")
    snapshot_download(
        repo_id=repo_id,
        repo_type="model",
        local_dir=str(local_dir),
        local_dir_use_symlinks=False,
    )
    marker = local_dir / ".dplm_download_complete"
    marker.write_text(repo_id + "\n", encoding="utf-8")
    print(f"OK: {repo_id}")
PY

ok "model checkpoints are cached"
