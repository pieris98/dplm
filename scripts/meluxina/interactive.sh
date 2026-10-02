#!/usr/bin/env bash
# Interactive access on Meluxina — mirrors the sbatch environment
# exactly (same common.sh binds/env), so what works here works in sbatch.
#
# IMPORTANT: the shell is placed ON the compute node via `srun --pty`.
# (salloc alone would run the shell on the SUBMITTING host — still a login
# node — while merely holding the allocation: no GPUs, no container support.)
#
# Usage (from the repo root, or anywhere — paths are script-relative):
#   scripts/meluxina/interactive.sh alloc   # GPU allocation, HOST shell on the
#                                           # node (training: dplm container via
#                                           # run_in_container[_shell])
#   scripts/meluxina/interactive.sh evalsh  # allocation tuned for the eval
#                                           # stack (eval_generate/eval_ips/
#                                           # eval_dg/eval_score helpers; 1 GPU,
#                                           # qos=short, 4 h)
#   scripts/meluxina/interactive.sh shell   # inside an existing allocation:
#                                           # enter the dplm container shell
#                                           # (NOT for IPS/DeepGO-SE — no
#                                           # apptainer nesting, no java)
#   scripts/meluxina/interactive.sh check   # full precondition checks
#                                           # (auto-allocates on a login node)
#
# Env overrides: ACCOUNT, QOS, GPUS, TIME; evalsh: QOS_EVAL, TIME_EVAL,
# e.g.: QOS=short GPUS=2 scripts/meluxina/interactive.sh alloc

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ACCOUNT="${ACCOUNT:-p201418}"
QOS="${QOS:-test}"
GPUS="${GPUS:-4}"
# NOTE: Meluxina's 'test' QOS caps walltime below 1h (QOSMaxWallDurationPerJobLimit);
# 30 min matches what the smoke sbatch uses successfully.
TIME="${TIME:-00:30:00}"

COMMON="${ROOT_DIR}/scripts/meluxina/common.sh"
CHECKS_IN_CONTAINER="/workspace/dplm/scripts/meluxina/checks.py"

on_login_node() {
  [[ "$(hostname -s)" == login* || -z "${SLURM_JOB_ID:-}" ]]
}

# Default mode: 'check' on a login node (allocates GPUs itself), 'shell'
# on a compute node (already inside an allocation).
if [[ $# -eq 0 ]]; then
  if on_login_node; then
    MODE="check"
    echo "[interactive] no subcommand given; on login node → defaulting to 'check'"
    echo "[interactive] (use 'alloc' for a training host shell, 'evalsh' for eval work,"
    echo "[interactive]  or 'shell' when already inside an allocation)"
  else
    MODE="shell"
  fi
else
  MODE="$1"
fi

case "${MODE}" in
  alloc)
    # HOST shell ON the compute node (common.sh pre-sourced). Exiting releases
    # the allocation.
    BASHRC="${TMPDIR:-/tmp}/dplm_alloc_bashrc.$$"
    cat > "${BASHRC}" <<EOS
source '${COMMON}'
echo "[interactive] HOST shell on \$(hostname -s) — allocation alive (qos=${QOS}, ${TIME})."
echo '  dplm container (training / generation / scoring):'
echo '    interactive : run_in_container_shell   (exit returns here; the container bash'
echo '                  is old 4.4 and has NO apptainer/java/IPS — eval tools must run'
echo '                  in THIS host shell, see eval_ips / eval_dg below)'
echo '    one-off     : run_in_container <cmd...>'
echo '    full checks : run_in_container "${DPLM_PY}" /workspace/dplm/scripts/meluxina/checks.py'
echo '  eval tools (host): ensure_java · eval_ips <evaldir> <arm> · eval_dg <evaldir> <arm>'
echo '  exit        : release the allocation'
EOS
    exec srun --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
      --gres="gpu:${GPUS}" -N1 --ntasks=1 --cpus-per-task=16 -t "${TIME}" \
      --pty bash --noprofile --rcfile "${BASHRC}" -i
    ;;
  evalsh)
    # HOST shell ON the compute node, tuned for the eval stack: 1 GPU, longer
    # wall, stage helpers from common.sh. The dplm container stays one-shot
    # (a container cannot run apptainer inside itself → no DeepGO-SE, no java).
    QOS="${QOS_EVAL:-short}"; GPUS=1; TIME="${TIME_EVAL:-04:00:00}"
    BASHRC="${TMPDIR:-/tmp}/dplm_evalsh_bashrc.$$"
    cat > "${BASHRC}" <<EOS
source '${COMMON}'
ensure_java || echo "[interactive] WARN: java unavailable — eval_ips will fail"
echo "[interactive] EVAL host shell on \$(hostname -s) — qos=${QOS} ${TIME}, ${GPUS} GPU."
echo '  helpers (evaldir is repo-relative, e.g. eval_runs/fn_eval_v1; every stage skips'
echo '  finished outputs, so re-running is cheap):'
echo '    eval_generate <evaldir> <ckpt> [extra args...]   # generation, container+GPU'
echo '        sweep: eval_generate <evaldir> <ckpt> --cfg-scale 2 --arms ours_cond'
echo '    eval_ips <evaldir> <arm> [cpus]                  # InterProScan, host+java (slow!)'
echo '    eval_dg <evaldir> <arm>                          # DeepGO-SE sandbox, GPU'
echo '    eval_score <evaldir> <arm,arm,...>               # scorer one-shot, container'
echo '  typical full pass:'
echo '    CK=logs/cond_dplm2_650m_cfpgen/checkpoints/step_99999.0-loss_1.95.ckpt'
echo '    eval_generate eval_runs/fn_eval_v1 $CK'
echo '    for a in ours_cond ours_null vanilla real; do eval_ips eval_runs/fn_eval_v1 $a; eval_dg eval_runs/fn_eval_v1 $a; done'
echo '    eval_score eval_runs/fn_eval_v1 ours_cond,ours_null,vanilla,real'
echo '  (headless equivalent: sbatch scripts/meluxina/eval_pipeline.sbatch)'
echo '  exit        : release the allocation'
EOS
    exec srun --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
      --gres="gpu:${GPUS}" -N1 --ntasks=1 --cpus-per-task=16 --mem=48G -t "${TIME}" \
      --pty bash --noprofile --rcfile "${BASHRC}" -i
    ;;
  shell)
    if on_login_node; then
      echo "[interactive] REFUSING to run a container shell on a login node:"
      echo "  no GPUs here, and login nodes often lack squashfuse so apptainer"
      echo "  would extract the entire ~57 GB SIF into a temp sandbox."
      echo "Use: $0 alloc   (interactive shell with GPUs)"
      echo "  or: $0 check   (automated precondition checks)"
      exit 1
    fi
    source "${COMMON}"
    run_in_container_shell
    ;;
  check)
    CMD="source '${COMMON}' && run_in_container \"\${DPLM_PY}\" '${CHECKS_IN_CONTAINER}'"
    if on_login_node; then
      echo "[interactive] on login node — allocating ${GPUS} GPU(s), qos=${QOS}, ${TIME} ..."
      # srun (not salloc): the checks must execute ON the compute node.
      exec srun --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
        --gres="gpu:${GPUS}" -N1 --ntasks=1 --cpus-per-task=16 -t "${TIME}" \
        bash -c "${CMD}"
    else
      echo "[interactive] already on compute node $(hostname -s) — running checks"
      source "${COMMON}"
      run_in_container "${DPLM_PY}" "${CHECKS_IN_CONTAINER}"
    fi
    ;;
  *)
    echo "usage: $0 {alloc|evalsh|shell|check}" >&2
    echo "  alloc  — GPU allocation, HOST shell on the node (training; dplm container one-shot or interactive)"
    echo "  evalsh — GPU allocation for eval work (eval_generate/eval_ips/eval_dg/eval_score helpers; 1 GPU, qos=short, 4 h)"
    echo "  shell  — inside an existing allocation: enter the dplm container (NOT for IPS/DeepGO-SE)"
    echo "  check  — automated precondition checks (auto-allocates on a login node)"
    exit 2
    ;;
esac
