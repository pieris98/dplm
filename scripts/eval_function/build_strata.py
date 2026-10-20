"""Build property-stratified anchor sets from the CFP-Gen test corpus.

Implements the strata definitions of the anchored steering experiment
(plans/anchored_steering_experiment.md): for each target IPR family, members
are stratified by graded membership (domain coverage from ``domain_sites``)
into within-family terciles; a length-matched non-member set is sampled as
the de-novo arm's control. Outputs FASTAs (generation anchors) plus a
manifest with the prompt labels (GO-F + IPR accessions and their CFP-Gen
int ids) per anchor.

  python scripts/eval_function/build_strata.py \
      --out eval_runs/steer_v1/anchors \
      --families IPR036291,IPR012340,IPR002052
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
from collections import defaultdict
from pathlib import Path
from statistics import median, quantiles

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TEST_PKL = (
    "/home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/"
    "cfpgen_general_dataset/test.pkl"
)
DEFAULT_GO_MAP = "/home/cherry/dev/phd/cfpgen/go_mapping.pkl"
DEFAULT_IPR_MAP = "/home/cherry/dev/phd/cfpgen/ipr_mapping.pkl"


def merged_coverage(sites: list[tuple[int, int]], seq_len: int) -> float:
    """Fraction of [1, seq_len] covered by the union of 1-based inclusive intervals."""
    if not sites or seq_len == 0:
        return 0.0
    sites = sorted(sites)
    covered, cur_s, cur_e = 0, sites[0][0], sites[0][1]
    for s, e in sites[1:]:
        if s <= cur_e + 1:
            cur_e = max(cur_e, e)
        else:
            covered += cur_e - cur_s + 1
            cur_s, cur_e = s, e
    covered += cur_e - cur_s + 1
    return min(1.0, covered / seq_len)


def family_members(test: list[dict], family: str) -> list[dict]:
    """Members of ``family`` with their graded coverage (position-annotated only)."""
    out = []
    for p in test:
        if family not in p["ipr_numbers"]:
            continue
        sites = [
            (int(d["start_position"]), int(d["end_position"]))
            for d in p.get("domain_sites", [])
            if d["ipr_number"] == family
        ]
        L = len(p["sequence"])
        if sites:
            out.append({**p, "coverage": merged_coverage(sites, L), "length": L})
    return out


def tercile_strata(members: list[dict]) -> tuple[list[dict], tuple[float, float]]:
    """Assign ``stratum`` in {low, mid, high} by within-family coverage terciles."""
    covs = sorted(m["coverage"] for m in members)
    if len(covs) < 3:
        raise ValueError("need >= 3 members for terciles")
    q33, q66 = quantiles(covs, n=3, method="inclusive")
    for m in members:
        m["stratum"] = "low" if m["coverage"] <= q33 else (
            "mid" if m["coverage"] <= q66 else "high"
        )
    return members, (q33, q66)


def nonmember_set(
    test: list[dict], family: str, member_len_median: float, n: int, seed: int = 0
) -> list[dict]:
    """Length-matched (±20%) proteins not carrying ``family``."""
    rng = random.Random(seed)
    pool = [
        p
        for p in test
        if family not in p["ipr_numbers"]
        and abs(len(p["sequence"]) - member_len_median) <= 0.2 * member_len_median
    ]
    rng.shuffle(pool)
    return pool[:n]


def _int_ids(accessions: list[str], mapping: dict) -> list[int]:
    return [mapping[a] for a in accessions if a in mapping]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test-pkl", default=DEFAULT_TEST_PKL)
    ap.add_argument("--go-mapping", default=DEFAULT_GO_MAP)
    ap.add_argument("--ipr-mapping", default=DEFAULT_IPR_MAP)
    ap.add_argument("--families", default="IPR036291,IPR012340,IPR002052")
    ap.add_argument("--out", default="eval_runs/steer_v1/anchors")
    ap.add_argument("--max-per-stratum", type=int, default=100)
    ap.add_argument("--n-nonmembers", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    with open(args.test_pkl, "rb") as f:
        test = pickle.load(f)
    with open(args.go_mapping, "rb") as f:
        go_map = pickle.load(f)
    with open(args.ipr_mapping, "rb") as f:
        ipr_map = pickle.load(f)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    manifest: dict = {"args": vars(args), "families": {}}
    summary = [
        "# Anchor strata (steering experiment v1)", "",
        "| family | low cut | high cut | low n | mid n | high n | no-pos n | non-members n |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for fam in args.families.split(","):
        members = family_members(test, fam)
        n_no_pos = sum(
            1 for p in test if fam in p["ipr_numbers"]
        ) - len(members)
        members, (q33, q66) = tercile_strata(members)
        med_len = median(m["length"] for m in members)
        nonmembers = nonmember_set(test, fam, med_len, args.n_nonmembers, args.seed)

        fam_dir = out_dir / fam
        fam_dir.mkdir(parents=True, exist_ok=True)

        records = {}
        for stratum in ("low", "mid", "high"):
            subset = [m for m in members if m["stratum"] == stratum]
            rng.shuffle(subset)
            subset = subset[: args.max_per_stratum]
            with open(fam_dir / f"{stratum}.fasta", "w") as f:
                for m in subset:
                    f.write(
                        f">{m['uniprot_id']} coverage={m['coverage']:.4f} "
                        f"len={m['length']} stratum={stratum}\n{m['sequence']}\n"
                    )
            for m in subset:
                records[m["uniprot_id"]] = {
                    "stratum": stratum,
                    "coverage": round(m["coverage"], 4),
                    "length": m["length"],
                    "go_f": m["go_numbers"].get("F", []),
                    "go_f_int": _int_ids(m["go_numbers"].get("F", []), go_map),
                    "ipr": m["ipr_numbers"],
                    "ipr_int": _int_ids(m["ipr_numbers"], ipr_map),
                }
        with open(fam_dir / "nonmembers.fasta", "w") as f:
            for p in nonmembers:
                f.write(
                    f">{p['uniprot_id']} coverage=0.0 len={len(p['sequence'])} "
                    f"stratum=none\n{p['sequence']}\n"
                )
        for p in nonmembers:
            records[p["uniprot_id"]] = {
                "stratum": "none",
                "coverage": 0.0,
                "length": len(p["sequence"]),
                "go_f": p["go_numbers"].get("F", []),
                "go_f_int": _int_ids(p["go_numbers"].get("F", []), go_map),
                "ipr": p["ipr_numbers"],
                "ipr_int": _int_ids(p["ipr_numbers"], ipr_map),
            }

        counts = {
            s: sum(1 for r in records.values() if r["stratum"] == s)
            for s in ("low", "mid", "high", "none")
        }
        manifest["families"][fam] = {
            "description": next(
                d["ipr_description"]
                for p in test if fam in p["ipr_numbers"]
                for d in p.get("domain_sites", []) if d["ipr_number"] == fam
            ),
            "cuts": {"q33": round(q33, 4), "q66": round(q66, 4)},
            "member_len_median": round(med_len, 1),
            "counts": counts,
            "n_no_pos_members": n_no_pos,
            "records": records,
        }
        summary.append(
            f"| {fam} | {q33:.3f} | {q66:.3f} | {counts['low']} | {counts['mid']} "
            f"| {counts['high']} | {n_no_pos} | {counts['none']} |"
        )

    (out_dir / "anchors_manifest.json").write_text(json.dumps(manifest, indent=2))
    (out_dir / "README.md").write_text("\n".join(summary) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
