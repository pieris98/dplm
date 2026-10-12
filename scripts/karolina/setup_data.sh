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
step "3. ESM-2 3B cache"
mkdir -p "$ROOT_DIR/eval/esm_torch_hub"
for f in esm2_t36_3B_UR50D.pt esm2_t36_3B_UR50D-contact-regression.pt; do
  if [[ -s "$ROOT_DIR/eval/esm_torch_hub/$f" ]]; then
    echo "exists: $f"
  else
    wget -c -O "$ROOT_DIR/eval/esm_torch_hub/$f.part" \
      "https://dl.fbaipublicfiles.com/fair-esm/models/$f" &&
      mv "$ROOT_DIR/eval/esm_torch_hub/$f.part" "$ROOT_DIR/eval/esm_torch_hub/$f"
  fi
done

# --- 4. InterProScan 5.78-109.0 (GitHub tarball; EBI FTP is blocked) ---------
step "4. InterProScan 5.78-109.0"
if [[ -x "$ROOT_DIR/eval/interproscan-5.78-109.0/interproscan.sh" ]]; then
  echo "exists — skipping"
else
  IPS_URL="${IPS_URL:-https://github.com/ebi-pf-team/interproscan/releases/download/5.78-109.0/interproscan-5.78-109.0.tar.gz}"
  need_cmd wget; need_cmd tar
  echo "downloading ~6.6 GB from GitHub, then extracting (~35 GB)"
  wget -c -O /tmp/ips578.tar.gz "$IPS_URL"
  tar -xzf /tmp/ips578.tar.gz -C "$ROOT_DIR/eval/"
  rm -f /tmp/ips578.tar.gz
fi
# small validate run (bundled test proteins) — takes a few minutes
if [[ ! -f "$ROOT_DIR/eval/.ips_validated" ]]; then
  echo "validating IPS on its bundled test FASTA (few minutes; needs java — ensure_java)"
  ( source "$ROOT_DIR/scripts/meluxina/common.sh" && ensure_java ) || true
  ( cd "$ROOT_DIR/eval/interproscan-5.78-109.0" && \
    ./interproscan.sh -i core/test_all_proteins/test_all_proteins.fasta \
      -f TSV -o /tmp/ips_validate.tsv --disable-precalc -cpu 4 >/tmp/ips_validate.log 2>&1 ) \
    && touch "$ROOT_DIR/eval/.ips_validated" \
    || echo "WARN: IPS validation failed — check /tmp/ips_validate.log (java missing is the usual cause)"
fi

# --- 5. go.obo (GO DAG for ancestor-expanded scoring) ------------------------
step "5. go.obo"
if [[ -s "$ROOT_DIR/eval/go.obo" ]]; then echo "exists"; else
  wget -q -O "$ROOT_DIR/eval/go.obo" https://purl.obolibrary.org/obo/go.obo && echo "downloaded"
fi

# --- 6. scorer pure-python deps (obonet via PYTHONPATH — keeps image intact) -
step "6. eval/pylibs (obonet)"
if python3 -c "import sys; sys.path.insert(0, '$ROOT_DIR/eval/pylibs'); import obonet" 2>/dev/null; then
  echo "exists"
else
  python3 -m pip install --target "$ROOT_DIR/eval/pylibs" "obonet==1.2.0"
fi

# --- 7. assets that must come from the local workstation ---------------------
step "7. rsync-only assets (checked, not downloaded)"
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
if compgen -G "$ROOT_DIR/eval/deepgo2/data/mf/deepgozero_esm_plus_*.th" >/dev/null; then
  echo "OK: deepgo2/data (DeepGO-SE model weights)"
else
  missing=1
  cat <<'EOS'
TODO — from the LOCAL workstation (16 GB, web-archive recovery; cannot re-download):
  rsync -avP /home/cherry/dev/phd/dplm/eval/deepgo2/data/ \
    <user>@karolina.it4i.cz:/scratch/project/<ACCOUNT>/dplm-repo/eval/deepgo2/data/
EOS
fi
[[ $missing -eq 0 ]] && echo -e "\n[setup] COMPLETE — ready to sbatch training + eval" \
                    || echo -e "\n[setup] Done except rsync TODOs above."
