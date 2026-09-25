# Weekly Progress — Function-Conditioned DPLM-2

**Week of 2026-09-15 → 2026-09-22**
Companion docs: [technical reference](conditional_dplm2_technical_reference.md) · [progress report](conditional_dplm2_progress_report.md) · [CFP-Gen vs DPLM-2 synthesis](cfpgen_dplm2_meeting_synthesis.md) · [meeting log](../plans/meetings.md)

---

## 1. Recap: first Conditional-DPLM-2 checkpoint trained and evaluated

The full function-conditioning run **completed** on Meluxina: 100,000 steps, 4×A100 DDP bf16, final **val/loss 1.95** (from 2.99 at step 0). The trained checkpoint (19.6M trainable adapter/embedder parameters on the frozen 650M base) is downloaded and wired into the evaluation pipeline. This week's engineering went entirely into making that checkpoint *measurable*: classifier-free-guidance (CFG) sampling and the complete function-recovery evaluation stack. **The first evaluation has now run end-to-end — findings in §7.**

---

## 2. What was implemented this week

### 2.1 CFG sampling (classifier-free guidance)

**What:** at every denoising step, the model now runs a second pass with the learned *null-condition* embeddings and combines:

```
logits = uncond + w · (cond − uncond)
```

* `w = 1` → vanilla conditional sampling (single pass, shortcut)
* `w = 0` → null-conditioned sampling
* `w > 1` → extrapolates away from the base prior toward the requested labels

**Why it matters:** likelihood training doesn't optimize conditional *distinguishability* — the frozen base predicts amino acids well from structure context alone, so "ignoring" the condition costs little training loss (the classic condition-ignoring failure mode). The CFG term amplifies the labels' marginal evidence, compounding across ~256 denoising steps. The dropout (p=0.1) during training was the down-payment; this is the payoff.

**Verification (7/7 checks pass):** w=0 reproduces the null pass *exactly*; w=1 reproduces the conditional pass *exactly*; w=2 equals the affine combination *exactly*; guidance distance grows monotonically in w; end-to-end generation at w=1 vs w=4 differs at fixed seed. Sweep verified on the trained checkpoint (`go:5`, w=1/2/4).

### 2.2 Function-recovery evaluation stack

| Component | File | Status |
|---|---|---|
| Metrics port (spectrum k-mer embedding, MMD linear/Gaussian, MRR) | `src/byprot/eval/function.py` | ✅ 12/12 sanity checks |
| Multi-label set-match (micro/macro F1, AUPR, AUC) | same | ✅ |
| CAFA protein-centric **Fmax** (native) | same | ✅ |
| InterProScan runner + TSV parser | `src/byprot/eval/predictors.py` | ✅ parser tested |
| DeepGO-SE TSV parser + subprocess hook | same | ✅ parser tested |
| **4-arm generation driver** | `scripts/eval_function/generate_eval_set.py` | ✅ end-to-end smoke |
| Scorer → `results.json`/`results.md` | `scripts/eval_function/score_function_eval.py` | ✅ smoke |

### 2.3 The 4-arm evaluation protocol

Drawn from CFP-Gen's held-out set (`test.pkl`, 8,309 proteins never seen in training, 30 per label):

| Arm | Model | Condition | Role |
|---|---|---|---|
| `ours_cond` | trained ckpt | the protein's own GO+IPR labels | **the result** |
| `ours_null` | trained ckpt | null | isolates the conditioning effect (strongest control) |
| `vanilla` | pretrained DPLM-2 650M | unconditional | does adapter training degrade the base? (baseline) |
| `real` | — | — | positive control = predictor ceiling on natural sequences |

Label selection prefers GO molecular-function labels with ≥K real held-out sequences; prompts are the proteins' *own* annotations (CFP-Gen protocol). Everything lands in a manifest + per-arm FASTAs, scored to a single comparison table.

---

## 3. Metric choices: sources, standing, alternatives

### InterProScan (IPR recovery) — chosen

- **Source:** EMBL-EBI ([Jones et al. 2014; Blum et al. 2021, current](https://doi.org/10.1093/bioinformatics/btu031)). The de-facto standard for conserved-domain annotation; scans signatures from ~20 member databases.
- **Usage:** generated sequences → InterProScan `-goterms` → predicted IPR accessions + GO terms → set-match vs prompted labels (micro/macro F1, AUPR).
- **Precedent:** this is exactly CFP-Gen's IPR protocol, so numbers are cross-paper comparable.

### DeepGO-SE (GO recovery) — chosen

- **Source:** [Kulmanov et al., *Nature Machine Intelligence* 2024](https://www.nature.com/articles/s42256-024-00795-w); code [deepgo2](https://github.com/bio-ontology-research-group/deepgo2).
- **Standing:** state of the art among sequence-based GO predictors in its benchmark — protein-LLM embeddings + approximate semantic reasoning over the GO DAG. Reported **MFO Fmax 0.386**, ahead of NetGO3, DeepGOPlus, and TALE.
- **Why it was selected:** (a) it emits **per-term probabilities**, which the CAFA threshold-free metrics require (see below); (b) CFP-Gen used it for GO recovery, so `eval_go.py` parses its output natively; (c) it is a *learned* GO predictor, not a domain-mapping proxy.

### The CAFA protocol (Fmax / Smin) — the evaluation standard

- **Source:** the CAFA challenges, the community benchmark for protein function prediction ([Zhou et al. 2019, CAFA3 report](https://pmc.ncbi.nlm.nih.gov/articles/PMC6864930/), 600+ citations).
- **Fmax** — max F1 over per-term prediction thresholds (threshold-free, protein-centric). **Smin** — semantic distance weighting errors by GO-DAG distance.
- **Implementation:** native Fmax ported (validated); full protocol incl. Smin via the open-source [CAFA-evaluator (BioComputingUP)](https://github.com/BioComputingUP/CAFA-evaluator).
- **Key requirement:** per-term *probabilities* — the reason DeepGO-SE (not InterProScan) drives the GO metrics. InterProScan's GO terms come via InterPro2GO domain mapping (no scores) and serve as a free cross-check only.

### Alternatives considered

| Alternative | Status | Reason not chosen (now) |
|---|---|---|
| **NetGO 3.0** | baseline in DeepGO-SE's benchmark | web-server-first; harder to run at batch scale locally |
| **DeepGOPlus** | evaluated | simpler CNN+alignment, weaker than DeepGO-SE |
| **TALE** | evaluated | same benchmark family, below DeepGO-SE |
| **STRING** | noted | a protein–*interaction* network database — a source of functional *associations*, not a GO-term prediction standard; no established "STRING recovery" metric for conditional generation. Not comparable to the CAFA/DeepGO-SE line |
| CLEAN | deferred | EC-recovery predictor; our dataset has no EC labels yet |

### Distribution metrics (predictor-free, runnable today)

**MRR** and **MMD** (linear/Gaussian over k-mer spectrum embeddings) ported from CFP-Gen's `metrics/`. MRR asks: does a generated group for label *L* sit closest to the real sequences of label *L* (vs all other labels)? MMD asks: how far is the generated sequence distribution from the real one? These require **no external tool** and are already computed in the smoke run.

→ **Metric primer with per-metric explanations below (§4).**

---

## 4. Metric primer — what each metric measures

### 4.1 MRR (Mean Reciprocal Rank) — *sequence modality · label-conditionality*

- **Measures:** whether sequences generated *for* label L are recognizable as members of L's family. For each prompted label L, the mean k-mer spectrum embedding of its generated group is ranked — by MMD distance — among the mean embeddings of **all** reference label-groups; the reciprocal of L's rank is averaged over labels.
- **Modality / data:** amino-acid sequences only (no predictor). Inputs: generated FASTA + real held-out FASTA + per-sequence label sets from the manifest.
- **Range / reading:** 1.0 = every generated group is closest to its own real group; ~1/n_labels = chance. Higher is better.
- **Cannot tell you:** whether the sequences would actually *function* — it measures distributional proximity in k-mer space, not annotation fidelity.

### 4.2 MMD (Maximum Mean Discrepancy) — *sequence modality · distributional fidelity*

- **Measures:** the distance between the generated sequence distribution and the real distribution in k-mer spectrum space. Linear kernel: Euclidean distance between mean embeddings. Gaussian kernel: full kernel two-sample statistic with median-heuristic bandwidth — sensitive to higher-order composition differences.
- **Modality / data:** amino-acid sequences only; each generated arm vs the `real` arm.
- **Range / reading:** 0 = indistinguishable distributions; lower is better. Interpret *relative* to the real arm's self-MMD (the noise floor).
- **Caveat:** MMD ≈ 0 means "protein-like overall composition", not "the right protein" — it is condition-blind unless computed per-label.

### 4.3 IPR set-match (InterProScan recovery) — *function modality*

- **Measures:** whether generated sequences, when annotated by InterProScan, recover the IPR domains they were conditioned on. Per sequence: predicted IPR accessions (predictor output) vs the prompted IPR set → multi-label precision/recall/F1 (micro + macro), AUPR, AUC over the binarized label matrix.
- **Modality / data:** generated sequences → InterProScan (external predictor) → IPR accession sets; ground truth = the prompt labels from held-out annotations.
- **Range / reading:** F1 ∈ [0, 1], higher better. Micro weights frequent domains; macro treats rare domains equally — report both.
- **Caveat:** bounded by InterProScan's own recall on natural proteins — which is exactly why the `real` arm is scored identically: it defines the predictor ceiling (upper bound below 1.0).

### 4.4 GO set-match (DeepGO-SE recovery, thresholded) — *function modality*

- **Measures:** the same recovery idea for GO terms. DeepGO-SE assigns a probability to every GO term for each generated sequence; terms scoring ≥ 0.5 form the predicted set, compared against the prompted GO set.
- **Modality / data:** generated sequences → DeepGO-SE → per-term probability vector; ground truth = prompted GO molecular-function terms, **ancestor-expanded** over the GO DAG (go.obo) so that a semantically-near prediction (e.g. predicting a parent or child of the true term) is not penalized as a full miss.
- **Range / reading:** F1/AUPR as in 4.3. Threshold choice is arbitrary — which is why the threshold-free metric below is the headline GO number.

### 4.5 GO Fmax (CAFA protein-centric) — *function modality · threshold-free*

- **Measures:** the CAFA-standard maximum F1: sweep a score threshold across DeepGO-SE's per-term probabilities, compute per-protein precision/recall at each threshold (excluding proteins with neither prediction nor truth), average, and take the maximum over the sweep.
- **Modality / data:** DeepGO-SE probability matrix vs ancestor-expanded GO ground-truth matrix (sequences × terms).
- **Range / reading:** [0, 1], higher better. Calibration point: DeepGO-SE on *natural* sequences reports MFO Fmax ≈ 0.386 — our generated sequences should be read against that natural-protein ceiling, not against 1.0.
- **Why it's the headline GO metric:** threshold-free (no arbitrary cutoff), community-standard (CAFA), and sensitive to the *ranking* of terms, not just coverage.

### 4.6 Smin (deferred) — *function modality · semantic distance*

- **Measures:** minimum semantic distance between predicted and true GO sets — weights a wrong prediction by how specific it is (information content in the GO DAG). Lower is better. Computed by the CAFA-evaluator tool on the same DeepGO-SE inputs as Fmax; deferred because it needs information-content weights over the full GO corpus.

### 4.7 Structural metrics (degradation check, queued) — *structure modality*

- For the "does function training degrade the pretrained model?" question (feedback point 5): **designability** (fraction of co-generated structures with ESMFold self-consistency scTM ≥ 0.5 vs the co-generated structure), **pLDDT** (per-residue ESMFold confidence), **diversity** (mean pairwise scTM / Foldseek cluster count), and **forward-folding TM-score** on CAMEO 2022.
- **Modality / data:** co-generated struct tokens → decoded PDBs → ESMFold / TMscore. Identical-settings comparison: pretrained DPLM-2 vs our checkpoint under *null* conditions — any drop in these metrics is the degradation signal.

### 4.8 Training/validation losses (logged curves) — *both modalities*

- **val/loss** — the DPLM-2 denoising objective: label-smoothed cross-entropy on noised tokens, weighted by diffusion timestep λ(t), computed over the struct track and the aa track and summed. This is the quantity adapter training must not degrade.
- **val/aatype_loss · val/struct_loss** — the per-modality components. During adapter training the aa component plateaued early (≈3.87 from step 2K) while the struct component kept improving — the adapters modulate shared hidden states, so conditioning also (mildly) helps structure denoising.
- **val/*_index_accuracy** — fraction of noised positions whose argmax prediction equals the target token, per modality. Note: 0.114 aa accuracy is against the full 8,229-token vocab at *mixed* noise levels — not a clean 20-way classification, so absolute values look low; the trend is the signal.

---

## 5. Connection to last week's discussion points

| # | Last week's point | Status this week |
|---|---|---|
| 1 | Metrics & loss decomposition explained | delivered in [technical reference](conditional_dplm2_technical_reference.md); markdown/Mermaid format per decision |
| 2 | Function-modality evaluation design | **partially delivered**: metric ports + 4-arm driver done; the *in-validation* loss-delta proxy + recovery probe remain (callback task) |
| 3 | Training/inference diagram | queued (Mermaid, with #1) |
| 4 | Function evaluation vs vanilla baseline | **protocol implemented this week**; predictor runs pending (IPS downloaded; DeepGO-SE setup next) |
| 5 | Seq/struct degradation vs pretrained DPLM-2 | queued: co-generation designability identical-settings re-run + CAMEO 2022 forward folding + paper-numbers table |
| 6 | Qualitative W&B logging | queued (callback task, with #2) |
| Future | CFG / classifier guidance | **CFG implemented + tested** (classifier guidance remains a fallback if CFG sweep shows insufficient signal) |
| Future | Scaling data / foreign-dataset import / ESM tokenizer | unchanged; gated on first eval results |

---

## 6. First evaluation results (2026-09-25) — findings

Full 7-arm run executed: 256 conditioned sequences per arm (32 held-out GO-F labels × 8 seqs, len 256), InterProScan 5.78-109.0 on every arm, scorer applied after fixing a ground-truth conversion bug (prompt label ints were compared against predictor *accessions* — the positive control caught it).

### Results table (IPR recovery via InterProScan; distribution metrics vs `real`)

| Arm | IPR F1 micro | IPR F1 macro | IPR AUPR micro | MRR ↑ | MMD linear vs real ↓ | MMD gaussian ↓ |
|---|---|---|---|---|---|---|
| **ours_cond (w=1)** | 0.0000 | 0.0000 | 0.0294 | **0.1104** | 0.8874 | 0.5633 |
| ours_cond w=2 (CFG) | 0.0000 | 0.0000 | 0.0272 | **0.1277** | 0.4921 | 0.3017 |
| ours_cond w=4 (CFG) | 0.0000 | 0.0000 | 0.0283 | 0.0865 | 0.3757 | 0.2241 |
| ours_cond w=8 (CFG) | 0.0000 | 0.0000 | 0.0302 | 0.0860 | 0.4069 | 0.2439 |
| ours_null | 0.0000 | 0.0000 | 0.0312 | 0.0778 | 0.8660 | 0.5484 |
| vanilla (pretrained) | 0.0072 | 0.0005 | 0.0066 | 0.0787 | **0.1601** | **0.0983** |
| **real (ceiling)** | **0.9782** | **0.9262** | **0.9574** | 0.8099* | 0 | 0 |

\* real self-recovery MRR = 0.81 (not 1.0) — the sample-size noise floor of the metric at 8 seqs/label; this recalibrates all MRR readings.

### Positive results

1. **A genuine conditioning signal exists.** MRR: ours_cond 0.110 vs ours_null 0.078 / vanilla 0.079 — controls sit near the random-ranking floor (~0.068 for 59 groups); the conditioned model rises ~40% above both controls, with several labels ranking 1st–4th of 59. CFG amplifies it further: **w=2 → MRR 0.128 (+64% over controls)**.
2. **CFG controls the fidelity/realism trade-off exactly as designed.** Higher w pulls sequences strongly back toward the natural distribution: MMD-linear drops 0.887 → 0.492 → 0.376 across w=1→2→4. The dose-response machinery works end-to-end.
3. **The evaluation stack is validated by its own controls.** The real arm scores IPR F1 = 0.978 (InterProScan recovers prompted domains from natural sequences nearly perfectly) — the metric, the ID conversion, and the pipeline are trustworthy. The positive control also *caught* the ground-truth-conversion bug before it produced invalid conclusions.
4. **The full pipeline is reproducible and fast**: 4×(256 seqs × 256 denoising steps) + 4 IPS scans + scoring ≈ 1 h on one RTX 3090.

### Negative results

1. **Zero function recovery across every generated arm.** IPR F1 = 0.0000 for all 1,280 generated sequences (4 arms × 256). Not one prompted domain was recovered by InterProScan, while the same scan recovers 98% of prompted domains from real sequences.
2. **Sequence-realism regression from adapter training.** MMD to the real distribution: vanilla 0.16 vs our arms 0.37–0.89 — adapter training moved generation into a low-complexity composition (Ala/Gly-rich) far from natural proteins. Even the best CFG point (w=4, 0.376) stays >2× worse than vanilla.
3. **CFG cannot rescue recovery.** MRR peaks at w=2 and reverts to control levels by w=4–8; IPR AUPR is flat in w (~0.03). Sampling is not the bottleneck.
4. **Degradation vs vanilla confirmed in function space.** Vanilla recovers a few domains by chance (F1 micro 0.0072); our conditioned model recovers exactly zero — the conditioned outputs are *more* degenerate than vanilla random generation.

### Diagnosis

The three observations are mutually consistent with the training curves: **aatype loss plateaued at step ~2K** while struct loss kept improving. The 19.6M adapters, trained at frozen base on 45K proteins for 100K steps, transmit *weak* label information (enough to bias MRR and nudge struct denoising) but far too little to shape low-complexity outputs into domain-bearing sequences. The zero-recovery result is a *capacity/training-signal* limitation, not a sampling or evaluation artifact — the positive control and CFG dose-response rule out both.

---

## 7. Current state & immediate next steps

1. **Meluxina:** InterProScan 5.78 + `go.obo` downloaded and **IPS runs validated on the eval FASTAs** (EBI's FTP host was refusing connections — the IPS 5.78 GitHub tarball bundles all databases and works). DeepGO-SE setup pending (KAUST data link broken — issue filed upstream; docker image `coolmaksat/deepgose` is the first fallback to probe for bundled data).
2. **Next: training changes to attack the zero-recovery result** (in priority order):
   - **LoRA on attention+FFN** (the pre-planned escape hatch): adapter-only capacity has been shown insufficient; LoRA adds trainable capacity *inside* each layer while keeping most of the base frozen.
   - **Longer training / more data**: 45K proteins is small; scale toward the full CFP-Gen general set (104K) via the AFDB-tokenizer path, or UniProt-annotated expansions (SwissProt + InterPro + EC as the professors suggested).
   - **Balanced label sampling** and possibly increasing CFG dropout (0.1 → 0.2) to strengthen the conditional gradient.
   - Re-run the eval stack per iteration — the full loop now costs ~1 h GPU + ~30 min CPU.
3. **Remaining implementation:** validation callback (loss-delta proxy + qualitative table with PDB/pLDDT — for future/continued runs), degradation check wiring (co-generation designability + CAMEO 2022), docs (diagram + loss decomposition).

### Known observations carried into the eval

- **aatype loss plateaued** since ~2K steps (3.87) while struct loss kept improving — sequence-mode quality is the weak axis; function-mode fidelity is what the CFG sweep + recovery eval will quantify.
- Quick local CFG sweep on the trained ckpt (`go:5`, w=1/2/4) already shows composition changes across w — the dose-response machinery works; recovery curves will tell whether it improves *fidelity*.

### Numbers cheat-sheet

| Quantity | Value |
|---|---|
| Training | 100K steps, 4×A100 DDP bf16, final val/loss **1.95**, val/ppl 7.46 |
| Frozen base / trainable | 650M / **19.6M** (2.9%) |
| Eval held-out set | CFP-Gen `test.pkl`, 8,309 seqs, 30/label |
| Eval arms | ours_cond / ours_null / vanilla / real |
| Guidance sweep | w ∈ {1, 2, 4, …} |
| Predictors | InterProScan 5.78 (IPR+GO-mapping), DeepGO-SE (GO probabilities) |
| Metrics | IPR set-match F1/AUPR · GO Fmax (CAFA) · MRR · MMD |
