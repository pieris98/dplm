# Weekly Next Steps: Function-Modality Slides, Tokenizer Design, Eval Runs

## Context

Weekly progress meeting identified four work items for the coming cycle:

1. Methodology slides for the two function-modality choices (CFP-Gen alignment vs. extending the DPLM-2 LFQ tokenizer to function tokens).
2. Slides on the function-annotation token vocabulary CFP-Gen / DPLM-2 use, with a recommendation on coverage.
3. Design (no code yet) of a function-tokenizer implementation.
4. Running existing evaluation tasks on DPLM-2, plus reading the DPLM-2 paper and the `representationlearning` branch to identify missing benchmarks.

This plan covers all four. Slide content is described textually so it can be lifted into KeyNote/PowerPoint; tokenizer design is architecture-level only; eval runs are concrete commands plus a missing-benchmark audit.

## Codebase findings driving the plan

### CFP-Gen training surface (verified from `/home/cherry/dev/phd/cfpgen`)
- **Single loss only**: weighted cross-entropy diffusion loss (`RDMCrossEntropyLoss` at [cfpgen/src/byprot/modules/cross_entropy.py:143-223](../cfpgen/src/byprot/modules/cross_entropy.py#L143-L223); training step [cfpgen/src/byprot/tasks/lm/cfp_gen.py:98-127](../cfpgen/src/byprot/tasks/lm/cfp_gen.py#L98-L127)).
- **Zero contrastive / InfoNCE / alignment losses.** Grep across the entire CFP-Gen repo returns no matches. Function signal is purely architectural (AGFM FiLM + RCFE ControlNet). CFP-Gen explicitly differs from ProDVA on this point.
- **Function injection**: AGFM FiLM at every ESM layer ([cfpgen/src/byprot/models/lm/esm_cfpgen.py:148-207](../cfpgen/src/byprot/models/lm/esm_cfpgen.py#L148-L207)); RCFE ControlNet side-branch ([cfpgen/src/byprot/models/lm/esm_cfpgen.py:21-49](../cfpgen/src/byprot/models/lm/esm_cfpgen.py#L21-L49)); classifier-free-guidance dropout in `FuncTagEmbedder` ([cfpgen/src/byprot/models/lm/esm_cfpgen.py:265-289, 323-327](../cfpgen/src/byprot/models/lm/esm_cfpgen.py#L265-L289)).
- **Vocabularies** (from configs, verified against `go_mapping.pkl`/`ipr_mapping.pkl`):
  - General dataset: GO=375, IPR=1154, EC=661 ([cfpgen/configs/experiment/cfpgen/cfpgen_650m_stage1.yaml:30,33,36](../cfpgen/configs/experiment/cfpgen/cfpgen_650m_stage1.yaml)).
  - Enzyme dataset: GO=780, IPR=4982, EC=661 ([cfpgen/configs/experiment/cfpgen/cfpgen_enzyme_650m.yaml:30,33,36](../cfpgen/configs/experiment/cfpgen/cfpgen_enzyme_650m.yaml)).
  - Frequency-filtered (≥100 sequences per term, cfpgen_paper.md §6.1 lines 859-868). Extensible design, paper explicitly invites Pfam (cfpgen_paper.md line 280).
- **On-disk format**: multi-hot lists of token IDs, `-1` padding in collate ([cfpgen/src/byprot/datamodules/dataset/uniprotKB.py:188-212, 282-306](../cfpgen/src/byprot/datamodules/dataset/uniprotKB.py#L188-L306)).

### DPLM-2 LFQ tokenizer (verified from this repo)
- Architecture: GVP encoder → `pre_quant` MLP → `LFQ` quantizer → `post_quant` MLP+transformer → ESMFold decoder ([src/byprot/models/structok/structok_lfq.py:55-87](../src/byprot/models/structok/structok_lfq.py#L55-L87)).
- Codebook 8192 entries × 13-dim binary ({-1,+1}^13) — [structok_lfq.py:52-63](../src/byprot/models/structok/structok_lfq.py#L52-L63), [configs/experiment/structok/structok_lfq_8k_pdb_swissprot_c512.yaml:54-55](configs/experiment/structok/structok_lfq_8k_pdb_swissprot_c512.yaml#L54-L55).
- Trained standalone via `StructureVQLoss` (FAPE + distogram + LM + violation + optional TM) at [src/byprot/models/structok/modules/loss.py:1702-1876](../src/byprot/models/structok/modules/loss.py#L1702-L1876), then frozen for DPLM-2 main training.
- DPLM-2 main model embeds struct tokens via the **shared** AA+struct embedding table (vocab = 33 + 8192 + 4 = 8229, [dplm2.py:33-34](../src/byprot/models/dplm2/dplm2.py#L33-L34)). Modality inferred from token-id range by `get_modality_type` ([dplm2.py:220-227](../src/byprot/models/dplm2/dplm2.py#L220-L227)).
- DPLM-2 Bit variant uses a **separate** `quant2emb` linear projection for structure tokens ([dplm2_bit.py:667-670](../src/byprot/models/dplm2/dplm2_bit.py#L667-L670)), binary classification per codebook dim in `lm_head_struct` ([dplm2_bit.py:648-653](../src/byprot/models/dplm2/dplm2_bit.py#L648-L653)). Cleaner modality separation.

### Eval surface (verified)
- **Fully runnable**: `scripts/reproduce/run_folding.sh` (CAMEO 2022 forward folding); `generate_dplm2.py --task co_generation` (unconditional joint); `run/scaffold_generate_dplm2.py` (motif generation).
- **Generation works, eval incomplete**: inverse folding (`generate_dplm2.py --task inverse_folding` — no benchmark data); motif success rate (`analysis/motif_analysis.ipynb` — no CLI for motif-RMSD<1Å / scTM>0.8).
- **Missing on `main`**: representation-learning / SaProt-style downstream tasks (Thermostability, HumanPPI, MetalIonBinding, EC, GO-MF/BP/CC, DeepLoc-Subcellular, DeepLoc-Binary) — these live on the `origin/representationlearning` branch as a separate lineage using `dplm_annotation_model` with `config/{TaskName}/dplm.yaml` (verified by inspecting `git show origin/representationlearning:config/EC/dplm.yaml`). The branch pre-dates the `configs/` Hydra layout, so porting is non-trivial.
- **External deps** (per `scripts/reproduce/check_env.sh`): ESMFold, TMscore/TMalign, Foldseek (optional), ProteinMPNN (optional, vendor not present), pyhmmer (for Pfam eval).
- **HMMER Pfam eval** (relevant to conditional DPLM-2): `scripts/evaluate_pfam_pyhmmer.py` is ready; `scripts/prepare_pfam_dataset.py` exists but limited to 8 families in debug mode.
- **Smoke outputs** already exist at `docker-smoke-output/dplm2-smoke/co_generation/length_64/` and the bit-variant analog — co-gen has been run.

## Work item 1: Methodology slides (3-4 slides, deep architecture diagrams)

**Audience:** research supervisor. **Goal:** defend a methodology choice for adding function modality to DPLM-2.

### Slide A — Option 1: CFP-Gen port (parallel adapter + FiLM)
- *One-paragraph summary*: inject GO/IPR/EC/Pfam annotations as continuous vectors via ProCALM-style parallel adapters (already prototyped — see [reports/conditional_dplm2_session_impl.md](../reports/conditional_dplm2_session_impl.md)). Condition is a per-sample vector; base DPLM-2 frozen.
- *Architecture diagram (ASCII / drawn in KeyNote)*:
  ```
  Annotation IDs (multi-hot) ──▶ nn.Embedding per type ──▶ sum ──▶ ProjectionMLP ─┐
                                                                                    │
  [struct|aa] tokens ──▶ ModifiedEsmLayer ──▶ h ──┐                                ▼
                                                  └─▶ ParallelAdapterLayer(h, s) ──▶ h + adapter_out ──▶ next layer
  ```
- *Loss*: weighted cross-entropy diffusion loss only (matches CFP-Gen). No contrastive / alignment term — CFP-Gen validates that architectural injection is sufficient.
- *Pros*: low-risk, frozen base, plug-and-play for any continuous external-memory encoder (retrieval, text).
- *Cons*: condition is a vector, not a token; does not give DPLM-2 the ability to *generate* function tokens; weaker per-layer leverage than FiLM.

### Slide B — Option 2: Extend DPLM-2 LFQ tokenizer to function tokens
- *One-paragraph summary*: train a function LFQ tokenizer analogously to the structure tokenizer; function tokens join AA + struct in the unified vocabulary; DPLM-2 learns to co-generate seq + struct + function.
- *Architecture diagram*:
  ```
  Function tokenizer (trained standalone, frozen):
    multi-hot annotations ──▶ Set Transformer encoder ──▶ pre_quant ──▶ LFQ codebook (2^k entries) ──▶ indices
                                                                                                          │
    Reconstruction head: indices ──▶ multi-label BCE over original annotation set ◀───────────────────────┘

  DPLM-2 main:
    [func | struct | aa] tokens ──▶ shared embedding table ──▶ ModifiedEsmLayer stack ──▶ predict all three modalities
  ```
- *Loss*: DPLM-2 diffusion CE over all three modalities + standalone function-tokenizer training loss (BCE reconstruction + LFQ entropy + commitment).
- *Required edits* (cite [dplm2.py:33-34, 220-227](../src/byprot/models/dplm2/dplm2.py#L33-L34), [dplm2_modeling_esm.py:40-43](../src/byprot/models/dplm2/modules/dplm2_modeling_esm.py#L40-L43), [dplm2.py:243-256](../src/byprot/models/dplm2/dplm2.py#L243-L256)):
  - `vocab_size = 33 + 8192 + N_func + specials`
  - `get_modality_type` ternary classification by id range
  - `ModifiedRotaryEmbedding` extend to 3-way split
  - `single_modality` training mode: chunk attention bias into 3
- *Pros*: function becomes a first-class generative modality — DPLM-2 can predict a protein's function, not just be conditioned on it; unified architecture; matches the paper's "any-to-any generation" narrative.
- *Cons*: large engineering surface (vocab extension, rotary extension, attention-bias extension, re-pretraining or continued training from `airkingbd/dplm2_650m`); requires a trained function tokenizer.

### Slide C — Option 3 (recommended): Hybrid
- Function LFQ tokenizer produces function tokens (gives DPLM-2 generative function capability).
- During *training*, also pass the same function annotations through parallel adapters (Slide A) — gives a dual-granularity signal: per-position conditioning (adapter) + discrete token co-generation (LFQ).
- During *inference*, support three modes: (i) condition-only (adapter), (ii) generate-only (LFQ tokens), (iii) both.
- Reduces risk: if the LFQ tokens under-train, the adapter still carries signal; if the adapter under-fits at frozen base, the LFQ branch can still generate function.

### Slide D — Recommendation + risks
- *Recommendation*: start with Option 1 (already prototyped — [reports/conditional_dplm2_session_impl.md](../reports/conditional_dplm2_session_impl.md)) to get a result on Pfam conditioning in the coming cycle. Design Option 2/3 in parallel; commit to the function-tokenizer build only if Option 1 underperforms on HMMER hit-rate.
- *Key risk for Option 1*: ProCALM was validated on autoregressive ProGen2, not diffusion. The adapter is applied T=500 times per sample; signal may attenuate or amplify. Mitigation: the `weight_init` knob on `linear_up` and the optional FiLM path (per the [conditional_dplm2_architecture.md](conditional_dplm2_architecture.md) plan).
- *Key risk for Option 2*: DPLM-2's rotary embedding halving assumes a 2-modality stream. Extending to 3 requires care; the Bit variant's separate-projection design ([dplm2_bit.py:667-670](../src/byprot/models/dplm2/dplm2_bit.py#L667-L670)) is cleaner here and should be the preferred base.

## Work item 2: Function-annotation vocabulary slide

Cover **CFP-Gen's set** (GO/IPR/EC) and **the standard 4-type set** (Pfam + InterPro + GO + EC). One slide, table-driven.

| Annotation type | What it captures | CFP-Gen vocab | Realistic vocab | Source |
|---|---|---|---|---|
| GO (MF/BP/CC) | Molecular function, biological process, cellular component | 375 (general), 780 (enzyme) | 45k GO terms; ~3-5k after ≥100-seq frequency filter | geneontology.org |
| IPR (InterPro) | Domain / family / site signatures | 1154 (general), 4982 (enzyme) | 38k InterPro entries; ~5-10k after filter | ebi.ac.uk/interpro |
| EC | Enzyme class (4-level) | 661 | ~8k full EC; 661 is the standard CFP-Gen subset | ebi.ac.uk/intenz |
| Pfam | Domain families | (not used by CFP-Gen) | ~20k Pfam-A families; ~12k after filter | ebi.ac.uk/pfam |

**Recommendation on the slide:**
- Pfam + InterPro + GO + EC is the right starting set. Pfam and InterPro overlap heavily (~70% of Pfam families map to InterPro), but Pfam is the standard evaluator for family-conditioned generation (HMMER pipeline, see [conditional_dplm_pfam.md](conditional_dplm_pfam.md)).
- The frequency filter (≥100 sequences per term) is necessary — long-tail terms break both embedding learning and eval. Mention this explicitly.
- *Not enough on its own* if the project goal includes enzyme design — flag EC subnumber-level and active-site residues (M-CSA) as a future extension.
- *Out of scope for now*: UniProt keywords, tissue specificity, taxonomy. Mention as a later text-conditioning extension, not a current milestone.

## Work item 3: Function tokenizer design (no code yet)

Mirror the structure tokenizer at [src/byprot/models/structok/structok_lfq.py:32-100](../src/byprot/models/structok/structok_lfq.py#L32-L100). The design is sketched here only — implementation waits for meeting feedback.

**Components**:
1. **Encoder**: Set Transformer over per-type `nn.Embedding` lookups. Input is the multi-hot annotation set; output is a fixed-dimensional "function state" vector per protein (not per residue — function is a protein-level property).
2. **Quantizer**: LFQ with codebook size 2^k for k ∈ {10, 11, 12} (1024–4096 entries). Smaller than structure (8192) because the function space is smaller.
3. **Decoder / reconstruction head**: multi-label BCE over the original annotation set — used only to train the tokenizer standalone.
4. **Loss**: `FunctionVQLoss` mirroring `StructureVQLoss` ([src/byprot/models/structok/modules/loss.py:1702-1767](../src/byprot/models/structok/modules/loss.py#L1702-L1767)) — BCE reconstruction + LFQ entropy loss (weight 0.1) + commitment loss (weight 0.25).
5. **Integration into DPLM-2 main**: function tokens join the vocabulary; `get_modality_type` becomes ternary; rotary embeddings extended to 3-way split; `single_modality` training mode chunks the attention bias into 3.

**Open design questions to resolve in the meeting:**
- *Per-residue or per-protein?* Structure tokens are per-residue (one per amino acid). Function is intrinsically per-protein. Options: (a) one function token at the start of the sequence (BOS-style), (b) broadcast across all residues (matches structure-token count but loses the "function is global" inductive bias), (c) a small set of M learnable function slots.
- *Bit-variant first?* The Bit variant's separate `quant2emb` projection ([dplm2_bit.py:667-670](../src/byprot/models/dplm2/dplm2_bit.py#L667-L670)) makes adding a third modality much less invasive than the shared-vocab standard DPLM-2. Recommendation: build function-tokenizer on the Bit base.
- *Pretraining corpus*: SwissProt (~560k reviewed, full annotation coverage) is the natural source; can also use UniProtKB/TrEMBL for scale at the cost of noisier labels.

## Work item 4: Evaluation runs

### Tasks to actually run this cycle

1. **Forward folding smoke (CAMEO 2022, 2 seqs)** — `bash scripts/reproduce/run_folding.sh --limit 2`
   - Pre-req: `bash scripts/reproduce/download_eval_data.sh` (CAMEO 2022 metadata).
   - Output: per-target `top_sample.csv`, aggregate `summary.csv` (mean/median RMSD, TM-score by length bin).
   - Validates the ESMFold + TMscore/TMalign eval harness end-to-end.

2. **Unconditional co-generation smoke (len 64)** — `python generate_dplm2.py --task co_generation --model_name airkingbd/dplm2_650m --seq_lens 64 --num_samples 2`
   - Already in `docker-smoke-output/` for the Bit variant; run the standard DPLM-2 path to confirm parity.
   - Output: `aatype.fasta`, `struct_token.fasta`, decoded PDBs; then `src/byprot/utils/protein/evaluator_dplm2.py` produces designability + scTM.

3. **Motif scaffolding (24-problem benchmark)** — `python run/scaffold_generate_dplm2.py`
   - Pre-req: `bash scripts/download_motif_scaffolds.sh`.
   - Generation works today. **Gap**: no success-rate CLI (motif-RMSD < 1Å + scTM > 0.8). As part of this cycle, add a small `scripts/evaluate_motif_scaffolding.py` that takes the scaffold outputs, runs ESMFold on each, and computes motif-RMSD + scTM using the existing `analysis/TMscore` binary + the motif fixed-position mask. This is a 100-200 line script — small enough to write this cycle.

4. **Inverse folding** — `python generate_dplm2.py --task inverse_folding --model_name airkingbd/dplm2_650m --input_fasta_path <benchmark.fasta>`
   - **Gap**: no standard benchmark data is wired up. Decide in the meeting which benchmark to use — CATH 4.3 (the `configs/datamodule/cath_4.3.yaml` exists on main but is unused), the inverse-folding subset of CAMEO 2022, or the DPLM-2 paper's choice. Then write a 50-line benchmark loader.

### Missing-benchmark audit (for slide + to drive the next meeting)

Read [dplm2_paper.md](../dplm2_paper.md) §4.1–4.4 and Appendix, and inspect `origin/representationlearning`. The following are **not** reproducible from `main` today:

| Paper task | Status on `main` | Status on `representationlearning` branch | Action |
|---|---|---|---|
| Unconditional co-generation (designability, scTM, pLDDT) | runnable | absent | Run smoke (item 2 above) |
| Forward folding (CAMEO 2022 TM-score) | runnable | absent | Run smoke (item 1 above) |
| Inverse folding | generation only, no benchmark | absent | Pick benchmark + write loader (item 4) |
| Motif scaffolding (24 problems, motif-RMSD, scTM) | generation only, no success-rate CLI | absent | Write `evaluate_motif_scaffolding.py` (item 3) |
| Secondary-structure statistics | partial (helix/strand computed inside evaluator) | absent | Plot-only task; can compose from existing metrics |
| Novelty (Foldseek cluster count vs PDB) | partial (foldseek utility present, no PDB reference db) | absent | Build PDB-cluster index; non-trivial |
| Representation-learning downstream probes (Thermostability, HumanPPI, MetalIonBinding, EC, GO-MF/BP/CC, DeepLoc) | absent | present as a separate lineage using `dplm_annotation_model` + `config/{Task}/dplm.yaml` (verified `git show origin/representationlearning:config/EC/dplm.yaml`) | Decision needed: port to `main` (non-trivial — branch uses old `config/` layout, pre-`configs/` Hydra), or run on the legacy branch as-is |
| Conditional Pfam generation (this project's metric) | `scripts/evaluate_pfam_pyhmmer.py` ready; `prepare_pfam_dataset.py` debug-only | n/a | Blocked on the conditional-DPLM-2 model itself ([reports/conditional_dplm2_session_impl.md](../reports/conditional_dplm2_session_impl.md)) |

**Meeting ask**: confirm whether to port the `representationlearning` branch tasks to `main` (a separate sub-project — they use `dplm_annotation_model`, an annotation-head fine-tuning path that's architecturally separate from the parallel-adapter approach we're building). If not, accept that representation-learning results will cite the original DPLM-2 paper rather than be reproduced.

## Verification

Each work item has a natural completion check:
- *Slides*: supervisor signs off on Option 1/2/3 choice at the meeting; vocabulary recommendation accepted.
- *Function tokenizer design*: design doc reviewed; per-residue vs per-protein question resolved.
- *Forward folding smoke*: `scripts/reproduce/run_folding.sh --limit 2` produces a `summary.csv` with finite TM-scores on 2 CAMEO targets.
- *Co-generation smoke*: designability > 0 on 2 samples at len 64; matches the existing `docker-smoke-output/` Bit-variant results qualitatively.
- *Motif scaffolding*: 24 problems × N samples generate; the new `evaluate_motif_scaffolding.py` produces a per-problem success rate.
- *Inverse folding*: pick benchmark at meeting; generate sequences; `evaluator_dplm2.py` reports sequence-recovery vs reference.

## Critical files referenced

- Slide source material: [cfpgen_paper.md](../cfpgen_paper.md), [dplm2_paper.md](../dplm2_paper.md), [conditional_dplm2_architecture.md](conditional_dplm2_architecture.md), [reports/conditional_dplm2_session_impl.md](../reports/conditional_dplm2_session_impl.md).
- Architecture references for the tokenizer slide: [src/byprot/models/structok/structok_lfq.py](../src/byprot/models/structok/structok_lfq.py), [src/byprot/models/dplm2/dplm2.py](../src/byprot/models/dplm2/dplm2.py), [src/byprot/models/dplm2/dplm2_bit.py](../src/byprot/models/dplm2/dplm2_bit.py).
- Eval harness: [scripts/reproduce/run_folding.sh](../scripts/reproduce/run_folding.sh), [generate_dplm2.py](../generate_dplm2.py), [run/scaffold_generate_dplm2.py](../run/scaffold_generate_dplm2.py), [src/byprot/utils/protein/evaluator_dplm2.py](../src/byprot/utils/protein/evaluator_dplm2.py).
- New script to write (item 3 gap): `scripts/evaluate_motif_scaffolding.py` (motif-RMSD + scTM>0.8 success rate).
- New script to write (item 4 gap): inverse-folding benchmark loader for whichever benchmark is chosen.
