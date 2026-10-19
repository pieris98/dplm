#!/usr/bin/env bash
#SBATCH --job-name=steer
#SBATCH --partition=qgpu
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --gres=gpu:4
#SBATCH --time=12:00:00
#SBATCH --output=logs/slurm/steer_%j.out
#SBATCH --error=logs/slurm/steer_%j.err
#
# Anchored steering experiment (plans/anchored_steering_experiment.md):
# masked-infill generation over the real-data strata, 4 shards on 4 GPUs.
#   steer_infill.py is resumable (existing cell FASTAs are skipped), so
#   resubmitting the same command after a timeout/failure just continues.
#
# Prereqs (once):
#   1. bash scripts/karolina/setup_data.sh          (repo + eval assets)
#   2. rsync the anchor strata from the workstation:
#        rsync -av eval_runs/steer_v1/anchors/ <karolina>:$ROOT/eval_runs/steer_v1/anchors/
#   3. export ACCOUNT=<IT4I project id>
#
# Submit:
#   ACCOUNT=<project> sbatch --export=ALL scripts/karolina/steer_infill.sbatch
# Env overrides: RUN_NAME, CKPT (auto|path), FAMILIES, STRATA, ARMS, PS, SEEDS,
#                CFG_SCALE, TOKEN_BUDGET, NUM_SHARDS, SKIP_GENERATE, RUN_ANALYSIS.

set -Eeuo pipefail
ROOT_DIR="${ROOT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "${ROOT_DIR}"
mkdir -p logs/slurm
source "${ROOT_DIR}/scripts/meluxina/common.sh"

ACCOUNT="${ACCOUNT:?export ACCOUNT=<IT4I project id>}"
export DPLM_BASE="${DPLM_BASE:-/scratch/project/${ACCOUNT}}"

RUN_NAME="${RUN_NAME:-steer_v1}"
ANCHORS="eval_runs/steer_v1/anchors"
OUTDIR="eval_runs/${RUN_NAME}/gen"
# CKPT=auto (default) -> highest-step checkpoint under $DPLM_LOGS; or CKPT=<path>.
CKPT="${CKPT:-auto}"
if [[ "$CKPT" == "auto" ]]; then
  CKPT="$(find "$DPLM_LOGS" -path '*cond_dplm2*' -name '*.ckpt' -not -name '*states*' 2>/dev/null |
    while IFS= read -r f; do
      s="$(echo "$f" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; echo "${s:-0} $f"
    done | sort -rn | head -1 | cut -d' ' -f2-)"
  [[ -z "$CKPT" ]] && { echo "[steer] FATAL: no checkpoint under $DPLM_LOGS — set CKPT=<path>"; exit 1; }
fi

FAMILIES="${FAMILIES:-IPR036291,IPR012340,IPR002052}"
STRATA="${STRATA:-low,high,none}"
ARMS="${ARMS:-vanilla,cond,cond_null}"
PS="${PS:-0.3,0.8}"
SEEDS="${SEEDS:-0}"
CFG_SCALE="${CFG_SCALE:-1.0}"
TOKEN_BUDGET="${TOKEN_BUDGET:-32768}"
NUM_SHARDS="${NUM_SHARDS:-4}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

[[ -d "$ANCHORS" ]] || { echo "[steer] FATAL: $ANCHORS missing — rsync the anchor strata (see header)"; exit 1; }
[[ -f eval/cfpgen_eval/ipr_mapping.pkl ]] || { echo "[steer] FATAL: eval/cfpgen_eval/ipr_mapping.pkl missing — run setup_data.sh"; exit 1; }

echo "[steer] run=$RUN_NAME ckpt=$CKPT shards=$NUM_SHARDS token_budget=$TOKEN_BUDGET"

if [[ "${SKIP_GENERATE:-0}" != "1" ]]; then
  pids=()
  for gpu in $(seq 0 $((NUM_SHARDS - 1))); do
    (
      # --cleanenv strips the environment; forward the device pin explicitly
      CONTAINER_ARGS+=(--env CUDA_VISIBLE_DEVICES="$gpu")
      run_in_container "$DPLM_PY" scripts/eval_function/steer_infill.py \
        --ckpt "$CKPT" \
        --anchors "$ANCHORS" --out "$OUTDIR" \
        --ipr-mapping eval/cfpgen_eval/ipr_mapping.pkl \
        --families "$FAMILIES" --strata "$STRATA" --arms "$ARMS" \
        --ps "$PS" --seeds "$SEEDS" \
        --cfg-scale "$CFG_SCALE" --token-budget "$TOKEN_BUDGET" \
        --batch-size 64 \
        --shard "$gpu" --num-shards "$NUM_SHARDS"
    ) &
    pids+=($!)
  done
  rc=0
  for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
  [[ $rc -ne 0 ]] && { echo "[steer] FATAL: at least one shard failed"; exit 1; }
else
  echo "[steer] generation skipped (SKIP_GENERATE=1)"
fi

# --- first-pass CPU analysis (no GPU; safe to run every submission) ----------
if [[ "${RUN_ANALYSIS:-1}" == "1" ]]; then
  echo "[steer] CPU analysis over finished cells"
  run_in_container "$DPLM_PY" scripts/eval_function/analyze_steer_infill.py \
    --gen "$OUTDIR" --anchors "$ANCHORS" \
    --real-fasta eval_runs/fn_eval_v1/real/aatype.fasta || \
    echo "[steer] WARN: analysis failed (non-fatal)"
fi

echo "[steer] DONE — cells under $OUTDIR, tables in $OUTDIR/analysis/"
