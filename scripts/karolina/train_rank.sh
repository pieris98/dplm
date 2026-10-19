#!/usr/bin/env bash
# Per-rank launcher for MULTI-NODE training on Karolina (qgpu).
# Called by srun from cond_dplm2_train_multinode.sbatch — one task per GPU.
# Expects from the environment (exported by the parent sbatch):
#   MAX_STEPS, MT (max_tokens/rank), RESUME_ARGS (optional space-separated
#   hydra overrides), MASTER_ADDR, MASTER_PORT, WANDB_MODE, ACCOUNT.
set -Eeuo pipefail
ROOT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${ROOT_DIR}"
source scripts/meluxina/common.sh >/dev/null 2>&1

export RANK="${SLURM_PROCID:-0}"
export LOCAL_RANK="${SLURM_LOCALID:-0}"
export WORLD_SIZE="${SLURM_NTASKS:-1}"

echo "[rank $RANK/$WORLD_SIZE] node=$(hostname -s) local=$LOCAL_RANK cuda=$CUDA_VISIBLE_DEVICES"
exec run_in_container "$DPLM_PY" train.py \
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina \
  trainer=meluxina_ddp_bf16 \
  trainer.num_nodes="${SLURM_NNODES:-1}" \
  trainer.devices=1 \
  datamodule.max_tokens="${MT}" \
  trainer.max_steps="${MAX_STEPS}" \
  logger=wandb logger.wandb.offline=True \
  ${RESUME_ARGS:-}
