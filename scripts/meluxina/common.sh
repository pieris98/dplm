#!/usr/bin/env bash
# Shared launcher library for conditional-DPLM2 HPC jobs.
#
# Despite the directory name, this is CLUSTER-SHARED: sourced by the sbatch /
# interactive scripts under scripts/meluxina/ AND scripts/karolina/. All
# cluster specifics are behind env overrides (DPLM_BASE, DPLM_SIF,
# APPTAINER_BIN, …) and guarded auto-discovery.
#
# Sourced by the sbatch scripts. Responsibilities:
#   1. Locate the Apptainer SIF (pulled once from Docker Hub).
#   2. Bind-mount ONLY the code subdirectories that change (src/, configs/,
#      scripts/, the generation CLI) over the image's /workspace/dplm — so a
#      `git pull` updates the code without an image rebuild, while the image's
#      compiled vendor/openfold CUDA kernels and baked-in dataset stay visible.
#      (Mounting the WHOLE repo dir would shadow the built artifacts and break
#      openfold with `ModuleNotFoundError: attn_core_inplace_cuda`.)
#   3. Bind persistent writable dirs for logs / wandb / generation outputs
#      (the image filesystem is read-only).
#   4. Provide `run_in_container CMD...` that executes inside the SIF with
#      GPU access (--nv) and all relevant env vars forwarded.

# --- Ensure apptainer is available -----------------------------------------
# SLURM batch shells are minimal: they do NOT source ~/.bashrc or the full
# /etc/profile, so neither the module function nor module-loaded binaries
# carry over from the login shell.
#
# HANG-SAFETY: module init scripts can block in batch shells. We therefore
# NEVER source anything in this live shell. Instead, run the whole
# module-init + module-load in a THROWAWAY subshell under `timeout`, have
# it print its resolved PATH, and import only that PATH string here.
if ! command -v apptainer >/dev/null 2>&1; then
  if [[ -n "${APPTAINER_BIN:-}" && -x "${APPTAINER_BIN}/apptainer" ]]; then
    export PATH="${APPTAINER_BIN}:${PATH}"
  else
    _resolved_path="$(timeout 20 bash -c '
      for init in /etc/profile.d/modules.sh /usr/share/modules/init/bash /etc/profile.modules /usr/share/lmod/lmod/init/bash; do
        [[ -f "$init" ]] && source "$init" >/dev/null 2>&1 && break
      done
      if command -v module >/dev/null 2>&1; then
        module load Apptainer >/dev/null 2>&1 || module load Singularity >/dev/null 2>&1 || true
      fi
      command -v apptainer >/dev/null 2>&1 && printf "%s" "$PATH"
    ' 2>/dev/null || true)"
    if [[ -n "${_resolved_path}" ]]; then
      export PATH="${_resolved_path}"
      echo "[meluxina] apptainer located via guarded module discovery"
    fi
    unset _resolved_path
  fi
fi
if ! command -v apptainer >/dev/null 2>&1; then
  # Try common install prefixes — cheap and hang-free. The Meluxina
  # EasyBuild path is listed FIRST: /apps is shared storage, so this exact
  # prefix works on every compute node and removes any dependency on the
  # Lmod module system inside the batch shell.
  for p in /apps/USE/easybuild/release/2025.1/software/Apptainer/1.4.2-GCCcore-14.2.0/bin \
           /usr/bin /usr/local/bin /opt/paraview/Apptainer/bin /usr/local/apptainer/bin \
           /opt/apptainer/bin /opt/cesga/apptainer/bin /mnt/tier2/opt/apptainer/bin; do
    [[ -x "$p/apptainer" ]] && export PATH="${p}:${PATH}" && break
  done
fi
if ! command -v apptainer >/dev/null 2>&1; then
  echo "[meluxina] ERROR: apptainer not found after guarded module discovery + prefix search."
  echo "[meluxina] On the LOGIN node, find the real path:"
  echo "  module load Apptainer; which apptainer"
  echo "then either:"
  echo "  (a) export APPTAINER_BIN=<dir-containing-apptainer> before sbatch, or"
  echo "  (b) symlink it: ln -s <path>/apptainer $HOME/bin/apptainer  (ensure ~/bin is on PATH)"
  exit 1
fi
echo "[meluxina] apptainer: $(command -v apptainer)"

# squashfuse: unprivileged Apptainer FUSE-mounts the SIF; if missing it
# FALLS BACK TO EXTRACTING THE WHOLE ~57 GB IMAGE into a temp sandbox.
# On EasyBuild stacks squashfuse ships as a separate module path that the
# bare batch shell doesn't have — probe for it so we never sandbox-convert.
if ! command -v squashfuse >/dev/null 2>&1; then
  for d in /apps/USE/easybuild/release/*/software/squashfuse/*/bin \
           /usr/bin /usr/local/bin; do
    [[ -x "$d/squashfuse" ]] && export PATH="${d}:${PATH}" && break
  done
fi
if ! command -v squashfuse >/dev/null 2>&1; then
  echo "[meluxina] WARNING: squashfuse not found — apptainer may extract the"
  echo "  full SIF to a temp sandbox (slow, fills /tmp). If 'module load"
  echo "  Apptainer' works in this shell, prefer running through it."
fi

# --- Storage base ----------------------------------------------------------
# Meluxina project layout: no $SCRATCH. Use $PROJECT for large artifacts
# (the ~57 GB SIF, HF cache), falling back to $HOME if unset.
DPLM_BASE="${DPLM_BASE:-${PROJECT:-${SCRATCH:-$HOME}}}"

# --- Locate the image (SIF file OR sandbox directory) ----------------------
# Precedence: $DPLM_SIF (file OR dir), then $DPLM_BASE/dplm_sandbox (preferred
# over the SIF — plain-dir rootfs avoids the squashfuse mount flake), then
# $DPLM_BASE/dplm_cond.sif. NOTE: -e, not -f — a sandbox is a directory.
DPLM_SIF="${DPLM_SIF:-}"
if [[ -z "$DPLM_SIF" ]]; then
  for cand in "${DPLM_BASE}/dplm_sandbox" "${DPLM_BASE}/dplm_cond.sif" \
              "$HOME/dplm_cond.sif" "$(pwd)/dplm_cond.sif"; do
    [[ -e "$cand" ]] && DPLM_SIF="$cand" && break
  done
fi
if [[ -z "$DPLM_SIF" || ! -e "$DPLM_SIF" ]]; then
  echo "[meluxina] ERROR: container image not found (SIF or sandbox dir)."
  echo "[meluxina] Either pull the SIF:"
  echo "  apptainer pull ${DPLM_BASE}/dplm_cond.sif docker://pieris98/dplm:cu121-torch220-cond"
  echo "[meluxina] or build the sandbox (preferred — no SIF-mount flake):"
  echo "  apptainer build --sandbox ${DPLM_BASE}/dplm_sandbox docker://pieris98/dplm:cu121-torch220-cond"
  echo "[meluxina] or export DPLM_SIF=/absolute/path/to/{dplm_cond.sif,dplm_sandbox}"
  exit 1
fi
echo "[meluxina] image: ${DPLM_SIF}"

# --- Repo checkout (source of fresh code) ----------------------------------
# ROOT_DIR is normally set by the sbatch caller. When sourced directly
# (interactive.sh salloc path), derive it from this file's location:
# common.sh lives at <repo>/scripts/meluxina/common.sh.
if [[ -z "${ROOT_DIR:-}" ]]; then
  _common_src="${BASH_SOURCE[0]:-}"
  if [[ -n "${_common_src}" && -f "${_common_src}" ]]; then
    ROOT_DIR="$(cd "$(dirname "${_common_src}")/../.." && pwd)"
  else
    ROOT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
  fi
fi
REPO_DIR="${ROOT_DIR}"
echo "[meluxina] repo: ${REPO_DIR}"

# --- Persistent writable dirs (bind-mounted into the container) ------------
DPLM_LOGS="${DPLM_LOGS:-${DPLM_BASE}/dplm-logs}"
DPLM_WANDB="${DPLM_WANDB:-${DPLM_BASE}/dplm-wandb}"
DPLM_GEN="${DPLM_GEN:-${DPLM_BASE}/dplm-gen}"
RUN_SCRATCH="${RUN_SCRATCH:-${DPLM_BASE}/dplm_run_${SLURM_JOB_ID:-manual}}"
mkdir -p "${DPLM_LOGS}" "${DPLM_WANDB}" "${DPLM_GEN}" "${RUN_SCRATCH}/tmp"

# --- Environment forwarded into the container ------------------------------
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-8}"
export TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM:-false}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
export WANDB_PROJECT="${WANDB_PROJECT:-CondDPLM2_650m}"

# Absolute interpreter path inside the container. PATH-based resolution of
# `python` proved unreliable across Apptainer env semantics (host-env
# propagation vs --cleanenv vs OCI-pulled image env); an absolute path
# bypasses PATH entirely.
DPLM_PY="/opt/venv/bin/python"
export DPLM_PY

# Distributed env (Lightning reads these when multi-node)
export MASTER_ADDR="${MASTER_ADDR:-$(hostname)}"
export MASTER_PORT="${MASTER_PORT:-29500}"

# --- The launcher -----------------------------------------------------------
# Karolina's module environment exports SINGULARITYENV_LD_PRELOAD, which
# injects HOST libraries into every container process — an ABI-mismatch risk
# for the image's python/torch. Our env forwarding is done via --env flags,
# so drop the legacy-injection vars before exec.
unset SINGULARITYENV_LD_PRELOAD SINGULARITYENV_PYTHONPATH 2>/dev/null || true

# One argument array shared by exec AND interactive shell, so the manual /
# interactive path is guaranteed identical to the sbatch path.
CONTAINER_ARGS=(
  --nv
  --cleanenv
  --bind "${REPO_DIR}/.git:/workspace/dplm/.git"
  --bind "${REPO_DIR}/src:/workspace/dplm/src"
  --bind "${REPO_DIR}/configs:/workspace/dplm/configs"
  --bind "${REPO_DIR}/scripts:/workspace/dplm/scripts"
  --bind "${REPO_DIR}/train.py:/workspace/dplm/train.py"
  --bind "${REPO_DIR}/generate_conditional_dplm2.py:/workspace/dplm/generate_conditional_dplm2.py"
  --bind "${DPLM_LOGS}:/workspace/dplm/logs"
  --bind "${DPLM_WANDB}:/workspace/dplm/wandb"
  --bind "${DPLM_GEN}:/workspace/dplm/generation-results"
  --bind "${RUN_SCRATCH}/tmp:/tmp"
  --env PATH="/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  --env OMP_NUM_THREADS="${OMP_NUM_THREADS}"
  --env TOKENIZERS_PARALLELISM="${TOKENIZERS_PARALLELISM}"
  --env PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF}"
  --env WANDB_API_KEY="${WANDB_API_KEY:-}"
  --env WANDB_PROJECT="${WANDB_PROJECT}"
  --env WANDB_MODE="${WANDB_MODE:-online}"
  --env WANDB_DIR="/workspace/dplm/wandb"
  --env HF_HUB_OFFLINE=1
  --env GIT_DISCOVERY_ACROSS_FILESYSTEM=1
  --env MASTER_ADDR="${MASTER_ADDR}"
  --env MASTER_PORT="${MASTER_PORT}"
  --pwd /workspace/dplm
)

# Optional eval-stack binds (present only after the eval assets are synced to
# this checkout): read-only predictor/asset inputs under eval/, writable
# eval_runs/ output dir, and pure-python deps (obonet) pip-installed with
# --target into eval/pylibs — PYTHONPATH keeps the image untouched, so this
# works for both sandbox dirs and read-only SIFs. Guarded so training jobs on
# checkouts without these dirs are unaffected.
if [[ -d "${REPO_DIR}/eval" ]]; then
  CONTAINER_ARGS+=(--bind "${REPO_DIR}/eval:/workspace/dplm/eval:ro")
fi
if [[ -d "${REPO_DIR}/eval_runs" ]]; then
  CONTAINER_ARGS+=(--bind "${REPO_DIR}/eval_runs:/workspace/dplm/eval_runs")
fi
if [[ -d "${REPO_DIR}/eval/pylibs" ]]; then
  CONTAINER_ARGS+=(--env PYTHONPATH="/workspace/dplm/eval/pylibs")
fi

run_in_container() {
  # Retry wrapper: unprivileged SIF mounts (squashfuse_ll) intermittently
  # come up in a bad state ("stat ... permission denied" / mount-hook FATAL
  # at container creation). The flake strikes within the first seconds, so
  # retry ONLY fast failures — never a command that ran for a while (would
  # risk restarting a long training run).
  local attempt rc=1 start
  for attempt in 1 2 3; do
    start=$SECONDS
    apptainer exec "${CONTAINER_ARGS[@]}" "${DPLM_SIF}" "$@"
    rc=$?
    if [[ $rc -eq 0 ]]; then
      return 0
    fi
    if (( SECONDS - start > 120 )); then
      echo "[meluxina] command ran $((SECONDS - start))s before failing (rc=$rc) — not retrying."
      return "$rc"
    fi
    echo "[meluxina] attempt $attempt failed fast (rc=$rc — likely SIF-mount flake); retrying in 5s ..."
    sleep 5
  done
  return "$rc"
}

# Interactive bash inside the container (same binds/env as run_in_container).
run_in_container_shell() {
  local -a sh=(bash)
  [[ -t 0 ]] && sh=(bash -i)
  apptainer exec "${CONTAINER_ARGS[@]}" "${DPLM_SIF}" "${sh[@]}"
}
# NOTE: the container's bash is the image's own (ancient, 4.4 on this image).
# That is cosmetic — but inside the container there is NO apptainer (cannot
# nest), NO java, NO InterProScan: the eval tools (IPS, the deepgose sandbox)
# must run in the HOST shell of the allocation. Use run_in_container_shell
# only for training/generation/scoring; run IPS and DeepGO-SE on the host.

# --- Java (needed by InterProScan) ------------------------------------------
# The module function is unavailable in SLURM batch shells and --noprofile
# interactive shells, and module init can hang — same hang-safe throwaway
# subshell discovery as the apptainer block above, plus a filesystem glob
# fallback over EasyBuild prefixes. Safe to call repeatedly.
_java_major() {
  # "openjdk version \"1.8.0_502\"" -> 8 ; "\"11.0.20\"" -> 11
  java -version 2>&1 | head -1 | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p'
}

ensure_java() {
  # IPS 5.78 requires Java >= 11. Karolina's default environment carries
  # Java 1.8 — so PRESENCE of java is not enough, the major version must
  # pass. Order: existing java (if >= 11), guarded module load (Java/11
  # then Java), EasyBuild glob fallback.
  if command -v java >/dev/null 2>&1; then
    local maj; maj="$(_java_major)"
    if [[ -n "$maj" ]] && (( maj >= 11 )); then
      java -version 2>&1 | head -1; return 0
    fi
  fi
  local _jbin
  _jbin="$(timeout 20 bash -c '
    for init in /etc/profile.d/modules.sh /usr/share/modules/init/bash /etc/profile.modules /usr/share/lmod/lmod/init/bash; do
      [[ -f "$init" ]] && source "$init" >/dev/null 2>&1 && break
    done
    if command -v module >/dev/null 2>&1; then
      module load Java/11 >/dev/null 2>&1 || module load Java >/dev/null 2>&1 || true
    fi
    command -v java >/dev/null 2>&1 && dirname "$(readlink -f "$(command -v java)")"
  ' 2>/dev/null || true)"
  if [[ -n "$_jbin" && -x "$_jbin/java" ]]; then
    export PATH="${_jbin}:${PATH}"
  else
    local j
    for j in /apps/USE/easybuild/release/*/software/Java/*/bin /usr/lib/jvm/*/bin; do
      [[ -x "$j/java" ]] && export PATH="${j}:${PATH}" && break
    done
  fi
  if ! command -v java >/dev/null 2>&1; then
    echo "[meluxina] ERROR: java not found (required by InterProScan)"; return 1
  fi
  local maj; maj="$(_java_major)"
  if [[ -z "$maj" ]] || (( maj < 11 )); then
    echo "[meluxina] ERROR: java >= 11 required for InterProScan, found: $(java -version 2>&1 | head -1)"
    return 1
  fi
  java -version 2>&1 | head -1
}

# InterProScan's MobiDB-lite step (bin/mobidb/idrpred) is a Python >= 3.8
# script — it annotates `re.Match`, which only exists in 3.8+. EL8 hosts
# (Karolina) default python3 to 3.6, so the step dies with
# `AttributeError: module 're' has no attribute 'Match'`. Shim BOTH python
# and python3 (in a PATH segment prepended only for IPS runs) to the newest
# host python3 that passes a re.Match check.
ensure_ips_python() {
  local shim_dir="${REPO_DIR}/eval/ips_bin"
  local c p="" want=""
  for c in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    p=""
    if command -v "$c" >/dev/null 2>&1; then p="$(command -v "$c")"
    elif [[ -x "/usr/bin/$c" ]]; then p="/usr/bin/$c"
    fi
    [[ -n "$p" ]] || continue
    if "$p" -c "import re; assert hasattr(re, 'Match')" >/dev/null 2>&1; then want="$p"; break; fi
  done
  if [[ -z "$want" ]]; then
    echo "[meluxina] WARN: no python >= 3.8 found — IPS MobiDB step will fail"
    return 1
  fi
  mkdir -p "$shim_dir"
  ln -sf "$want" "$shim_dir/python"
  ln -sf "$want" "$shim_dir/python3"
  export PATH="$shim_dir:$PATH"
  echo "[meluxina] ips_bin shim: python,python3 -> $want"
}

# --- Eval-stack stage helpers -------------------------------------------------
# Same stages as eval_pipeline.sbatch, callable from an interactive HOST shell
# (interactive.sh evalsh). EVALDIR is repo-relative, e.g. eval_runs/fn_eval_v1.
# Each stage skips already-completed outputs, so re-running is cheap.

eval_generate() {
  # eval_generate <evaldir> <ckpt> [extra generate_eval_set.py args...]
  # e.g. sweep: eval_generate eval_runs/fn_eval_v1 <ckpt> --cfg-scale 2 --arms ours_cond
  # NOTE: bash's `shift N` fails SILENTLY when fewer positionals exist, leaving
  # "$@" intact — a missing <ckpt> would then re-append <evaldir> to the driver
  # command as an unrecognized positional. Guard explicitly.
  if [[ $# -lt 2 || -z "${1:-}" || -z "${2:-}" ]]; then
    echo "usage: eval_generate <evaldir> <ckpt> [extra generate_eval_set.py args...]" >&2
    echo "  e.g. eval_generate eval_runs/fn_eval_v1 logs/cond_dplm2_650m_cfpgen/checkpoints/step_99999.0-loss_1.95.ckpt" >&2
    return 2
  fi
  local evaldir="$1" ckpt="$2"
  shift 2
  run_in_container "$DPLM_PY" scripts/eval_function/generate_eval_set.py \
    --ckpt "$ckpt" --out "$evaldir" \
    --test-pkl eval/cfpgen_eval/test.pkl \
    --go-mapping eval/cfpgen_eval/go_mapping.pkl \
    --ipr-mapping eval/cfpgen_eval/ipr_mapping.pkl \
    "$@"
}

eval_ips() {
  # eval_ips <evaldir> <arm> [cpus]   — HOST side (java; slow on degenerate arms)
  if [[ $# -lt 2 || -z "${1:-}" || -z "${2:-}" ]]; then
    echo "usage: eval_ips <evaldir> <arm> [cpus]" >&2
    return 2
  fi
  ensure_java || return 1
  ensure_ips_python || true   # MobiDB-lite needs a `python` (2.7) shim
  local evaldir="$1" arm="$2" cpus="${3:-${SLURM_CPUS_PER_TASK:-16}}"
  [[ -s "$REPO_DIR/$evaldir/$arm/ips.tsv" ]] && { echo "[eval] $arm ips.tsv exists — skipping"; return 0; }
  [[ -f "$REPO_DIR/$evaldir/$arm/aatype.fasta" ]] || { echo "[eval] $arm has no aatype.fasta"; return 1; }
  "$REPO_DIR/eval/interproscan-5.78-109.0/interproscan.sh" \
    -i "$REPO_DIR/$evaldir/$arm/aatype.fasta" -f TSV \
    -o "$REPO_DIR/$evaldir/$arm/ips.tsv" -T "$REPO_DIR/$evaldir/$arm/ips_tmp" \
    -goterms --disable-precalc -cpu "$cpus"
}

eval_dg() {
  # eval_dg <evaldir> <arm>   — DeepGO-SE sandbox on GPU. Container paths are
  # ABSOLUTE on purpose: apptainer maps the caller's cwd into the container
  # (via SINGULARITY_BINDPATH), so relative paths resolve against wherever the
  # shell happens to stand and break silently.
  if [[ $# -lt 2 || -z "${1:-}" || -z "${2:-}" ]]; then
    echo "usage: eval_dg <evaldir> <arm>" >&2
    return 2
  fi
  local evaldir="$1" arm="$2"
  if compgen -G "$REPO_DIR/$evaldir/$arm/aatype_preds_*.tsv.gz" >/dev/null; then
    echo "[eval] $arm preds exist — skipping"; return 0
  fi
  apptainer exec --cleanenv --nv \
    --bind "$REPO_DIR/eval/deepgo2/data:/workspace/deepgo2/data" \
    --bind "$REPO_DIR/eval/esm_torch_hub:/root/.cache/torch/hub/checkpoints" \
    --bind "$REPO_DIR/$evaldir:/workspace/deepgo2/eval" \
    "$REPO_DIR/eval/deepgose_sandbox" \
    python /workspace/deepgo2/predict.py -if "/workspace/deepgo2/eval/$arm/aatype.fasta" -dr /workspace/deepgo2/data -d cuda
}

eval_score() {
  # eval_score <evaldir> <arm,arm,...>   — scorer one-shot in the dplm container
  if [[ $# -lt 2 || -z "${1:-}" || -z "${2:-}" ]]; then
    echo "usage: eval_score <evaldir> <arm,arm,...>" >&2
    return 2
  fi
  local evaldir="$1" arms="$2" arm
  run_in_container "$DPLM_PY" -c "import sklearn, obonet" >/dev/null 2>&1 || {
    echo "[eval] FATAL: sklearn/obonet unavailable in the container."
    echo "[eval] On the LOGIN node run once:"
    echo "  python3 -m pip install --target $REPO_DIR/eval/pylibs 'obonet==1.2.0'"
    return 1
  }
  local -a args=(scripts/eval_function/score_function_eval.py
                 --evaldir "$evaldir" --arms "$arms" --obo eval/go.obo)
  local IFS=','
  for arm in $arms; do
    args+=(--ips-tsv "$arm=$evaldir/$arm/ips.tsv")
    args+=("--deepgose-tsv=$arm=$evaldir/$arm/aatype_preds_*.tsv.gz")
  done
  run_in_container "$DPLM_PY" "${args[@]}"
}

# NOTE: --cleanenv + explicit PATH are required. Unlike Docker, Apptainer
# propagates the HOST environment into the container, overriding the image's
# ENV PATH — and the host PATH has no `python` (the image's interpreter is
# /opt/venv/bin/python), so every python invocation dies with
# `"python": executable file not found in $PATH`. --cleanenv restores the
# image's own ENV (CUDA_HOME, TORCH_HOME, HF_HOME, ...) as the base, and the
# --env forwards above re-apply what the job needs on top.
# NOTE: deliberately NO bind over /opt/huggingface. The image bakes the
# pretrained HF models (dplm2_650m, struct_tokenizer, ...) into that path.
# Binding an empty scratch dir over it would shadow the cache, forcing
# from_pretrained to attempt downloads — which hang/fail on compute nodes
# without internet egress. The image also sets sensible HF env defaults
# (HF_HOME=/opt/huggingface, etag timeouts) so cache hits work read-only.
