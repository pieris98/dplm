"""UMAP visualization of the function-recovery eval arms (feedback 5).

Projects mean-pooled ESM-2 3B embeddings (the `aatype_esm.pkl` files the
DeepGO-SE runs leave next to each FASTA) of all eval arms into a single
shared 2-D space — one UMAP fit on the concatenation, so positions are
comparable across arms.

Panels:
  A. all sequences, colored by arm
  B. arm centroids (+ lines to the real centroid)
  C. per-label highlight: `ours_cond` CFG-sweep + real points prompted with
     the top per-family-MMD labels, one subplot per label

Also prints/saves quantitative separation stats computed in the RAW
normalized embedding space (UMAP is for looking, not for measuring):
centroid-to-real-centroid distance, mean point-to-real distance,
intra-arm compactness.

Usage:
  python scripts/eval_function/plot_umap.py --evaldir eval_runs/fn_eval_v1 \
      [--arms ours_cond,ours_cond_cfg2,ours_cond_cfg4,ours_cond_cfg8,ours_null,vanilla,real]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

ARM_COLORS = {
    "ours_cond": "#1f77b4",
    "ours_cond_cfg2": "#ff7f0e",
    "ours_cond_cfg4": "#9467bd",
    "ours_cond_cfg8": "#17becf",
    "ours_null": "#d62728",
    "vanilla": "#2ca02c",
    "real": "#000000",
}
ARM_MARKERS = {
    "ours_cond": "o", "ours_cond_cfg2": "o", "ours_cond_cfg4": "o",
    "ours_cond_cfg8": "o", "ours_null": "s", "vanilla": "^", "real": "*",
}
ARM_LABELS = {
    "ours_cond": "ours w=1", "ours_cond_cfg2": "ours w=2",
    "ours_cond_cfg4": "ours w=4", "ours_cond_cfg8": "ours w=8",
    "ours_null": "ours null", "vanilla": "vanilla", "real": "real",
}


def load_arm(evaldir: Path, arm: str):
    """FASTA-ordered embeddings for one arm (pkl may carry stale extra ids)."""
    seqs, name = {}, None
    with open(evaldir / arm / "aatype.fasta") as f:
        for line in f:
            line = line.strip()
            if line.startswith(">"):
                name = line[1:].split()[0]
                seqs[name] = True
            # order of first appearance is the FASTA order
    obj = torch.load(evaldir / arm / "aatype_esm.pkl", map_location="cpu",
                     weights_only=False)
    emb_by_id = {p: obj["data"][i].numpy() for i, p in enumerate(obj["proteins"])}
    ids = [i for i in seqs if i in emb_by_id]
    missing = [i for i in seqs if i not in emb_by_id]
    if missing:
        print(f"[{arm}] warn: {len(missing)} fasta ids missing from esm pkl")
    return ids, np.asarray([emb_by_id[i] for i in ids], dtype=np.float32)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evaldir", required=True)
    ap.add_argument("--arms", default="ours_cond,ours_cond_cfg2,ours_cond_cfg4,"
                    "ours_cond_cfg8,ours_null,vanilla,real")
    ap.add_argument("--n-neighbors", type=int, default=30)
    ap.add_argument("--min-dist", type=float, default=0.1)
    ap.add_argument("--n-label-panels", type=int, default=6,
                    help="top per-family-MMD labels highlighted in panel C")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    import umap

    evdir = Path(args.evaldir)
    outdir = evdir / "figs"
    outdir.mkdir(parents=True, exist_ok=True)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    # --- load + L2-normalize (removes length/magnitude effects; all content) -
    ids_by_arm, embs_by_arm = {}, {}
    for arm in arms:
        ids, emb = load_arm(evdir, arm)
        emb = emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-8, None)
        ids_by_arm[arm], embs_by_arm[arm] = ids, emb
        print(f"[{arm}] {emb.shape[0]} embeddings")

    all_emb = np.vstack([embs_by_arm[a] for a in arms])
    offsets, o = {}, 0
    for a in arms:
        offsets[a] = o
        o += len(embs_by_arm[a])

    print(f"fitting UMAP on {all_emb.shape} ...")
    reducer = umap.UMAP(n_neighbors=args.n_neighbors, min_dist=args.min_dist,
                        metric="euclidean", random_state=args.seed,
                        n_components=2)
    xy = reducer.fit_transform(all_emb)
    xy_by_arm = {a: xy[offsets[a]:offsets[a] + len(embs_by_arm[a])] for a in arms}

    # --- quantitative separation in the RAW space (not UMAP) ---------------
    real_c = embs_by_arm["real"].mean(axis=0)
    summary = {}
    for a in arms:
        c = embs_by_arm[a].mean(axis=0)
        intra = np.linalg.norm(embs_by_arm[a] - c, axis=1).mean()
        summary[a] = {
            "centroid_to_real": float(np.linalg.norm(c - real_c)),
            "mean_point_to_real_centroid":
                float(np.linalg.norm(embs_by_arm[a] - real_c, axis=1).mean()),
            "intra_arm_spread": float(intra),
        }
        print(f"{a:16s} |centroid-real|={summary[a]['centroid_to_real']:.4f}  "
              f"mean pt→real={summary[a]['mean_point_to_real_centroid']:.4f}  "
              f"intra-spread={intra:.4f}")
    (evdir / "umap_summary.json").write_text(json.dumps(summary, indent=2))

    # --- panel A: by arm -----------------------------------------------------
    fig, ax = plt.subplots(figsize=(9, 7.5))
    for a in arms:
        ax.scatter(*xy_by_arm[a].T, s=14 if a != "real" else 60,
                   c=ARM_COLORS.get(a, "gray"), marker=ARM_MARKERS.get(a, "o"),
                   label=ARM_LABELS.get(a, a), alpha=0.75,
                   edgecolors="none" if a != "real" else "k", linewidths=0.4)
    ax.set_title("Function-eval arms in shared ESM-2 embedding space (UMAP)")
    ax.legend(markerscale=1.6, frameon=True, loc="upper left",
              bbox_to_anchor=(0, 1), fontsize=9)
    ax.set_xticks([]), ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(outdir / "umap_by_arm.png", dpi=180)
    plt.close(fig)

    # --- panel B: centroids (labeled with RAW-space distance to real) --------
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    for a in arms:
        c = xy_by_arm[a].mean(axis=0)
        ax.scatter(*c, s=170, c=ARM_COLORS.get(a, "gray"),
                   marker=ARM_MARKERS.get(a, "o"), edgecolors="k",
                   linewidths=0.7, zorder=3)
        d_raw = summary[a]["centroid_to_real"]
        ax.annotate(f"{ARM_LABELS.get(a, a)}  (d={d_raw:.2f})", c, fontsize=8,
                    xytext=(5, 5), textcoords="offset points")
        if a != "real":
            ax.plot(*zip(c, xy_by_arm["real"].mean(axis=0)), color="gray",
                    lw=0.7, ls=":", zorder=1)
    ax.set_title("Arm centroids in UMAP space (d = raw ESM-space |centroid − real|)")
    ax.set_xticks([]), ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(outdir / "umap_centroids.png", dpi=180)
    plt.close(fig)

    # --- panel C: per-label highlight on ours_cfg arms + real ----------------
    res_file = evdir / "results.json"
    if res_file.exists():
        res = json.load(open(res_file))
        detail = res.get("ours_cond_cfg2", {}).get("pfmmd_detail", {}).get("linear", {})
        deltas = detail.get("per_label_delta", {})
        top_labels = sorted(deltas, key=lambda k: -deltas[k])[:args.n_label_panels]
        man = json.load(open(evdir / "manifest.json"))
        go_inv = {v: k for k, v in man["label_map"]["go"].items()}
        import obonet
        g = obonet.read_obo(REPO_ROOT / "eval" / "go.obo")

        fig, axes = plt.subplots(2, 3, figsize=(13.5, 8.2), sharex=True, sharey=True)
        for ax, lid in zip(axes.flat, top_labels):
            acc = go_inv.get(int(lid), f"label {lid}")
            term = g.nodes.get(acc, {})
            name = term.get("name", acc)
            sel_by_arm = {}
            for a in arms:
                recs = {r["seq_id"]: r for r in man["arms"].get(a, [])}
                sel = [i for i, sid in enumerate(ids_by_arm[a])
                       if sid in recs and str(lid) in
                       [str(t) for t in recs[sid].get("prompt_go", [])]]
                sel_by_arm[a] = sel
            for a in arms:  # context: everything else faint
                m = np.ones(len(ids_by_arm[a]), bool)
                m[sel_by_arm[a]] = False
                ax.scatter(xy_by_arm[a][m, 0], xy_by_arm[a][m, 1], s=4,
                           c="lightgray", marker="o", alpha=0.35, linewidths=0)
            for a in arms:
                sel = sel_by_arm[a]
                if not sel:
                    continue
                ax.scatter(xy_by_arm[a][sel, 0], xy_by_arm[a][sel, 1], s=26,
                           c=ARM_COLORS.get(a, "gray"), marker=ARM_MARKERS.get(a, "o"),
                           label=ARM_LABELS.get(a, a), alpha=0.9, edgecolors="k",
                           linewidths=0.25)
            ax.set_title(f"{name} (Δ={deltas[lid]:+.3f})", fontsize=9)
        handles, labels = axes.flat[0].get_legend_handles_labels()
        by_lab = dict(zip(labels, handles))
        fig.legend(by_lab.values(), by_lab.keys(), loc="lower center",
                   ncol=7, frameon=False, fontsize=8)
        fig.suptitle("Top per-family-Δ GO labels: prompted points vs all (gray)",
                     fontsize=11)
        fig.tight_layout(rect=(0, 0.05, 1, 0.97))
        fig.savefig(outdir / "umap_labels.png", dpi=180)
        plt.close(fig)
        print(f"wrote {outdir}/umap_labels.png (labels: {top_labels})")

    print(f"wrote {outdir}/umap_by_arm.png, {outdir}/umap_centroids.png")


if __name__ == "__main__":
    main()
