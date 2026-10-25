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
# NCCL debug for the containerized multi-rank path: the first true 16-rank
# collectives died in NCCL topology init (nvmlDeviceGetHandleByPciBusId:
# Not Found — CUDA/NVML device-view mismatch under CUDA_VISIBLE_DEVICES
# masking in apptainer, smoke 2026-10-25). INFO shows the failing bus id.
export NCCL_DEBUG="${NCCL_DEBUG:-INFO}"
# ROOT CAUSE (same smoke): --cleanenv strips CUDA_VISIBLE_DEVICES and it was
# never forwarded — CUDA inside the task enumerated all 8 node GPUs while the
# SLURM cgroup only permits the assigned one, so NCCL's topology probe hit
# 'Not Found' on every blocked peer. Forwarding the task's mask makes CUDA
# see exactly the assigned GPU; NVML then resolves its single bus id.
echo "[rank] CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}"

# keep stderr visible: a silently-failed source cost us a debug cycle
source scripts/meluxina/common.sh

echo "[rank $RANK/$WORLD_SIZE] node=$(hostname -s) local=$LOCAL_RANK cuda=$CUDA_VISIBLE_DEVICES"
# NOTE: run_in_container is a bash FUNCTION from common.sh (retry-wrapped
# apptainer exec) — never `exec` it (exec only launches binaries → exit 127;
# bit us on the first smoke, job 5259738).
# num_nodes = WORLD_SIZE (16), devices = 1: Lightning's SubprocessScriptLauncher
# validates num_nodes * devices == WORLD_SIZE even when
# creates_processes_externally=True (it then just runs our already-launched
# process) — so num_nodes counts rank-slots here, not physical nodes.
# The container deliberately does NOT see SLURM_* (SLURMEnvironment would
# demand ntasks-per-node == devices and try to manage the world itself).
run_in_container "$DPLM_PY" train.py \
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina \
  trainer=karolina_ddp_bf16 \
  trainer.num_nodes="${WORLD_SIZE}" \
  trainer.devices=1 \
  datamodule.max_tokens="${MT}" \
  trainer.max_steps="${MAX_STEPS}" \
  logger=wandb logger.wandb.offline=True \
  train.test=false \
  ${RESUME_ARGS:-} ${HYDRA_ARGS:-}
