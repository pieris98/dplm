#!/usr/bin/env bash
# One-time setup on Karolina (run on a LOGIN node; needs internet for pulls).
# Idempotent: every step checks for its output and skips if present.
#
#   export ACCOUNT=OPEN-XX-XX                 # your IT4I project id
#   export DPLM_BASE=/scratch/project/$ACCOUNT
#   bash scripts/karolina/setup_data.sh
#
# Everything lands under $DPLM_BASE (scratch, 20 TB, 90-day PURGE — plan
# backups of checkpoints/results) except repo-relative eval assets.
# Two assets CANNOT be downloaded here and must be rsynced from the local
# workstation (see the printed TODOs): eval/deepgo2/data (16 GB, web-archive
# recovery) and eval/cfpgen_eval/ (CFP-Gen test split, local-only).

set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"
DPLM_BASE="${DPLM_BASE:-${SCRATCH:-/scratch/project/${ACCOUNT:?export ACCOUNT=<IT4I project id>}}}"
mkdir -p "$DPLM_BASE"
echo "[setup] base: $DPLM_BASE"
echo "[setup] repo: $ROOT_DIR"

# Apptainer's docker-layer cache defaults to ~/.apptainer/cache — on Karolina
# HOME has a 25 GB quota and the dplm image's layers alone are ~20-30 GB, so
# any build dies with "disk quota exceeded". Point cache + temp at scratch.
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$DPLM_BASE/apptainer_cache}"
export SINGULARITY_CACHEDIR="${SINGULARITY_CACHEDIR:-$APPTAINER_CACHEDIR}"  # legacy name
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-$DPLM_BASE/apptainer_tmp}"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
echo "[setup] apptainer cache/tmp: $APPTAINER_CACHEDIR / $APPTAINER_TMPDIR (scratch)"

step() { echo -e "\n=== $* ==="; }
need_cmd() { command -v "$1" >/dev/null || { echo "FATAL: '$1' not found — module load first?"; exit 1; }; }

# --- 1. dplm sandbox (training/generation/scoring image; ~57 GB) -------------
step "1. dplm sandbox"
if [[ -d "$DPLM_BASE/dplm_sandbox" ]]; then
  echo "exists: $DPLM_BASE/dplm_sandbox — skipping"
else
  need_cmd apptainer
  echo "building from docker://pieris98/dplm:cu121-torch220-cond (~15-30 min)"
  apptainer build --sandbox "$DPLM_BASE/dplm_sandbox" docker://pieris98/dplm:cu121-torch220-cond
fi

# --- 2. deepgose sandbox (DeepGO-SE predict env; ~12 GB) ---------------------
step "2. deepgose sandbox"
if [[ -e "$ROOT_DIR/eval/deepgose_sandbox" ]]; then
  echo "exists: $ROOT_DIR/eval/deepgose_sandbox — skipping"
else
  need_cmd apptainer
  mkdir -p "$ROOT_DIR/eval"
  apptainer build --sandbox "$ROOT_DIR/eval/deepgose_sandbox" docker://coolmaksat/deepgose
fi

# --- 3. ESM-2 3B torch-hub cache (5.7 GB; DeepGO-SE runtime dep) -------------
# NOTE: the contact-regression weights live under /fair-esm/regression/, NOT
# /fair-esm/models/ (the models/ path 403s).
step "3. ESM-2 3B cache"
mkdir -p "$ROOT_DIR/eval/esm_torch_hub"
esm_fetch() {  # <filename> <url>
  local f="$1" url="$2"
  if [[ -s "$ROOT_DIR/eval/esm_torch_hub/$f" ]]; then echo "exists: $f"; return 0; fi
  wget -c -O "$ROOT_DIR/eval/esm_torch_hub/$f.part" "$url" &&
    mv "$ROOT_DIR/eval/esm_torch_hub/$f.part" "$ROOT_DIR/eval/esm_torch_hub/$f"
}
esm_fetch esm2_t36_3B_UR50D.pt \
  "https://dl.fbaipublicfiles.com/fair-esm/models/esm2_t36_3B_UR50D.pt"
esm_fetch esm2_t36_3B_UR50D-contact-regression.pt \
  "https://dl.fbaipublicfiles.com/fair-esm/regression/esm2_t36_3B_UR50D-contact-regression.pt"

# --- 4. InterProScan 5.78-109.0 ---------------------------------------------
# The GitHub release carries NO tarball asset; its notes point to EBI's HTTPS
# host (2026-10: reachable — the earlier Meluxina-era block is gone).
step "4. InterProScan 5.78-109.0"
if [[ -x "$ROOT_DIR/eval/interproscan-5.78-109.0/interproscan.sh" ]]; then
  echo "exists — skipping"
else
  IPS_URL="${IPS_URL:-https://ftp.ebi.ac.uk/pub/software/unix/iprscan/5/5.78-109.0/interproscan-5.78-109.0-64-bit.tar.gz}"
  IPS_MD5="${IPS_MD5:-3bb9a0794e9d69a0418a5298cdb04445}"   # from the release notes
  need_cmd wget; need_cmd tar
  echo "downloading ~6.6 GB from EBI (HTTPS), md5-verifying, extracting (~35 GB)"
  wget -c -O /tmp/ips578.tar.gz "$IPS_URL"
  echo "$IPS_MD5  /tmp/ips578.tar.gz" | md5sum -c - || {
    echo "FATAL: md5 mismatch — download corrupted"; exit 1; }
  tar -xzf /tmp/ips578.tar.gz -C "$ROOT_DIR/eval/"
  rm -f /tmp/ips578.tar.gz
fi
# small validate run (bundled test proteins) — takes a few minutes
if [[ ! -f "$ROOT_DIR/eval/.ips_validated" ]]; then
  echo "validating IPS (prefers a Java 11 module — IPS 5.78 wants 11; 17 can break member DBs)"
  # Prefer Java/11 over whatever plain `module load Java` gives (17 on Karolina).
  bash -c '
    for init in /usr/share/lmod/lmod/init/bash /etc/profile.d/modules.sh /usr/share/modules/init/bash; do
      [[ -f "$init" ]] && source "$init" >/dev/null 2>&1 && break
    done
    if command -v module >/dev/null 2>&1; then
      module load Java/11 >/dev/null 2>&1 || module load Java >/dev/null 2>&1 || true
    fi
    java -version 2>&1 | head -1
  ' || true
  ( cd "$ROOT_DIR/eval/interproscan-5.78-109.0" && \
    TEST_FA="core/test_all_proteins/test_all_proteins.fasta"; \
    [[ -f "$TEST_FA" ]] || TEST_FA="$(find . -maxdepth 3 -path '*test*' -name '*.fasta' | head -1)"; \
    ./interproscan.sh -i "$TEST_FA" \
      -f TSV -o /tmp/ips_validate.tsv --disable-precalc -cpu 4 >/tmp/ips_validate.log 2>&1 ) \
    && touch "$ROOT_DIR/eval/.ips_validated" \
    || { echo "WARN: IPS validation failed — last log lines:"; tail -15 /tmp/ips_validate.log; }
fi

# --- 5. go.obo (GO DAG for ancestor-expanded scoring) ------------------------
step "5. go.obo"
if [[ -s "$ROOT_DIR/eval/go.obo" ]]; then echo "exists"; else
  wget -q -O "$ROOT_DIR/eval/go.obo" https://purl.obolibrary.org/obo/go.obo && echo "downloaded"
fi

# --- 6. scorer pure-python deps (obonet via PYTHONPATH — keeps image intact) -
# Karolina's default python3 is 3.6 — too old for obonet 1.2 (pip on 3.6 only
# sees obonet <=0.3.0). Use python3.9 DELIBERATELY: it matches the sandbox's
# /opt/venv python, so pip resolves wheels (networkx!) that are importable
# inside the py3.9 container via PYTHONPATH. A 3.12 pip would pull networkx
# >=3.4 (py>=3.10 wheels) and break the container-side import.
step "6. eval/pylibs (obonet)"
PY_SETUP="$(command -v python3.9 || command -v python3.11 || command -v python3.12 || command -v python3)"
echo "using host python: $PY_SETUP ($("$PY_SETUP" -V 2>&1))"
if "$PY_SETUP" -c "import sys; sys.path.insert(0, '$ROOT_DIR/eval/pylibs'); import obonet" 2>/dev/null; then
  echo "exists"
elif "$PY_SETUP" -m pip install --target "$ROOT_DIR/eval/pylibs" "obonet==1.2.0"; then
  "$PY_SETUP" -c "import sys; sys.path.insert(0, '$ROOT_DIR/eval/pylibs'); import obonet; print('obonet import OK')"
else
  echo "pip module missing? trying ensurepip, then a bare pip-3.9 fallback"
  "$PY_SETUP" -m ensurepip 2>/dev/null || true
  "$PY_SETUP" -m pip install --target "$ROOT_DIR/eval/pylibs" "obonet==1.2.0" \
    || pip3.9 install --target "$ROOT_DIR/eval/pylibs" "obonet==1.2.0"
fi

# --- 7. DeepGO-SE model data (bio2vec.net mirror; the old KAUST link rotted) -
step "7. deepgo2 data"
if compgen -G "$ROOT_DIR/eval/deepgo2/data/mf/deepgozero_esm_plus_*.th" >/dev/null; then
  echo "OK: deepgo2/data (model weights present)"
else
  need_cmd wget; need_cmd tar
  DG_URL="${DG_DATA_URL:-https://bio2vec.net/data/deepgo/deepgo2/data.tar.gz}"
  echo "downloading $DG_URL (several GB; wget -c resumes if interrupted)"
  mkdir -p "$ROOT_DIR/eval/deepgo2"
  wget -c -O "$ROOT_DIR/eval/deepgo2/data.tar.gz.part" "$DG_URL" &&
    mv "$ROOT_DIR/eval/deepgo2/data.tar.gz.part" "$ROOT_DIR/eval/deepgo2/data.tar.gz"
  tar -xzf "$ROOT_DIR/eval/deepgo2/data.tar.gz" -C "$ROOT_DIR/eval/deepgo2/"
  # normalize a nested data/data layout, if the archive has one
  if [[ ! -d "$ROOT_DIR/eval/deepgo2/data" && -d "$ROOT_DIR/eval/deepgo2/data/data" ]]; then
    mv "$ROOT_DIR/eval/deepgo2/data" "$ROOT_DIR/eval/deepgo2/data.tmp"
    mv "$ROOT_DIR/eval/deepgo2/data.tmp/data" "$ROOT_DIR/eval/deepgo2/data"
    rmdir "$ROOT_DIR/eval/deepgo2/data.tmp" 2>/dev/null || true
  fi
  if compgen -G "$ROOT_DIR/eval/deepgo2/data/mf/deepgozero_esm_plus_*.th" >/dev/null; then
    echo "weights OK — archive can be deleted: rm $ROOT_DIR/eval/deepgo2/data.tar.gz"
  else
    echo "WARN: weights not found after extraction — inspect $ROOT_DIR/eval/deepgo2/"
  fi
fi

# --- 8. asset that must come from the local workstation ----------------------
step "8. cfpgen_eval (checked, not downloadable)"
missing=0
if [[ -f "$ROOT_DIR/eval/cfpgen_eval/test.pkl" ]]; then echo "OK: cfpgen_eval (test.pkl + mappings)"
else
  missing=1
  cat <<'EOS'
TODO — from the LOCAL workstation (14 MB):
  ssh <user>@karolina.it4i.cz 'mkdir -p /scratch/project/<ACCOUNT>/dplm-repo/eval/cfpgen_eval'
  rsync -avP \
    /home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/test.pkl \
    /home/cherry/dev/phd/cfpgen/go_mapping.pkl \
    /home/cherry/dev/phd/cfpgen/ipr_mapping.pkl \
    <user>@karolina.it4i.cz:/scratch/project/<ACCOUNT>/dplm-repo/eval/cfpgen_eval/
EOS
fi
[[ $missing -eq 0 ]] && echo -e "\n[setup] COMPLETE — ready to sbatch training + eval" \
                    || echo -e "\n[setup] Done except the rsync TODO above."
