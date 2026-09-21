"""Sanity checks for the ported function-eval metrics (no GPU, no tools).

Verifies against hand-constructable ground truth:
  * spectrum_map: shape + L2 normalization
  * mmd(x, x) ≈ 0, mmd(x, y) > 0 for disjoint compositions
  * mrr: perfect generation (copies of reference) → MRR = 1.0;
         swapped-label generation → degraded
  * set_match_metrics: exact matches → F1 = 1.0; empty predictions → 0
  * fmax: perfect scores → 1.0; random → low

Run: python scripts/eval_function/check_function_metrics.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from byprot.eval.function import (  # noqa: E402
    fmax,
    mmd,
    mrr,
    set_match_metrics,
    spectrum_map,
)

rng = np.random.default_rng(0)
ok = True


def check(name, cond, detail=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    ok = ok and cond


# --- spectrum_map ---
aas = "ARNDCEQGHILKMFPSTWYV"
seqs_a = ["".join(rng.choice(list(aas), 200)) for _ in range(16)]
seqs_b = ["".join(rng.choice(list("AK"), 200)) for _ in range(16)]  # disjoint-ish
emb = spectrum_map(seqs_a)
check("spectrum shape", emb.shape == (16, 20**3))
check("spectrum L2-normalized", np.allclose(np.linalg.norm(emb, axis=1), 1.0))

# --- MMD ---
check("MMD(x,x) ≈ 0", mmd(emb1=emb.copy(), emb2=emb.copy()) < 1e-6)
emb_b = spectrum_map(seqs_b)
check("MMD(a,b) > 0", mmd(emb1=emb, emb2=emb_b) > 0.1)
check("MMD gaussian(a,b) > 0", mmd(emb1=emb, emb2=emb_b, kernel="gaussian") > 0.1)

# --- MRR: three reference groups with genuinely different compositions ---
ref_seqs, ref_labels = [], []
for g in range(3):
    for _ in range(8):
        ref_seqs.append("".join(rng.choice(list(aas[5 * g : 5 * (g + 1)]), 200)))
        ref_labels.append([g])
gen_good = list(ref_seqs)          # perfect: identical composition + labels
gen_labels_good = list(ref_labels)
m, ranks = mrr(gen_good, gen_labels_good, ref_seqs, ref_labels)
check("MRR perfect generation = 1.0", abs(m - 1.0) < 1e-6, f"mrr={m:.3f}")

# swapped: sequences keep their composition but labels rotate 0→1→2→0
gen_labels_bad = [[(l[0] + 1) % 3] for l in ref_labels]
m_bad, _ = mrr(gen_bad := list(ref_seqs), gen_labels_bad, ref_seqs, ref_labels)
check("MRR swapped labels < 1.0", m_bad < m, f"mrr={m_bad:.3f}")

# --- set-match ---
pred = [{"A", "B"}, {"C", "D"}, {"A"}]
gt = [{"A", "B"}, {"C", "D"}, {"A"}]
m = set_match_metrics(pred, gt)
check("set-match perfect → F1 micro = 1.0", m["f1_micro"] > 0.999,
      f"f1_micro={m['f1_micro']:.3f}")
pred_partial = [{"A", "B"}, {"C"}, set()]  # misses D and A
m_part = set_match_metrics(pred_partial, gt)
check("set-match partial → F1 micro = 0.75", abs(m_part["f1_micro"] - 0.75) < 1e-9,
      f"f1_micro={m_part['f1_micro']:.3f}")
m_empty = set_match_metrics([set()] * 3, gt)
check("set-match empty predictions → f1 = 0", m_empty["f1_micro"] == 0.0)

# --- fmax ---
gt_bin = np.zeros((30, 4))
gt_bin[:15, 0] = 1
gt_bin[15:, 2] = 1
perfect = gt_bin.copy()  # exact 0/1 "probabilities"
check("fmax perfect scores = 1.0", fmax(perfect, gt_bin) == 1.0)
# noisy with overlapping score ranges: positives [0.6,1], negatives [0,0.8]
noisy = np.clip(gt_bin * 0.6 + rng.random(gt_bin.shape) * 0.7, 0, 1)
f_noisy = fmax(noisy, gt_bin)
check("fmax noisy < 1.0 but > 0.5", 0.5 < f_noisy < 1.0, f"fmax={f_noisy:.3f}")

print("\n=== VERDICT ===")
print("ALL METRIC CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
