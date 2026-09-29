# Weekly Progress — Evaluation of the Function-Conditioned Model

**Week of 2026-09-22 → 2026-09-29**
Companion docs: [last week's report](weekly_meeting_2026-09-22.md) (methodology, metric primer, dataset merging) · [technical reference](conditional_dplm2_technical_reference.md) · [meeting log](../plans/meetings.md)

---

## 1. Headline

The first complete **function-recovery evaluation of the trained Conditional-DPLM-2** ran end-to-end this week: **7 arms** (ours_cond at CFG w=1/2/4/8, ours_null, vanilla pretrained DPLM-2, real positive control), ~256 sequences per arm, InterProScan 5.78 + DeepGO-SE (GPU) + GO-DAG-expanded scoring on all of them. Four findings:

1. **Positive:** a real but weak *conditioning signal* exists — MRR rises ~40% over both controls (0.110 vs 0.078), and CFG amplifies it to **0.128 at w=2**.
2. **Positive (new, final scoring):** the **first non-zero function recovery** — GO Fmax rises with guidance (0.014 → 0.037 across w=1→4), crossing the null control at w≥4, with GO recall climbing monotonically (0.005 → 0.095). Recovered terms are GO ancestors/descendants of the prompts — weak, but a real dose-response on the *global* function axis.
3. **Negative:** **zero IPR domain recovery** across all 1,536 generated sequences — against a **0.978 recovery ceiling on real sequences**. The GO-vs-IPR contrast partially revives the granularity hypothesis: global labels are (weakly) reachable, local domain placement is not.
4. **Negative:** a quantified **sequence-realism regression** from adapter training — our outputs sit 0.38–0.89 MMD from the natural distribution, vs 0.16 for vanilla DPLM-2. Vanilla also still beats every conditioned arm on GO Fmax (0.059 vs ≤0.037).

---

## 2. What was done this week

- **InterProScan installed and validated.** EBI's `ftp.ebi.ac.uk` refused connections from both our network and datacenter ranges (breaking the IPS 6/nextflow route and direct downloads). Workaround: the **5.78-109.0 GitHub release tarball** (6.6 GB, bundles all member databases), validated on the bundled test proteins, then on all eval FASTAs.
- **DeepGO-SE data recovered + run on all 7 arms (GPU).** The only data source (`deepgo.cbrc.kaust.edu.sa/data/deepgo2/data.tar.gz`) is a dead link — [issue filed upstream]. Workaround: the full dataset recovered via the **web archive**; the official docker image (`coolmaksat/deepgose`) as runner; ESM-2 3B checkpoint persisted to a host cache mount (no re-download per run); ~1 min/arm on GPU (`-d cuda`). `go.obo` fetched for GO-DAG-expanded scoring.
- **InterProScan zero-result audit (scorer-free).** Exact-zero IPR in all conditional arms looked suspicious enough to rule out a silent bug (`diagnose_ips.py`): join integrity, hand-computed raw overlap, and a composition-preserved shuffled-output control. Verdict: zeros are genuine; our arms' only annotatable mode is a low-complexity repeat artifact (§3).
- **Per-family MMD implemented** (feedback 1): condition-aware realism — generated-for-L vs real-for-L with a cross-family baseline; unit-checked; run on all 7 arms (§3).
- **CFG dose-response sweep executed** on the trained checkpoint: w ∈ {1, 2, 4, 8}, ~256 sequences each, on identical held-out prompts.
- **`cfpgen` comparison arm implemented** (standalone driver running CFP-Gen 650M in its own repo/env on our exact prompts — no package integration needed).
- **Eval throughput:** generation batching with a `--batch-size` knob (~5–8× faster).

---

## 3. Results

Setup: 32 held-out GO molecular-function labels × 8 real sequences each → prompts are the proteins' own GO+IPR annotations; ~256 conditioned generations per arm (len 256, 256 denoising steps). GO ground truth is ancestor-expanded over the GO DAG (go.obo); GO Fmax is the CAFA threshold-free max-F1 over DeepGO-SE per-term probabilities.

| Arm | GO Fmax ↑ | GO recall micro | IPR F1 micro | IPR AUPR micro | MRR ↑ | MMD-linear vs real ↓ | MMD-gaussian ↓ |
|---|---|---|---|---|---|---|---|
| ours_cond (w=1) | 0.0139 | 0.0052 | 0.0000 | 0.0292 | **0.1053** | 0.8967 | 0.5694 |
| ours_cond w=2 | 0.0277 | 0.0420 | 0.0000 | 0.0272 | **0.1277** | 0.4921 | 0.3017 |
| ours_cond w=4 | **0.0369** | 0.0745 | 0.0000 | 0.0283 | 0.0865 | 0.3757 | 0.2241 |
| ours_cond w=8 | 0.0337 | 0.0953 | 0.0000 | 0.0302 | 0.0860 | 0.4069 | 0.2439 |
| ours_null | 0.0310 | 0.0022 | 0.0000 | 0.0314 | 0.0816 | 0.8544 | 0.5398 |
| vanilla (pretrained 650M) | 0.0591 | 0.0966 | 0.0037 | 0.0074 | 0.0745 | **0.1601** | **0.0938** |
| **real (positive control)** | **0.2228** | 0.3043 | **0.9782** | **0.9574** | 0.8099* | 0 | 0 |

\* real self-recovery MRR = 0.81 (not 1.0): the sample-size noise floor of the metric at 8 seqs/label — this recalibrates every MRR reading (the ceiling is ~0.81, not 1.0). Real GO Fmax 0.2228 is the natural-sequence ceiling of this predictor+GT on this set (DeepGO-SE's published MFO Fmax on natural proteins: 0.386).

*Caveat:* the sweep arms (w=2/4/8) were generated on the pre-dedup prompt set (256 seqs) vs 250 for the main arms — within-sweep trends are internally consistent; sub-0.01 cross-arm deltas should not be over-read.

### What each number says

- **GO Fmax (function recovery, global axis):** the only non-zero recovery channel. Rises with guidance 0.014 → 0.028 → 0.037 (peak w=4), crossing the null-conditioned control (0.031) at w≥4; recall micro climbs monotonically 0.005 → 0.095. Recovered terms are DAG-neighbours of the prompts (MF-term *exact* overlap stays 0–1; MF diversity 32 → 62 terms under guidance vs 243 natural / 369 vanilla) — weak, graded, and directionally correct, but still below vanilla unconditional (0.059) and 6× below the natural ceiling (0.223).
- **MRR (label-conditionality, sequence modality):** controls sit near the random floor for 59 reference groups (~0.068); ours_cond rises above it at every w, peaking at **w=2 (+64% over controls)**. Per-label ranks confirm structure: some labels rank 1st–4th of 59, others sit near the bottom — the conditioning signal is real but covers a subset of labels.
- **IPR set-match (function recovery, local axis):** **zero recovery in every generated arm** — no prompted domain is ever annotated by InterProScan. The positive control scores 0.978 on the *same* prompts, so the pipeline, ID handling, and metric are correct: the zeros are a property of the generated sequences. Read against the GO column, this is the granularity signal: global GO labels get graded partial recovery under a global conditioning pathway; local IPR domain placement gets none.
- **MMD (distributional realism):** adapter training moved generation far from the natural composition (0.89 linear-MMD at w=1 vs vanilla's 0.16). CFG *improves* naturalness monotonically-ish (0.49 at w=2, 0.38 at w=4) — the unconditioned component of the CFG mix pulls outputs back toward generic protein-like composition.

### Per-family MMD (condition-aware) — new metric, feedback point 1

Arm-level MMD is condition-blind; the per-family variant subsets the ground truth by the prompted family: `delta = MMD(gen-for-L, real-L')̄ − MMD(gen-for-L, real-for-L)` — positive delta means the generated group sits closer to *its own* family than to a random other family.

| Arm | delta ↑ (linear) | frac own-closest ↑ | | Arm | delta ↑ | frac ↑ |
|---|---|---|---|---|---|---|
| ours_cond w=1 | +0.0045 | 0.53 | | ours_null | −0.0009 | 0.54 |
| ours_cond **w=2** | **+0.0185** | **0.71** | | vanilla | −0.0011 | 0.51 |
| ours_cond w=4 | +0.0122 | 0.66 | | real (calibration) | +0.8006 | 1.00 |
| ours_cond w=8 | +0.0066 | 0.61 | | (real: own = 0 by construction) | | |

Controls sit exactly at chance; ours_cond separates above it and **peaks at w=2 — the same optimum as MRR**, from a completely different construction. The conditioning carries genuine label-specific composition information; it is far too weak to produce annotatable function. (MRR itself needs no analogous adjustment — it is already label-grouped; per-family MMD adds the distance-level analog plus the explicit cross-family baseline.)

### UMAP in shared ESM-2 space (feedback 5)

One UMAP fit on all 1,768 mean-pooled ESM-2 3B embeddings (L2-normalized, the `aatype_esm.pkl` files DeepGO-SE leaves per arm); separation quantified in the **raw** embedding space, not UMAP. Script: `scripts/eval_function/plot_umap.py`; figures: `figs/umap_{by_arm,centroids,labels}.png` (copies in `reports/figures/`).

![UMAP by arm](figures/umap_by_arm.png)

| Arm | \|centroid − real\| ↓ (raw ESM) | intra-arm spread |
|---|---|---|
| vanilla | **0.086** | 0.223 |
| ours_cond w=2 | 0.319 | 0.278 |
| ours_cond w=4 | 0.328 | 0.297 |
| ours_cond w=1 | 0.366 | 0.181 |
| ours_null | 0.367 | 0.178 |
| ours_cond w=8 | 0.402 | 0.253 |
| real (reference) | 0 | 0.181 |

![Centroids](figures/umap_centroids.png)

1. **Adapter training displaced the entire output distribution.** Vanilla DPLM-2 samples sit essentially *on* the real manifold (d=0.086); every adapter-trained arm — including the null-conditioned one — occupies a disjoint region ~4× farther away (d=0.32–0.40). The displacement is a property of the trained weights, not of conditioning.
2. **CFG does not approach the real manifold in ESM space.** The MMD improvement under guidance (0.90→0.38) is composition-level (k-mer space); in ESM space the centroids stay at 0.32–0.40 with no monotone approach (w=2 closest, w=8 farthest). Guidance makes outputs *compositionally* more natural while they remain *semantically* off-manifold — which is exactly what the IPS audit saw: sequences that look protein-like in composition but annotate only to a low-complexity repeat.
3. **The collapse, seen structurally.** w=1/null intra-arm spread (0.178) is *tighter than the real arm's own spread* (0.181) — a single narrow mode; CFG restores spread (0.25–0.30), matching the MF-diversity counts (32→62 terms).
4. **Per-label conditioning is qualitatively visible.** In the per-label panels, prompted points for the highest-Δ labels (NAD binding, cobalamin synthase, translation-initiation-factor activity) clump within the ours region, separated from other labels — the per-family MMD signal, visible by eye. The clump lives inside the off-manifold region: label-specific yet unnatural.

![Per-label panels](figures/umap_labels.png)

5. The natural proteins that embed closest to the adapter-trained region are the most Ala/Gly-rich ones available (A+G 23–31% vs real-arm mean 16.8%, max 31.5%) — the ours region is the low-complexity corner of protein space, consistent with the antifreeze-repeat IPS artifact.

### InterProScan zero-result audit — zeros are genuine, not a bug

| Check | ours_cond | ours_null | vanilla | real |
|---|---|---|---|---|
| stale/unmatched IDs | 0 | 0 | 0 | 0 |
| raw TP (hand-computed overlap) | **0** | **0** | 4 | 1,144 |
| label-groups with ≥1 recovery | 0/59 | 0/59 | 10/59 | 59/59 |

The identical code path yields 0.978 on real — no join/ID/conversion bug exists. What IPS *does* find in our arms: **52% of all rows are one signature — PRINTS "Type I antifreeze protein repeat" (IPR000104)**, a small Ala/Gly-rich low-complexity match (vs a broad Gene3D/Pfam/SUPERFAMILY spread in vanilla/real). The decisive control — IPS on composition-preserved *shuffled* sequences:

| IPS run | rows | proteins | unique IPR | top signature |
|---|---|---|---|---|
| ours_cond (as generated) | 851 | 221 | 19 | antifreeze repeat (444) |
| ours_cond **shuffled** | 870 | 225 | 20 | antifreeze repeat (408) |
| real (natural) | 2,907 | 250 | 147 | Gene3D domains |
| real **shuffled** | 75 | 41 | **0** | MobiDBLite disorder only |

Shuffling our outputs *changes nothing* (same volume, same accessions, same top hit): every annotation they attract survives destroying sequence order — zero sequence-specific domain content, pure composition artifact. Shuffling natural sequences *destroys* annotation (147 accessions → 0), confirming IPS output is genuinely sequence-sensitive where real domains exist. Incidentally, the shuffled-ours IPS run took ~25 min in the PANTHER stage vs seconds for natural sequences — the degenerate composition matches thousands of profile HMMs. Also flagged: `ipr_aupr_*` is uninterpretable for set-valued predictor output (nonzero micro-AP baseline even at exactly TP=0) — read only P/R/F1 for IPS.
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

1. ~~Finish the current eval pass~~ — **done**: DeepGO-SE ran on all 7 arms (GPU, ~1 min/arm, persisted ESM-2 3B cache), scorer applied with GO-DAG-expanded GT → complete table in §3. Remaining in the eval queue: `cfpgen` comparison arm, per-family MMD, UMAP visualization.
2. **Training change #1 — LoRA on attention+FFN** (the pre-planned capacity escape hatch), trained on the same 45K set, evaluated with the identical protocol. Success criterion: IPR recovery > 0 at w=1–2 with MMD not worse than vanilla.
3. **Training change #2 — data scaling:** tokenize the ~49K missing CFP-Gen proteins with the corrected v2 pipeline (validated: lengths now match DPLM-2 exactly; ~10% single-bit LFQ noise accepted) → LoRA on the combined ~82K.
4. ~~DeepGO-SE scoring~~ — **done** (GO Fmax/set-match in §3). Smin via CAFA-evaluator still deferred (needs information-content weights over the GO corpus).
5. **Docs:** loss-decomposition + Mermaid training/inference diagrams; validation callback (loss-delta proxy + qualitative examples) for future runs.

---

## Appendix: pipeline state

| Piece | State |
|---|---|
| Trained ckpt (100K steps, val/loss 1.95) | ✅ local + Meluxina |
| CFG sampling (w sweep) | ✅ implemented + tested |
| InterProScan 5.78-109.0 | ✅ local install, IPS runs verified |
| DeepGO-SE data | ✅ recovered via web archive |
| DeepGO-SE predict run (all 7 arms, GPU) | ✅ done (~1 min/arm; ESM-2 3B cache persisted to host) |
| GO scoring with DAG-expanded GT (obonet) | ✅ |
| 4-arm generation driver (batched, deduped, sanitized) | ✅ |
| Scorer (MRR/MMD/set-match/Fmax) | ✅ |
| `cfpgen` comparison arm driver | ✅ written; run pending |
| LoRA training run | ⚪ next (doubles as the granularity-layer test) |
