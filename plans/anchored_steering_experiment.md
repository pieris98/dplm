# Anchored steering experiment (meeting 2026-10-13 — points 1, 2, 4)

Status: **plan drafted 2026-10-13, awaiting sign-off before any GPU time.**
Companion docs: [meetings.md](meetings.md) (point 1 discussion), census outputs
[../eval_runs/real_data_census/census.md](../eval_runs/real_data_census/census.md).

## Objective

**Mechanism test, not capability test.** From-scratch arms (fn_eval_v1) confound
"conditioning exerts a directed force" with "sampler collapses to poly-A".
Here the input is controlled: *real* anchors, partially masked, passed through
vanilla / conditional / conditional-null — same anchor, only the condition
varies. Per-sequence paired deltas give the power that 8-seqs-per-label
sampling could never provide, and the outcome selects between competing
explanations of the w=1 collapse (see anticipation table).

## Families and strata (v1)

Chosen from the 33 census strata-ready families to span annotation
granularity levels (superfamily-domain / fold-family / short motif), because
steering difficulty should differ across them:

| family | description | members | cov p10/p50/p90 | low-third | high-third | member len med | len-matched non-members |
|---|---|---|---|---|---|---|---|
| IPR036291 | NAD(P)-binding domain superfamily | 488 | 0.33/0.50/0.98 | 164 | 163 | 334 | 2 203 |
| IPR012340 | Nucleic acid-binding, OB-fold | 91 | 0.18/0.65/1.00 | 30 | 31 | 189 | 1 496 |
| IPR002052 | DNA methylase N-6, conserved site (motif-level) | 119 | 0.010/0.019/0.029 | 40 | 41 | 374 | 2 470 |

Strata: within-family coverage terciles (census cuts, *not* absolute % —
5%/25% was illustrative). F-low = bottom tercile, F-high = top tercile.
Take all anchors in the tercile up to 100 (IPR012340 has 30/31 — paired
Wilcoxon at n=30 is workable for consistent effects; fallback = bottom/top
half, n=45).
Non-member set: 100 proteins per family, length-matched ±20% to the member
length median, not carrying the family (verify absence by IPS at scoring).

## Design

- **Pass-through:** masked infill — sample p·L positions uniformly (seeded),
  freeze the rest at real tokens via `partial_masks`, denoise the masked
  positions. p ∈ {0.1, 0.3, 0.5, 0.8, **1.0**} — p=1.0 is the bridge cell
  that must reproduce the from-scratch behaviour.
- **Arms per anchor set:** `vanilla` (pretrained DPLM-2), `cond(F)`
  (our ckpt, family prompt), `cond(null)` (our ckpt, null). Optional 4th:
  shuffled-F negative control.
- **CFG weight:** v1 fixed at **w=1** — cleanest mechanism read; w-sweep is a
  secondary knob, only if H1 is confirmed.
- **Seeds:** 3 per cell, deltas averaged over seeds.
- Implementation detail: mask the aatype track only; struct track denoises
  freely as in existing generation. Driver:
  `scripts/eval_function/steer_infill.py` (to write) over
  `generate(partial_masks=...)` — confirm the conditional path composes
  `partial_masks` with CFG before building.
- Scale: 3 families × (2 strata + non-members) × 3 arms × 5 p × 3 seeds ≈
  1 000 generation cells of ≤100 seqs — small cluster job.

## Metrics (all paired unless noted)

1. **Δcoverage** — per-anchor family coverage after − before; coverage from
   InterProScan on edited FASTAs through the existing scorer path (same
   install/version as fn_eval_v1). Test: Wilcoxon signed-rank cond(F) vs
   vanilla on identical anchors, per p.
2. **Steering projection** — shared ESM-2 3B mean-pool space;
   d(F) = centroid(F-high real) − centroid(F-low real); score =
   ⟨x_edit − x_anchor, d⟩/‖d‖. Positive + monotone in p = consistent steering.
3. **MMD**(edited F-low → real F-high) per arm × p (existing kernel).
4. **Naturalness delta** — ESM-2 PLL(edited) − PLL(anchor): the realism tax.
5. **Per-AA cond−null delta signature** — composition of edited positions by
   arm × p; the poly-A detector (w=1 from-scratch sits at A=78%).
6. **Retention** (F-high anchors): does cond(F) preserve coverage better than
   vanilla when much is masked? **False-positive rate** (non-member anchors):
   F-structure appearing under cond(F) vs vanilla chance.

## Anticipation table (written before running — point 4 discipline)

Competing hypotheses for the w=1 collapse:

- **H1 — conditioning functional; from-scratch failure is sampler/init
  pathology.** Anchored edits steer toward F; realism roughly preserved at
  low p.
- **H2 — adapter-intrinsic poly-A attractor.** Any adapter forward pass
  (even null-conditioned) drags composition toward poly-A regardless of
  context; coverage and naturalness degrade with p.
- **H3 — conditioning too weak to move anchored edits.** Base logits on real
  context are confident; adapters are swamped; all arms behave like vanilla.

| observable (F-low anchors unless noted) | H1 | H2 | H3 |
|---|---|---|---|
| Δcoverage cond(F) − vanilla | > 0, grows with p | ~0 or negative (poly-A edits destroy coverage) | ≈ 0 exactly |
| A-fraction of edited positions, cond arms | ≈ anchor-level (< 0.15), flat in p | rises steeply with p (≥ 0.4 at p ≥ 0.5) | flat at vanilla level |
| cond(null) vs vanilla | ≈ vanilla (no leakage) | also drifts poly-A (attractor is condition-independent) | ≈ vanilla |
| steering projection | > 0, monotone in p | ≈ 0 or negative (drift is toward poly-A region, not F-high centroid) | ≈ 0 |
| naturalness delta vs p | mild cost | steep degradation | vanilla-like |
| retention: F-high coverage at p = 0.8 | cond(F) > vanilla (condition maintains domain) | both degrade, cond worse | equal |
| non-member F-hits under cond(F) | slightly > vanilla chance (de-novo local insertion; exploratory) | junk low-complexity artifacts only | ≈ vanilla |

**Discriminating pairs:** H2 vs H3 → the cond(null) poly-A-drift row.
H1 vs H3 → the Δcoverage row. Every cell of the results table gets an
anticipation-vs-result entry (point 4).

## Decision tree

- **H1 confirmed** → keep the adapters; fix the *sampler*: init schedule /
  temperature, CFG schedule as a function of diffusion timestep, guidance
  rescaling. Paper reports mechanism (anchored) and capability (from-scratch)
  separately.
- **H2 confirmed** → architecture problem: run the point-4a ablation
  (`adapter_input: fused | base`) and per-layer ‖Δ‖ norms on the existing
  checkpoint (zero-training diagnostics first), then retrain with output
  regularization / adapter-scale annealing.
- **H3 confirmed** → strengthen conditioning: per-position condition vectors,
  cross-attention instead of additive adapters, larger bottleneck; revisit
  condition-dropout rate and function-aware checkpoint selection (09-29
  feedback item 4).

## Deliverables

- `scripts/eval_function/steer_infill.py` — anchor-set builder + infill
  generation driver.
- Results table per family in anticipation-vs-result format; JSON + markdown
  under `eval_runs/steer_v1/`.
- Figures: anchor→edited arrow plot in shared ESM space; per-p metric curves;
  per-AA delta heatmap; all with real strata as gray backdrop (the
  "informative t-SNE" of meeting point 1).

## Open items

- Confirm `generate()` composes `partial_masks` with the CFG path (read code
  before building the driver).
- IPS runtime for ~10⁵ edited sequences — batched, existing install; fine.
- GO version of the design (specificity strata via corpus IC terciles) —
  separate follow-up after the IPR mechanism read.
- InterProScan granularity caveat: IPR002052 is motif-level, IPR036291
  superfamily-level — coverage magnitudes are not comparable across the three
  families, only within.
