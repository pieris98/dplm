#!/usr/bin/env bash
# Bootstrap the internet-downloadable eval assets into the repo's eval/ tree.
#
# This is the "bottom of the Dockerfile": everything here can be fetched from
# the public internet on any host's login node, so it is intentionally NOT
# baked into the docker image (keeps the image ~57 GB instead of ~130 GB).
# Run this ONCE per new host, on a login node, before the first eval:
#
#     bash scripts/eval/download_eval_assets.sh
#
# Downloads (all resumable `wget -c`, verified where an md5 is published):
#   eval/esm_torch_hub/esm2_t36_3B_UR50D.pt                    (~5.2 GB, fair-esm)
#   eval/esm_torch_hub/esm2_t36_3B_UR50D-contact-regression.pt (~2.2 MB, fair-esm)
#       → ESM-2 3B checkpoints for DeepGO-SE (exact filenames its predict.py
#         expects under the torch-hub checkpoint dir)
#   eval/deepgo2/data.tar.gz → eval/deepgo2/data/               (~16 GB, bio2vec)
#       → DeepGO-SE training DBs for the -dr data flag
#   eval/interproscan-5.78-109.0/                               (6.6 GB → 35 GB, EBI)
#       → InterProScan 5.78-109.0 (md5-verified)
#   eval/go.obo (36 MB, purl.obolibrary.org)                    — fallback; new
#       image tags already bake this file
#
# --stage-cfpgen additionally copies the CFP-Gen held-out eval split + label
# mappings from the local cfpgen checkout into eval/cfpgen_eval/ — the one
# asset that has no public download URL (it is baked into new image tags; this
# flag exists so a host running an OLD image tag can still be completed).
#
# Disk: ~60 GB of downloads + ~86 GB extracted. Time: network-bound.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

EV="$ROOT/eval"
mkdir -p "$EV/esm_torch_hub" "$EV/deepgo2" "$EV/cfpgen_eval"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "FATAL: '$1' not found"; exit 1; }; }
need_cmd wget; need_cmd tar; need_cmd md5sum

STAGE_CFPGEN=0
SKIP_EXISTING=1
for arg in "$@"; do
  case "$arg" in
    --stage-cfpgen) STAGE_CFPGEN=1 ;;
    --no-skip)      SKIP_EXISTING=0 ;;
    *) echo "unknown arg: $arg"; exit 2 ;;
  esac
done

have() { [[ -s "$1" ]]; }
mark_done() { [[ "${SKIP_EXISTING:-1}" == "1" && -s "$1" ]] && { echo "[skip] $1 (exists)"; return 0; }; return 1; }

# ---------------------------------------------------------------------------
# 1. ESM-2 3B checkpoints (DeepGO-SE torch-hub expectations; fair-esm files)
# ---------------------------------------------------------------------------
ESM_BASE="https://dl.fbaipublicfiles.com/fair-esm/models"
for f in esm2_t36_3B_UR50D.pt esm2_t36_3B_UR50D-contact-regression.pt; do
  dst="$EV/esm_torch_hub/$f"
  mark_done "$dst" && continue
  echo "[get] $f"
  wget -c -q --show-progress -O "$dst.part" "$ESM_BASE/$f"
  mv "$dst.part" "$dst"
done

# ---------------------------------------------------------------------------
# 2. DeepGO-SE data (bio2vec live mirror; 2026-10 verified reachable)
# ---------------------------------------------------------------------------
DG_TGZ="$EV/deepgo2/data.tar.gz"
if ! mark_done "$DG_TGZ"; then
  echo "[get] DeepGO-SE data (~16 GB)"
  wget -c -q --show-progress -O "$DG_TGZ.part" \
    "https://bio2vec.net/data/deepgo/deepgo2/data.tar.gz"
  mv "$DG_TGZ.part" "$DG_TGZ"
fi
if [[ ! -d "$EV/deepgo2/data" ]]; then
  echo "[extract] eval/deepgo2/data/"
  mkdir -p "$EV/deepgo2"
  tar -xzf "$DG_TGZ" -C "$EV/deepgo2"
fi
[[ -d "$EV/deepgo2/data" ]] || { echo "FATAL: eval/deepgo2/data missing after extract — inspect the tarball layout"; exit 1; }

# ---------------------------------------------------------------------------
# 3. InterProScan 5.78-109.0 (EBI HTTPS; md5 from the release notes)
# ---------------------------------------------------------------------------
IPS_TGZ="/tmp/ips578.tar.gz"
IPS_MD5="3bb9a0794e9d69a0418a5298cdb04445"
if ! mark_done "$EV/interproscan-5.78-109.0/interproscan.sh"; then
  echo "[get] InterProScan 5.78-109.0 (6.6 GB from EBI)"
  wget -c -O "$IPS_TGZ" \
    "https://ftp.ebi.ac.uk/pub/software/unix/iprscan/5/5.78-109.0/interproscan-5.78-109.0-64-bit.tar.gz"
  echo "$IPS_MD5  $IPS_TGZ" | md5sum -c -
  tar -xzf "$IPS_TGZ" -C "$EV"
fi
[[ -x "$EV/interproscan-5.78-109.0/interproscan.sh" ]] || {
  echo "FATAL: IPS install incomplete"; exit 1; }

# ---------------------------------------------------------------------------
# 4. go.obo fallback (new image tags bake it — this only fills OLD checkouts)
# ---------------------------------------------------------------------------
if ! have "$EV/go.obo"; then
  wget -c -q --show-progress -O "$EV/go.obo" "https://purl.obolibrary.org/obo/go.obo"
fi

# ---------------------------------------------------------------------------
# 5. cfpgen_eval staging (local-only asset — no public URL)
# ---------------------------------------------------------------------------
if [[ "$STAGE_CFPGEN" == "1" ]]; then
  SRC=/home/cherry/dev/phd/cfpgen
  for f in test.pkl go_mapping.pkl ipr_mapping.pkl; do
    cp -n "$SRC/data-bin/uniprotKB/cfpgen_general_dataset/$f" "$EV/cfpgen_eval/" 2>/dev/null \
      || cp -n "$SRC/$f" "$EV/cfpgen_eval/" 2>/dev/null || true
  done
  ls -la "$EV/cfpgen_eval/"
fi

echo "DONE — eval assets under $EV"
