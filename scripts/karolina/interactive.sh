#!/usr/bin/env bash
# Interactive access on Karolina — mirrors the sbatch environment exactly
# (same shared common.sh binds/env as scripts/meluxina/common.sh).
#
# NOTE: shells are placed ON the compute node via `srun --pty` (a bare salloc
# would run the shell on the login host while merely holding the allocation).
#
# Usage:
#   scripts/karolina/interactive.sh evalsh  # eval work: eval_generate/eval_ips/
#                                           # eval_dg/eval_score helpers; 1 GPU,
#                                           # qgpu, 8 h
#   scripts/karolina/interactive.sh alloc   # training debugging: qgpu_big whole
#                                           # node, 8 GPUs, 4 h
#   scripts/karolina/interactive.sh shell   # inside an allocation: enter the
#                                           # dplm sandbox (NOT for IPS/DeepGO-SE)
#   scripts/karolina/interactive.sh check   # precondition checks (auto-allocates)
#
# Env: ACCOUNT (required), TIME_EVAL, QOS_EVAL.
# IT4I quirk: if apptainer misbehaves, unload XALT first (`ml -f XALT`).

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# IT4I project id, e.g. OPEN-25-XX / DD-23-XX
ACCOUNT="${ACCOUNT:?export ACCOUNT=<IT4I project id>}"
export DPLM_BASE="${DPLM_BASE:-/scratch/project/${ACCOUNT}}"

COMMON="${ROOT_DIR}/scripts/meluxina/common.sh"
CHECKS_IN_CONTAINER="/workspace/dplm/scripts/meluxina/checks.py"

on_login_node() {
  [[ "$(hostname -s)" == login* || -z "${SLURM_JOB_ID:-}" ]]
}

if [[ $# -eq 0 ]]; then
  if on_login_node; then
    MODE="check"
    echo "[interactive] no subcommand; on login node → defaulting to 'check'"
  else
    MODE="shell"
  fi
else
  MODE="$1"
fi

case "${MODE}" in
  alloc)
    # Whole-node (8×A100) HOST shell for training debugging.
    TIME="${TIME_ALLOC:-04:00:00}"
    BASHRC="${ROOT_DIR}/.alloc_rc.$$"   # shared FS: /tmp from the login node
    cat > "${BASHRC}" <<EOS             # does not exist on the compute node
source '${COMMON}'
echo "[interactive] HOST shell on \$(hostname -s) — qgpu_big, 8 GPUs, ${TIME}."
echo '  dplm sandbox (training):'
echo '    interactive : run_in_container_shell   (container bash is old and has NO'
echo '                  apptainer/java/IPS — eval tools run in THIS host shell)'
echo '    one-off     : run_in_container <cmd...>'
echo '  eval tools (host): eval_ips <evaldir> <arm> · eval_dg <evaldir> <arm> · ensure_java'
echo '  exit        : release the allocation'
rm -f "\${BASH_SOURCE[0]}"
EOS
    exec srun --account="${ACCOUNT}" -p qgpu_big -N1 --ntasks=1 \
      --cpus-per-task=32 --gres=gpu:8 -t "${TIME}" \
      --pty bash --noprofile --rcfile "${BASHRC}" -i
    ;;
  evalsh)
    # Eval stack: 1 GPU (qgpu), 8 h, HOST shell with helpers loaded.
    TIME="${TIME_EVAL:-08:00:00}"
    BASHRC="${ROOT_DIR}/.evalsh_rc.$$"
    cat > "${BASHRC}" <<EOS
source '${COMMON}'
ensure_java || echo "[interactive] WARN: java unavailable — eval_ips will fail"
echo "[interactive] EVAL host shell on \$(hostname -s) — qgpu, 1 GPU, ${TIME}."
echo '  helpers (evaldir is repo-relative, e.g. eval_runs/fn_eval_v1; stages skip'
echo '  finished outputs, so re-running is cheap):'
echo '    eval_generate <evaldir> <ckpt> [extra args...]   # generation, sandbox+GPU'
echo '    eval_ips <evaldir> <arm> [cpus]                  # InterProScan, host+java (slow!)'
echo '    eval_dg <evaldir> <arm>                          # DeepGO-SE sandbox, GPU'
echo '    eval_score <evaldir> <arm,arm,...>               # scorer one-shot, sandbox'
echo '  exit        : release the allocation'
rm -f "\${BASH_SOURCE[0]}"
EOS
    exec srun --account="${ACCOUNT}" -p qgpu -N1 --ntasks=1 \
      --cpus-per-task=16 --mem=48G --gres=gpu:1 -t "${TIME}" \
      --pty bash --noprofile --rcfile "${BASHRC}" -i
    ;;
  shell)
    if on_login_node; then
      echo "[interactive] REFUSING a sandbox shell on a login node — allocate first:"
      echo "  $0 evalsh / $0 alloc"
      exit 1
    fi
    source "${COMMON}"
    run_in_container_shell
    ;;
  check)
    CMD="source '${COMMON}' && run_in_container \"\${DPLM_PY}\" '${CHECKS_IN_CONTAINER}'"
    if on_login_node; then
      echo "[interactive] on login node — allocating 1 GPU (qgpu, ${TIME:-01:00:00}) ..."
      exec srun --account="${ACCOUNT}" -p qgpu -N1 --ntasks=1 \
        --cpus-per-task=16 --mem=48G --gres=gpu:1 -t "${TIME_CHECK:-01:00:00}" \
        bash -c "${CMD}"
    else
      echo "[interactive] already on compute node $(hostname -s) — running checks"
      source "${COMMON}"
      run_in_container "${DPLM_PY}" "${CHECKS_IN_CONTAINER}"
    fi
    ;;
  *)
    echo "usage: $0 {alloc|evalsh|shell|check}" >&2
    echo "  alloc  — qgpu_big whole node (8×A100), HOST shell, training debugging"
    echo "  evalsh — qgpu 1 GPU, HOST shell with eval_* helpers (IPS/DeepGO-SE/scoring)"
    echo "  shell  — inside an allocation: enter the dplm sandbox (NOT for IPS/DeepGO-SE)"
    echo "  check  — automated precondition checks (auto-allocates on a login node)"
    exit 2
    ;;
esac
