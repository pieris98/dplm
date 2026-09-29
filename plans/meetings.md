# Meeting Notes

Running log of weekly progress meetings. Newest entry first.

---

## 2026-09-29 — Weekly progress meeting (evaluation results review)

**Context at meeting time:** first complete 7-arm function-recovery evaluation done (see [../reports/weekly_meeting_2026-09-29.md](../reports/weekly_meeting_2026-09-29.md)): real conditioning signal exists (MRR 0.110/0.128 vs 0.078 controls), but **zero IPR recovery** in all 1,280 generated sequences, and a large sequence-realism regression (MMD 0.37–0.89 vs vanilla 0.16). Positive control validates the metric (real arm IPR F1 0.978).

### Feedback — Important

1. **Understand conditioning input and metrics better**: are they global protein-level vs local amino-acid/motif/domain-level? Understand MMD's relevance to measuring conditional performance (is it per family, or should the ground truth be subset based on the family condition for InterPro?). Understand why IPR is 0 everywhere in the conditional arms — bug, or is InterProScan/IPR unsuitable for this kind of conditioning measurement?
2. **Run DeepGO-SE for the GO conditioning part.**
3. **Identify other standard benchmarks** — not necessarily only CFP-Gen's; also other known function-conditional / multi-modal PLMs.
4. **Run the eval during training every n steps** to produce metric curves. Perhaps the adapters were overtrained to the point of steering the model too much? Checkpoint selection used "best" *total* loss — does total loss weigh all modalities (seq, struct, func)? Need a way to incorporate function into eval/validation.
5. **Visualize generated samples** (UMAP / qualitative plots): cond vs uncond vs real, colored — understand manifolds and how samples differ.
6. **Visualize and compare structural + sequence metrics alongside functional metrics** — how all metrics change in the conditional arms.

### Preliminary evidence-based answers (diagnostics run post-meeting input)

**On 1a — conditioning granularity: GLOBAL protein-level, by construction.**
The conditioning input is a protein-level multi-hot label set (one vector per protein: GO-F terms + IPR domains). The adapter broadcast spreads this single vector across *all* residue positions (`parallel_adapter.py` — per-sample `s` expanded over the sequence dim). There is **no residue-level or motif-level conditioning pathway** in the architecture. Consequence: the adapters can bias *global composition* (amino-acid propensities), but cannot place domains at specific positions. IPR domains are **local, position-specific motifs** — plausibly unreachable through a global composition bias alone; GO molecular-function terms are comparatively more global (whole-protein activities) → the DeepGO-SE probe (feedback 2) directly tests this asymmetry.

**On 1b — why IPR = 0 everywhere: RESOLVED (2026-09-29 audit) — not a bug; zeros are genuine + a composition artifact identified.**
Suspected a silent plumbing failure of the DeepGO-SE kind; ran a scorer-free audit (`scripts/eval_function/diagnose_ips.py`): join integrity perfect (0 stale/unmatched IDs in all arms), hand-computed raw set overlap confirms TP=0 for ours_cond/ours_null on the identical code path that gives TP=1144 (real, F1 0.978) and TP=4 (vanilla). **The zeros are real.** The accession collapse stands post-dedup (ours_cond: 19 unique IPR accessions from 221/250 annotated proteins; ours_null: 8; vanilla: 517; real: 147). What IPS *does* find in our arms: 52% of rows are a single signature — PRINTS "Type I antifreeze protein repeat" (IPR000104, small Ala/Gly-rich low-complexity), vs broad Gene3D/Pfam/SUPERFAMILY spread in vanilla/real. **Shuffled-output controls prove the artifact:** IPS on composition-preserved shuffled ours_cond is statistically identical to the real run (870 rows/20 accessions vs 851/19, same top hit) — all annotation survives destroying sequence order → zero sequence-specific domain content; while shuffled natural sequences collapse 147 accessions → 0. Bonus finding: `ipr_aupr_*` is uninterpretable for set-valued predictors — micro-AP on binary matrices has a nonzero baseline even at exactly TP=0 (ours arms "AUPR" 0.029 with zero overlap; vanilla 0.007 *with* overlap). Read only P/R/F1 for IPS; AUPR only matters for DeepGO-SE's per-term scores.

**On 1c — per-family MMD: IMPLEMENTED + run (2026-09-29).** `per_family_mmd()` in `byprot/eval/function.py`, wired into the scorer: for each label L, own = MMD(gen-for-L, real-for-L) vs cross = mean MMD(gen-for-L, real-for-L'≠L); delta = cross − own > 0 ⇒ label-specific proximity. Controls validated (null/vanilla ≈ chance, real arm calibrates own=0/frac=1.0, unit checks in check_function_metrics.py all pass). **Result: independently confirms the MRR story** — delta peaks at w=2 (+0.0185 linear, 71% of labels own-closest) vs null −0.0009 / vanilla −0.0011 (both at chance), decaying at w=4/8. **MRR does not need the same adjustment** — it is already label-grouped; per-family MMD adds the distance-level analog (MRR is a rank saturating at the 8-seqs/label noise floor 0.81) and the explicit cross-family baseline.

**On 5 — UMAP visualization: DONE (2026-09-29).** Shared UMAP over all 1,768 mean-pooled ESM-2 3B embeddings (`scripts/eval_function/plot_umap.py` → `eval_runs/fn_eval_v1/figs/`, copies in `reports/figures/`). Findings: (a) vanilla sits ON the real manifold (raw ESM |centroid−real| = 0.086) while every adapter-trained arm incl. null occupies a disjoint region (0.32–0.40) — the displacement is in the trained weights; (b) CFG does *not* approach the real manifold in ESM space (no monotone trend, 0.32–0.40) — its MMD gain is composition-level only, matching the IPS low-complexity-artifact finding; (c) w=1/null intra-arm spread (0.178) is tighter than the real arm's own (0.181) — visual confirmation of the diversity collapse that CFG partially lifts; (d) high-Δ labels show visible prompted-point clumps (qualitative per-family conditioning).

**On 1c — MMD relevance:** agreed. Current MMD is computed **per arm** (all generated vs all real) — it is condition-blind and only measures realism. Next iteration adds **per-family MMD**: for each prompted label L, MMD(generated-for-L vs real-L) — i.e. subset the ground truth by the family condition exactly as proposed. MRR already does the label-grouped version; per-family MMD complements it with a threshold-free distance curve.

**On 4 — checkpoint selection & loss weighting:** confirmed from code: total val/loss = the **sum of the aa-track and struct-track** diffusion cross-entropies, weighted by diffusion timestep. **Function has no loss term** — it is conditioning-only. So "best total loss" checkpoint selection is structurally blind to function fidelity, and the aatype plateau (step ~2K) while struct kept improving means "best total loss" was driven by struct gains. The overtraining hypothesis is testable: sweep saved checkpoints through the recovery probe (function recovery may peak early while realism degrades late — or never rise at all).

### Next steps (priority order)

1. **Run DeepGO-SE** on the four existing arms (data recovered; docker runner ready) → GO set-match + Fmax. Directly tests the granularity hypothesis: GO (global) may recover where IPR (local domains) cannot.
2. **Per-family MMD** (subset real GT by prompted family) added to the scorer — condition-aware realism metric.
3. **Checkpoint sweep through the recovery probe** — test the overtraining hypothesis (recovery may peak early).
4. **UMAP/qualitative visualization** — real vs ours_cond vs ours_null vs vanilla, colored by arm/label (spectrum + ESM embeddings).
5. **Benchmark survey** — function-conditional / multi-modal PLM evaluation protocols beyond CFP-Gen (ProGen-family conditional evals, ESM3 function-annotation evals, ZymCTRL EC evals, CAFA).
6. **Combined metric comparison** — structural (designability/scTM) + sequence (MMD/MRR) + functional (recovery) per arm, per w.
7. **Training changes gated on 1–3:** LoRA on attention+FFN; higher CFG dropout; checkpoint selection by function-recovery probe; validation callback (loss-delta proxy + qualitative table) for all future runs.

---

## 2026-09-15 — Weekly progress meeting

**Context at meeting time:** ConditionalDPLM2 (frozen DPLM-2 650M + ProCALM-style parallel adapters + CFP-Gen-style GO/IPR annotation embedder) training on Meluxina: 48K+/100K steps, val/loss 2.99 → ~2.02, aatype acc 0.10→0.114, struct acc 0→0.027, wandb run `fkokcvtq`. Eval stack not yet built; CFG sampling not yet implemented. Design/docs: [conditional_dplm2_progress_report.md](../reports/conditional_dplm2_progress_report.md), [conditional_dplm2_technical_reference.md](../reports/conditional_dplm2_technical_reference.md), [cfpgen_dplm2_meeting_synthesis.md](cfpgen_dplm2_meeting_synthesis.md).

### Feedback — Important

1. **Metrics & loss decomposition.** Explain what the existing metrics mean, how the loss decomposes, and what that means according to the DPLM-2 paper — and what was added for the function conditioning.
2. **Function-modality evaluation.** Evaluation purely for function-modality performance; curves for function during training/validation.
3. **Training/inference diagram.** A diagram for how training is done, what changes were implemented in training/validation loss for the adapters, and how inference is done with function conditioning.
4. **Function evaluation vs baseline.** Function evaluation curves and comparison with the vanilla pretrained-model baseline.
5. **Sequence/structure degradation check.** Previous existing (sequence & structure) metrics and how the function-conditional trained model compares to the pre-trained DPLM-2 baseline — does performance degrade?
6. **Qualitative logging.** Visualization / W&B logging of representative qualitative examples during training/validation.

### Feedback — Future steps

- CFG / classifier guidance.
- Scaling up data; resolve the problem of importing foreign datasets into the DPLM-2 struct tokenizer; perhaps use ESMFold2 [as stated; likely meaning an ESM-family structure tokenizer] for the structure modality + all available protein function data (SwissProt, InterPro, EC).

### Point-by-point resolutions

1. **Metrics & loss decomposition** — Deliverable format: **Markdown + Mermaid in the repo** (no slides/PDF). Content: extend [../reports/conditional_dplm2_technical_reference.md](../reports/conditional_dplm2_technical_reference.md) with a loss-decomposition section — DPLM-2's per-modality weighted cross-entropy (struct + aa, timestep weighting λ(t)), the metric glossary (val/loss, aatype/struct losses, index accuracies, scTM/designability…), and our exact delta (no loss changes; adapter/embedder parameters only).
2. **Function-modality evaluation design** — **Loss-delta proxy (every validation: same batches under null vs real conditions → condition-sensitivity curve) + periodic recovery probe (every ~5K steps: generate ~32 seqs for ~8 fixed prompts) + port established GO/IPR metrics** (CAFA-standard Fmax/Smin via the CAFA-evaluator tool; CFP-Gen-style set-match F1/AUPR + MRR/MMD). Research summary recorded below.
3. **Training/inference diagram** — **Mermaid diagrams in markdown**, covering: adapter training path, loss routing (identical to vanilla), validation path, and function-conditioned inference (stash → adapters at every denoising step).
4. **Function evaluation vs baseline** — **InterProScan for IPR recovery + DeepGO-SE for GO scoring under the CAFA-evaluator Fmax/Smin protocol**. Baselines: unconditional DPLM-2 generation + our model with null conditions + positive control (real test-seq sequences through the same predictors). **Version decision (2026-09-23):** all arms scored with our single InterProScan install **5.78-109.0** (InterPro release 109); CFP-Gen used **5.69-101.0** (InterPro 101) + Java 11, hardcoded in their `eval_ipr.py:49`. Arm-to-arm comparison is internally consistent (same predictor for every arm); absolute comparability with CFP-Gen's published table carries an InterPro-101→109 caveat. Post-run check planned: fraction of prompted IPR accessions that appear in real-arm IPS output (coverage — retired/merged accessions would systematically dip recall in all arms equally).
5. **Sequence/structure degradation check** — Scope: **co-generation designability+diversity (identical-settings re-run: pretrained DPLM-2 vs our ckpt with null conditions) + forward folding on CAMEO 2022 + paper-numbers comparison**. No inverse folding for now.
6. **Qualitative W&B logging** — **Sequence table every ~2000 steps + structural metrics**: 12 generations per occurrence (4 GO-prompt / 4 IPR-prompt / 4 null) alongside 1–2 real reference sequences, plus PDB decode with per-sample pLDDT + scTM logged in the table.

### Research summary (GO/IPR metrics, 2026-09-15)

- **CAFA-standard metrics** are the established GO-evaluation protocol: **Fmax** (protein-centric max F1 over precision-recall thresholds) and **Smin** (semantic distance, weights errors by GO-ontology distance). Source: [CAFA3 report (Zhou et al. 2019)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6864930/).
- **Portable implementation**: [CAFA-evaluator (BioComputingUP)](https://github.com/BioComputingUP/CAFA-evaluator) — open-source Python, handles GO-hierarchy propagation, computes Fmax/Smin/precision-recall curves. Requires *per-term probabilities* from the predictor (threshold-free).
- **DeepGO-SE** ([Nature Machine Intelligence 2024](https://www.nature.com/articles/s42256-024-00795-w); code: [bio-ontology-research-group/deepgo2](https://github.com/bio-ontology-research-group/deepgo2)) — protein-LLM embeddings + approximate semantic reasoning over GO; reports per-branch (MFO/BPO/CCO) per-term probabilities (e.g. MFO Fmax 0.386, beating NetGO3 / DeepGOPlus / TALE baselines). Emits the per-term probabilities the CAFA-Fmax protocol needs. This is also the predictor CFP-Gen used for GO recovery.
- **CFP-Gen's own protocol** (for cross-paper comparability): predictor outputs vs prompted labels → micro/macro F1, AUPR, AUC + MRR/MMD.

### Action items

- [x] Implement CFG sampling in `generate()` — `w=1` must reproduce conditional output, `w=0` the null output.
- [ ] Implement validation callback: null-vs-conditional loss-delta proxy + qualitative table (12 gens + references + PDB/pLDDT/scTM). **Decision: stop & resume from `last.ckpt` once landed** (~10 min downtime) so the remaining ~50K steps carry function curves.
- [x] Eval stack: generation driver over held-out labels (CFP-Gen `test.pkl`) + null baseline + positive control; InterProScan driver (IPR set-match); DeepGO-SE install + CAFA-evaluator Fmax/Smin port; MRR/MMD port from `cfpgen/eval`.
- [ ] Degradation check: co-generation designability identical-settings (pretrained DPLM-2 vs our ckpt, null conditions) + CAMEO 2022 forward folding + paper-numbers comparison table.
- [ ] Docs: loss-decomposition + Mermaid training/inference diagrams in the technical reference.
