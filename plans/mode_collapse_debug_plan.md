# Mode-collapse debug plan (revised priorities — 2026-10-20)

Status: **RESOLVED 2026-10-20 — root cause found on day one of Phase 0; it was a code bug.**
Phase 0 is complete (23/23 gauntlet checks green), Phases 1–2 are moot as written,
Phase 3 is the immediate path. See the Resolution section below; the original
phases are retained for the record.

**Problem statement (as drafted).** Adapter-trained ConditionalDPLM-2 collapses to
a poly-A attractor in the from-scratch regime (A = 78% of residues at w=1 vs 9.2%
real), while the frozen base is clean under the identical sampler. The PIs'
direction: before any further training, *localise at which step of the pipeline
the collapse enters* — it is either a model problem or a code bug, and the code
must be shown correct first.

## Resolution (2026-10-20)

**The code bug: the pretrained DPLM-2 was never loaded.** Both conditional
training configs set `net.pretrain: false`; `get_net_dplm2` performs all
pretrained-weight loading inside `if cfg.net.pretrain:` — so the run constructed
`EsmForDPLM2(config, ...)` **from scratch** (random init), froze it
(`freeze_base: true`), and trained the 19.6M conditioning modules on top for
100K steps. Verified by four independent reads of the checkpoint: (i) base
weights differ from the HF release by O(1–13) everywhere; (ii) `lm_head.bias`
exactly all-zero and all base tensors exactly N(0, 0.02), kurtosis 3.0 — the raw
initializer; (iii) **bit-exact reconstruction** of the checkpoint's base from a
seeded (`train.seed: 42`) direct construction; (iv) optimizer state holds moments
for exactly the 19.6M conditioning params and the base did not move 80K→100K.
The config trap: with `pretrain: true`, the `continue_train_from_dplm2` branch
used to `assert is_local`, crashing on the HF repo id — the false flag was
likely set to silence that assert.

**Fix (committed):** HF-repo-id loading in the `continue_train_from_dplm2`
branch ([models/utils.py](../src/byprot/models/utils.py)); `pretrain: true` in
both conditional configs; regression test in the Phase 0 gauntlet
([scripts/debug/phase0_gauntlet.py](../scripts/debug/phase0_gauntlet.py),
23/23 green on the fixed path — pretrained-base 573/573 tensors bitwise, identity-at-init
across forward/generation/infill, parent-body equivalence, adapter census 33/66/0,
null-path + eval-mode, CFG w∈{0,4}, training-data round-trip).

**What this explains** — every item in the evidence snapshot below (E1–E8)
becomes a *consequence of a random encoder under adapters*, not a property of
the method: the poly-A attractor is the adapters' learned marginal on random
features; E1's "collapse in trained weights" is trivially true; the ours_null
arm is resolved too — the eval driver passes `conditions=None`, which bypasses
the adapters entirely, so null was the *raw random encoder* (hence its displaced
manifold); E3's anchored-p=0.3 cleanliness is context imitation dominating at
low p. **No conditional-arm result survives as evidence about the method**; the
eval stack, data pipeline, and tooling do (they were validated by controls and
the round-trip audit). The checkpoint sweep's "overtraining" reading, the
non-monotone w-curve, and the H1/H2/H3 verdicts are all void.

**Forward path = Phase 3 as written, plus:** commit the fix → sync to Karolina →
checkpoint-cadence fix (`save_top_k`) → Tier-1 callback + abort criteria →
20-step smoke → fixed 100K baseline → full eval re-run (fn_eval_v1 protocol +
steering grid) on the new checkpoint. Phase 1 forensics are dropped (nothing to
localise in a random encoder). P2.2's remaining steering cells are only worth
running on the *fixed* checkpoint.

---

*(Original plan text below, retained for the record.)*

Status: drafted 2026-10-20 in response to PI feedback (see
[meetings.md](meetings.md), 10-20 entry). Superseded the *ordering* of
[anchored_steering_experiment.md](anchored_steering_experiment.md), which remains the
hypothesis framework. The queued Karolina 100K rerun was **gated** on Phases 0–1.

---

## Evidence snapshot (any explanation must fit all of E1–E8)

- **E1 — collapse is in the trained weights, not the conditioning.** ours_null
  shows it like ours_cond (MMD 0.854 vs 0.897; ESM centroid-to-real 0.367 vs
  0.366) — under null conditions the adapters still fire (learned null embedding)
  and still displace generation.
- **E2 — the vanilla baseline and the sampler are clean.** Vanilla DPLM-2 is
  compositionally indistinguishable from real at *every* mask rate (A-frac
  0.10–0.11, JS ≤ 0.006) — same driver, same schedule, same init.
- **E3 — the pathology is regime-dependent, not per-pass.** cond anchored infill
  at p=0.3 behaves (A-frac 0.185 = anchor-inherited level, JS 0.036); cond p=1.0
  collapses (A-frac 0.685). A single adapter pass on near-real hidden states does
  *not* drag output toward alanine → H2's strong form is disfavoured; the
  far-from-data regime is where the attractor lives.
- **E4 — degradation progresses with adapter training.** MMD 0.854→0.897 from
  80K→100K while steering metrics stay flat (n=2).
- **E5 — known architectural deviation.** Our adapters consume the **fused
  post-residual stream** at all 33 layers
  ([dplm2_modeling_esm.py:432-446](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L432-L446))
  — each layer's adapter sees every previous layer's accumulated update.
  ProCALM's adapters consume a *pre-residual branch sum*, so previous adapters
  reach later ones only indirectly. Our adapters compound strictly more.
- **E6 — capacity is at ProCALM's ablated negative.** c_s 128, c_hidden 16
  (bottleneck inner 144 after condition concat), 2-layer MLP, 19.6M trainable
  (2.9%) — the "Small" configuration that failed in ProCALM's own ablation.
- **E7 — intermediate checkpoints are gone.** `save_top_k: 2` every 2000 steps →
  only best-2-by-val-loss survive (80K/100K). When degradation started is
  unrecoverable; the n=2 sweep cannot be extended retroactively.
- **E8 — CFG repairs composition non-monotonically but never approaches the real
  manifold in ESM space** (JS across w=1/2/4/8: 0.49/0.20/0.17/0.25; ESM
  displacement flat 0.32–0.40).

## Principles

1. **Zero-training before retraining.** Every diagnostic that runs on the
   existing checkpoint or a fresh init costs minutes on the local 3090; the
   36-h × 16-GPU rerun is gated on their verdict.
2. **Localise before fixing.** The deliverable of debugging is a statement of
   the form "the collapse enters at (data-path | layer-range | timestep-regime |
   trajectory-stage)", not another metric table.
3. **Pre-register everything** (anticipation vs result — 10-13 feedback point 4).
   Each phase below states its readings *before* running.
4. **Controls first.** Every new instrument runs on vanilla DPLM-2 and the real
   data arm before the conditional model does.
5. **Every killed bug class becomes a permanent committed test** (the war-story
   discipline from RESEARCH_STATE §3.6). The Phase 0 gauntlet is pytest-able and
   stays in the repo.

---

## Phase 0 — code-correctness gauntlet ("is it a bug in code?") — ✅ DONE 2026-10-20

**Outcome: root cause found and fixed; 23/23 checks green** on the fixed path
([scripts/debug/phase0_gauntlet.py](../scripts/debug/phase0_gauntlet.py)).
P0.0 (`pretrained-base`) was the check that caught the incident. P0.7 (scorer
re-run on committed outputs) remains available as a documented command. The two
initial data-check FAILs were check bugs (crop-window comparison; separator
assumption in the joined struct string), fixed in the same pass — the data
pipeline itself is clean.

Original design (all local-GPU, hours each):

| # | Check | Kills | How |
|---|---|---|---|
| P0.1 | **Identity at init** — fresh `ConditionalDPLM2` vs `airkingbd/dplm2_650m`: (a) teacher-forced logits max\|Δ\|; (b) full from-scratch generation, fixed seed → token-identical sequences; (c) partial-mask infill → identical. | Adapter plumbing dead-weight, stash leakage, `generate()` delegation drift (the replicated-forward + stash design, [dplm2_conditional.py:274-368](../src/byprot/models/dplm2/dplm2_conditional.py#L274-L368)) | direct comparison scripts; fixed seeds; CPU + GPU |
| P0.2 | **Replicated-body equivalence** — parent `DPLM2.forward` vs our `_forward_net(..., layer_adapter_inputs=None)` on identical inputs. | Attention-bias / embedding replication drift in `_forward_net` (the parent hardcodes net kwargs; we re-implemented its body) | same as P0.1a |
| P0.3 | **Adapter-invocation census** — hooks counting adapter calls per path: training forward = 33; CFG generate step = 66 (cond + uncond); w=1 = 33; validation = 33 *with* conditions (task passes them — [conditional_dplm2.py (task)](../src/byprot/tasks/lm/conditional_dplm2.py)). | Adapters silently never firing / double-firing on any path (the failure mode the replication was written to avoid) | forward-hook counter context manager |
| P0.4 | **Null-path + eval-mode consistency** — `force_unconditional=True` embedding ≡ training-time dropped-sample embedding (same table row — [annotation_embedder.py:92-120](../src/byprot/models/dplm2/conditioning/annotation_embedder.py#L92-L120)); adapter inputs identical under both; `.eval()` confirmed at generation (verified: [generate_conditional_dplm2.py:110](../generate_conditional_dplm2.py#L110) — keep as regression test). | Train/inference mismatch: dropout active at inference; null embedding drift between training and CFG-uncond pass | numeric comparison on the real ckpt |
| P0.5 | **CFG degeneracy** — w=1 output ≡ single conditional pass; w=0 ≡ null pass; logits at w>1 finite (no NaN/±inf poisoning top-p/annealing). | CFG arithmetic bugs; extrapolated-logit interaction with the sampler (relevant to the owed E8 explanation) | equality checks on short generations |
| P0.6 | **Training-data round-trip audit** (highest-value data check) — load `joined_train_safe.parquet` batches through the *actual* datamodule/collator; decode the aatype track back to AA; verify (a) sequence identity vs source proteins, (b) AA composition matches source (JS < real split-half noise floor 0.0007 bits), (c) GO/IPR label tensors belong to the right protein (hash-aligned, not spot-check), (d) struct tokens ≡ DPLM-2's shipped tokens for the same protein, (e) mask/noise construction matches the vanilla training path. | Corrupted or misaligned training data — would produce exactly the observed signature (frozen base clean, adapter-trained weights collapsed) while every eval-side control stays green. The real eval arm decodes `test.pkl` directly and **never exercises the training tokenization path**; the repo has a tokenization-mismatch history and the 09-15 feedback flagged the foreign-dataset → struct-tokenizer import as an unresolved problem | decode scripts over ≥1K sampled batches |
| P0.7 | **Eval-side regression** — re-run the scorer on committed `fn_eval_v1` outputs; confirm headline numbers reproduce (real arm IPS F1 0.978 etc.). | Eval-stack regressions masquerading as model findings | `score_function_eval.py` re-run |

**Gate:** all green → the code-bug branch is substantially eliminated (data,
plumbing, null-path, CFG, eval all verified) and Phase 1's findings are about
the *model*. Any red → fix + permanent test + re-run the gauntlet before
Phase 1; if the bug could have shaped training, Phase 3's rerun becomes a
*fixed-config* rerun and the ablation arms are re-prioritised.

---

## Phase 1 — zero-training forensics on `step_99999` ("where does the model break?") — VOID

*Moot after the Resolution: there is nothing to localise in a randomly-initialized
encoder. The instruments designed here (per-layer ‖Δh‖ census, timestep-resolved
probe, α sweep, trajectory logging, weight forensics) remain the right toolkit
if a collapse re-emerges on the fixed checkpoint.*

All local-3090, no training. Instruments: forward hooks on all 33 adapters
(update norms), `keep_history=True` trajectories, batched noised-real probes.

- **P1.1 — per-layer ‖Δh‖ census.** For each adapter l: ‖update_l‖/‖h_l‖ under
  (a) teacher-forced real sequences, (b) null conditions, (c) from-scratch
  generation states, (d) anchored p=0.3 states; cond vs null.
  *Pre-registered reads:* fused-input compounding (E5) predicts relative update
  magnitude growing with depth; a global weight pathology predicts large updates
  uniformly. A layer-localised spike names the culprit layer range for the
  Phase 3 input-swap ablation.
- **P1.2 — timestep-resolved probe (the sharpest single experiment).** Noise
  real batches at each of the 500 timesteps exactly as training does; forward
  through the ckpt; measure predicted-x₀ composition (A-probability, argmax
  A-fraction) + logit entropy; arms: cond (real labels) / null / vanilla base.
  *Pre-registered reads:*
  - **R1 — poly-A at high t in-distribution** (while vanilla-base predicts the
    ~9% unigram marginal it was trained on): the *weights themselves* predict
    poly-A in the high-noise regime where the from-scratch path lives → weight
    pathology → input-path/regularisation fixes + retrain (short arms first).
  - **R2 — clean at all t in-distribution, collapse only free-running:** one-step
    behaviour is fine; the drift is accumulated along the sampling trajectory
    (compounding OOD) → sampler-regime fixes (schedule, self-correction,
    guidance schedule), possibly *no retrain needed*.
  - **R3 — cond drifts but null clean in-distribution:** conditioning itself
    drives high-noise collapse — would sharply update E1's framing.
  Companion read: loss mass by timestep band under the linear weighting — was
  high-t behaviour even being penalised during training?
- **P1.3 — adapter-output scale sweep.** Scale every adapter update by
  α ∈ {0, 0.25, 0.5, 0.75, 1.0} at inference (one-line hook); from-scratch + p∈{0.3, 1.0} anchored cells on the OB-fold strata. α=0 must reproduce vanilla
  (a free inside-generation re-check of P0.1). *Reads:* graded degradation vs
  threshold effect distinguishes "updates uniformly toxic" from "pathology
  emerges only past a compounding threshold"; the α* where realism first breaks
  calibrates how far the learned updates overshoot.
- **P1.4 — trajectory logging.** `keep_history=True` from-scratch runs (cond,
  null, vanilla): per-step A-fraction, masked count, per-position entropy,
  unique-k-mer diversity, cond−uncond KL per step. *Reads:* is the very first
  denoise step already poly-A (attractor present at init of the trajectory) or
  does A commit mid-trajectory (drift)? Where does diversity die? This is also
  the data behind the owed E8 w-curve explanation.
- **P1.5 — adapter-input swap (coarse, secondary).** Run the ckpt with adapters
  fed the clean-base stream instead of the fused stream. Adapters were trained
  on fused stats, so this is OOD for them — informative about how much drift the
  accumulated stream carries, **not** conclusive; the conclusive version is the
  Phase 3 retrained arm.
- **P1.6 — weight-space forensics.** ‖W_up‖ growth per layer (init std 1e-5 →
  trained); effective rank of adapter updates; condition-vector separability —
  encode the 59 eval labels' s vectors, nearest-neighbour / silhouette
  classification of held-out labels in s-space. *Reads:* s-space non-separable
  → capacity evidence (E6, H3); huge ‖W_up‖ growth concentrated deep → E5
  compounding evidence.

**Deliverable:** the *localisation table* — collapse entry point by (data-path ✓/✗,
layer-range, timestep-regime, trajectory-stage) — plus updated H-frame verdict.
This table selects the Phase 3 arms.

---

## Phase 2 — hypothesis-experiment completion (feedback item 2) — DEFERRED to the fixed checkpoint

*The H1/H2/H3 frame was built to explain a random encoder's behaviour; the
refined anticipation table and the remaining steering cells are only meaningful
on the fixed retrained model. The w-curve explanation owed from 10-13 is also
void (it described the broken model).*

- **P2.1 — refine the hypothesis set first.** H2's strong form is disfavoured
  (E3) and Phase 1 will have sharpened the frame (R1/R2/R3). Write the refined
  anticipation table **before** running the remaining cells.
- **P2.2 — remaining steering-grid cells:** the 18 deferred cells
  (3 families × strata × arms × p), IPR012340 vanilla/null cells, the
  **cond(null) drift row** (cheap, and directly informs P1.1's null reads — pull
  this one forward), Δcoverage via InterProScan. Karolina `evalsh` interactive or
  local 3090, resumable launcher already built.
- **P2.3 — owed w-curve explanation** (10-13 addendum): non-monotone
  JS(w) 0.49/0.20/0.17/0.25 written up against P1.4's trajectory data —
  working hypothesis: w=1 sits inside the attractor, moderate w escapes it,
  high w overshoots into a different off-manifold composition.
- **P2.4 — PI-facing figures:** A-fraction-vs-timestep heatmaps (P1.2), per-layer
  ‖Δh‖ profiles (P1.1), trajectory spaghetti plots (P1.4), α-sweep curves
  (P1.3) — these show *where* the collapse enters, which is what the PIs asked
  to see.

---

## Phase 3 — instrumented retrain (Karolina; gated on Phase 0 green + Phase 1 readout) — **NOW THE ACTIVE PATH**

*Phase 0 is green and Phase 1 is void, so the gate is open. Pre-launch checklist
(2026-10-20): commit + sync the fix to Karolina; `save_top_k` cadence fix;
Tier-1 callback + pre-registered abort criteria; wandb online/offline decision;
20-step smoke with callback plumbing; then the 100K baseline.*

1. **Implement the two-tier validation callback first** (designed 2026-10-02,
   RESEARCH_STATE §4.2.4 — this *is* PI feedback item 3). Tier 1, in-process,
   every validation: loss-delta proxy (fixed batches, null vs real conditions →
   condition-sensitivity curve); generation probe every N steps (8 fixed labels
   × 4 seqs) with **predictor-free metrics to wandb: probe A-fraction, per-AA
   JS vs real, MMD-proxy vs a cached real subset, probe MRR, per-layer ‖Δh‖
   (sampled layers), adapter ‖W‖ norms, grad norms**. Tier 2 (decoupled,
   `eval_pipeline.sbatch SKIP_GENERATE=1`) unchanged.
2. **Pre-registered early-failure criteria** (written before launch, e.g.:
   "probe A-fraction > 0.25 at two consecutive probes after step 5K → alarm;
   no monotone improvement in probe MMD-proxy by step 20K → abort-and-ablate").
   The point is to *detect adapters failing early* (PI item 3) and stop burning
   steps — the 100K horizon is the experiment's budget, not its default.
3. **Checkpoint cadence fix:** every 2000 steps, **unpruned** (50 × 2.6 GB ≈
   130 GB on 20 TB scratch — fine) → the next sweep has real n and E7 never
   recurs.
4. **The queued 20-step multi-node smoke carries the callback plumbing** —
   validate wandb logging, probe generation, checkpoint writing before the big
   run.
5. **The 100K baseline rerun proceeds config-unchanged** (comparability with the
   Meluxina baseline) but fully instrumented. Short-horizon (20–30K) ablation
   arms follow, **selected by the Phase 1/2 verdict** rather than all run:
   (a) adapter input: fused ‖ ProCALM-faithful pre-residual ‖ clean base
   (E5; the three-way version of the queued 4a ablation);
   (b) capacity: c_hidden 16→64, adapter_nlayers 2→4 (E6);
   (c) output regularisation: ‖Δh‖ penalty or adapter-scale annealing (under
   R1);
   (d) trajectory-side fix at sampling time only (under R2 — possibly no
   retrain at all).

---

## Updated decision tree — RESOLVED

| Phase 0 | Phase 1 | Verdict | Action |
|---|---|---|---|
| **red** | — | **code bug** | **← actual outcome: pretrained weights never loaded; fixed + regression-tested** |

The remaining rows (sampler-regime / weight-pathology / capacity verdicts) stay
relevant only if a collapse re-emerges on the fixed checkpoint — then Phase 1's
forensic toolkit runs as designed.

| Phase 0 | Phase 1 | Verdict | Action |
|---|---|---|---|
| red | — | code bug | fix + permanent test; retrain only what the bug tainted |
| green | R1 (poly-A in-distribution at high t) | weight pathology, high-noise regime | arms (a)/(c); short horizons; sampling unchanged |
| green | R2 (clean in-distribution, drifts free-running) | trajectory/compounding pathology | sampler-side fixes; retrain deferred or arm (c) only |
| green | R3 (cond drifts, null clean, in-distribution) | conditioning-driven high-noise collapse | arms (a)/(b) + guidance schedule |
| green | P1.6 s-space non-separable | capacity/conditioning too weak | arm (b); stronger pathway decision |
| — | P2.2 completes with Δcoverage > 0 cond vs vanilla | conditioning works where anchored | paper: mechanism (anchored) vs capability (from-scratch) split, per original plan |

## Deliverables

- `scripts/debug/` — Phase 0 gauntlet (pytest where pure-model) + Phase 1
  forensic drivers (hooks, probes, trajectory analysis); all committed.
- Localisation table + refined anticipation tables + anticipation-vs-result
  entries per cell (Phase 2 discipline).
- Two-tier callback + pre-registered abort criteria + unpruned checkpoint
  config, wired into `scripts/karolina/` before launch.
- Figures per P2.4.

## Post-launch queue (2026-10-26 — allocation validity ends 2026-10-27)

The fixed campaign launched under a hard allocation cap: ~37 h of wall remain,
so the run lands around **step ~75–80K** (0.65 it/s + ~90 s validation per 500
steps). The periodic checkpoint series (every 2,000) is the deliverable;
chaining ends when the allocation does. The eval stack and all analysis move to
the **local workstation** with checkpoints synced from Karolina.

| # | Item | Where | Status |
|---|---|---|---|
| Q1 | Monitor the 100K early trajectory | Karolina logs | **in flight** — reading @14K (2026-10-26): val/loss 0.750→0.706 ✓; adapter_w 4.2→12.7 (steep); generation-side drift MILD but real — a_frac peaked 0.191@10K then receded (0.155@14K), mmd +0.05, diversity −0.16, no alarm (threshold 0.25×2 consecutive not tripped). **`probe/loss_delta` sensor bug found live**: the force_unconditional patch wrapped both passes → delta exactly 0 forever (393d643 fixes it; applies from next chunk after git pull). New **Q10**: offline condition-sensitivity check on the latest ckpt (real-vs-null loss delta, ~10 min, 1 GPU) — answers what the flat sensor could not | local/Karolina | pending |
| Q2 | P0.7 scorer regression re-run on committed `fn_eval_v1` outputs (validates the eval stack before it becomes the critical path) | local | **done 2026-10-26** — MRR/MMD/IPR bit-exact; GO metrics drifted ~5% relative: the restored `eval/go.obo` is a different ontology release than September's (file had vanished from local eval/); scorer now LOUD-FAILS on missing --obo (8b2d0e2). Keep this go.obo as the campaign reference |
| Q3 | Checkpoint sync Karolina → local (final + best + periodic spaced ~10–20K apart) | rsync | pending (at run end) |
| Q4 | Eval-stack validation. Generation stage validated locally (clean 3-arm run on the quarantined ckpt). IPS + DeepGO assets missing locally (eval/ gutted; only go.obo survives) → **Plan A+B (2026-10-26)**: A = cluster Tier-2 eval NOW (eval_pipeline.sbatch, CKPT=auto → newest mid-training ckpt, all assets + java 11 on-cluster); B = restore IPS (35 GB) + deepgo2 data (16 GB) locally via tar-over-ssh + conda openjdk 11 | A: Karolina; B: local | **A submitted / B in progress** |
| Q5 | Per-layer learning analysis for the paper (Phase 1's paper-grade subset): per-layer adapter ‖W‖ / ‖Δh‖ profiles across the synced ckpt series → layer × step figure; condition-space (s) separability probe | local | pending |
| Q6 | Tier-2 reframed: predictor-based function-recovery eval on periodic checkpoints — first instance is Q4's cluster run on a mid-training ckpt; subsequent instances local after B | hybrid | **in progress** |
| Q7 | Phase 2: refined anticipation table + remaining steering cells + Δcoverage/IPS + w-curve (if still relevant) | local; needs new ckpt + first eval numbers | gated on Q4/Q6 |
| Q8 | Phase 3.5 ablation arms (adapter-input three-way, capacity, regularization) | gated on 100K outcome AND compute (new allocation, or local arms at reduced batch with internal comparability) | gated |
| Q9 | libibverbs in sandbox | Karolina | **moot** (allocation ends; ~few % step time) |

Closed: wandb offline (resolved); sync to Karolina (done); broken ckpts
quarantined on Karolina (local copies remain in `logs/` with the README warning
in scripts/debug/README.md); no EMA (verified).

Launch-campaign record (2026-10-24/26): six smoke rounds surfaced and fixed, in
order — SLURM memory-env leak, `exec`-on-function, torch env forwarding
(→ torchrun architecture after Lightning's SLURM/TorchElastic semantics fought
the per-task pattern), CUDA_VISIBLE_DEVICES stripping (NCCL NVML topology
failure), torchrun entrypoint, MAX_STEPS export, resume key (`train.ckpt_path`).
Green smoke + resume + post-flip all verified; metrics extracted from the
offline `.wandb` logs and healthy (val/loss ≈ 0.75, a_frac 0.08–0.12, adapter_w
growing, alarms 0).

## Open items

- P0.7 — see Q2. Tier-2 — see Q6. Phase 2 — see Q7. Ablations — see Q8.
