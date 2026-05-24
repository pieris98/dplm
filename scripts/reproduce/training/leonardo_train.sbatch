#!/usr/bin/env bash
#SBATCH --partition=boost_usr_prod
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --time=24:00:00

set -Eeuo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
CONDA_ACTIVATE="${CONDA_ACTIVATE:-${CONDA_PREFIX:-}/bin/activate}"
CONDA_ENV="${CONDA_ENV:-dplm}"

cd "${ROOT_DIR}"
mkdir -p logs/slurm

if [[ -n "${MODULES:-}" ]]; then
  for module_name in ${MODULES}; do
    module load "${module_name}"
  done
fi

export CONDA_ACTIVATE
export CONDA_ENV
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-${TMPDIR:-/tmp}/dplm-matplotlib-cache}"
export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-${TMPDIR:-/tmp}/dplm-triton-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${TMPDIR:-/tmp}/dplm-xdg-cache}"
export HF_HOME="${HF_HOME:-${SCRATCH:-${HOME}}/.cache/huggingface}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-${HF_HOME}/datasets}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}/transformers}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "${MPLCONFIGDIR}" "${TRITON_CACHE_DIR}" "${XDG_CACHE_HOME}" "${HF_DATASETS_CACHE}" "${TRANSFORMERS_CACHE}"

printf 'SLURM_JOB_ID=%s\n' "${SLURM_JOB_ID:-unknown}"
printf 'SLURM_NODELIST=%s\n' "${SLURM_NODELIST:-unknown}"
printf 'CUDA_VISIBLE_DEVICES=%s\n' "${CUDA_VISIBLE_DEVICES:-unset}"
printf 'ROOT_DIR=%s\n' "${ROOT_DIR}"

exec bash scripts/reproduce/training/run_train.sh --devices 4 "$@"
