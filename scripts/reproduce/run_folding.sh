#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_ACTIVATE="${CONDA_ACTIVATE:-${CONDA_PREFIX:-}/bin/activate}"
CONDA_ENV="${CONDA_ENV:-dplm}"

MODEL_NAME="airkingbd/dplm2_650m"
SPLIT="cameo2022"
OUTPUT_ROOT="generation-results/reproduce"
RESULTS_ROOT="results/reproduce"
MAX_ITER=100
BATCH_SIZE="${BATCH_SIZE:-1}"
LIMIT=0
SKIP_GENERATION=0
SKIP_EVALUATION=0
OVERWRITE_GENERATION=0

usage() {
  cat <<'EOF'
Usage: scripts/reproduce/run_folding.sh [options] [model_name] [split] [output_root] [max_iter]

Runs sequence-conditioned DPLM-2 forward folding and normalizes evaluator output.

Options:
  --model-name NAME      Hugging Face model name. Default: airkingbd/dplm2_650m
  --split NAME           Evaluation split under data-bin. Default: cameo2022
  --output-root DIR      Bulky generation/evaluation root. Default: generation-results/reproduce
  --results-root DIR     Final summary/config root. Default: results/reproduce
  --max-iter N           DPLM-2 mask-predict iterations. Default: 100
  --batch-size N         Generation batch size. Default: 1
  --limit N              Use the first N FASTA records for a tiny smoke run.
  --skip-generation      Reuse an existing generated folding directory.
  --skip-evaluation      Reuse an existing evaluator directory.
  --overwrite-generation Remove existing generated folding output before generation.
  -h, --help             Show this help.

Examples:
  scripts/reproduce/run_folding.sh --limit 2
  scripts/reproduce/run_folding.sh airkingbd/dplm2_650m cameo2022 generation-results/reproduce 100
EOF
}

positional=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model-name)
      MODEL_NAME="$2"
      shift 2
      ;;
    --split)
      SPLIT="$2"
      shift 2
      ;;
    --output-root)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --results-root)
      RESULTS_ROOT="$2"
      shift 2
      ;;
    --max-iter)
      MAX_ITER="$2"
      shift 2
      ;;
    --batch-size)
      BATCH_SIZE="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    --skip-generation)
      SKIP_GENERATION=1
      shift
      ;;
    --skip-evaluation)
      SKIP_EVALUATION=1
      shift
      ;;
    --overwrite-generation)
      OVERWRITE_GENERATION=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      positional+=("$@")
      break
      ;;
    -*)
      printf 'ERROR: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done

if [[ ${#positional[@]} -gt 0 ]]; then MODEL_NAME="${positional[0]}"; fi
if [[ ${#positional[@]} -gt 1 ]]; then SPLIT="${positional[1]}"; fi
if [[ ${#positional[@]} -gt 2 ]]; then OUTPUT_ROOT="${positional[2]}"; fi
if [[ ${#positional[@]} -gt 3 ]]; then MAX_ITER="${positional[3]}"; fi
if [[ ${#positional[@]} -gt 4 ]]; then
  printf 'ERROR: too many positional arguments\n' >&2
  usage >&2
  exit 2
fi

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

ok() {
  printf 'OK: %s\n' "$*"
}

[[ "${MAX_ITER}" =~ ^[0-9]+$ ]] || fail "--max-iter must be an integer"
[[ "${BATCH_SIZE}" =~ ^[0-9]+$ ]] || fail "--batch-size must be an integer"
[[ "${LIMIT}" =~ ^[0-9]+$ ]] || fail "--limit must be an integer"

cd "${ROOT_DIR}"

if [[ -f "${CONDA_ACTIVATE}" ]]; then
  set +u
  # shellcheck disable=SC1090
  source "${CONDA_ACTIVATE}" "${CONDA_ENV}"
  set -u
  ok "activated conda environment '${CONDA_ENV}'"
fi

export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/dplm-matplotlib-cache}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-/tmp/dplm-triton-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/tmp/dplm-xdg-cache}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
mkdir -p "${MPLCONFIGDIR}" "${TRITON_CACHE_DIR}" "${XDG_CACHE_HOME}"

scripts/reproduce/download_eval_data.sh

model_slug="${MODEL_NAME##*/}"
run_slug="${model_slug}"
if [[ "${LIMIT}" != "0" ]]; then
  run_slug="${model_slug}_first${LIMIT}"
fi
split_slug="${SPLIT}"
run_dir="${OUTPUT_ROOT}/folding/${split_slug}/${run_slug}"
result_dir="${RESULTS_ROOT}/folding/${split_slug}/${run_slug}"
input_fasta="data-bin/${SPLIT}/aatype.fasta"
metadata_csv="data-bin/metadata/pdb_afdb_cameo.csv"

[[ -s "${input_fasta}" ]] || fail "input FASTA is missing or empty: ${input_fasta}"
[[ -s "${metadata_csv}" ]] || fail "metadata CSV is missing or empty: ${metadata_csv}"

mkdir -p "${run_dir}/inputs" "${result_dir}"

if [[ "${LIMIT}" != "0" ]]; then
  input_fasta="${run_dir}/inputs/${SPLIT}.first_${LIMIT}.aatype.fasta"
  awk -v limit="${LIMIT}" '
    /^>/ { seen += 1 }
    seen <= limit { print }
  ' "data-bin/${SPLIT}/aatype.fasta" > "${input_fasta}"
  [[ -s "${input_fasta}" ]] || fail "failed to create limited FASTA: ${input_fasta}"
  ok "created tiny FASTA with first ${LIMIT} records at ${input_fasta}"
fi

generated_root="${run_dir}/generated"
generated_folding_dir="${generated_root}/folding"
eval_subdir="eval_max_iter_${MAX_ITER}"
eval_dir="${generated_folding_dir}/forward_folding/${eval_subdir}/folding/eval"

commit="$(git rev-parse HEAD 2>/dev/null || printf 'unknown')"
dirty="false"
if ! git diff --quiet --ignore-submodules -- 2>/dev/null || ! git diff --cached --quiet --ignore-submodules -- 2>/dev/null; then
  dirty="true"
fi

cat > "${result_dir}/run_config.yaml" <<EOF
model: ${MODEL_NAME}
model_slug: ${model_slug}
run_slug: ${run_slug}
split: ${SPLIT}
input_fasta: ${input_fasta}
metadata_csv: ${metadata_csv}
output_root: ${OUTPUT_ROOT}
run_dir: ${run_dir}
generated_root: ${generated_root}
generated_folding_dir: ${generated_folding_dir}
eval_dir: ${eval_dir}
results_dir: ${result_dir}
max_iter: ${MAX_ITER}
batch_size: ${BATCH_SIZE}
sampling_strategy: argmax
unmasking_strategy: deterministic
limit: ${LIMIT}
commit: ${commit}
dirty_worktree: ${dirty}
overwrite_generation: ${OVERWRITE_GENERATION}
started_at: $(date --iso-8601=seconds)
EOF

if [[ "${SKIP_GENERATION}" != "1" ]]; then
  if [[ "${OVERWRITE_GENERATION}" == "1" && -e "${generated_folding_dir}" ]]; then
    ok "removing existing generated folding output at ${generated_folding_dir}"
    rm -rf "${generated_folding_dir}"
  elif [[ -e "${generated_folding_dir}/aatype.fasta" || -e "${generated_folding_dir}/struct_token.fasta" || -d "${generated_folding_dir}/pdb" ]]; then
    fail "generated output already exists at ${generated_folding_dir}; use --skip-generation to reuse it or --overwrite-generation to regenerate without appending duplicate FASTA records"
  fi
  ok "running DPLM-2 folding generation into ${generated_folding_dir}"
  python generate_dplm2.py \
    --model_name "${MODEL_NAME}" \
    --task folding \
    --input_fasta_path "${input_fasta}" \
    --saveto "${generated_root}" \
    --sampling_strategy argmax \
    --unmasking_strategy deterministic \
    --max_iter "${MAX_ITER}" \
    --batch_size "${BATCH_SIZE}"
else
  ok "skipping generation; reusing ${generated_folding_dir}"
fi

[[ -s "${generated_folding_dir}/aatype.fasta" ]] || fail "generated aatype FASTA missing: ${generated_folding_dir}/aatype.fasta"
[[ -s "${generated_folding_dir}/struct_token.fasta" ]] || fail "generated struct-token FASTA missing: ${generated_folding_dir}/struct_token.fasta"
[[ -d "${generated_folding_dir}/pdb" ]] || fail "generated PDB directory missing: ${generated_folding_dir}/pdb"

if [[ "${SKIP_EVALUATION}" != "1" ]]; then
  ok "running forward-folding evaluator"
  python src/byprot/utils/protein/evaluator_dplm2.py \
    -cn forward_folding \
    "env.PROJECT_ROOT=${ROOT_DIR}" \
    "inference.input_fasta_dir=${generated_folding_dir}" \
    "inference.inference_subdir=${eval_subdir}" \
    "inference.metadata.data_dir=./data-bin" \
    "inference.metadata.csv_path=./data-bin/metadata/pdb_afdb_cameo.csv"
else
  ok "skipping evaluation; reusing ${eval_dir}"
fi

python scripts/reproduce/summarize_folding.py \
  --eval-dir "${eval_dir}" \
  --output-csv "${result_dir}/summary.csv" \
  --details-csv "${result_dir}/all_top_samples.csv" \
  --model "${model_slug}" \
  --split "${SPLIT}" \
  --run-config "${result_dir}/run_config.yaml" \
  --input-fasta "${input_fasta}" \
  --generation-dir "${generated_folding_dir}"

ok "wrote ${result_dir}/summary.csv"
ok "wrote ${result_dir}/run_config.yaml"
