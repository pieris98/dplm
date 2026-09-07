# Conditional DPLM-2: Background, Implementation, and Progress

**PhD progress meeting — 2026-08-25**

This report is self-contained: §1–§3 recap the background and the findings from the previous presentation ("CFP-Gen vs DPLM-2: What Already Exists"), §4–§8 cover new work since then — the ConditionalDPLM2 implementation, dataset merging with CFP-Gen, structure-tokenization forensics, verification results, and next steps.

---

## 1. Background: the model lineage

Three models form the chain our work builds on:

**DPLM** (sequence-only diffusion LM). A discrete-diffusion language model pretrained on UniRef50 (~45M sequences, ~14B amino-acid tokens), following ESM2's architecture and scales (150M / 650M / 3B). Trained with masked/diffusion-style denoising over amino-acid tokens.

**DPLM-2** (sequence + structure co-generation). Extends DPLM by making 3D structure a **native discrete token modality**: a frozen Lookup-Free-Quantization (LFQ) tokenizer compresses each residue's backbone into one of 8192 codes (a 13-bit binary codebook), and the LM consumes a `[struct | aa]` concatenated token stream through a single self-attention stack with **no cross-attention**. Trained from DPLM init (with LoRA) on ~200K PDB + AlphaFold-SwissProt structures (pLDDT > 85, length ≤ 512) with a four-way loss mixture (single-modality / folding / inverse-folding / joint co-generation, 0.25 each). The result is *any-to-any* generation: folding, inverse folding, co-generation, and motif scaffolding from one model.

**CFP-Gen** (function-conditioned generation). A separate work that conditions an amino-acid diffusion LM (built on DPLM-1) on functional annotations (GO terms, InterPro domains, EC numbers), sequence motifs, and backbone structure. Its central limitation for us: **function is a condition, never a generated output** — and its structure branch uses the GVP + cross-attention design that DPLM-2 explicitly moved away from.

**The gap:** DPLM-2 — the strongest open seq+struct foundation model — has **zero function annotations** anywhere in its training data (verified by inspecting every dataset class and on-disk field). Our project asks: *can function control be added to DPLM-2 without disturbing its pretrained abilities?*

---

## 2. Recap of previous presentation: the design fork

The previous presentation (full version: [cfpgen_dplm2_meeting_synthesis.md](cfpgen_dplm2_meeting_synthesis.md)) analyzed the two existing approaches to adding a non-sequence modality, and ended on a design question. Key conclusions:

### CFP-Gen's approach: function as a side-channel condition

CFP-Gen injects all conditions through three bolt-on modules on a frozen backbone:

| Module | Mechanism |
|---|---|
| **AGFM** (annotations) | FiLM modulation: per-type `nn.Embedding` for GO/IPR/EC, summed; one shared adaLN MLP regresses 6 params/layer (shift/scale/gate × attention and FFN sublayers); applied at every ESM layer |
| **RCFE** (motifs) | ControlNet-style trainable copy of the first half of the ESM blocks, zero-init projections; dynamically updates motif residues during sampling (unlike DPLM's fixed-residue infilling) |
| **GVPT** (structure) | Frozen ESM-IF1 GVP-Transformer encoding backbone coords, injected via a cross-attention adapter reused from DPLM-1 **with no further training** |

Loss: plain diffusion cross-entropy — **no contrastive/InfoNCE term anywhere**. The model's only output is the amino-acid sequence; function/structure/motif are inputs, never generated.

### DPLM-2's approach: structure as a native generative token

The opposite philosophy: tokenize the modality into the same discrete vocabulary as amino acids and let the standard transformer process it as first-class tokens. No cross-attention, no adapters — the whole backbone is retrained to handle the unified vocabulary.

### The punchline that framed our decision

> CFP-Gen treats function *exactly the way DPLM-1 treated structure* — as a side-channel condition injected into a frozen backbone via FiLM + cross-attention. DPLM-2 is precisely the upgrade *away* from that pattern: it removes cross-attention and makes the modality a native, generative token.

Hence the design fork for function on DPLM-2:
- **Adapter path** (CFP-Gen-style): fast, frozen base, non-generative — bolt conditions onto the pretrained model.
- **Tokenization path** (DPLM-2-style): train a function LFQ tokenizer, extend the vocabulary, retrain — slower but generative and unified.

**Our decision (this cycle): adapter path first.** It is the cheap experiment that de-risks the expensive one: if function signal cannot survive T=500 diffusion steps through a frozen backbone at all, the tokenization path would fail at the same bottleneck after months of extra work.

### A third ingredient: ProCALM's parallel adapters

For the *mechanism* of conditioning we borrowed from a third system — **ProCALM** (EC/taxonomy conditioning on ProGen2, an autoregressive LM). ProCALM attaches a **low-rank parallel adapter** at every transformer block: a small bottleneck module (LayerNorm → down-project → fuse condition → MLP → up-project) whose output is *summed into the residual stream*. Two properties made it the right fit:

1. **Strictly additive:** the base layer's forward computation is untouched — unlike FiLM, which must replace the layer internals. The frozen-base invariant is provable rather than hoped-for.
2. **Separable multi-condition composition:** ProCALM's `ParallelAdapterLayer` gives each condition its own adapter and sums the updates; CFP-Gen's AGFM collapses all annotations into one summed embedding before a single adaLN, making conditions inseparable.

One honest caveat carried forward: ProCALM was validated on an *autoregressive* LM. DPLM-2 applies the adapter at every one of ~500 denoising steps — repetitive application could attenuate or amplify the signal. Only a real training run answers this.

### Comparison of the three conditioning designs

| Dimension | CFP-Gen (AGFM FiLM) | ProCALM (parallel adapter) | DPLM-2 (native tokens) |
|---|---|---|---|
| Where the condition enters | Inside every layer (FiLM params modulate LN outputs) | Residual stream after each layer (additive Δ) | Input token stream |
| Backbone forward graph | Modified (layer internals replaced) | **Untouched** | Replaced (retrained) |
| Frozen-base guarantee | Identity at init, but requires modified layer code | **Provably additive; near-zero init** | N/A (retrained) |
| Multi-condition | Pre-summed into one embedding (inseparable) | **Separable adapters per condition** | N/A |
| Condition representation | Discrete label IDs only (nn.Embedding) | **Any continuous vector** (one-hot, fingerprints, text embeddings) | Discrete tokens only |
| Generative over the modality | No | No | **Yes** |
| Validated on diffusion LM? | **Yes** (DPLM-1) | No (ProGen2, autoregressive) | Yes |

**Our hybrid:** ProCALM's parallel-adapter *mechanism* + CFP-Gen's discrete-annotation *embedder* (per-type `nn.Embedding` for GO/IPR with CFG null token) feeding a projection MLP into the adapters — the best-frozen-base of both, and an interface that will accept future retrieval/text conditions unchanged.

---

## 3. Recap: datasets and evaluation (previous presentation, condensed)

**Datasets** (verified from code and on-disk files):

| Dataset | # seqs | Content |
|---|---|---|
| DPLM (UniRef50) | ~45M | sequence only |
| DPLM-2 (pdb_swissprot) | ~220K (198K AFDB + 22K PDB) | seq + LFQ struct tokens; **no raw coordinates at the LM level** |
| CFP-Gen general | 103,939 | seq + GO (375) + IPR (1154) labels; ≥100 seqs/term frequency filter |
| CFP-Gen enzyme | 139,551 | + EC (661), from SwissProt ∩ CARE |

Notably, CFP-Gen and DPLM-2 draw structures from the **same two sources** (PDB + AFDB) but with incompatible filtering philosophies — DPLM-2 filters on structural quality (pLDDT > 85), CFP-Gen on annotation coverage (≥100 seqs/term). This becomes central to our dataset work in §5.

**Tokenizers:** DPLM-2's structure tokenizer is a *learned* LFQ quantizer (frozen ESM-IF1 GVP encoder → 13-dim sign-binarized latent → 8192 implicit codes → ESMFold decoder), trained standalone on the same ~200K structures and then frozen. CFP-Gen's function "tokenizer" is just label-embedding tables — no quantization, the vocabulary *is* the ontology.

**Evaluation:** DPLM-2 scores geometric fidelity against ground-truth coordinates (scTM, RMSD, AAR, motif-RMSD on CAMEO 2022 / CATH / 24-motif benchmarks). CFP-Gen scores function recovery by running generated sequences back through **neural predictors** (DeepGO-SE for GO, InterProScan for IPR, CLEAN for EC) — meaning its metrics are properties of (protein + predictor) with a predictor-bounded ceiling, not of the protein alone. **Consequence for us:** structural metrics alone cannot demonstrate a function condition is satisfied; our eval plan must include the predictor gauntlet or held-out label recovery (§8).

---

## 4. What we built: ConditionalDPLM2 vs vanilla DPLM-2

### The delta (everything else is inherited unchanged)

| Component | Vanilla DPLM-2 | ConditionalDPLM2 |
|---|---|---|
| Backbone (650M) | trained | **frozen** (`requires_grad=False` on all `net` params) |
| Input tokens | `[struct \| aa]` | identical — untouched |
| Cross-attention | none | none (adapters are residual, not attention) |
| New modules | — | `AnnotationEmbedder` (per-type GO/IPR tables, CFG null token), `ProjectionMLP` ×2, `ParallelAdapterLayer` ×33 (one per encoder layer) |
| Trainable params | 650M | **19.5M** (adapters + embedders + projectors only) |
| Loss | diffusion CE (struct + aa) | identical — no contrastive/auxiliary terms |
| Generation | parent `generate()` | overridden: condition vectors stashed once, applied at every denoising step |

### Condition flow

```
GO/IPR label IDs ──▶ nn.Embedding per type ──▶ sum ──▶ ProjectionMLP ──┐
                                                                       ▼ (per layer)
[struct|aa] tokens ──▶ ModifiedEsmLayer ──▶ h ──▶ AdapterLayer(h, s) ──▶ h + Δ ──▶ next layer
```

- `AdapterLayer`: low-rank bottleneck (LN → down → concat condition → MLP → up), `weight_init=1e-5` on the up-projection → **identity at init** (bit-exact frozen-base behaviour).
- `ParallelAdapterLayer`: one independent adapter per annotation type (IPR, GO), outputs summed — conditions stay separable.
- Multi-label conditions supported (CFP-Gen style): `--labels go:5,27 ipr:10,11,12`.

### Implementation inventory (all committed)

| File | Role |
|---|---|
| `src/byprot/models/dplm2/conditioning/parallel_adapter.py` | `ProjectionMLP`, `AdapterLayer`, `ParallelAdapterLayer` (ported from ProCALM; fixed a per-position condition-broadcast bug present in the original) |
| `src/byprot/models/dplm2/conditioning/annotation_embedder.py` | `AnnotationEmbedder` — multi-hot GO/IPR embedding + CFG dropout (learnable null token) |
| `src/byprot/models/dplm2/dplm2_conditional.py` | `ConditionalDPLM2` wrapper — frozen base, adapters, `compute_loss(conditions=)`, `generate(conditions=)` |
| `src/byprot/models/dplm2/modules/dplm2_modeling_esm.py` | additive `layer_adapters` plumbing in the encoder stack — byte-identical when unset |
| `src/byprot/datamodules/dataset/annotated_protein.py` | `AnnotatedProteinDataset` + `Collater` (struct+aa tokens + `-1`-padded label lists) |
| `src/byprot/datamodules/annotated_protein_datamodule.py` | LightningDataModule (length-bucketed sampler) |
| `src/byprot/tasks/lm/conditional_dplm2.py` | `ConditionalDPLM2TrainingTask` — threads `batch["annotations"]` into `compute_loss` |
| `generate_conditional_dplm2.py` | generation CLI — loads a ckpt (self-configuring), conditional / unconditional / co-generation modes |
| `configs/experiment/dplm2/cond_dplm2_650m_cfpgen{,_meluxina}.yaml` | smoke + full-run configs |
| `configs/trainer/meluxina_ddp_bf16.yaml` | 4×A100 DDP bf16, parameterized `num_nodes` |
| `scripts/meluxina/` | sbatch + README |
| Smoke checks | `scripts/check_conditional_dplm2_{frozen_base,compute_loss}.py`, `check_conditional_train_step.py` |

---

## 5. Dataset: merging CFP-Gen labels with DPLM-2

### The problem
CFP-Gen's general dataset has function labels but no struct tokens; DPLM-2's parquet has struct tokens but no labels. Join key: UniProt accession (extracted from DPLM-2's `AF-{ACCESSION}-F1-model_v4` names).

### Finding 1: only 47.8% of CFP-Gen overlaps DPLM-2
95,627 CFP-Gen train entries → **45,696** (47.8%) present in DPLM-2's parquet. 93% of the missing IDs are ≤512 residues — **length is not the reason**; they were simply never preprocessed into the DPLM-2 release. (Recall from §3: the two datasets' filters — structural quality vs annotation coverage — exclude largely disjoint halves of SwissProt.)

### Finding 2: the loss is uniform across labels (safe to proceed)
Per-label survival analysis on the 45,696 join: **71% of GO labels and 70% of IPR labels retain 50–70% of their sequences** (median 139 GO / 121 IPR seqs per label). The pLDDT>85 filter drops ~half of *every* label uniformly — no rare-label bias. Coverage: 1038/1085 IPR and 355/366 GO labels have ≥20 sequences.

### Finding 3: recovering the missing 54K from AFDB v4 (partial success)
- Downloaded `swissprot_pdb_v4.tar` (28 GB). PDB headers confirm **01-JUN-22** — the same vintage as DPLM-2's training data (rules out version skew).
- 96.9% of missing IDs have AFDB files; after DPLM-2's filter (avg pLDDT > 85, length ∈ [60, 512]): 36,076 survivors (8,911 rejected on pLDDT, 3,418 on length).
- **But** re-tokenizing these structures did not reproduce DPLM-2's shipped tokens → §6.

### Outcome: two datasets
| Dataset | Size | Struct tokens | Status |
|---|---|---|---|
| `joined_train_safe.parquet` | **45,696** | DPLM-2's shipped (guaranteed correct) | ✅ in use for training |
| `missing_afdb_struct_tokens.parquet` | 36,076 | our re-tokenization (~5–10% bit-flip noise) | ⚠️ shelved pending §6 conclusion |

---

## 6. Tokenization mismatch: forensic findings

**Symptom:** re-tokenizing the *same proteins from the same PDB files* gave 0% exact match vs DPLM-2's shipped `struct_seq` — 82% length mismatches, 18% token mismatches (median 22 diffs/seq, overwhelmingly single-bit flips in the 13-bit LFQ code).

**Three real causes found, ranked:**

1. **pLDDT end-crop threshold: paper says 50, code uses 70.** The DPLM-2 paper (§Dataset) claims cropping ends with pLDDT < 50; the actual preprocessing code uses **70** (both in `tokenized_protein.py` and the `crop_by_conf` step of `process_chain`). Fixing 50→70 collapsed length mismatches from **82% → 0.1%**. *This paper-vs-code discrepancy is a citable reproduction finding.*

2. **Our parser bypassed DPLM-2's feature pipeline.** The GVP encoder requires the full OpenFold transform chain (`process_pdb_file` → `parse_chain_feats` centering/scaling/masking → `atom37_to_frames` → torsion angles → pseudo-beta → backbone frames) — not just raw N/CA/C coordinates. Switching to DPLM-2's own `load_from_pdb` + `process_chain` (with plddt injected) produced **exact length matches** on previously mismatched cases.

3. **Residual ~10% single-bit flips per sequence remain — environmental.** With identical code path, identical PDB files (verified same 2022 vintage), deterministic inference (3 trials identical), and batch_size=1: Hamming-1 bit flips persist. Cause: numerical differences (PyTorch/CUDA version) between DPLM-2's original preprocessing environment and ours. LFQ sign-binarization is brittle at near-zero activations *by construction* — any tiny numeric difference flips a bit. **Not fixable without matching their exact build environment.**

**Assessment:** the residual noise is likely *within the diffusion training floor* (DPLM-2 itself trains on noised struct tokens) and lengths now align — but we chose not to risk it for the first result. The 45K safe dataset is the training set; the 82K combined set is a queued follow-up if more scale is needed.

---

## 7. Verification: everything tested, everything passing

| Check | Result |
|---|---|
| **Frozen-base equivalence** (fresh model, no conditions, random AA tokens) | logit diff **0.00e+00** — bit-exact |
| **Strong equivalence** (real `[struct\|aa]` batches from the dataset; exercises dual-stream rotary, modality typing, attention bias) | struct-half **0.00e+00**, aa-half **0.00e+00** |
| Adapter fires with conditions | diff 4.5e-04 (fresh) / 9.3e-04 (real labels) — small, non-zero, as designed |
| Gradient isolation after backward | **0 base params** with grad; 802 adapter params with grad |
| `compute_loss` with conditions | loss differs from unconditional (12.74 vs 11.44) — conditions flow through training |
| **Training smoke (200 steps, 1×RTX3090, wandb)** | val/loss **2.92 → 2.32**; aatype loss 9.05 → 4.37; **aatype index accuracy 0 → 0.10**; struct loss flat (~9.5 — frozen base untouched, as designed) |
| **Generation smoke (200-step ckpt)** | conditional (`go:5`) vs unconditional (null), same seed: **completely different amino-acid distributions** — conditions demonstrably steer sampling |
| Co-generation smoke | struct tokens (correct 0-padded format) + aa FASTAs saved |
| Checkpoint loading | self-configures from the ckpt's saved conditioning config; 802/802 adapter keys loaded; errors loudly if trainable keys are missing |

**Interpretation:** the conditioning *mechanism* is fully proven end-to-end — train → checkpoint → conditional generation → FASTA. Sequence *quality* at 200 steps is poor (Gly/Ala-rich) — expected; that is what the full run is for.

wandb: `CondDPLM2_650m/cond_dplm2_smoke_200step` (run `1wtsp9yu`).

---

## 8. Infrastructure: ready for the full run

- **Docker image** `pieris98/dplm:cu121-torch220-cond` **pushed** (digest `sha256:75c505...`). Contains all conditional code, wandb 0.26.1 (in a separate late Docker layer so the heavy base/model-cache stages stay cached — rebuild ~3 min instead of ~30), and the 45K dataset baked in (1.1 GB).
- **Meluxina configs**: `trainer=meluxina_ddp_bf16` (4×A100 DDP bf16, sync BN, `ddp_find_unused_parameters_true` — required because the base has frozen params), experiment config with lr 3e-4 / 100K steps / max_tokens 4000.
- **sbatch** (`scripts/meluxina/cond_dplm2_train.sbatch`): parameterized `--nodes`, Hydra override pass-through, wandb env handling.

**Full-run hyperparameters** (vs smoke): lr 1e-3→3e-4, max_tokens 1200→4000, steps 200→100K, 1×3090→4×A100 bf16. Estimated ~2–3 days on one node.

---

## 9. Design decisions worth defending

1. **ProCALM adapters over CFP-Gen's FiLM (AGFM):** adapters keep the base forward graph byte-identical (provable frozen-base invariant, verified bit-exact); FiLM requires replacing layer internals. Adapters also give separable multi-condition composition and accept *any* continuous vector (retrieval, text encoders) — our future Pfam/text path.
2. **Frozen base, no LoRA (first run):** isolates the question "does function signal survive T=500 diffusion steps through a frozen backbone?" LoRA is the pre-planned escape hatch if conditioning proves too weak.
3. **45K safe dataset over 82K noisy:** guaranteed-correct struct tokens for the first result; scale recovery is queued behind the tokenization conclusion (§6).
4. **Known risk carried forward:** ProCALM's adapter was validated on autoregressive ProGen2, not diffusion. The Meluxina run answers this empirically.
5. **The longer-term fork stays open:** if the adapter path succeeds, the *generative* function-modality question (a function LFQ tokenizer, DPLM-2-style) becomes the natural follow-up; if the adapter path fails at frozen base, the same bottleneck likely dooms the tokenization path too — the cheap experiment protects the expensive one either way.

---

## 10. TODOs / next steps (priority order)

1. **Classifier-free-guidance (CFG) sampling** — training already performs CFG dropout (`p=0.1`), but sampling cannot yet exploit it. Add a guidance scale to `generate()` *before* evaluating the full-run checkpoint. Cheap now, expensive to retrofit.
2. **Submit the Meluxina full run** — image pushed, configs ready. First a 100-step DDP dry-run on Meluxina to validate multi-GPU inside the container, then the 100K-step job. Scale to 2 nodes if throughput warrants.
3. **Function-recovery evaluation** (build while Meluxina trains):
   - Unconditional baseline generation (mechanism already works via `--labels null`).
   - Scorer: InterProScan for IPR recovery (CFP-Gen's protocol) or DeepGO-SE for GO; adapt CFP-Gen's metrics (MRR, MMD, micro/macro F1, AUPR) from `cfpgen/eval/`.
   - Report with the caveat from §3: these are neural-predictor-based metrics with a predictor-bounded ceiling — prefer held-out label recovery where possible, and note the HMMER/Pfam route (`scripts/evaluate_pfam_pyhmmer.py`, ready) is a ground-truth-style alternative if we add Pfam labels later.
4. **Scale decision after first result:** if 45K gives positive function recovery, evaluate whether the 82K combined set (36K re-tokenized with ~10% bit-flip noise) helps; decide on LoRA unfreeze if conditioning is weak.
5. **Longer-term roadmap:** Pfam/HMMER conditioning-eval path; retrieval/text conditions via the `external` projector hook; the function-LFQ-tokenizer ("Option 2/3" from the design fork) as the generative-function follow-up if the adapter path succeeds.

---

## Appendix: numbers cheat-sheet

| Quantity | Value |
|---|---|
| DPLM pretraining | UniRef50, ~45M seqs |
| DPLM-2 training | ~220K structs (198K AFDB-SwissProt + 22K PDB), pLDDT>85, len≤512 |
| DPLM-2 struct vocab | 8192 LFQ codes (13-bit), unified vocab 8229 |
| CFP-Gen general dataset | 103,939 seqs (GO 375 / IPR 1154 labels) |
| Join (safe dataset) | **45,696** proteins (47.8% of CFP-Gen train) |
| AFDB v4 coverage of missing IDs | 48,405 / 49,931 (96.9%) |
| Re-tokenized survivors (shelved) | 36,076 |
| Trainable params (ConditionalDPLM2) | 19.5M (2 condition types) |
| Frozen base | 650M, bit-exact at init |
| Smoke training | 200 steps, val/loss 2.92→2.32, aatype acc 0→0.10 |
| Tokenization mismatch (initial → after fixes) | 0% exact / 82% length → 0.1% length; token flips remain ~10%/seq (environmental) |
| Docker image | `pieris98/dplm:cu121-torch220-cond` (57.4 GB, digest sha256:75c505...) |
