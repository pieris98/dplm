#!/usr/bin/env bash
# Interactive container access on Meluxina — mirrors the sbatch environment
# exactly (same common.sh binds/env), so what works here works in sbatch.
#
# Usage (from the repo root, or anywhere — paths are script-relative):
#   scripts/meluxina/interactive.sh alloc   # login node: GPU allocation, HOST
#                                           # shell (training: container via
#                                           # run_in_container[_shell])
#   scripts/meluxina/interactive.sh evalsh  # GPU allocation tuned for the eval
#                                           # stack (IPS + DeepGO-SE on the HOST
#                                           # shell; 1 GPU, qos=short, 4 h)
#   scripts/meluxina/interactive.sh shell   # inside an existing allocation:
#                                           # enter the dplm container shell
#                                           # (NOT usable for IPS/DeepGO-SE —
#                                           # no apptainer nesting, no java)
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
    echo "[interactive] (use 'alloc' for an interactive container shell with GPUs,"
    echo "[interactive]  or 'shell' when already inside an allocation)"
  else
    MODE="shell"
  fi
else
  MODE="$1"
fi

case "${MODE}" in
  alloc)
    # Land in a HOST shell inside the allocation (common.sh pre-sourced), so
    # exiting the container returns to the host shell; a second exit releases
    # the allocation.
    BASHRC="${TMPDIR:-/tmp}/dplm_alloc_bashrc.$$"
    cat > "${BASHRC}" <<EOS
source '${COMMON}'
echo "[interactive] HOST shell on \$(hostname -s) — allocation alive."
echo "  container shell : run_in_container_shell    (exit returns here; NOTE:"
echo "                    the container bash is ancient 4.4 and has NO apptainer/java/IPS —"
echo "                    eval tools (InterProScan, deepgose) must run in THIS host shell)"
echo "  one-off command : run_in_container <cmd...>   (training / generation / scoring)"
echo "  full checks     : run_in_container \"\${DPLM_PY}\" /workspace/dplm/scripts/meluxina/checks.py"
echo "  host diagnostics: nvidia-smi -L; ls -l /dev/nvidia*; ls -l \$(command -v apptainer)"
echo "  eval tools      : ensure_java   (then interproscan.sh / apptainer exec eval/deepgose_sandbox ...)"
echo "  exit            : release the allocation"
EOS
    exec salloc --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
      --gres="gpu:${GPUS}" -N1 --cpus-per-task=16 -t "${TIME}" \
      bash --noprofile --rcfile "${BASHRC}" -i
    ;;
  evalsh)
    # Host shell tuned for the eval stack (IPS + DeepGO-SE): 1 GPU, longer
    # wall, more CPUs for IPS, java + apptainer ready. The dplm container
    # stays one-shot here: generation/scoring via run_in_container, never
    # nested (a container cannot run apptainer inside itself).
    QOS="${QOS_EVAL:-short}"; GPUS=1; TIME="${TIME_EVAL:-04:00:00}"
    BASHRC="${TMPDIR:-/tmp}/dplm_evalsh_bashrc.$$"
    cat > "${BASHRC}" <<EOS
source '${COMMON}'
ensure_java || echo "[interactive] WARN: java unavailable — InterProScan stages will fail"
echo "[interactive] EVAL host shell on \$(hostname -s) — qos=${QOS} ${TIME}, 1 GPU."
echo "  IPS        : eval/interproscan-5.78-109.0/interproscan.sh -i <fasta> -f TSV -o <out>.tsv -goterms --disable-precalc -cpu 16"
echo "  DeepGO-SE  : apptainer exec --cleanenv --nv --bind \$PWD/eval/deepgo2/data:/workspace/deepgo2/data \\"
echo "               --bind \$PWD/eval/esm_torch_hub:/root/.cache/torch/hub/checkpoints \\"
echo "               --bind <evaldir>:/workspace/deepgo2/eval eval/deepgose_sandbox \\"
echo "               python predict.py -if eval/<arm>/aatype.fasta -dr data -d cuda"
echo "  generation : run_in_container \${DPLM_PY} scripts/eval_function/generate_eval_set.py --ckpt <ckpt> --out <evaldir> \\"
echo "               --test-pkl eval/cfpgen_eval/test.pkl --go-mapping eval/cfpgen_eval/go_mapping.pkl \\"
echo "               --ipr-mapping eval/cfpgen_eval/ipr_mapping.pkl"
echo "  scoring    : run_in_container \${DPLM_PY} scripts/eval_function/score_function_eval.py --evaldir <evaldir> --arms ... --obo eval/go.obo"
echo "  (or skip all manual steps: sbatch scripts/meluxina/eval_pipeline.sbatch)"
echo "  exit       : release the allocation"
EOS
    exec salloc --account="${ACCOUNT}" -p gpu --qos="${QOS}" \
      --gres="gpu:${GPUS}" -N1 --cpus-per-task=16 --mem=48G -t "${TIME}" \
      bash --noprofile --rcfile "${BASHRC}" -i
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
    echo "usage: $0 {alloc|evalsh|shell|check}" >&2
    echo "  alloc  — GPU allocation, HOST shell (training: enter the dplm container one-shot or interactively)"
    echo "  evalsh — GPU allocation tuned for the eval stack (IPS/DeepGO-SE on the host; 1 GPU, qos=short, 4 h)"
    echo "  shell  — inside an existing allocation: enter the dplm container (NOT for IPS/DeepGO-SE)"
    echo "  check  — automated precondition checks (auto-allocates on a login node)"
    exit 2
    ;;
esac
