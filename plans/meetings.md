# Meeting Notes

Running log of weekly progress meetings. Newest entry first.

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

- [ ] Implement CFG sampling in `generate()` — `w=1` must reproduce conditional output, `w=0` the null output.
- [ ] Implement validation callback: null-vs-conditional loss-delta proxy + qualitative table (12 gens + references + PDB/pLDDT/scTM). **Decision: stop & resume from `last.ckpt` once landed** (~10 min downtime) so the remaining ~50K steps carry function curves.
- [ ] Eval stack: generation driver over held-out labels (CFP-Gen `test.pkl`) + null baseline + positive control; InterProScan driver (IPR set-match); DeepGO-SE install + CAFA-evaluator Fmax/Smin port; MRR/MMD port from `cfpgen/eval`.
- [ ] Degradation check: co-generation designability identical-settings (pretrained DPLM-2 vs our ckpt, null conditions) + CAMEO 2022 forward folding + paper-numbers comparison table.
- [ ] Docs: loss-decomposition + Mermaid training/inference diagrams in the technical reference.
