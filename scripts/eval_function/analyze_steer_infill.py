"""First-pass analysis of anchored-infill cells (CPU-only metrics).

For every generated cell (fam/stratum/arm/p/seed) compute, against the
anchor FASTAs:
  - realized edit rate: fraction of anchor positions actually changed
    (p is the budget; arms may under/overshoot it — that is signal)
  - per-AA composition + log2 odds vs the real reference arm (fn_eval_v1)
  - JS divergence of composition vs real, and the A-fraction poly-A detector

Paired coverage (IPS) and embedding steering projections are separate
post-generation steps; this script covers everything that needs no GPU and
no predictor.

  python scripts/eval_function/analyze_steer_infill.py \
      --gen eval_runs/steer_v1/gen --anchors eval_runs/steer_v1/anchors
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

AA = "ACDEFGHIKLMNPQRSTVWY"


def read_fasta(path: Path) -> list[tuple[str, str]]:
    out, cur_id, cur = [], None, []
    with open(path) as f:
        for line in f:
            if line.startswith(">"):
                if cur_id is not None:
                    out.append((cur_id, "".join(cur)))
                cur_id, cur = line[1:].split()[0], []
            else:
                cur.append(line.strip())
    if cur_id is not None:
        out.append((cur_id, "".join(cur)))
    return out


def composition(seqs: list[str]) -> dict[str, float]:
    c = {a: 0 for a in AA}
    total = 0
    for s in seqs:
        for ch in s:
            if ch in c:
                c[ch] += 1
                total += 1
    return {a: (c[a] / total if total else 0.0) for a in AA}


def js_bits(p: dict[str, float], q: dict[str, float]) -> float:
    keys = AA
    mid = {k: 0.5 * (p[k] + q[k]) for k in keys}

    def kl(x, y):
        return sum(x[k] * math.log2(x[k] / y[k]) for k in keys if x[k] > 0)

    return 0.5 * kl(p, mid) + 0.5 * kl(q, mid)


def realized_edit_rate(edited: list[tuple[str, str]], anchors: dict[str, str]) -> float:
    fracs = []
    for uid, s in edited:
        a = anchors.get(uid)
        if not a or not s:
            continue
        n = min(len(a), len(s))
        diff = sum(1 for x, y in zip(a[:n], s[:n]) if x != y) + abs(len(a) - len(s))
        fracs.append(diff / len(a))
    return sum(fracs) / len(fracs) if fracs else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gen", default="eval_runs/steer_v1/gen")
    ap.add_argument("--anchors", default="eval_runs/steer_v1/anchors")
    ap.add_argument(
        "--real-fasta", default="eval_runs/fn_eval_v1/real/aatype.fasta",
        help="reference composition (real held-out arm)",
    )
    args = ap.parse_args()

    real_comp = composition([s for _, s in read_fasta(Path(args.real_fasta))])

    cells = []
    for cell_fasta in sorted(Path(args.gen).glob("*/*/*/p*_s*.fasta")):
        meta_path = cell_fasta.with_suffix(".json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text())
        fam, stratum, arm, p, seed = (
            meta["family"], meta["stratum"], meta["arm"], meta["p"], meta["seed"],
        )
        anchors_fasta = (
            Path(args.anchors) / fam
            / ("nonmembers.fasta" if stratum == "none" else f"{stratum}.fasta")
        )
        anchors = dict(read_fasta(anchors_fasta))
        edited = read_fasta(cell_fasta)
        comp = composition([s for _, s in edited])
        cells.append(
            {
                "family": fam, "stratum": stratum, "arm": arm, "p": p, "seed": seed,
                "n": len(edited),
                "realized_edit": round(realized_edit_rate(edited, anchors), 4),
                "a_fraction": round(comp["A"], 4),
                "js_vs_real": round(js_bits(comp, real_comp), 4),
                "freq": {a: round(comp[a], 4) for a in AA},
            }
        )

    out_dir = Path(args.gen) / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cells.json").write_text(json.dumps(cells, indent=2))

    lines = [
        "# Anchored-infill first-pass metrics (CPU-only)",
        "",
        "realized edit = fraction of anchor positions actually changed "
        "(budget p); a_fraction = poly-A detector; js = composition vs real.",
        "",
        "| family | stratum | arm | p | n | realized edit | A-frac | JS vs real |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(cells, key=lambda c: (c["family"], c["stratum"], c["arm"], c["p"])):
        lines.append(
            f"| {c['family']} | {c['stratum']} | {c['arm']} | {c['p']:g} "
            f"| {c['n']} | {c['realized_edit']:.3f} | {c['a_fraction']:.3f} "
            f"| {c['js_vs_real']:.4f} |"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {out_dir}/{{cells.json,summary.md}}")


if __name__ == "__main__":
    main()
