"""Property figures for the real-data strata (meeting point 1: "informative plots").

Three figures over the target families' members, from the CFP-Gen test corpus:
  1. coverage histograms with within-family tercile cuts (the strata definition)
  2. coverage vs length scatter colored by stratum (checks the length confound)
  3. per-AA composition of each stratum as log2 odds vs the full-corpus background

Palette: documented reference slots — sequential blue ramp for the ordinal
strata, diverging blue/orange with neutral midpoint for composition odds.

  python scripts/eval_function/plot_strata_properties.py \
      --families IPR036291,IPR012340,IPR002052 \
      --out eval_runs/real_data_census/figs
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_strata import DEFAULT_TEST_PKL, family_members, tercile_strata  # noqa: E402

AA = "ACDEFGHIKLMNPQRSTVWY"
FAMILIES = ["IPR036291", "IPR012340", "IPR002052"]

# reference palette (light mode)
BLUE = "#2a78d6"
ORANGE = "#eb6834"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e4e3e0"
STRATUM_BLUES = {"low": "#b9d4f0", "mid": "#6ea3e0", "high": "#1f5fae"}
DIVERGING = LinearSegmentedColormap.from_list(
    "div_blue_orange", [BLUE, "#f7f6f3", ORANGE]
)

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "axes.edgecolor": INK2,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "font.size": 9,
    }
)


def corpus_background(test: list[dict]) -> dict[str, float]:
    counts = {a: 0 for a in AA}
    total = 0
    for p in test:
        for ch in p["sequence"]:
            if ch in counts:
                counts[ch] += 1
                total += 1
    return {a: counts[a] / total for a in AA}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test-pkl", default=DEFAULT_TEST_PKL)
    ap.add_argument(
        "--families", default=",".join(FAMILIES)
    )
    ap.add_argument("--out", default="eval_runs/real_data_census/figs")
    args = ap.parse_args()

    with open(args.test_pkl, "rb") as f:
        test = pickle.load(f)
    fams = args.families.split(",")
    bg = corpus_background(test)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    fam_members: dict[str, list[dict]] = {}
    fam_cuts: dict[str, tuple[float, float]] = {}
    for fam in fams:
        members, cuts = tercile_strata(family_members(test, fam))
        fam_members[fam] = members
        fam_cuts[fam] = cuts

    # ---- fig 1: coverage histograms, bars colored by stratum ----------------
    fig, axes = plt.subplots(1, len(fams), figsize=(3.2 * len(fams), 2.7))
    for ax, fam in zip(np.atleast_1d(axes), fams):
        covs = [m["coverage"] for m in fam_members[fam]]
        q33, q66 = fam_cuts[fam]
        counts, edges = np.histogram(covs, bins=24)
        for c, e0, e1 in zip(counts, edges[:-1], edges[1:]):
            mid = 0.5 * (e0 + e1)
            color = (
                STRATUM_BLUES["low"] if mid <= q33
                else STRATUM_BLUES["mid"] if mid <= q66
                else STRATUM_BLUES["high"]
            )
            ax.bar(mid, c, width=e1 - e0, color=color, linewidth=0)
        ax.axvline(q33, color=INK2, linewidth=1.0, linestyle="--")
        ax.axvline(q66, color=INK2, linewidth=1.0, linestyle="--")
        ax.set_title(fam, fontsize=9)
        ax.set_xlabel("domain coverage")
    axes[0].set_ylabel("members")
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=STRATUM_BLUES[s], linewidth=0)
        for s in ("low", "mid", "high")
    ]
    axes[-1].legend(
        handles, ["low", "mid", "high"], frameon=False, fontsize=8,
        loc="upper left", bbox_to_anchor=(1.02, 1.0),
    )
    fig.suptitle(
        "Real-member graded membership (CFP-Gen test corpus)", y=1.02, fontsize=10
    )
    fig.tight_layout()
    fig.savefig(out_dir / "fig1_coverage_histograms.png", dpi=200, bbox_inches="tight")
    fig.savefig(out_dir / "fig1_coverage_histograms.pdf", bbox_inches="tight")
    plt.close(fig)

    # ---- fig 2: coverage vs length, colored by stratum ----------------------
    fig, axes = plt.subplots(1, len(fams), figsize=(3.2 * len(fams), 2.7))
    for ax, fam in zip(np.atleast_1d(axes), fams):
        for stratum in ("low", "mid", "high"):
            pts = [m for m in fam_members[fam] if m["stratum"] == stratum]
            ax.scatter(
                [m["length"] for m in pts],
                [m["coverage"] for m in pts],
                s=16, linewidths=0, color=STRATUM_BLUES[stratum],
                label=stratum, alpha=0.85,
            )
        ax.set_title(fam, fontsize=9)
        ax.set_xlabel("sequence length (aa)")
    axes[0].set_ylabel("domain coverage")
    axes[0].legend(frameon=False, loc="upper right", fontsize=8)
    fig.suptitle(
        "Coverage vs length by stratum (length confound check)", y=1.02, fontsize=10
    )
    fig.tight_layout()
    fig.savefig(out_dir / "fig2_coverage_vs_length.png", dpi=200, bbox_inches="tight")
    fig.savefig(out_dir / "fig2_coverage_vs_length.pdf", bbox_inches="tight")
    plt.close(fig)

    # ---- fig 3: per-AA log2 odds per stratum vs corpus background -----------
    fig, axes = plt.subplots(
        len(fams), 1, figsize=(6.8, 1.3 * len(fams) + 0.6), layout="constrained"
    )
    vmax = 0.0
    odds_by_fam: dict[str, dict[str, list[float]]] = {}
    for fam in fams:
        odds_by_fam[fam] = {}
        for stratum in ("low", "mid", "high"):
            pts = [m for m in fam_members[fam] if m["stratum"] == stratum]
            counts = {a: 0 for a in AA}
            total = 0
            for m in pts:
                for ch in m["sequence"]:
                    if ch in counts:
                        counts[ch] += 1
                        total += 1
            odds = [
                np.log2((counts[a] + 0.5) / (total + 10) / bg[a]) for a in AA
            ]
            odds_by_fam[fam][stratum] = odds
            vmax = max(vmax, float(np.abs(odds).max()))
    vmax = float(np.ceil(vmax * 2) / 2)
    for ax, fam in zip(np.atleast_1d(axes), fams):
        mat = np.array([odds_by_fam[fam][s] for s in ("low", "mid", "high")])
        im = ax.imshow(
            mat, cmap=DIVERGING, vmin=-vmax, vmax=vmax, aspect="auto"
        )
        ax.set_xticks(range(len(AA)), list(AA), fontsize=8)
        ax.set_yticks(range(3), ["low", "mid", "high"], fontsize=8)
        ax.set_title(fam, fontsize=9, loc="left")
        ax.grid(False)
    cb = fig.colorbar(im, ax=list(np.atleast_1d(axes)), shrink=0.9, pad=0.02)
    cb.set_label("log2 odds vs corpus background", fontsize=8)
    fig.suptitle(
        "Per-AA composition of real strata (order-blind view)", fontsize=10
    )
    fig.savefig(out_dir / "fig3_aa_composition_strata.png", dpi=200, bbox_inches="tight")
    fig.savefig(out_dir / "fig3_aa_composition_strata.pdf", bbox_inches="tight")
    plt.close(fig)

    meta = {
        "background_total_residues": int(sum(bg.values()) * len(test) and sum(
            1 for p in test for ch in p["sequence"] if ch in AA
        )),
        "vmax": vmax,
        "cuts": {f: list(fam_cuts[f]) for f in fams},
    }
    (out_dir / "figs_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote 3 figures to {out_dir} (vmax={vmax})")


if __name__ == "__main__":
    main()
