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
# SLURM batch shells are minimal: they do NOT source ~/.bashrc or the full
# /etc/profile, so neither the module function nor module-loaded binaries
# carry over from the login shell. Initialize the module system explicitly.
if ! command -v apptainer >/dev/null 2>&1; then
  # Source the environment-modules init for bash if the module function is absent
  if ! command -v module >/dev/null 2>&1; then
    for init in /etc/profile.d/modules.sh /usr/share/modules/init/bash /etc/profile.modules; do
      [[ -f "$init" ]] && source "$init" >/dev/null 2>&1 && break
    done
  fi
  if command -v module >/dev/null 2>&1; then
    echo "[meluxina] loading Apptainer module (not inherited from login shell)"
    module load Apptainer 2>/dev/null || true
  fi
fi
if ! command -v apptainer >/dev/null 2>&1; then
  # Try common install prefixes on Meluxina
  for p in /opt/paraview/Apptainer/bin /usr/local/apptainer/bin /opt/apptainer/bin \
           /opt/cesga/apptainer/bin /mnt/tier2/opt/apptainer/bin; do
    [[ -x "$p/apptainer" ]] && export PATH="${p}:${PATH}" && break
  done
fi
if ! command -v apptainer >/dev/null 2>&1; then
  # APPTAINER_BIN override as last resort
  [[ -n "${APPTAINER_BIN:-}" && -x "${APPTAINER_BIN}/apptainer" ]] && \
    export PATH="${APPTAINER_BIN}:${PATH}"
fi
if ! command -v apptainer >/dev/null 2>&1; then
  echo "[meluxina] ERROR: apptainer not found after module load + prefix search."
  echo "[meluxina] On the LOGIN node, find the real path:"
  echo "  module load Apptainer; which apptainer"
  echo "then either:"
  echo "  (a) export APPTAINER_BIN=<dir-containing-apptainer> before sbatch, or"
  echo "  (b) symlink it: ln -s <path>/apptainer $HOME/bin/apptainer  (ensure ~/bin is on PATH)"
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
