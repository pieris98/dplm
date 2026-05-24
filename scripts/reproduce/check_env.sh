#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_ACTIVATE="${CONDA_ACTIVATE:-${CONDA_PREFIX:-}/bin/activate}"
CONDA_ENV="${CONDA_ENV:-dplm}"
EXPECT_CUDA="${EXPECT_CUDA:-auto}"
RUN_MODEL_SMOKE="${RUN_MODEL_SMOKE:-0}"

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

if [[ -f "${CONDA_ACTIVATE}" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "${CONDA_ACTIVATE}" "${CONDA_ENV}"
  set -u
  ok "activated conda environment '${CONDA_ENV}'"
else
  warn "conda activate script not found at ${CONDA_ACTIVATE}; using current Python"
fi

cd "${ROOT_DIR}"

export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/dplm-matplotlib-cache}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-/tmp/dplm-triton-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/dplm-xdg-cache}"
mkdir -p "${MPLCONFIGDIR}" "${TRITON_CACHE_DIR}" "${XDG_CACHE_HOME}"

python - <<'PY'
import importlib
import sys

packages = {
    "torch": "torch",
    "byprot": "byprot",
    "transformers": "transformers",
    "esm": "esm",
    "openfold": "openfold",
    "fairscale": "fairscale",
    "mdtraj": "mdtraj",
    "Bio": "biopython",
}

missing = []
for module, label in packages.items():
    try:
        imported = importlib.import_module(module)
    except Exception as exc:
        missing.append((label, module, repr(exc)))
    else:
        version = getattr(imported, "__version__", "unknown")
        print(f"OK: import {module} ({label}) version={version}")

if missing:
    print("\nMissing or broken Python imports:", file=sys.stderr)
    for label, module, exc in missing:
        print(f"  - {label} ({module}): {exc}", file=sys.stderr)
    sys.exit(1)
PY

python - "${EXPECT_CUDA}" <<'PY'
import sys
import torch

expect = sys.argv[1].lower()
visible = torch.cuda.is_available()
count = torch.cuda.device_count() if visible else 0

print(f"OK: torch={torch.__version__}")
print(f"OK: cuda_available={visible} cuda_device_count={count}")
if visible:
    for idx in range(count):
        print(f"OK: cuda_device[{idx}]={torch.cuda.get_device_name(idx)}")

if expect in {"1", "true", "yes", "required"} and not visible:
    raise SystemExit("ERROR: CUDA was expected but torch.cuda.is_available() is false")
if expect in {"0", "false", "no", "disabled"} and visible:
    print("WARN: CUDA is visible even though EXPECT_CUDA requested no CUDA", file=sys.stderr)
PY

for tool in analysis/TMscore analysis/TMalign; do
  [[ -f "${tool}" ]] || fail "${tool} is missing"
  [[ -x "${tool}" ]] || fail "${tool} exists but is not executable; run: chmod +x ${tool}"
  ok "${tool} exists and is executable"
done

for exe in foldseek colabfold_batch; do
  if command -v "${exe}" >/dev/null 2>&1; then
    ok "optional tool '${exe}' found at $(command -v "${exe}")"
  else
    warn "optional tool '${exe}' not found on PATH"
  fi
done

if [[ -d vendor/ProteinMPNN ]]; then
  ok "optional vendor/ProteinMPNN directory exists"
else
  warn "optional vendor/ProteinMPNN directory is missing"
fi

if [[ "${RUN_MODEL_SMOKE}" == "1" ]]; then
  python scripts/reproduce/smoke_dplm2_load.py
else
  ok "skipped model-load smoke test; set RUN_MODEL_SMOKE=1 to load pretrained checkpoints"
fi
