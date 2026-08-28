#!/usr/bin/env bash
#SBATCH --job-name=cond_dplm2
#SBATCH --account=p201418
#SBATCH --partition=gpu
#SBATCH --qos=long
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --time=24:00:00
#SBATCH --output=logs/slurm/cond_dplm2_%j.out
#SBATCH --error=logs/slurm/cond_dplm2_%j.err
#
# Conditional DPLM-2 FULL training on Meluxina (A100 40GB, 4 GPUs/node),
# running inside the Apptainer container.
#
# Submit (AFTER the smoke test passes):
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch
#
# Multi-node (e.g. 2 nodes × 4 GPUs):
#   sbatch --nodes=2 scripts/meluxina/cond_dplm2_train.sbatch
#
# Override hyperparameters by appending Hydra flags after `--`:
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch -- train.lr=1e-4 trainer.max_steps=50000
#
# Env vars: see scripts/meluxina/common.sh (DPLM_SIF, WANDB_API_KEY, …).

set -Eeuo pipefail

# Parastation/SLURM executes this script from a spool copy, so __FILE__-based
# path resolution breaks. Use SLURM_SUBMIT_DIR (the submission cwd) instead.
ROOT_DIR="${ROOT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "${ROOT_DIR}"
mkdir -p logs/slurm
source "${ROOT_DIR}/scripts/meluxina/common.sh"

# --- Resolve node count (SLURM --nodes takes precedence) ---
NUM_NODES="${SLURM_NNODES:-${NUM_NODES:-1}}"
GPUS_PER_NODE="${GPUS_PER_NODE:-4}"
echo "[meluxina] SLURM_JOB_ID=${SLURM_JOB_ID:-unknown}"
echo "[meluxina] SLURM_NODELIST=${SLURM_NODELIST:-unknown}"
echo "[meluxina] nodes=${NUM_NODES}  gpus/node=${GPUS_PER_NODE}"
echo "[meluxina] CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"

if [[ -z "${WANDB_API_KEY:-}" && "${WANDB_MODE:-online}" == "online" ]]; then
  echo "[meluxina] WARNING: WANDB_API_KEY not set; set WANDB_MODE=offline to log locally only."
fi

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

echo "[meluxina] (in container) python train.py ${HYDRA_ARGS[*]} ${HYDRA_OVERRIDES[*]:-}"
run_in_container python train.py "${HYDRA_ARGS[@]}" ${HYDRA_OVERRIDES[@]+"${HYDRA_OVERRIDES[@]}"}
