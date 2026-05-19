#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CONDA_ACTIVATE="${CONDA_ACTIVATE:-/home/cherry/miniconda/bin/activate}"
CONDA_ENV="${CONDA_ENV:-dplm}"

RECIPE="dplm2_650m"
DEVICES="${DEVICES:-4}"
NUM_NODES="${NUM_NODES:-1}"
MAX_TOKENS=""
ACCUMULATE_GRAD_BATCHES=""
MAX_STEPS=""
NAME=""
LOGGER="${LOGGER:-tensorboard}"
TRAINER=""
DATA_DIR=""
DPLM_CHECKPOINT=""
DPLM2_CHECKPOINT=""
BIT_PRETRAINED_PATH=""
FAST_DEV_RUN=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/reproduce/training/run_train.sh [options] [-- HYDRA_OVERRIDES...]

Launch a checked-in DPLM/DPLM-2 training recipe with 4xA100-friendly defaults.

Options:
  --recipe NAME                  Recipe to run. Default: dplm2_650m
                                 Choices: dplm2_150m, dplm2_650m, dplm2_3b,
                                          dplm2_bit_650m, dplm2_selfmixup_650m,
                                          dplm_invfold_150m, dplm_invfold_650m,
                                          dplm_invfold_3b, dplm_150m, dplm_650m,
                                          dplm_3b, struct_tokenizer
  --devices N                    GPUs per node. Default: 4
  --num-nodes N                  Number of nodes. Default: 1
  --max-tokens N                 Override datamodule.max_tokens.
  --accumulate-grad-batches N    Override trainer.accumulate_grad_batches.
  --max-steps N                  Override trainer.max_steps.
  --name NAME                    Override Hydra run name/log path.
  --logger NAME                  Logger config. Default: tensorboard
  --trainer NAME                 Override trainer config, e.g. ddp_bf16.
  --data-dir DIR                 Override paths.data_dir.
  --dplm-checkpoint NAME_OR_DIR   Override DPLM checkpoint for DPLM-2 warm start.
  --dplm2-checkpoint DIR_OR_CKPT  Override DPLM-2 checkpoint for self-mixup stage.
  --bit-pretrained-path DIR      Override model.bit.load_path and enable loading.
  --fast-dev-run                 Run Lightning fast_dev_run=true.
  --dry-run                      Print the command without executing it.
  -h, --help                     Show this help.

Examples:
  scripts/reproduce/training/run_train.sh --recipe dplm2_650m --dry-run
  scripts/reproduce/training/run_train.sh --recipe dplm2_650m --devices 4 --max-tokens 8192 --accumulate-grad-batches 2
  scripts/reproduce/training/run_train.sh --recipe dplm2_650m --fast-dev-run --max-steps 2
EOF
}

EXTRA_OVERRIDES=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --recipe)
      RECIPE="$2"
      shift 2
      ;;
    --devices)
      DEVICES="$2"
      shift 2
      ;;
    --num-nodes)
      NUM_NODES="$2"
      shift 2
      ;;
    --max-tokens)
      MAX_TOKENS="$2"
      shift 2
      ;;
    --accumulate-grad-batches)
      ACCUMULATE_GRAD_BATCHES="$2"
      shift 2
      ;;
    --max-steps)
      MAX_STEPS="$2"
      shift 2
      ;;
    --name)
      NAME="$2"
      shift 2
      ;;
    --logger)
      LOGGER="$2"
      shift 2
      ;;
    --trainer)
      TRAINER="$2"
      shift 2
      ;;
    --data-dir)
      DATA_DIR="$2"
      shift 2
      ;;
    --dplm-checkpoint)
      DPLM_CHECKPOINT="$2"
      shift 2
      ;;
    --dplm2-checkpoint)
      DPLM2_CHECKPOINT="$2"
      shift 2
      ;;
    --bit-pretrained-path)
      BIT_PRETRAINED_PATH="$2"
      shift 2
      ;;
    --fast-dev-run)
      FAST_DEV_RUN=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      EXTRA_OVERRIDES+=("$@")
      break
      ;;
    -*)
      printf 'ERROR: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      EXTRA_OVERRIDES+=("$1")
      shift
      ;;
  esac
done

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

ok() {
  printf 'OK: %s\n' "$*"
}

[[ "${DEVICES}" =~ ^[0-9]+$|^auto$ ]] || fail "--devices must be an integer or auto"
[[ "${NUM_NODES}" =~ ^[0-9]+$ ]] || fail "--num-nodes must be an integer"
if [[ -n "${MAX_TOKENS}" ]]; then [[ "${MAX_TOKENS}" =~ ^[0-9]+$ ]] || fail "--max-tokens must be an integer"; fi
if [[ -n "${ACCUMULATE_GRAD_BATCHES}" ]]; then [[ "${ACCUMULATE_GRAD_BATCHES}" =~ ^[0-9]+$ ]] || fail "--accumulate-grad-batches must be an integer"; fi
if [[ -n "${MAX_STEPS}" ]]; then [[ "${MAX_STEPS}" =~ ^[0-9]+$ ]] || fail "--max-steps must be an integer"; fi

EXP=""
DEFAULT_TRAINER="ddp_bf16"
DEFAULT_MAX_TOKENS="8192"
DEFAULT_ACCUMULATE="2"
DEFAULT_MAX_STEPS="100000"
DEFAULT_NAME=""

case "${RECIPE}" in
  dplm2_150m)
    EXP="dplm2/dplm2_150m"
    DPLM_CHECKPOINT="${DPLM_CHECKPOINT:-airkingbd/dplm_150m}"
    DEFAULT_NAME="reproduce/dplm2_150m_from_dplm"
    ;;
  dplm2_650m)
    EXP="dplm2/dplm2_650m"
    DPLM_CHECKPOINT="${DPLM_CHECKPOINT:-airkingbd/dplm_650m}"
    DEFAULT_NAME="reproduce/dplm2_650m_from_dplm"
    ;;
  dplm2_3b)
    EXP="dplm2/dplm2_3b"
    DPLM_CHECKPOINT="${DPLM_CHECKPOINT:-airkingbd/dplm_3b}"
    DEFAULT_NAME="reproduce/dplm2_3b_from_dplm"
    ;;
  dplm2_bit_650m)
    EXP="dplm2/dplm2_bit_650m"
    DPLM_CHECKPOINT="${DPLM_CHECKPOINT:-airkingbd/dplm_650m}"
    DEFAULT_NAME="reproduce/dplm2_bit_650m_from_dplm"
    ;;
  dplm2_selfmixup_650m)
    EXP="dplm2/dplm2_650m_selfmixup"
    DPLM2_CHECKPOINT="${DPLM2_CHECKPOINT:-}"
    DEFAULT_NAME="reproduce/dplm2_650m_selfmixup"
    ;;
  dplm_invfold_150m)
    EXP="dplm/cond_dplm_150m"
    DEFAULT_TRAINER="ddp_fp16"
    DEFAULT_MAX_TOKENS="6000"
    DEFAULT_ACCUMULATE="1"
    DEFAULT_MAX_STEPS="200000"
    DEFAULT_NAME="cath_4.3/dplm_150m/invfold"
    ;;
  dplm_invfold_650m)
    EXP="dplm/cond_dplm_650m"
    DEFAULT_TRAINER="ddp_fp16"
    DEFAULT_MAX_TOKENS="6000"
    DEFAULT_ACCUMULATE="1"
    DEFAULT_MAX_STEPS="200000"
    DEFAULT_NAME="cath_4.3/dplm_650m/invfold"
    ;;
  dplm_invfold_3b)
    EXP="dplm/cond_dplm_3b"
    DEFAULT_TRAINER="ddp_fp16"
    DEFAULT_MAX_TOKENS="6000"
    DEFAULT_ACCUMULATE="1"
    DEFAULT_MAX_STEPS="200000"
    DEFAULT_NAME="cath_4.3/dplm_3b/invfold"
    ;;
  dplm_150m)
    EXP="dplm/dplm_150m"
    DEFAULT_MAX_TOKENS="8192"
    DEFAULT_ACCUMULATE="32"
    DEFAULT_MAX_STEPS="500000"
    DEFAULT_NAME="reproduce/dplm_150m_uniref50"
    ;;
  dplm_650m)
    EXP="dplm/dplm_650m"
    DEFAULT_MAX_TOKENS="8192"
    DEFAULT_ACCUMULATE="32"
    DEFAULT_MAX_STEPS="500000"
    DEFAULT_NAME="reproduce/dplm_650m_uniref50"
    ;;
  dplm_3b)
    EXP="dplm/dplm_3b"
    DEFAULT_MAX_TOKENS="8192"
    DEFAULT_ACCUMULATE="32"
    DEFAULT_MAX_STEPS="500000"
    DEFAULT_NAME="reproduce/dplm_3b_uniref50"
    ;;
  struct_tokenizer)
    EXP="structok/structok_lfq_8k_pdb_swissprot_c512"
    DEFAULT_TRAINER="default"
    DEFAULT_MAX_TOKENS=""
    DEFAULT_ACCUMULATE="1"
    DEFAULT_MAX_STEPS="200000"
    DEFAULT_NAME="structok/dplm2_structok_reproduce"
    ;;
  *)
    fail "unknown recipe '${RECIPE}'"
    ;;
esac

TRAINER="${TRAINER:-${DEFAULT_TRAINER}}"
MAX_TOKENS="${MAX_TOKENS:-${DEFAULT_MAX_TOKENS}}"
ACCUMULATE_GRAD_BATCHES="${ACCUMULATE_GRAD_BATCHES:-${DEFAULT_ACCUMULATE}}"
MAX_STEPS="${MAX_STEPS:-${DEFAULT_MAX_STEPS}}"
NAME="${NAME:-${DEFAULT_NAME}}"

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
mkdir -p "${MPLCONFIGDIR}" "${TRITON_CACHE_DIR}" "${XDG_CACHE_HOME}" logs/slurm

cmd=(
  python train.py
  "experiment=${EXP}"
  "name=${NAME}"
  "logger=${LOGGER}"
  "trainer=${TRAINER}"
  "trainer.devices=${DEVICES}"
  "++trainer.num_nodes=${NUM_NODES}"
  "trainer.accumulate_grad_batches=${ACCUMULATE_GRAD_BATCHES}"
  "trainer.max_steps=${MAX_STEPS}"
  "train.force_restart=true"
)

if [[ -n "${MAX_TOKENS}" ]]; then
  cmd+=("datamodule.max_tokens=${MAX_TOKENS}")
fi

if [[ -n "${DATA_DIR}" ]]; then
  cmd+=("paths.data_dir=${DATA_DIR}")
fi

case "${RECIPE}" in
  dplm2_150m|dplm2_650m|dplm2_3b|dplm2_bit_650m)
    cmd+=(
      "model.net.name=${DPLM_CHECKPOINT}"
      "model.net.pretrained_model_name_or_path=${DPLM_CHECKPOINT}"
      "model.training_stage=train_from_dplm"
    )
    ;;
  dplm2_selfmixup_650m)
    [[ -n "${DPLM2_CHECKPOINT}" ]] || fail "--dplm2-checkpoint is required for recipe dplm2_selfmixup_650m"
    cmd+=(
      "model.training_stage=continue_train_from_dplm2"
      "model.net.name=${DPLM2_CHECKPOINT}"
      "model.net.pretrained_model_name_or_path=${DPLM2_CHECKPOINT}"
      "model.self_mixup.enable=true"
    )
    ;;
esac

if [[ -n "${BIT_PRETRAINED_PATH}" ]]; then
  cmd+=(
    "model.bit.load_from_pretrained=true"
    "model.bit.load_path=${BIT_PRETRAINED_PATH}"
  )
fi

if [[ "${FAST_DEV_RUN}" == "1" ]]; then
  cmd+=(
    "++trainer.fast_dev_run=true"
    "trainer.num_sanity_val_steps=0"
    "datamodule.num_workers=0"
  )
fi

cmd+=("${EXTRA_OVERRIDES[@]}")

printf 'Training recipe: %s\n' "${RECIPE}"
printf 'Command:'
printf ' %q' "${cmd[@]}"
printf '\n'

if [[ "${DRY_RUN}" == "1" ]]; then
  ok "dry run only"
  exit 0
fi

"${cmd[@]}"
