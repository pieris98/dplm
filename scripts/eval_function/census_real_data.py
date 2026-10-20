"""Census of the real CFP-Gen test corpus: how much data per family / GO term?

Motivation (2026-10-13 meeting feedback, point 1): before designing
property-stratified steering experiments, quantify what the real data
supports — which IPR families and GO terms have enough member sequences,
and whether members spread enough in graded membership (domain coverage)
to form low/high strata.

Per-protein graded membership for IPR families comes from CFP-Gen's
``domain_sites`` annotation (residue ranges per family), so no extra
predictor run is needed for the census:

    coverage(F, p) = (union of F's site intervals in p) / len(p)

For GO terms the corpus gives membership counts (``go_numbers``) but no
natural graded axis, so the census reports member counts + corpus
information content (specificity proxy: IC(t) = -log2(n_t / N); naive —
no ontology-aware ancestor propagation).

Outputs under --out:
  census.json   full per-family / per-term tables
  census.md     human-readable summary with feasibility verdicts

Example:
  python scripts/eval_function/census_real_data.py \
      --test-pkl /home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/test.pkl \
      --out eval_runs/real_data_census
"""
from __future__ import annotations

import argparse
import json
import math
import pickle
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median, quantiles


def _merged_coverage(sites: list[tuple[int, int]], seq_len: int) -> float:
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


def _q(values: list[float], probs=(0.1, 0.25, 0.5, 0.75, 0.9)) -> dict:
    if len(values) < 2:
        return {"p50": round(values[0], 4) if values else None}
    qs = quantiles(values, n=100, method="inclusive")
    return {f"p{int(p * 100)}": round(qs[int(p * 100) - 1], 4) for p in probs}


def census(test: list[dict], min_members: int) -> dict:
    n = len(test)
    lengths = {p["uniprot_id"]: len(p["sequence"]) for p in test}
    corpus_len_q = _q(sorted(lengths.values()))

    # ---- IPR families: membership + graded coverage from domain_sites ----
    ipr_members = defaultdict(list)          # ipr -> [(uid, coverage, length)]
    ipr_ann_no_sites = Counter()             # annotated in ipr_numbers but no site
    for p in test:
        sites_by_ipr = defaultdict(list)
        for d in p.get("domain_sites", []):
            sites_by_ipr[d["ipr_number"]].append(
                (int(d["start_position"]), int(d["end_position"]))
            )
        uid, L = p["uniprot_id"], lengths[p["uniprot_id"]]
        for ipr in p["ipr_numbers"]:
            if ipr in sites_by_ipr:
                cov = _merged_coverage(sites_by_ipr[ipr], L)
                ipr_members[ipr].append((uid, cov, L))
            else:
                ipr_ann_no_sites[ipr] += 1
                ipr_members[ipr].append((uid, None, L))  # member without positions

    ipr_table = []
    all_covs = []
    for ipr, members in ipr_members.items():
        covs = [c for _, c, _ in members if c is not None]
        mem_lens = [L for _, _, L in members]
        all_covs.extend(covs)
        n_with_pos = len(covs)
        row = {
            "ipr": ipr,
            "n_members": len(members),
            "n_with_positions": n_with_pos,
            "n_ann_without_positions": ipr_ann_no_sites.get(ipr, 0),
            "member_len_median": round(median(mem_lens), 1) if mem_lens else None,
        }
        if covs:
            row["coverage_stats"] = _q(sorted(covs))
            # strata sizes at within-family tercile cuts of member coverage
            if len(covs) >= 3:
                cuts = quantiles(covs, n=3, method="inclusive")
                row["n_cov_low_third"] = sum(1 for c in covs if c <= cuts[0])
                row["n_cov_high_third"] = sum(1 for c in covs if c >= cuts[1])
            else:
                row["n_cov_low_third"] = len(covs)
                row["n_cov_high_third"] = len(covs)
            # length-matched non-member pool (±20% of median member length)
            med = median(mem_lens)
            row["n_len_matched_nonmembers"] = sum(
                1 for u, L in lengths.items() if abs(L - med) <= 0.2 * med
            ) - len(members)
        ipr_table.append(row)
    ipr_table.sort(key=lambda r: -r["n_members"])

    # ---- GO terms: membership counts + corpus IC ----
    go_members = defaultdict(list)  # go -> [uid]  (all branches, tracked per branch)
    for p in test:
        for branch, terms in p["go_numbers"].items():
            for t in terms:
                go_members[t].append((p["uniprot_id"], branch))
    go_table = [
        {
            "go": t,
            "branches": sorted({b for _, b in ms}),
            "n_members": len(ms),
            "ic_bits": round(-math.log2(len(ms) / n), 2),
        }
        for t, ms in go_members.items()
    ]
    go_table.sort(key=lambda r: -r["n_members"])

    # ---- feasibility roll-up ----
    ipr_feasible = [r for r in ipr_table if r["n_members"] >= min_members]
    ipr_strata_ready = [
        r for r in ipr_feasible
        if "n_cov_low_third" in r
        and r["n_cov_low_third"] >= min_members / 2
        and r["n_cov_high_third"] >= min_members / 2
    ]
    go_feasible = [r for r in go_table if r["n_members"] >= min_members]

    return {
        "n_proteins": n,
        "length_quantiles": corpus_len_q,
        "coverage_all_ipr_sites": _q(sorted(all_covs)) if all_covs else {},
        "ipr": {
            "n_unique_families": len(ipr_table),
            "n_families_ge_min": len(ipr_feasible),
            "n_strata_ready": len(ipr_strata_ready),
            "strata_ready_top": ipr_strata_ready[:25],
            "table_top": ipr_table[:50],
        },
        "go": {
            "n_unique_terms": len(go_table),
            "n_terms_ge_min": len(go_feasible),
            "table_top": go_table[:50],
        },
        "min_members_threshold": min_members,
    }


def write_md(c: dict, out: Path) -> None:
    n = c["n_proteins"]
    m = c["min_members_threshold"]
    L = [f"# Real-data census — CFP-Gen test split (n={n})", ""]
    L += [f"Length quantiles: `{c['length_quantiles']}`", ""]
    L += ["All IPR domain-coverage quantiles (members only): "
          f"`{c['coverage_all_ipr_sites']}`", ""]
    ipr, go = c["ipr"], c["go"]
    L += [f"## IPR families: {ipr['n_unique_families']} unique",
          f"- families with ≥{m} members: **{ipr['n_families_ge_min']}**",
          f"- strata-ready (≥{m} members *and* ≥{m // 2} in both coverage thirds): "
          f"**{ipr['n_strata_ready']}**", ""]
    L += ["| family | members | w/ positions | cov p10/p50/p90 | cov-low-third | cov-high-third | len-matched non-members | member len med |",
          "|---|---|---|---|---|---|---|---|"]
    for r in ipr["strata_ready_top"]:
        cs = r.get("coverage_stats", {})
        L.append(
            f"| {r['ipr']} | {r['n_members']} | {r['n_with_positions']} "
            f"| {cs.get('p10', '-')}/{cs.get('p50', '-')}/{cs.get('p90', '-')} "
            f"| {r.get('n_cov_low_third', '-')} | {r.get('n_cov_high_third', '-')} "
            f"| {r.get('n_len_matched_nonmembers', '-')} | {r['member_len_median']} |")
    L += ["", f"## GO terms: {go['n_unique_terms']} unique "
          f"(naive corpus IC = -log2(n_members/n); no ancestor propagation)",
          f"- terms with ≥{m} members: **{go['n_terms_ge_min']}**", ""]
    L += ["| term | branches | members | IC (bits) |", "|---|---|---|---|"]
    for r in go["table_top"][:25]:
        L.append(f"| {r['go']} | {','.join(r['branches'])} | {r['n_members']} | {r['ic_bits']} |")
    L += ["", "## Notes",
          "- Coverage = union of `domain_sites` intervals / sequence length (CFP-Gen "
          "annotations; InterProScan-based scoring remains separate for evaluation).",
          "- Strata cuts here are within-family terciles of member coverage; "
          "absolute % thresholds should be chosen after inspecting these "
          "distributions (meeting note: 5%/25% was illustrative).",
          "- GO terms get a specificity axis (IC) instead of graded coverage."]
    out.write_text("\n".join(L))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--test-pkl",
        default="/home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/test.pkl",
    )
    ap.add_argument("--out", default="eval_runs/real_data_census")
    ap.add_argument("--min-members", type=int, default=50,
                    help="minimum member count for a family/term to count as feasible")
    args = ap.parse_args()

    with open(args.test_pkl, "rb") as f:
        test = pickle.load(f)

    c = census(test, args.min_members)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "census.json").write_text(json.dumps(c, indent=2))
    write_md(c, out_dir / "census.md")

    print(f"proteins={c['n_proteins']}  "
          f"ipr_unique={c['ipr']['n_unique_families']}  "
          f"ipr_ge_{args.min_members}={c['ipr']['n_families_ge_min']}  "
          f"ipr_strata_ready={c['ipr']['n_strata_ready']}  "
          f"go_unique={c['go']['n_unique_terms']}  "
          f"go_ge_{args.min_members}={c['go']['n_terms_ge_min']}")
    print(f"wrote {out_dir}/census.json and census.md")


if __name__ == "__main__":
    main()
