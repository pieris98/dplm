#!/usr/bin/env bash
# NOTE: --account MUST be set — the default account "defac" has MaxSubmit=0
#SBATCH --account=eu-26-85
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
# Smoke first (DDP handshake + conditioning probe + checkpoint cadence, ~15 min).
# HYDRA_ARGS shrinks the probe/checkpoint intervals so both fire within 20 steps:
#   ACCOUNT=eu-26-85 FRESH=1 MAX_STEPS=20 \
#   HYDRA_ARGS='callbacks.conditioning_probe.probe_every_n_steps=5 callbacks.model_checkpoint.every_n_train_steps=10 trainer.val_check_interval=10' \
#   sbatch --export=ALL -N2 -t 00:40:00 scripts/karolina/cond_dplm2_train_multinode.sbatch
# Resume test (verifies auto-resume): resubmit the same command WITHOUT FRESH —
# it must pick up step_20 and stop at DONE.
# Full run (see KAROLINA_RUNBOOK.md pre-flight checklist first):
#   ACCOUNT=eu-26-85 FRESH=1 sbatch --export=ALL scripts/karolina/cond_dplm2_train_multinode.sbatch
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
export NCCL_DEBUG="${NCCL_DEBUG:-INFO}"   # drop to OFF once the smoke is green

# effective-batch parity: 16K tokens/step regardless of rank count
MT=$((16000 / (SLURM_NNODES * 8)))
export MT
echo "[ddp] max_tokens/rank=${MT} → effective $((MT * SLURM_NNODES * 8))/step"

# DDP rendezvous: first allocated node
export MASTER_ADDR="$(scontrol show hostnames "${SLURM_JOB_NODELIST}" | head -1)"
export MASTER_PORT="${MASTER_PORT:-29500}"
echo "[ddp] MASTER_ADDR=${MASTER_ADDR} PORT=${MASTER_PORT}"

# --- resume from the highest-step checkpoint ---------------------------------
# FRESH=1 skips the scan entirely. Set it on the FIRST submission of a new
# training campaign: the checkpoint scan globs cond_dplm2* under $DPLM_LOGS,
# and the BROKEN pre-fix checkpoints (random-init encoder, 2026-10 incident —
# see RESEARCH_STATE.md §0) must never be resumed. Quarantine them instead:
#   mkdir -p "$DPLM_BASE/quarantine_broken_ckpt" && \
#     find "$DPLM_LOGS" -path '*cond_dplm2*' -name '*.ckpt' -exec mv {} "$DPLM_BASE/quarantine_broken_ckpt/" \;
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
if [[ "${FRESH:-0}" == "1" ]]; then
  export RESUME_ARGS=""
  echo "[ddp] FRESH=1 — ignoring any existing checkpoints (fresh campaign start)"
elif LATEST_CKPT="$(find_latest_ckpt)"; then
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

# --- launch one containerized task per NODE; torchrun spawns 8 workers -------
JOB_START_EPOCH="$(date +%s)"   # for "did THIS job produce a ckpt" checks
# The old per-task pattern (--ntasks-per-node=8 --gpus-per-task=1) put 7 of 8
# /dev/nvidia* files outside each task's cgroup, and NCCL's NVML topology
# probe died on them ("nvmlDeviceGetHandleByPciBusId: Not Found") on every
# rank of both nodes. One task per node holds ALL 8 GPUs in its cgroup;
# torchrun (inside the container) spawns the 8 workers and sets their
# RANK/LOCAL_RANK/WORLD_SIZE itself. TorchElasticEnvironment then
# auto-detects the elastic launch (is_torchelastic_launched()).
#
# Memory-env guard: submitting from INSIDE an interactive allocation (evalsh/
# check use --mem=48G; alloc uses per-GPU defaults) leaks SLURM_MEM_PER_* into
# this environment via --export=ALL, and srun then dies with
# "SLURM_MEM_PER_CPU, SLURM_MEM_PER_GPU, and SLURM_MEM_PER_NODE are mutually
# exclusive" against IT4I's per-part GPU memory. Unset the trio (and a stale
# cpus hint) so the step takes the partition defaults.
unset SLURM_MEM_PER_CPU SLURM_MEM_PER_GPU SLURM_MEM_PER_NODE SLURM_CPUS_PER_TASK
chmod +x "${ROOT_DIR}/scripts/karolina/train_rank.sh"
set +e
srun --ntasks-per-node=1 --gpus-per-task=8 \
     bash "${ROOT_DIR}/scripts/karolina/train_rank.sh"
TRAIN_RC=$?
set -e
echo "[ddp] srun exited rc=${TRAIN_RC}"

# --- done? else resubmit --------------------------------------------------------
STEPS_DONE=0
if LATEST_CKPT="$(find_latest_ckpt)"; then
  STEPS_DONE="$(echo "$LATEST_CKPT" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; STEPS_DONE="${STEPS_DONE:-0}"
fi
# DONE requires srun success: a failed run whose stale ckpt *filename* happens
# to read >= MAX_STEPS must not be declared finished (smoke 5259927 printed
# DONE off the previous smoke's step_19.0 while itself failing instantly).
if [[ "${TRAIN_RC}" -eq 0 ]] && (( STEPS_DONE + 1 >= MAX_STEPS )); then
  # +1: PL global_step is 0-indexed in ckpt filenames — max_steps=20 lands as
  # step_19.0-loss_*.ckpt, which read 19 < 20 and chained forever (smoke 5259770).
  echo "[ddp] DONE — reached $((STEPS_DONE + 1))/${MAX_STEPS} steps: ${LATEST_CKPT}"
  exit 0
fi
# Chain only if THIS job produced a checkpoint (i.e. made real progress). An
# instant failure produces no ckpt and must not burn MAX_RESUBMITS doomed jobs
# (smoke 5259770 chained a failing job on rc=1 alone).
NEW_CKPT="$(find "$DPLM_LOGS" -path "*${RUN_GLOB}*" -name '*.ckpt' -newermt "@${JOB_START_EPOCH}" -not -name '*states*' 2>/dev/null | head -1)"
if [[ -z "${NEW_CKPT}" && "${TRAIN_RC}" -ne 0 ]]; then
  echo "[ddp] rc=${TRAIN_RC} with no new checkpoint this job — NOT chaining (fix the failure and resubmit manually)"
  exit 1
fi
echo "[ddp] at ${STEPS_DONE}/${MAX_STEPS} steps (rc=${TRAIN_RC}) — chaining next chunk"
resubmit || true
