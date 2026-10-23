#!/usr/bin/env bash
# Per-rank launcher for MULTI-NODE training on Karolina (qgpu).
# Called by srun from cond_dplm2_train_multinode.sbatch — one task per GPU.
# Expects from the environment (exported by the parent sbatch):
#   MAX_STEPS, MT (max_tokens/rank), RESUME_ARGS (optional space-separated
#   hydra overrides), HYDRA_ARGS (optional extra hydra overrides, e.g. probe
#   tuning for the smoke), MASTER_ADDR, MASTER_PORT, WANDB_MODE, ACCOUNT.
set -Eeuo pipefail
ROOT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${ROOT_DIR}"
# keep stderr visible: a silently-failed source cost us a debug cycle
source scripts/meluxina/common.sh

export RANK="${SLURM_PROCID:-0}"
export LOCAL_RANK="${SLURM_LOCALID:-0}"
export WORLD_SIZE="${SLURM_NTASKS:-1}"

echo "[rank $RANK/$WORLD_SIZE] node=$(hostname -s) local=$LOCAL_RANK cuda=$CUDA_VISIBLE_DEVICES"
# NOTE: run_in_container is a bash FUNCTION from common.sh (retry-wrapped
# apptainer exec) — never `exec` it (exec only launches binaries → exit 127;
# bit us on the first smoke, job 5259738).
run_in_container "$DPLM_PY" train.py \
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina \
  trainer=meluxina_ddp_bf16 \
  trainer.num_nodes="${SLURM_NNODES:-1}" \
  trainer.devices=1 \
  datamodule.max_tokens="${MT}" \
  trainer.max_steps="${MAX_STEPS}" \
  logger=wandb logger.wandb.offline=True \
  ${RESUME_ARGS:-} ${HYDRA_ARGS:-}
