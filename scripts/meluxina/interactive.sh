#!/usr/bin/env bash
# Interactive container access on Meluxina — mirrors the sbatch environment
# exactly (same common.sh binds/env), so what works here works in sbatch.
#
# Usage (from the repo root, or anywhere — paths are script-relative):
#   scripts/meluxina/interactive.sh alloc   # login node: grab a GPU allocation,
#                                           # land in a container shell
#   scripts/meluxina/interactive.sh shell   # inside an existing allocation:
#                                           # enter the container shell
#   scripts/meluxina/interactive.sh check   # run the full precondition
#                                           # checks (auto-allocates if on a
#                                           # login node)
#
# Env overrides: ACCOUNT, QOS, GPUS, TIME (see defaults below),
# e.g.: QOS=short GPUS=2 scripts/meluxina/interactive.sh alloc

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${1:-shell}"

ACCOUNT="${ACCOUNT:-p201418}"
QOS="${QOS:-test}"
GPUS="${GPUS:-4}"
TIME="${TIME:-01:00:00}"

COMMON="${ROOT_DIR}/scripts/meluxina/common.sh"
CHECKS_IN_CONTAINER="/workspace/dplm/scripts/meluxina/checks.py"

on_login_node() {
  [[ "$(hostname -s)" == login* || -z "${SLURM_JOB_ID:-}" ]]
}

case "${MODE}" in
  alloc)
    # salloc executes this on the compute node; the trailing bash -c keeps
    # the allocation alive only while the container shell runs.
    CMD="source '${COMMON}' && run_in_container_shell"
    exec salloc --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
      --gres="gpu:${GPUS}" -N1 --cpus-per-task=16 -t "${TIME}" \
      bash -c "${CMD}"
    ;;
  shell)
    source "${COMMON}"
    run_in_container_shell
    ;;
  check)
    CMD="source '${COMMON}' && run_in_container \"\${DPLM_PY}\" '${CHECKS_IN_CONTAINER}'"
    if on_login_node; then
      echo "[interactive] on login node — allocating ${GPUS} GPU(s), qos=${QOS}, ${TIME} ..."
      exec salloc --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
        --gres="gpu:${GPUS}" -N1 --cpus-per-task=16 -t "${TIME}" \
        bash -c "${CMD}"
    else
      echo "[interactive] already on compute node $(hostname -s) — running checks"
      source "${COMMON}"
      run_in_container "${DPLM_PY}" "${CHECKS_IN_CONTAINER}"
    fi
    ;;
  *)
    echo "usage: $0 {alloc|shell|check}" >&2
    exit 2
    ;;
esac
