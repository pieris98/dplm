#!/usr/bin/env bash
# NOTE: --account MUST be set — the default account "defac" has MaxSubmit=0
#SBATCH --account=eu-26-85
#SBATCH --job-name=fn_eval
#SBATCH --partition=qgpu
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=48G
#SBATCH --gres=gpu:1
#SBATCH --time=08:00:00
#SBATCH --output=logs/slurm/fn_eval_%j.out
#SBATCH --error=logs/slurm/fn_eval_%j.err
#
# End-to-end function-recovery evaluation on Karolina (one GPU job):
#   A. generation   — 4 main arms (w=1) + optional CFG sweep, in the dplm
#                    sandbox (GPU)
#   B. InterProScan — per arm, on the host (Java via ensure_java)
#   C. DeepGO-SE    — per arm, in the deepgose sandbox (--nv, GPU)
#   D. scoring      — in the dplm sandbox (sklearn/obonet via eval/pylibs)
#
# One-time setup first: bash scripts/karolina/setup_data.sh  (+ the printed
# rsync TODOs from the local workstation).
#
# Submit:
#   ACCOUNT=<project> sbatch --export=ALL scripts/karolina/eval_pipeline.sbatch
# Env overrides: RUN_NAME, CKPT, SWEEP, SKIP_GENERATE, N_LABELS, SEQS_PER_LABEL.
# Stages skip existing outputs → resubmit the same command to resume.

set -Eeuo pipefail
ROOT_DIR="${ROOT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "${ROOT_DIR}"
mkdir -p logs/slurm
source "${ROOT_DIR}/scripts/meluxina/common.sh"

ACCOUNT="${ACCOUNT:?export ACCOUNT=<IT4I project id>}"
export DPLM_BASE="${DPLM_BASE:-/scratch/project/${ACCOUNT}}"

RUN_NAME="${RUN_NAME:-fn_eval_v1}"
EVALDIR="eval_runs/${RUN_NAME}"
# CKPT=auto (default) → highest-step checkpoint under $DPLM_LOGS; or set CKPT=<path>.
CKPT="${CKPT:-auto}"
if [[ "$CKPT" == "auto" ]]; then
  CKPT="$(find "$DPLM_LOGS" -path '*cond_dplm2*' -name '*.ckpt' -not -name '*states*' 2>/dev/null |
    while IFS= read -r f; do
      s="$(echo "$f" | grep -oE 'step[=_][0-9]+' | grep -oE '[0-9]+' | tail -1)"; echo "${s:-0} $f"
    done | sort -rn | head -1 | cut -d' ' -f2-)"
  [[ -z "$CKPT" ]] && { echo "[fn_eval] FATAL: no checkpoint under $DPLM_LOGS — set CKPT=<path>"; exit 1; }
fi
SWEEP="${SWEEP:-}"
N_LABELS="${N_LABELS:-32}"
SEQS_PER_LABEL="${SEQS_PER_LABEL:-8}"
mkdir -p "$EVALDIR" logs/slurm

ARMS_MAIN="ours_cond ours_null vanilla real"
ARMS_ALL="$ARMS_MAIN"
for w in $SWEEP; do ARMS_ALL="$ARMS_ALL ours_cond_cfg${w}"; done
echo "[fn_eval] run=$RUN_NAME ckpt=$CKPT arms=($ARMS_ALL)"

# --- Java for InterProScan (shared hang-safe discovery in common.sh) ---------
ensure_java || { echo "[fn_eval] FATAL: no usable java"; exit 1; }
ensure_ips_python || echo "[fn_eval] WARN: no python2 shim — the MobiDB step will fail"

# --- A. generation (dplm sandbox, GPU) ---------------------------------------
GEN_ARGS=(
  scripts/eval_function/generate_eval_set.py
  --ckpt "$CKPT"
  --out "$EVALDIR"
  --test-pkl eval/cfpgen_eval/test.pkl
  --go-mapping eval/cfpgen_eval/go_mapping.pkl
  --ipr-mapping eval/cfpgen_eval/ipr_mapping.pkl
  --n-labels "$N_LABELS" --seqs-per-label "$SEQS_PER_LABEL"
  --batch-size 64 --len 256
)
if [[ "${SKIP_GENERATE:-0}" != "1" ]]; then
  echo "[fn_eval] A: generating main arms (w=1)"
  run_in_container "$DPLM_PY" "${GEN_ARGS[@]}"
  for w in $SWEEP; do
    echo "[fn_eval] A: generating sweep w=$w"
    run_in_container "$DPLM_PY" "${GEN_ARGS[@]}" --cfg-scale "$w" --arms ours_cond
  done
else
  echo "[fn_eval] A: skipped (SKIP_GENERATE=1)"
fi

# --- B. InterProScan (host; degenerate arms can take ~25 min each in PANTHER) -
IPS="$REPO_DIR/eval/interproscan-5.78-109.0/interproscan.sh"
[[ -x "$IPS" ]] || { echo "[fn_eval] FATAL: $IPS missing"; exit 1; }
for arm in $ARMS_ALL; do
  fasta="$EVALDIR/$arm/aatype.fasta"
  [[ -f "$fasta" ]] || { echo "[fn_eval] B: no fasta for $arm — skipping"; continue; }
  [[ -s "$EVALDIR/$arm/ips.tsv" ]] && { echo "[fn_eval] B: $arm ips.tsv exists — skipping"; continue; }
  echo "[fn_eval] B: InterProScan on $arm"
  "$IPS" -i "$fasta" -f TSV -o "$EVALDIR/$arm/ips.tsv" \
         -T "$EVALDIR/$arm/ips_tmp" -goterms --disable-precalc \
         -cpu "$SLURM_CPUS_PER_TASK"
done

# --- C. DeepGO-SE (own sandbox, GPU) -----------------------------------------
DG_SANDBOX="$REPO_DIR/eval/deepgose_sandbox"
[[ -d "$DG_SANDBOX" || -f "$DG_SANDBOX" ]] || { echo "[fn_eval] FATAL: $DG_SANDBOX missing"; exit 1; }
for arm in $ARMS_ALL; do
  fasta="$EVALDIR/$arm/aatype.fasta"
  [[ -f "$fasta" ]] || continue
  if compgen -G "$EVALDIR/$arm/aatype_preds_*.tsv.gz" >/dev/null; then
    echo "[fn_eval] C: $arm preds exist — skipping"; continue
  fi
  echo "[fn_eval] C: DeepGO-SE on $arm"
  apptainer exec --cleanenv --nv \
    --bind "$REPO_DIR/eval/deepgo2/data:/workspace/deepgo2/data" \
    --bind "$REPO_DIR/eval/esm_torch_hub:/root/.cache/torch/hub/checkpoints" \
    --bind "$REPO_DIR/$EVALDIR:/workspace/deepgo2/eval" \
    "$DG_SANDBOX" \
    python predict.py -if "eval/$arm/aatype.fasta" -dr data -d cuda
done

# --- D. scoring (dplm sandbox; sklearn+obonet via eval/pylibs) ---------------
if ! run_in_container "$DPLM_PY" -c "import sklearn, obonet" >/dev/null 2>&1; then
  echo "[fn_eval] FATAL: sklearn/obonet unavailable in the sandbox."
  echo "[fn_eval] On the LOGIN node run: bash scripts/karolina/setup_data.sh   (step 6)"
  exit 1
fi
SCORER_ARGS=(scripts/eval_function/score_function_eval.py
             --evaldir "$EVALDIR"
             --arms "$(IFS=,; echo "$ARMS_ALL")"
             --obo eval/go.obo)
for arm in $ARMS_ALL; do
  SCORER_ARGS+=(--ips-tsv "$arm=$EVALDIR/$arm/ips.tsv")
  SCORER_ARGS+=("--deepgose-tsv=$arm=$EVALDIR/$arm/aatype_preds_*.tsv.gz")
done
echo "[fn_eval] D: scoring"
run_in_container "$DPLM_PY" "${SCORER_ARGS[@]}"

echo "[fn_eval] DONE — results: $EVALDIR/results.{json,md}"
