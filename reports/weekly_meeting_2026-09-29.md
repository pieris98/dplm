# Weekly Progress — Evaluation of the Function-Conditioned Model

**Week of 2026-09-22 → 2026-09-29**
Companion docs: [last week's report](weekly_meeting_2026-09-22.md) (methodology, metric primer, dataset merging) · [technical reference](conditional_dplm2_technical_reference.md) · [meeting log](../plans/meetings.md)

---

## 1. Headline

The first complete **function-recovery evaluation of the trained Conditional-DPLM-2** ran end-to-end this week: **7 arms** (ours_cond at CFG w=1/2/4/8, ours_null, vanilla pretrained DPLM-2, real positive control), 256 sequences per arm, InterProScan 5.78 + scoring on all of them. Three findings:

1. **Positive:** a real but weak *conditioning signal* exists — MRR rises ~40% over both controls (0.110 vs 0.078), and CFG amplifies it to **0.128 at w=2**.
2. **Negative:** **zero IPR domain recovery** across all 1,280 generated sequences — against a **0.978 recovery ceiling on real sequences**. The generated proteins are too compositionally degenerate for any domain annotation.
3. **Negative:** a quantified **sequence-realism regression** from adapter training — our outputs sit 0.38–0.89 MMD from the natural distribution, vs 0.16 for vanilla DPLM-2.

The DeepGO-SE GO columns are the last missing piece — its broken data link was worked around this week (web archive), and its first scoring pass is running now.

---

## 2. What was done this week

- **InterProScan installed and validated.** EBI's `ftp.ebi.ac.uk` refused connections from both our network and datacenter ranges (breaking the IPS 6/nextflow route and direct downloads). Workaround: the **5.78-109.0 GitHub release tarball** (6.6 GB, bundles all member databases), validated on the bundled test proteins, then on all eval FASTAs.
- **DeepGO-SE data recovered.** The only data source (`deepgo.cbrc.kaust.edu.sa/data/deepgo2/data.tar.gz`) is a dead link — [issue filed upstream]. Workaround: the full dataset recovered via the **web archive**; the official docker image (`coolmaksat/deepgose`, bundles the predict env) probed as the runner. `go.obo` fetched for GO-DAG logic.
- **CFG dose-response sweep executed** on the trained checkpoint: w ∈ {1, 2, 4, 8}, 256 sequences each, on identical held-out prompts.
- **Eval pipeline hardened** through five real failures (section 4) — each fixed permanently in the committed scripts.
- **`cfpgen` comparison arm implemented** (standalone driver running CFP-Gen 650M in its own repo/env on our exact prompts — no package integration needed).
- **Eval throughput:** generation batching made label-crossing with a `--batch-size` knob (~5–8× faster).

---

## 3. Results

Setup: 32 held-out GO molecular-function labels × 8 real sequences each → prompts are the proteins' own GO+IPR annotations; 256 conditioned generations per arm (len 256, 256 denoising steps).

| Arm | IPR F1 micro | IPR F1 macro | IPR AUPR micro | MRR ↑ | MMD-linear vs real ↓ | MMD-gaussian ↓ |
|---|---|---|---|---|---|---|
| ours_cond (w=1) | 0.0000 | 0.0000 | 0.0294 | **0.1104** | 0.8874 | 0.5633 |
| ours_cond w=2 | 0.0000 | 0.0000 | 0.0272 | **0.1277** | 0.4921 | 0.3017 |
| ours_cond w=4 | 0.0000 | 0.0000 | 0.0283 | 0.0865 | 0.3757 | 0.2241 |
| ours_cond w=8 | 0.0000 | 0.0000 | 0.0302 | 0.0860 | 0.4069 | 0.2439 |
| ours_null | 0.0000 | 0.0000 | 0.0312 | 0.0778 | 0.8660 | 0.5484 |
| vanilla (pretrained 650M) | 0.0072 | 0.0005 | 0.0066 | 0.0787 | **0.1601** | **0.0983** |
| **real (positive control)** | **0.9782** | **0.9262** | **0.9574** | 0.8099* | 0 | 0 |

\* real self-recovery MRR = 0.81 (not 1.0): the sample-size noise floor of the metric at 8 seqs/label — this recalibrates every MRR reading (the ceiling is ~0.81, not 1.0).

*(Table from the current eval set; a final regeneration on deduplicated FASTAs is in flight — see §4, fix #5 — and numbers may shift by small amounts.)*

### What each number says

- **MRR (label-conditionality, sequence modality):** controls sit near the random floor for 59 reference groups (~0.068); ours_cond rises above it at every w, peaking at **w=2 (+64% over controls)**. Per-label ranks confirm structure: some labels rank 1st–4th of 59, others sit near the bottom — the conditioning signal is real but covers a subset of labels.
- **IPR set-match (function recovery via predictor):** **zero recovery in every generated arm** — no prompted domain is ever annotated by InterProScan. The positive control scores 0.978 on the *same* prompts, so the pipeline, ID handling, and metric are correct: the zeros are a property of the generated sequences.
- **MMD (distributional realism):** adapter training moved generation far from the natural composition (0.89 linear-MMD at w=1 vs vanilla's 0.16). CFG *improves* naturalness monotonically-ish (0.49 at w=2, 0.38 at w=4) — the unconditioned component of the CFG mix pulls outputs back toward generic protein-like composition.
- **AUPR nuance:** ours arms (~0.03) sit slightly above vanilla (0.007) and even above… not above the real control's 0.957 — the tiny positive AUPR suggests weak ranking signal for prompted terms, far below annotation thresholds.

---

## 4. Issues & hurdles (each: symptom → root cause → fix → status)

| # | Symptom | Root cause | Fix | Status |
|---|---|---|---|---|
| 1 | EBI FTP (`ftp.ebi.ac.uk`) refused all connections — blocked IPS 6/nextflow and tarball downloads | EBI-side host refusing certain IP ranges (EBI main site reachable; FTP host not) | IPS 5.78 tarball via **GitHub releases** (bundles all DBs) | ✅ resolved |
| 2 | DeepGO-SE `data.tar.gz` dead link — no data, no weights | KAUST server link rotted; it is the README's only source | [Issue filed upstream]; full dataset recovered via **web archive**; docker image `coolmaksat/deepgose` as runner | ✅ resolved |
| 3 | Scorer crashed / positive control scored F1=0 | **Ground-truth conversion bug**: prompt label *integers* compared against predictor *accessions* (`IPR000276`) — guaranteed zero overlap | GT sets converted through the manifest's inverse label maps; **positive control now scores 0.978**, validating the metric | ✅ fixed & validated |
| 4 | InterProScan rejected the `ours_null` arm: `'sequence' is not an amino acid sequence` | The null-conditioned model samples **gap tokens** (`-` id 30, `.` id 29) from the DPLM-2 vocab; IPS's validator rejects non-standard residues | Sequences sanitized at FASTA-write time (gaps removed, other non-standard → X) in both generation drivers | ✅ fixed |
| 5 | DeepGO-SE crashed: `Found duplicate sequence labels` | Proteins selected via multiple labels were prompted **once per label** → duplicate FASTA headers (also double-counted in every metric) | Prompt proteins **deduplicated by UniProt ID** — one generation per unique protein; real arm deduped likewise | ✅ fixed |
| 6 | Re-running the driver for one arm wiped other arms' records from `manifest.json` | Manifest rewritten from scratch per run | Manifest now **merged per-arm** | ✅ fixed |
| 7 | GPU ~5% utilized during eval generation | Per-label batching (8 seqs) — the denoising loop is sequential, so throughput scales with batch size | Label-crossing flat batching with `--batch-size` (default 64) — ~5–8× faster | ✅ fixed |

---

## 5. Why we did not run training again this week

A deliberate call, for four reasons:

1. **No trustworthy eval existed until this week.** Any checkpoint from a changed training recipe would have been unmeasurable: the scorer had a metric-invalidating bug (fix #3), one predictor was missing entirely, and the eval protocol itself wasn't proven (fixes #4–#7). Running new training first would have produced checkpoints we cannot rank against each other or against the pretrained baseline.
2. **One variable at a time.** The first eval now gives a clean baseline: same prompts, same predictors, same metrics across ours_cond / ours_null / vanilla / real. The planned training changes (LoRA, more data, sampling tweaks) will each be measured against exactly this protocol — changing training before the ruler existed would confound every subsequent comparison.
3. **Compute went to the evaluation itself:** the 7-arm run is 1,792 generated sequences × 256 denoising steps (plus CFG double-passes), then four InterProScan scans — the numbers in §3 consumed the available GPU hours productively.
4. **The diagnosis is now evidence-based, so the training changes are now *specified* rather than guessed:** the aatype-loss plateau (step ~2K) plus zero recovery with a live MRR signal points at adapter *capacity* as the first lever (LoRA on attention+FFN), with data scale second (45K → 82–104K via the tokenization path) and CFG-dynamics third.

In short: the week bought the measuring stick, and the measuring stick immediately paid for itself by (a) validating itself via the positive control, (b) catching a scorer bug, and (c) producing the baseline every future run will be judged against.

---

## 6. Next steps (priority order)

1. **Finish the current eval pass** (in flight): regenerate all arms on deduplicated FASTAs → re-run IPS (~2 min/arm) → DeepGO-SE on all arms (data recovered; docker runner) → scorer → complete table incl. GO Fmax/set-match and the `cfpgen` comparison arm.
2. **Training change #1 — LoRA on attention+FFN** (the pre-planned capacity escape hatch), trained on the same 45K set, evaluated with the identical protocol. Success criterion: IPR recovery > 0 at w=1–2 with MMD not worse than vanilla.
3. **Training change #2 — data scaling:** tokenize the ~49K missing CFP-Gen proteins with the corrected v2 pipeline (validated: lengths now match DPLM-2 exactly; ~10% single-bit LFQ noise accepted) → LoRA on the combined ~82K.
4. **DeepGO-SE scoring** of all arms once prediction completes → GO Fmax/set-match columns + Smin via CAFA-evaluator.
5. **Docs:** loss-decomposition + Mermaid training/inference diagrams; validation callback (loss-delta proxy + qualitative examples) for future runs.

---

## Appendix: pipeline state

| Piece | State |
|---|---|
| Trained ckpt (100K steps, val/loss 1.95) | ✅ local + Meluxina |
| CFG sampling (w sweep) | ✅ implemented + tested |
| InterProScan 5.78-109.0 | ✅ local install, IPS runs verified |
| DeepGO-SE data | ✅ recovered via web archive |
| 4-arm generation driver (batched, deduped, sanitized) | ✅ |
| Scorer (MRR/MMD/set-match/Fmax) | ✅ |
| `cfpgen` comparison arm driver | ✅ written; run pending |
| DeepGO-SE predict run on eval FASTAs | 🟡 in flight |
| LoRA training run | ⚪ next |
