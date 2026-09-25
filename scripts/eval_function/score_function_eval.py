"""Score the function-recovery evaluation set produced by generate_eval_set.py.

Computes, per arm:
  * MRR   — generated label-groups ranked against real reference groups
  * MMD / MMD-Gauss — sequence-distribution distance to the real arm
  * IPR set-match (micro/macro F1 etc.)  — requires InterProScan TSVs
  * GO set-match + CAFA Fmax             — requires DeepGO-SE TSVs (+ go.obo
                                            for ancestor-expanded ground truth)

Inputs: the eval-run directory from generate_eval_set.py (manifest.json +
per-arm aatype.fasta), plus optional predictor outputs placed as
<evaldir>/<arm>/ips.tsv and <evaldir>/<arm>/deepgose.tsv.

Outputs: <evaldir>/results.json + results.md
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path
import warnings

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from byprot.eval.function import (  # noqa: E402
    fmax,
    mmd,
    mrr,
    propagate_ancestors,
    set_match_metrics,
    spectrum_map,
)
from byprot.eval.predictors import (  # noqa: E402
    parse_deepgose_tsv,
    parse_interproscan_tsv,
)


def read_fasta(path):
    seqs, name = {}, None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(">"):
                name = line[1:].split()[0]
                seqs[name] = ""
            elif name is not None and line:
                seqs[name] += line
    return seqs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evaldir", required=True)
    ap.add_argument("--arms", default="ours_cond,ours_null,vanilla")
    ap.add_argument("--ips-tsv", action="append", default=[],
                    help="arm=path.tsv (repeatable) — InterProScan outputs")
    ap.add_argument("--deepgose-tsv", action="append", default=[],
                    help="arm=path.tsv (repeatable) — DeepGO-SE outputs")
    ap.add_argument("--obo", default=None, help="go.obo for ancestor expansion")
    ap.add_argument("--mrr-label-type", default="go", choices=["go", "ipr"])
    args = ap.parse_args()

    # Sparse per-label matrices make sklearn emit these per column; the
    # averaged metrics are still computed. Silence the flood.
    warnings.filterwarnings("ignore", message="No positive class found")
    warnings.filterwarnings("ignore", message="Only one class is present")

    evdir = Path(args.evaldir)
    manifest = json.load(open(evdir / "manifest.json"))
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    real = read_fasta(evdir / "real" / "aatype.fasta")
    real_ids = list(real)
    real_seqs = [real[i] for i in real_ids]
    real_recs = {r["seq_id"]: r for r in manifest["arms"].get("real", [])}
    labels_key = "prompt_go" if args.mrr_label_type == "go" else "prompt_ipr"
    real_labels = [real_recs[i][labels_key] for i in real_ids]

    results = {}
    for arm in arms:
        d = evdir / arm
        fasta = d / "aatype.fasta"
        if not fasta.exists():
            print(f"skip {arm}: no aatype.fasta")
            continue
        seqs = read_fasta(fasta)
        ids = list(seqs)
        recs = {r["seq_id"]: r for r in manifest["arms"].get(arm, [])}
        gen_labels = [recs[i][labels_key] if i in recs else [] for i in ids]
        print(f"[{arm}] {len(ids)} sequences")

        r = {"n_seqs": len(ids)}
        # Distribution metrics (predictor-free).
        r["mmd_linear_vs_real"] = mmd(seq1=list(seqs.values()), seq2=real_seqs,
                                      kernel="linear")
        r["mmd_gaussian_vs_real"] = mmd(seq1=list(seqs.values()), seq2=real_seqs,
                                        kernel="gaussian")
        mrr_val, ranks = mrr(list(seqs.values()), gen_labels, real_seqs, real_labels)
        r["mrr"] = mrr_val
        r["mrr_ranks"] = {str(k): v for k, v in ranks.items()}

        # Inverse label maps: int id → ontology accession (IPR000276 / GO:...).
        ipr_inv = {v: k for k, v in manifest["label_map"]["ipr"].items()}
        go_inv = {v: k for k, v in manifest["label_map"]["go"].items()}

        # IPR set-match (needs InterProScan TSV).
        ips = dict(kv.split("=", 1) for kv in args.ips_tsv)
        if arm in ips and os.path.exists(ips[arm]):
            parsed = parse_interproscan_tsv(ips[arm])
            pred_sets = [parsed.get(sid, {}).get("ipr", set()) for sid in ids]
            gt_sets = [
                {ipr_inv[int(t)] for t in recs[sid]["prompt_ipr"] if int(t) in ipr_inv}
                if sid in recs else set()
                for sid in ids
            ]
            r.update({f"ipr_{k}": v for k, v in set_match_metrics(pred_sets, gt_sets).items()})
        elif arm in ips:
            print(f"warn: [{arm}] ips.tsv missing ({ips[arm]}) — IPR metrics skipped")

        # GO set-match / Fmax (needs DeepGO-SE TSV).
        dgo = dict(kv.split("=", 1) for kv in args.deepgose_tsv)
        if arm in dgo and os.path.exists(dgo[arm]):
            parsed = parse_deepgose_tsv(dgo[arm])
            gt_sets = [
                {go_inv[int(t)] for t in recs[sid]["prompt_go"] if int(t) in go_inv}
                if sid in recs else set()
                for sid in ids
            ]
            if args.obo and os.path.exists(args.obo):
                import obonet
                g = obonet.read_obo(args.obo)
                parents = {n: set(d.get("is_a", [])) for n, d in g.nodes(data=True)}
                gt_sets = propagate_ancestors(gt_sets, parents)

            # Set-match at a fixed threshold (CFP-Gen-style).
            pred_sets = [
                {t for t, sc in parsed.get(sid, {}).items() if sc >= 0.5}
                for sid in ids
            ]
            r.update({f"go_{k}": v for k, v in
                      set_match_metrics(pred_sets, gt_sets).items()})

            # CAFA protein-centric Fmax on raw per-term probabilities.
            go_inv = {v: k for k, v in manifest["label_map"]["go"].items()}
            gt_str = [set(go_inv.get(int(t), f"GO:{t:07d}") for t in s)
                      for s in gt_sets]
            terms = sorted({t for s in parsed.values() for t in s}
                           | set().union(*map(set, gt_str)))
            tidx = {t: j for j, t in enumerate(terms)}
            scores = np.zeros((len(ids), len(terms)))
            gt_bin = np.zeros((len(ids), len(terms)))
            for i, sid in enumerate(ids):
                for t, sc in parsed.get(sid, {}).items():
                    if t in tidx:
                        scores[i, tidx[t]] = sc
                for t in gt_str[i]:
                    if t in tidx:
                        gt_bin[i, tidx[t]] = 1
            r["go_fmax"] = fmax(scores, gt_bin)

        results[arm] = r

    (evdir / "results.json").write_text(json.dumps(results, indent=2))
    keys = sorted({k for r in results.values() for k in r})
    lines = ["| arm | " + " | ".join(keys) + " |",
             "|---" * (len(keys) + 1) + "|"]
    for arm, r in results.items():
        lines.append(f"| {arm} | " + " | ".join(
            f"{r.get(k, '—'):.4f}" if isinstance(r.get(k), float) else str(r.get(k, "—"))
            for k in keys) + " |")
    (evdir / "results.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
