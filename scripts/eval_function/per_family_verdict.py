"""Per-condition steering verdict table from existing fn_eval_v1 results (point 2, v0).

One row per evaluation label: the per-family MMD steering delta (linear kernel)
for each arm — cond, null, vanilla — plus the steering verdict
``delta_cond - max(delta_null, delta_vanilla)`` (positive = the conditional
arm is pulled toward its own family's real distribution beyond both controls)
and the per-label MRR rank per arm. This is the aggregate-run predecessor of
the per-family metric the 2026-10-13 meeting asked for; the anchored
experiment (steer_v1) supersedes it with paired per-sequence deltas.

  python scripts/eval_function/per_family_verdict.py \
      --eval-dir eval_runs/fn_eval_v1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ARMS = ("ours_cond", "ours_null", "vanilla")
ARM_SHORT = {"ours_cond": "cond", "ours_null": "null", "vanilla": "vanilla"}


def label_names(manifest: dict) -> dict[str, str]:
    """int label id -> 'GO:xxxx/name' or 'IPRxxxxx' via the manifest maps."""
    lm = manifest["label_map"]
    rev_go = {v: k for k, v in lm["go"].items()}
    rev_ipr = {v: k for k, v in lm["ipr"].items()}
    out: dict[str, str] = {}
    for arm_entries in manifest["arms"].values():
        for e in arm_entries:
            if "primary_label" not in e:
                continue
            kind, lid = e["primary_label"]
            key = str(lid)
            if key in out:
                continue
            acc = (rev_go if kind == "go" else rev_ipr).get(lid, f"{kind}:{lid}")
            out[key] = acc
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-dir", default="eval_runs/fn_eval_v1")
    args = ap.parse_args()

    eval_dir = Path(args.eval_dir)
    results = json.loads((eval_dir / "results.json").read_text())
    manifest = json.loads((eval_dir / "manifest.json").read_text())
    names = label_names(manifest)

    deltas = {
        a: results[a]["pfmmd_detail"]["linear"]["per_label_delta"] for a in ARMS
    }
    ranks = {a: results[a]["mrr_ranks"] for a in ARMS}
    labels = sorted(deltas["ours_cond"], key=int)

    rows = []
    for lid in labels:
        d = {a: deltas[a][lid] for a in ARMS}
        verdict = d["ours_cond"] - max(d["ours_null"], d["vanilla"])
        rows.append(
            {
                "label": lid,
                "name": names.get(lid, lid),
                "delta_cond": round(d["ours_cond"], 4),
                "delta_null": round(d["ours_null"], 4),
                "delta_vanilla": round(d["vanilla"], 4),
                "verdict": round(verdict, 4),
                "rr_cond": round(1.0 / ranks["ours_cond"][lid], 3),
                "rr_null": round(1.0 / ranks["ours_null"][lid], 3),
                "rr_vanilla": round(1.0 / ranks["vanilla"][lid], 3),
            }
        )
    rows.sort(key=lambda r: -r["verdict"])

    n_pos = sum(1 for r in rows if r["verdict"] > 0)
    n_pos_gt_rr = sum(
        1 for r in rows if r["verdict"] > 0 and r["rr_cond"] > max(r["rr_null"], r["rr_vanilla"])
    )
    out = {
        "n_labels": len(rows),
        "n_verdict_positive": n_pos,
        "n_verdict_positive_and_rr_best": n_pos_gt_rr,
        "rows": rows,
    }

    lines = [
        "# Per-condition steering verdict (fn_eval_v1, from-scratch arms)",
        "",
        f"{n_pos}/{len(rows)} labels steer (verdict > 0); "
        f"{n_pos_gt_rr}/{len(rows)} steer *and* rank their own family best.",
        "",
        "verdict = delta_cond - max(delta_null, delta_vanilla); "
        "delta = per-family MMD (linear) own-cross; rr = reciprocal rank.",
        "",
        "| label | name | d_cond | d_null | d_van | verdict | rr_c | rr_n | rr_v |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows[:20]:
        lines.append(
            f"| {r['label']} | {r['name']} | {r['delta_cond']:+.3f} "
            f"| {r['delta_null']:+.3f} | {r['delta_vanilla']:+.3f} "
            f"| **{r['verdict']:+.3f}** | {r['rr_cond']:.2f} | {r['rr_null']:.2f} "
            f"| {r['rr_vanilla']:.2f} |"
        )
    lines += ["| … | | | | | | | | |"]
    for r in rows[-5:]:
        lines.append(
            f"| {r['label']} | {r['name']} | {r['delta_cond']:+.3f} "
            f"| {r['delta_null']:+.3f} | {r['delta_vanilla']:+.3f} "
            f"| **{r['verdict']:+.3f}** | {r['rr_cond']:.2f} | {r['rr_null']:.2f} "
            f"| {r['rr_vanilla']:.2f} |"
        )

    out_dir = eval_dir / "verdict"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "verdict.json").write_text(json.dumps(out, indent=2))
    (out_dir / "verdict.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:14]))
    print(f"...\nwrote {out_dir}/verdict.{{json,md}}")


if __name__ == "__main__":
    main()
