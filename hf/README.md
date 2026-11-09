---
license: apache-2.0
tags:
- protein-language-model
- discrete-diffusion
- conditional-generation
- function-annotation
- computational-biology
base_model: airkingbd/dplm2_650m
---

# ConditionalDPLM-2 — function-conditioned DPLM-2 (42K-step checkpoint)

**ConditionalDPLM-2**: DPLM-2 650M (a discrete diffusion protein language model
over interleaved amino-acid + structure tokens) frozen at its pretrained
weights, conditioned on GO molecular-function and InterPro-domain label sets
via ProCALM-style parallel adapters (19.6 M trainable, 2.9 %) injected at all
33 layers, plus classifier-free guidance at sampling time.

This checkpoint is **mid-training** (42,000 steps of a 100,000-step run,
post-warmup lr ≈ 1.7e-4) on the corrected training pipeline — see Provenance.

## Provenance (important)

An earlier training campaign silently trained on a **randomly-initialized
encoder** (the pretrained weights were never loaded — a config bug). The root
cause was found, fixed, and locked behind a 23-check validation gauntlet
(`scripts/debug/phase0_gauntlet.py` in the training repo); every checkpoint
from this campaign post-dates the fix and the fix's regression test. The
42 K-step checkpoint here is the first correctly-trained artifact of the
project.

## Intended use

- Research use: studying whether function-annotation conditioning of a
  discrete-diffusion protein LM steers generation (structure tokens vs
  residue identity), and the realism-vs-conditioning tradeoff.
- **Not** for production or any applied/clinical use. Mid-training checkpoint:
  generation realism is known to drift with adapter growth (composition
  alanine fraction ~0.10–0.19 at 38–43K steps vs ~0.09 in real proteins;
  diversity −33 %) — see Limitations.

## Loading

Requires the training repo (byprot code) at the training commit:

```bash
git clone https://github.com/pieris98/dplm.git && cd dplm
# config + weights are self-contained in the .ckpt:
python - <<'PY'
from generate_conditional_dplm2 import load_conditional_model
model = load_conditional_model("step_41999.0-loss_0.67.ckpt", "cuda")
PY
```

Generation conditioned on a GO/IPR label set:

```bash
python generate_conditional_dplm2.py --ckpt <ckpt> \
  --labels go:<id>,ipr:<id> --cfg-scale 1.0
```

## Training

- Data: 45,696 proteins — CFP-Gen function annotations joined onto DPLM-2's
  shipped structure tokens (train split; eval on the held-out CFP-Gen test
  split).
- Objective: DPLM-2's timestep-weighted diffusion cross-entropy over the
  amino-acid + structure tracks, with the GO/IPR labels injected via parallel
  adapters at every layer (frozen 673 M base), classifier-free label dropout
  0.1.
- Effective batch: 16,000 tokens/step (rank-sharded), AdamW lr 3e-4 poly
  decay → 1e-5, warmup 1K steps.
- Live telemetry during training (the probe): condition-sensitivity (seeded
  real-vs-null loss delta per track), generation composition/diversity, and
  adapter-norm monitors — see Limitations for the 43K readings.

## Limitations (measured at 42K)

- **Condition-sensitivity is structure-biased**: seeded real-vs-null loss
  delta is ≈ −0.07 on the structure track (labels help) but ≈ 0 ± 0.003 on
  the amino-acid track — global protein-level labels help structure-class
  prediction without yet moving residue-identity prediction.
- **Free-running generation drifts from realism as training progresses**:
  alanine fraction of from-scratch generations peaked at 0.185 (real ≈ 0.10),
  k-mer MMD vs real +0.05, unique-3-gram diversity −33 % — while val/loss
  improved. Denoising quality and generation quality are decoupling; the
  checkpoint series (every 2,000 steps) is preserved to locate the best
  generation-quality point.
- Ablation arms (adapter-input pathway, capacity, position-aware
  conditioning) were evidence-triggered by these readings and are pending.

## Citation / provenance chain

Trained within a debugging campaign that established: (1) the original
"mode collapse" was a randomly-initialized-encoder bug (pretrained weights
never loaded — config `pretrain: false` skipped weight loading), fixed and
locked behind a 23-check validation gauntlet; (2) post-fix, the model's
denoising quality and generation quality decouple as adapters grow. Full
record: the training repo's `plans/meetings.md` (2026-10-20 → 10-27 entries)
and `plans/mode_collapse_debug_plan.md`.
