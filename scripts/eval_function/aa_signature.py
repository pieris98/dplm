"""Per-amino-acid frequency signatures for eval arms vs the real reference.

Cheap, order-blind realism readout requested in the 2026-10-13 meeting
follow-up: per-AA histograms ("signatures") for each generation arm,
compared to the real held-out sequences quantitatively via JS divergence
(bits), total variation, and per-AA log2 odds vs real (add-0.5 counts
pseudocount). Includes the composition-preserving shuffled arm as an
internal control: its signature must match the unshuffled arm's (the
metric is order-blind by construction).

  python scripts/eval_function/aa_signature.py \
      --eval-dir eval_runs/fn_eval_v1 --out eval_runs/fn_eval_v1/aa_signature
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

AA = "ACDEFGHIKLMNPQRSTVWY"
ARMS = ["real", "vanilla", "ours_null", "ours_cond",
        "ours_cond_cfg2", "ours_cond_cfg4", "ours_cond_cfg8",
        "ours_cond_shuf"]


def read_fasta(path: Path) -> list[str]:
    seqs, cur = [], []
    with open(path) as f:
        for line in f:
            if line.startswith(">"):
                if cur:
                    seqs.append("".join(cur))
                    cur = []
            else:
                cur.append(line.strip())
    if cur:
        seqs.append("".join(cur))
    return seqs


def counts(seqs: list[str]) -> tuple[dict[str, float], int]:
    c = {a: 0 for a in AA}
    c["X"] = 0
    total = 0
    for s in seqs:
        for ch in s:
            c[ch if ch in c else "X"] += 1
            total += 1
    return c, total


def normalise(c: dict[str, float], total: int) -> dict[str, float]:
    return {k: (v + 0.5) / (total + 0.5 * len(c)) for k, v in c.items()}


def js_bits(p: dict[str, float], q: dict[str, float]) -> float:
    keys = sorted(set(p) | set(q))
    mid = {k: 0.5 * (p.get(k, 0.0) + q.get(k, 0.0)) for k in keys}

    def kl(x: dict[str, float], y: dict[str, float]) -> float:
        return sum(x[k] * math.log2(x[k] / y[k]) for k in keys if x[k] > 0)

    return 0.5 * kl(p, mid) + 0.5 * kl(q, mid)


def tv(p: dict[str, float], q: dict[str, float]) -> float:
    keys = sorted(set(p) | set(q))
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def log2_odds(p: dict[str, float], q: dict[str, float]) -> dict[str, float]:
    return {a: round(math.log2(p[a] / q[a]), 3) for a in AA}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval-dir", default="eval_runs/fn_eval_v1")
    ap.add_argument("--out", default="eval_runs/fn_eval_v1/aa_signature")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    table: dict[str, dict] = {}
    for arm in ARMS:
        fasta = Path(args.eval_dir) / arm / "aatype.fasta"
        if not fasta.exists():
            print(f"skip {arm}: no {fasta}")
            continue
        seqs = read_fasta(fasta)
        c, total = counts(seqs)
        table[arm] = {
            "n_seqs": len(seqs),
            "n_residues": total,
            "freq": {k: v / total for k, v in c.items()},
            "counts": c,
        }

    ref = table["real"]
    ref_freq = {k: v / ref["n_residues"] for k, v in ref["counts"].items()}
    ref_sm = normalise(ref["counts"], ref["n_residues"])

    lines = [
        "# Per-AA frequency signatures (fn_eval_v1 arms)",
        "",
        "Order-blind composition readout; real = reference.",
        "",
        "| arm | n seqs | JS vs real (bits) | TV vs real | top |log2 odds| movers |",
        "|---|---|---|---|---|",
    ]
    results = {}
    for arm, t in table.items():
        arm_freq = {k: v / t["n_residues"] for k, v in t["counts"].items()}
        arm_sm = normalise(t["counts"], t["n_residues"])
        odds = log2_odds(arm_sm, ref_sm)
        movers = sorted(AA, key=lambda a: -abs(odds[a]))[:5]
        results[arm] = {
            "n_seqs": t["n_seqs"],
            "js_bits": round(js_bits(arm_freq, ref_freq), 4),
            "tv": round(tv(arm_freq, ref_freq), 4),
            "log2_odds": odds,
        }
        lines.append(
            f"| {arm} | {t['n_seqs']} | {results[arm]['js_bits']} "
            f"| {results[arm]['tv']} "
            f"| {', '.join(f'{a}{odds[a]:+.2f}' for a in movers)} |")

    lines += [
        "",
        "Notes:",
        "- JS/TV use raw frequencies; log2 odds use add-0.5 pseudocounts vs real.",
        "- `ours_cond_shuf` (composition-preserving shuffle) matching `ours_cond` "
        "confirms order-blindness: this readout cannot see sequence-order effects.",
        "- Real-vs-real spread: split-half JS of the real arm is the noise floor.",
    ]
    # split-half noise floor for the real arm
    half = len(table["real"]["counts"]) and None  # placeholder no-op
    real_seqs = read_fasta(Path(args.eval_dir) / "real" / "aatype.fasta")
    h1, h2 = real_seqs[: len(real_seqs) // 2], real_seqs[len(real_seqs) // 2:]
    c1, n1 = counts(h1)
    c2, n2 = counts(h2)
    floor_js = js_bits({k: v / n1 for k, v in c1.items()},
                       {k: v / n2 for k, v in c2.items()})
    results["real_split_half_js_bits"] = round(floor_js, 4)
    lines.append(f"- Measured real split-half JS floor: **{floor_js:.4f} bits**.")

    (out_dir / "aa_signature.json").write_text(json.dumps(results, indent=2))
    (out_dir / "aa_signature.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
