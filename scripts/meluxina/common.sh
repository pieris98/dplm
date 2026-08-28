#!/usr/bin/env bash
# Shared launcher library for Meluxina conditional-DPLM2 jobs.
#
# Sourced by the sbatch scripts. Responsibilities:
#   1. Locate the Apptainer SIF (pulled once from Docker Hub).
#   2. Set up container-safe environment (writable bind-mounts for caches,
#      wandb, logs, checkpoints).
#   3. Provide `run_in_container CMD...` that executes inside the SIF with
#      GPU access (--nv), the repo bind-mounted at the same path, and all
#      relevant env vars forwarded.

# --- Locate the SIF -------------------------------------------------------
# Order: $DPLM_SIF, then $SCRATCH/dplm_cond.sif, then repo root.
DPLM_SIF="${DPLM_SIF:-${SCRATCH:-$HOME}/dplm_cond.sif}"
if [[ ! -f "$DPLM_SIF" ]]; then
  # fall back to searching a couple of common spots
  for cand in "$HOME/dplm_cond.sif" "$(pwd)/dplm_cond.sif"; do
    [[ -f "$cand" ]] && DPLM_SIF="$cand" && break
  done
fi
if [[ ! -f "$DPLM_SIF" ]]; then
  echo "[meluxina] ERROR: Apptainer image not found."
  echo "[meluxina] Pull it first (one-time, on a login node):"
  echo "  module load Apptainer"
  echo "  apptainer pull ${SCRATCH:-\$HOME}/dplm_cond.sif docker://pieris98/dplm:cu121-torch220-cond"
  exit 1
fi
echo "[meluxina] SIF: ${DPLM_SIF}"

# --- Writable paths (bind-mounted into the container) ---------------------
# The repo lives on the shared FS and is mounted read-write so checkpoints,
# wandb logs, and generation outputs persist after the job.
REPO_DIR="${ROOT_DIR}"
RUN_SCRATCH="${RUN_SCRATCH:-${SCRATCH:-/tmp}/dplm_run_${SLURM_JOB_ID:-manual}}"
mkdir -p "${RUN_SCRATCH}/hf" "${RUN_SCRATCH}/tmp"

# --- Environment forwarded into the container ------------------------------
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export WANDB_PROJECT="${WANDB_PROJECT:-CondDPLM2_650m}"

# Distributed env (torchrun/Lightning reads these when multi-node)
export MASTER_ADDR="${MASTER_ADDR:-$(hostname)}"
export MASTER_PORT="${MASTER_PORT:-29500}"

# --- The launcher -----------------------------------------------------------
run_in_container() {
  apptainer exec \
    --nv \
    --bind "${REPO_DIR}:/workspace/dplm" \
    --bind "${RUN_SCRATCH}/hf:/opt/huggingface" \
    --bind "${RUN_SCRATCH}/tmp:/tmp" \
    --env OMP_NUM_THREADS="${OMP_NUM_THREADS}" \
    --env TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM}" \
    --env PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF}" \
    --env WANDB_API_KEY="${WANDB_API_KEY:-}" \
    --env WANDB_PROJECT="${WANDB_PROJECT}" \
    --env WANDB_MODE="${WANDB_MODE:-online}" \
    --env HF_HOME=/opt/huggingface \
    --env MASTER_ADDR="${MASTER_ADDR}" \
    --env MASTER_PORT="${MASTER_PORT}" \
    "${DPLM_SIF}" \
    "$@"
}
