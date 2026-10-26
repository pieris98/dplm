#!/usr/bin/env bash
# Per-NODE launcher for MULTI-NODE training on Karolina (qgpu).
# Called by srun from cond_dplm2_train_multinode.sbatch — ONE task per node,
# holding all 8 GPUs of the node in its cgroup. torchrun (inside the
# container) spawns the 8 worker processes and sets their
# RANK/LOCAL_RANK/WORLD_SIZE/TORCHELASTIC_* env itself; Lightning's
# TorchElasticEnvironment auto-detects that (is_torchelastic_launched).
#
# Expects from the environment (exported by the parent sbatch):
#   MAX_STEPS, MT (max_tokens/rank), RESUME_ARGS (optional space-separated
#   hydra overrides), HYDRA_ARGS (optional extra hydra overrides),
#   MASTER_ADDR, MASTER_PORT, WANDB_MODE, ACCOUNT.
#   SLURM_NNODES, SLURM_NODEID (host env of the srun task).
set -Eeuo pipefail
ROOT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "${ROOT_DIR}"

NNODES="${SLURM_NNODES:-1}"
NODE_RANK="${SLURM_NODEID:-0}"
NPROC_PER_NODE=8   # A100s per Karolina GPU node
WORLD_SIZE=$((NNODES * NPROC_PER_NODE))

# keep stderr visible: a silently-failed source cost us a debug cycle
source scripts/meluxina/common.sh

echo "[node_rank $NODE_RANK/$NNODES] node=$(hostname -s) gpus=$NPROC_PER_NODE world=$WORLD_SIZE port=$MASTER_PORT"
# NOTE: run_in_container is a bash FUNCTION from common.sh (retry-wrapped
# apptainer exec) — never `exec` it (exec only launches binaries → exit 127;
# bit us on the first smoke, job 5259738).
#
# trainer.num_nodes/devices are the PHYSICAL truth (2 nodes x 8 GPUs): PL's
# SubprocessScriptLauncher validates num_nodes*devices == WORLD_SIZE even for
# externally-launched elastic runs (smoke 5259927 follow-up). torchrun sets
# every worker's RANK/LOCAL_RANK/WORLD_SIZE — no manual env forwarding needed,
# and each worker's cgroup contains ALL 8 GPUs of the node, so NCCL's NVML
# topology probe resolves every bus id (the per-task confinement that caused
# 'nvmlDeviceGetHandleByPciBusId: Not Found' is gone).
run_in_container "$DPLM_PY" -m torch.distributed.run \
  --nnodes="$NNODES" \
  --node_rank="$NODE_RANK" \
  --nproc_per_node="$NPROC_PER_NODE" \
  --master_addr="$MASTER_ADDR" \
  --master_port="$MASTER_PORT" \
  train.py \
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina \
  trainer=karolina_ddp_bf16 \
  trainer.num_nodes="$NNODES" \
  trainer.devices="$NPROC_PER_NODE" \
  datamodule.max_tokens="${MT:-1000}" \
  trainer.max_steps="${MAX_STEPS:-100000}" \
  logger=wandb logger.wandb.offline=True \
  train.test=false \
  ${RESUME_ARGS:-} ${HYDRA_ARGS:-}
