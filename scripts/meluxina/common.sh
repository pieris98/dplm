#!/usr/bin/env bash
# Shared launcher library for Meluxina conditional-DPLM2 jobs.
#
# Sourced by the sbatch scripts. Responsibilities:
#   1. Locate the Apptainer SIF (pulled once from Docker Hub).
#   2. Bind-mount ONLY the code subdirectories that change (src/, configs/,
#      scripts/, the generation CLI) over the image's /workspace/dplm — so a
#      `git pull` updates the code without an image rebuild, while the image's
#      compiled vendor/openfold CUDA kernels and baked-in dataset stay visible.
#      (Mounting the WHOLE repo dir would shadow the built artifacts and break
#      openfold with `ModuleNotFoundError: attn_core_inplace_cuda`.)
#   3. Bind persistent writable dirs for logs / wandb / generation outputs
#      (the image filesystem is read-only).
#   4. Provide `run_in_container CMD...` that executes inside the SIF with
#      GPU access (--nv) and all relevant env vars forwarded.

# --- Ensure apptainer is available -----------------------------------------
# SLURM batch shells are minimal: they do NOT source ~/.bashrc, so a
# `module load Apptainer` done on the login node does not carry over.
if ! command -v apptainer >/dev/null 2>&1; then
  if command -v module >/dev/null 2>&1; then
    echo "[meluxina] loading Apptainer module (not inherited from login shell)"
    module load Apptainer 2>/dev/null || source /etc/profile.d/modules.sh 2>/dev/null && module load Apptainer
  fi
fi
if ! command -v apptainer >/dev/null 2>&1; then
  # Last resort: common install prefixes
  for p in /opt/paraview/Apptainer /usr/local/apptainer/bin /opt/apptainer/bin; do
    [[ -x "$p/apptainer" || -x "$p/bin/apptainer" ]] && export PATH="$p:$p/bin:${PATH}" && break
  done
fi
if ! command -v apptainer >/dev/null 2>&1; then
  echo "[meluxina] ERROR: apptainer not found. On the login node run:"
  echo "  module load Apptainer"
  echo "and either (a) add that to ~/.bashrc, or (b) find the binary:"
  echo "  module show Apptainer   # note the path it prepends"
  echo "and export APPTAINER_BIN=<that dir> before submitting."
  exit 1
fi

# --- Storage base ----------------------------------------------------------
# Meluxina project layout: no $SCRATCH. Use $PROJECT for large artifacts
# (the ~57 GB SIF, HF cache), falling back to $HOME if unset.
DPLM_BASE="${DPLM_BASE:-${PROJECT:-${SCRATCH:-$HOME}}}"

# --- Locate the SIF -------------------------------------------------------
# Order: $DPLM_SIF, then $DPLM_BASE/dplm_cond.sif, then repo root.
DPLM_SIF="${DPLM_SIF:-${DPLM_BASE}/dplm_cond.sif}"
if [[ ! -f "$DPLM_SIF" ]]; then
  for cand in "$HOME/dplm_cond.sif" "$(pwd)/dplm_cond.sif"; do
    [[ -f "$cand" ]] && DPLM_SIF="$cand" && break
  done
fi
if [[ ! -f "$DPLM_SIF" ]]; then
  echo "[meluxina] ERROR: Apptainer image not found."
  echo "[meluxina] Pull it first (one-time, on a login node):"
  echo "  module load Apptainer"
  echo "  apptainer pull ${DPLM_BASE}/dplm_cond.sif docker://pieris98/dplm:cu121-torch220-cond"
  exit 1
fi
echo "[meluxina] SIF: ${DPLM_SIF}"

# --- Repo checkout (source of fresh code) ----------------------------------
REPO_DIR="${ROOT_DIR}"

# --- Persistent writable dirs (bind-mounted into the container) ------------
DPLM_LOGS="${DPLM_LOGS:-${DPLM_BASE}/dplm-logs}"
DPLM_WANDB="${DPLM_WANDB:-${DPLM_BASE}/dplm-wandb}"
DPLM_GEN="${DPLM_GEN:-${DPLM_BASE}/dplm-gen}"
RUN_SCRATCH="${RUN_SCRATCH:-${DPLM_BASE}/dplm_run_${SLURM_JOB_ID:-manual}}"
mkdir -p "${DPLM_LOGS}" "${DPLM_WANDB}" "${DPLM_GEN}" \
         "${RUN_SCRATCH}/hf" "${RUN_SCRATCH}/tmp"

# --- Environment forwarded into the container ------------------------------
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export WANDB_PROJECT="${WANDB_PROJECT:-CondDPLM2_650m}"

# Distributed env (Lightning reads these when multi-node)
export MASTER_ADDR="${MASTER_ADDR:-$(hostname)}"
export MASTER_PORT="${MASTER_PORT:-29500}"

# --- The launcher -----------------------------------------------------------
run_in_container() {
  apptainer exec \
    --nv \
    --bind "${REPO_DIR}/src:/workspace/dplm/src" \
    --bind "${REPO_DIR}/configs:/workspace/dplm/configs" \
    --bind "${REPO_DIR}/scripts:/workspace/dplm/scripts" \
    --bind "${REPO_DIR}/train.py:/workspace/dplm/train.py" \
    --bind "${REPO_DIR}/generate_conditional_dplm2.py:/workspace/dplm/generate_conditional_dplm2.py" \
    --bind "${DPLM_LOGS}:/workspace/dplm/logs" \
    --bind "${DPLM_WANDB}:/workspace/dplm/wandb" \
    --bind "${DPLM_GEN}:/workspace/dplm/generation-results" \
    --bind "${RUN_SCRATCH}/hf:/opt/huggingface" \
    --bind "${RUN_SCRATCH}/tmp:/tmp" \
    --env OMP_NUM_THREADS="${OMP_NUM_THREADS}" \
    --env TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM}" \
    --env PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF}" \
    --env WANDB_API_KEY="${WANDB_API_KEY:-}" \
    --env WANDB_PROJECT="${WANDB_PROJECT}" \
    --env WANDB_MODE="${WANDB_MODE:-online}" \
    --env WANDB_DIR="/workspace/dplm/wandb" \
    --env HF_HOME=/opt/huggingface \
    --env MASTER_ADDR="${MASTER_ADDR}" \
    --env MASTER_PORT="${MASTER_PORT}" \
    --pwd /workspace/dplm \
    "${DPLM_SIF}" \
    "$@"
}
