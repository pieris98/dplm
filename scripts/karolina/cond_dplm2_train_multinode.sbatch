#!/usr/bin/env bash
#SBATCH --job-name=cond_dplm2_ddp
#SBATCH --partition=qgpu
## IT4I canonical GPU form: --gpus N (--nodes M). Do NOT add --gres/--cpus-per-task/--mem
## here — IT4I docs mark that as "undesirable" on GPU queues and their submit
## filter rejects such real submissions ("AssocMaxSubmitJobLimit") even when
## --test-only passes. GPU→task binding happens on the srun step below.
#SBATCH --nodes=2
#SBATCH --gpus 16
#SBATCH --time=47:00:00
#SBATCH --signal=B:TERM@300
#SBATCH --output=logs/slurm/cond_dplm2_ddp_%j.out
#SBATCH --error=logs/slurm/cond_dplm2_ddp_%j.err
#
# MULTI-NODE training on Karolina plain `qgpu` (whole 8-GPU nodes — multi-node
# requests must take ALL node GPUs; 1-GPU-per-node slices are rejected).
# -N2 = 16 GPUs; -N8 = 64 (override at submit: sbatch -N8 …).
#
# Batch-size parity with the Meluxina baseline (16K tokens/optimizer step):
# the script sets per-rank max_tokens = 16000 / total_ranks automatically
# (e.g. -N2 → 16 ranks × 1000). One task per GPU; Lightning drives each rank
# via env-init DDP (RANK/LOCAL_RANK/WORLD_SIZE/MASTER_* exported below).
#
# Smoke first (validates DDP handshake in ~10 min):
#   ACCOUNT=eu-26-85 MAX_STEPS=20 sbatch --export=ALL -N2 -t 00:40:00 \
#     scripts/karolina/cond_dplm2_train_multinode.sbatch
# Full run:
#   ACCOUNT=eu-26-85 sbatch --export=ALL scripts/karolina/cond_dplm2_train_multinode.sbatch
#
# Checkpoints/resume behave like the single-node script: highest-step ckpt is
# auto-resumed; the job resubmits itself if it hits the wall below MAX_STEPS.

set -Eeuo pipefail
ROOT_DIR="${ROOT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "${ROOT_DIR}"
mkdir -p logs/slurm
source scripts/meluxina/common.sh

ACCOUNT="${ACCOUNT:?export ACCOUNT=<IT4I project id>}"
export DPLM_BASE="${DPLM_BASE:-/scratch/project/${ACCOUNT}}"
MAX_STEPS="${MAX_STEPS:-100000}"
MAX_RESUBMITS="${MAX_RESUBMITS:-12}"
RUN_GLOB="cond_dplm2*"

echo "[ddp] job=${SLURM_JOB_ID:-manual} nodes=${SLURM_NNODES} ranks=$((SLURM_NNODES * 8))"
echo "[ddp] nodelist=${SLURM_JOB_NODELIST:-?}  DPLM_BASE=${DPLM_BASE}"

export WANDB_MODE="${WANDB_MODE:-offline}"

# effective-batch parity: 16K tokens/step regardless of rank count
MT=$((16000 / (SLURM_NNODES * 8)))
export MT
echo "[ddp] max_tokens/rank=${MT} → effective $((MT * SLURM_NNODES * 8))/step"

# DDP rendezvous: first allocated node
export MASTER_ADDR="$(scontrol show hostnames "${SLURM_JOB_NODELIST}" | head -1)"
export MASTER_PORT="${MASTER_PORT:-29500}"
echo "[ddp] MASTER_ADDR=${MASTER_ADDR} PORT=${MASTER_PORT}"

# --- resume from the highest-step checkpoint ---------------------------------
find_latest_ckpt() {
  local f s best="" best_s=-1
  while IFS= read -r f; do
    s="$(echo "$f" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; s="${s:-0}"
    if (( s > best_s )); then best_s=$s; best="$f"; fi
  done < <(find "$DPLM_LOGS" -path "*${RUN_GLOB}*" -name '*.ckpt' -not -name '*states*' 2>/dev/null)
  [[ -n "$best" ]] && { echo "$best"; return 0; }
  return 1
}
RESUME_ARGS=""
if LATEST_CKPT="$(find_latest_ckpt)"; then
  echo "[ddp] resuming from: ${LATEST_CKPT}"
  export RESUME_ARGS="++trainer.resume_from_checkpoint=${LATEST_CKPT}"
else
  export RESUME_ARGS=""
  echo "[ddp] no checkpoint — fresh start"
fi

# --- resubmit machinery (qgpu wall caps at 47h here) ---------------------------
export RESUB_COUNT="${RESUB_COUNT:-0}"
SBATCH_SUBMIT_FILE="$(realpath "${BASH_SOURCE[0]}")"
export SBATCH_SUBMIT_FILE
resubmit() {
  RESUB_COUNT=$((RESUB_COUNT + 1)); export RESUB_COUNT
  if (( RESUB_COUNT > MAX_RESUBMITS )); then
    echo "[ddp] resubmit limit (${MAX_RESUBMITS}) reached — stopping"; return 1
  fi
  sbatch --export=ALL --job-name=cond_dplm2_ddp "$SBATCH_SUBMIT_FILE"
}
trap 'resubmit || true; exit 0' TERM

# --- launch one containerized rank per GPU ------------------------------------
# The ALLOCATION used the canonical --gpus form (no per-task gres binding), so
# the srun step binds 1 GPU per task explicitly. CPU/memory shares come from
# IT4I's per-part defaults (16 cores/8 parts) — do not pin them.
chmod +x "${ROOT_DIR}/scripts/karolina/train_rank.sh"
set +e
srun --ntasks-per-node=8 --gpus-per-task=1 \
     bash "${ROOT_DIR}/scripts/karolina/train_rank.sh"
TRAIN_RC=$?
set -e
echo "[ddp] srun exited rc=${TRAIN_RC}"

# --- done? else resubmit --------------------------------------------------------
STEPS_DONE=0
if LATEST_CKPT="$(find_latest_ckpt)"; then
  STEPS_DONE="$(echo "$LATEST_CKPT" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; STEPS_DONE="${STEPS_DONE:-0}"
fi
if (( STEPS_DONE >= MAX_STEPS )); then
  echo "[ddp] DONE — reached ${STEPS_DONE} steps: ${LATEST_CKPT}"
  exit 0
fi
echo "[ddp] at ${STEPS_DONE}/${MAX_STEPS} steps (rc=${TRAIN_RC}) — chaining next chunk"
resubmit || true
