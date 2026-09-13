#!/usr/bin/env bash
#SBATCH --job-name=cond_dplm2
#SBATCH --account=p201418
#SBATCH --partition=gpu
#SBATCH --qos=long
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
# 100K steps at max_tokens 4000×4 GPUs may exceed 24h; qos=long allows up to
# 6 days. Checkpoints land every 2000 steps, so an interrupted run is
# resumable with: ++trainer.resume_from_checkpoint=<ckpt path>
#SBATCH --time=3-00:00:00
#SBATCH --output=logs/slurm/cond_dplm2_%j.out
#SBATCH --error=logs/slurm/cond_dplm2_%j.err
#
# Conditional DPLM-2 FULL training on Meluxina (A100 40GB, 4 GPUs/node),
# running inside the Apptainer container.
#
# Submit (AFTER the smoke test passes):
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch
#
# NOTE: SINGLE-NODE ONLY as written. `--nodes=2` would leave node 2 idle —
# multi-node needs an srun-based launch (srun --ntasks-per-node=1 python
# train.py ...) which is NOT wired here yet. Ask before scaling out.
#
# Override hyperparameters by appending Hydra flags after `--`:
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch -- train.lr=1e-4 trainer.max_steps=50000
# Resume from a checkpoint (note the ++ — the key isn't in the config struct):
#   sbatch scripts/meluxina/cond_dplm2_train.sbatch -- ++trainer.resume_from_checkpoint=$PROJECT/dplm-logs/<run>/checkpoints/last.ckpt
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
run_in_container "${DPLM_PY}" train.py "${HYDRA_ARGS[@]}" ${HYDRA_OVERRIDES[@]+"${HYDRA_OVERRIDES[@]}"}
