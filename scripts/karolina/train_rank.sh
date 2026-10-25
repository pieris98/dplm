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

# Export the torch env-init vars BEFORE sourcing common.sh: CONTAINER_ARGS
# (incl. the guarded --env forwarding) is built at source time — exporting
# after left the container without RANK/LOCAL_RANK/WORLD_SIZE and
# TorchElasticEnvironment.local_rank() died on KeyError('LOCAL_RANK')
# (smoke job 5259927).
export RANK="${SLURM_PROCID:-0}"
# LOCAL_RANK stays 0 for EVERY task: srun --gpus-per-task=1 gives each task a
# single visible GPU (CUDA_VISIBLE_DEVICES), so within the process the local
# rank is 0. Using SLURM_LOCALID here indexes devices=1's one-entry device
# list → IndexError on every task with localid>0 (caught in the local 2-GPU
# repro, PL ddp.py root_device).
export LOCAL_RANK=0
export WORLD_SIZE="${SLURM_NTASKS:-1}"

# keep stderr visible: a silently-failed source cost us a debug cycle
source scripts/meluxina/common.sh

echo "[rank $RANK/$WORLD_SIZE] node=$(hostname -s) local=$LOCAL_RANK cuda=$CUDA_VISIBLE_DEVICES"
# NOTE: run_in_container is a bash FUNCTION from common.sh (retry-wrapped
# apptainer exec) — never `exec` it (exec only launches binaries → exit 127;
# bit us on the first smoke, job 5259738).
# num_nodes/devices stay 1: the container deliberately does NOT see SLURM_*
# (Lightning's SLURMEnvironment would demand ntasks-per-node == devices),
# so Lightning joins the env:// world of WORLD_SIZE via _DefaultEnvironment —
# each of these processes IS one device of that world.
run_in_container "$DPLM_PY" train.py \
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina \
  trainer=karolina_ddp_bf16 \
  datamodule.max_tokens="${MT}" \
  trainer.max_steps="${MAX_STEPS}" \
  logger=wandb logger.wandb.offline=True \
  train.test=false \
  ${RESUME_ARGS:-} ${HYDRA_ARGS:-}
