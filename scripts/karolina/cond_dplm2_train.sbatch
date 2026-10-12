#!/usr/bin/env bash
#SBATCH --job-name=cond_dplm2
# ACCOUNT must be exported before submitting:
#   ACCOUNT=OPEN-XX-XX sbatch --export=ALL scripts/karolina/cond_dplm2_train.sbatch
#SBATCH --partition=qgpu_big
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --gres=gpu:8
#SBATCH --time=12:00:00
#SBATCH --signal=B:TERM@300
#SBATCH --output=logs/slurm/cond_dplm2_%j.out
#SBATCH --error=logs/slurm/cond_dplm2_%j.err
#
# Conditional DPLM-2 FULL training on Karolina (qgpu_big: whole node,
# 8× A100 40GB), running inside the Apptainer sandbox.
#
# Batch-size parity with the Meluxina baseline run (4 GPUs × max_tokens 4000
# = 16K tokens/step): here 8 GPUs × max_tokens 2000 = the same 16K, at ~2×
# the wall-clock speed. Same optimizer steps (100K), same LR schedule.
#
# Karolina's qgpu_big wall limit is 12 h; the full run needs ~2-3 chunks.
# This script therefore:
#   * auto-resumes: finds the newest checkpoint under $DPLM_LOGS and passes
#     ++trainer.resume_from_checkpoint;
#   * self-resubmits: on the pre-timeout SIGTERM (or on a normal exit that
#     hasn't reached MAX_STEPS), it submits ITSELF again (up to MAX_RESUBMITS).
#
# Submit:
#   ACCOUNT=<project> sbatch --export=ALL scripts/karolina/cond_dplm2_train.sbatch
# Overrides (after --): MAX_STEPS=... sbatch ... -- trainer.max_steps=...

set -Eeuo pipefail
ROOT_DIR="${ROOT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "${ROOT_DIR}"
mkdir -p logs/slurm
source "${ROOT_DIR}/scripts/meluxina/common.sh"

ACCOUNT="${ACCOUNT:?export ACCOUNT=<IT4I project id, e.g. OPEN-25-XX>}"
export DPLM_BASE="${DPLM_BASE:-/scratch/project/${ACCOUNT}}"
MAX_STEPS="${MAX_STEPS:-100000}"
MAX_RESUBMITS="${MAX_RESUBMITS:-12}"
RUN_GLOB="cond_dplm2*"

echo "[karolina] SLURM_JOB_ID=${SLURM_JOB_ID:-manual} node=$(hostname -s)"
echo "[karolina] DPLM_BASE=${DPLM_BASE}  CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"

# --- wandb: compute nodes have no internet → offline unless overridden -------
export WANDB_MODE="${WANDB_MODE:-offline}"
if [[ "$WANDB_MODE" == "offline" ]]; then
  echo "[karolina] WANDB_MODE=offline (curves land in ${DPLM_LOGS}/wandb; sync later: wandb sync <dir>)"
fi

# --- find latest checkpoint for auto-resume ----------------------------------
# Highest step number wins (best.ckpt can be newer-by-mtime than last.ckpt but
# sit at an earlier step → resuming from it could loop without progressing).
find_latest_ckpt() {
  local f s best="" best_s=-1
  while IFS= read -r f; do
    s="$(echo "$f" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; s="${s:-0}"
    if (( s > best_s )); then best_s=$s; best="$f"; fi
  done < <(find "$DPLM_LOGS" -path "*${RUN_GLOB}*" -name '*.ckpt' -not -name '*states*' 2>/dev/null)
  [[ -n "$best" ]] && { echo "$best"; return 0; }
  return 1
}
RESUME_ARGS=()
if LATEST_CKPT="$(find_latest_ckpt)"; then
  echo "[karolina] resuming from: ${LATEST_CKPT}"
  RESUME_ARGS=("++trainer.resume_from_checkpoint=${LATEST_CKPT}")
else
  echo "[karolina] no existing checkpoint — fresh start"
fi

# --- resubmit machinery (qgpu_big caps a job at 12 h) --------------------------
export RESUB_COUNT="${RESUB_COUNT:-0}"
SBATCH_SUBMIT_FILE="$(realpath "${BASH_SOURCE[0]}")"
export SBATCH_SUBMIT_FILE
resubmit() {
  RESUB_COUNT=$((RESUB_COUNT + 1)); export RESUB_COUNT
  if (( RESUB_COUNT > MAX_RESUBMITS )); then
    echo "[karolina] resubmit limit (${MAX_RESUBMITS}) reached — stopping"; return 1
  fi
  echo "[karolina] resubmitting chunk ${RESUB_COUNT}/${MAX_RESUBMITS}"
  sbatch --export=ALL --job-name=cond_dplm2 "$SBATCH_SUBMIT_FILE"
}
# Pre-timeout signal (SIGTERM 5 min before wall end): submit the next chunk now.
trap 'resubmit || true; exit 0' TERM

# --- training ------------------------------------------------------------------
HYDRA_ARGS=(
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina
  trainer=meluxina_ddp_bf16
  trainer.num_nodes=1
  trainer.devices=8
  datamodule.max_tokens=2000        # 8×2000 = 16K tokens/step = meluxina 4×4000 parity
  logger=wandb
)
[[ "$WANDB_MODE" == "offline" ]] && HYDRA_ARGS+=("logger.wandb.offline=True")
[[ -n "${LR:-}" ]] && HYDRA_ARGS+=("train.lr=${LR}")
HYDRA_ARGS+=("${RESUME_ARGS[@]:-}")
HYDRA_OVERRIDES=()
[[ $# -gt 0 ]] && HYDRA_OVERRIDES+=("$@")

set +e
run_in_container "${DPLM_PY}" train.py "${HYDRA_ARGS[@]}" ${HYDRA_OVERRIDES[@]+"${HYDRA_OVERRIDES[@]}"}
TRAIN_RC=$?
set -e
echo "[karolina] train.py exited rc=${TRAIN_RC}"

# --- done? else resubmit -------------------------------------------------------
STEPS_DONE=0
if LATEST_CKPT="$(find_latest_ckpt)"; then
  STEPS_DONE="$(echo "$LATEST_CKPT" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; STEPS_DONE="${STEPS_DONE:-0}"
fi
if (( STEPS_DONE >= MAX_STEPS )); then
  echo "[karolina] DONE — reached ${STEPS_DONE} steps: ${LATEST_CKPT}"
  exit 0
fi
echo "[karolina] at ${STEPS_DONE}/${MAX_STEPS} steps (rc=${TRAIN_RC}) — chaining next chunk"
resubmit || true
