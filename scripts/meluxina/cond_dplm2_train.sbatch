#!/usr/bin/env bash
#SBATCH --job-name=cond_dplm2
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --time=24:00:00
#SBATCH --output=logs/slurm/cond_dplm2_%j.out
#SBATCH --error=logs/slurm/cond_dplm2_%j.err
#
# Conditional DPLM-2 training on Meluxina (A100 40GB, 4 GPUs/node).
#
# Submit:
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch
#
# Multi-node (e.g. 2 nodes × 4 GPUs):
#   sbatch --nodes=2 scripts/meluxina/cond_dplm2_train.sbatch
#
# Override hyperparameters by appending Hydra flags after `--`:
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch -- train.lr=1e-4 trainer.max_steps=50000
#
# Required env vars (set in your submission environment or ~/.bashrc):
#   WANDB_API_KEY   — for wandb logging (set `WANDB_MODE=offline` to disable)
# Optional env vars:
#   DOCKER_IMAGE    — the dplm docker image (default: dplm:cu121-torch220)
#   NUM_NODES       — overrides --nodes if set
#   MAX_STEPS       — overrides trainer.max_steps
#   LR              — overrides train.lr

set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "${ROOT_DIR}"
mkdir -p logs/slurm

# --- Resolve node count (SLURM --nodes takes precedence) ---
NUM_NODES="${SLURM_NNODES:-${NUM_NODES:-1}}"
GPUS_PER_NODE="${GPUS_PER_NODE:-4}"
echo "[meluxina] SLURM_JOB_ID=${SLURM_JOB_ID:-unknown}"
echo "[meluxina] SLURM_NODELIST=${SLURM_NODELIST:-unknown}"
echo "[meluxina] nodes=${NUM_NODES}  gpus/node=${GPUS_PER_NODE}"
echo "[meluxina] CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"

# --- Environment for stable distributed training ---
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/dplm-matplotlib-cache}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-${TMPDIR:-/tmp}/dplm-triton-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${TMPDIR:-/tmp}/dplm-xdg-cache}"
export HF_HOME="${HF_HOME:-${SCRATCH:-${HOME}}/.cache/huggingface}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-${HF_HOME}/datasets}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}/transformers}"
# wandb
export WANDB_PROJECT="${WANDB_PROJECT:-CondDPLM2_650m}"
if [[ -z "${WANDB_API_KEY:-}" && "${WANDB_MODE:-online}" == "online" ]]; then
  echo "[meluxina] WARNING: WANDB_API_KEY not set; set WANDB_MODE=offline to log locally only."
fi

mkdir -p "${MPLCONFIGDIR}" "${TRITON_CACHE_DIR}" "${XDG_CACHE_HOME}" "${HF_DATASETS_CACHE}" "${TRANSFORMERS_CACHE}"

# --- Build the Hydra command ---
HYDRA_ARGS=(
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina
  trainer=meluxina_ddp_bf16
  trainer.num_nodes="${NUM_NODES}"
  trainer.devices="${GPUS_PER_NODE}"
  logger=wandb
)

# Optional overrides from env vars
[[ -n "${MAX_STEPS:-}" ]]    && HYDRA_ARGS+=("trainer.max_steps=${MAX_STEPS}")
[[ -n "${LR:-}" ]]           && HYDRA_ARGS+=("train.lr=${LR}")
if [[ "${WANDB_MODE:-online}" == "offline" ]]; then
  HYDRA_ARGS+=("logger.wandb.offline=True")
fi

# Pass through any `-- <hydra overrides>` from the sbatch command line
HYDRA_OVERRIDES=()
if [[ $# -gt 0 ]]; then
  HYDRA_OVERRIDES+=("$@")
fi

echo "[meluxina] python train.py ${HYDRA_ARGS[*]} ${HYDRA_OVERRIDES[*]:-}"
exec python train.py "${HYDRA_ARGS[@]}" "${HYDRA_OVERRIDES[@]}"
