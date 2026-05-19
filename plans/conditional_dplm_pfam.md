# Conditional DPLM Pfam-First Plan

## Purpose

This plan defines a Pfam-first strategy for conditional DPLM sequence generation. The immediate goal is to make DPLM generate protein domain sequences conditioned on a requested Pfam family ID, with evaluation strong enough to distinguish real conditional control from generic protein-like generation.

The first benchmark target is Pfam family conditioning only. Clan, InterPro, UniProt metadata, and text/function annotations should be preserved or planned for, but they are not the first optimization target.

The plan deliberately separates three concerns:

1. A rock-solid Pfam-A data and evaluation pipeline.
2. A minimal conditional DPLM architecture that is easy to ablate.
3. Guidance and modality-alignment extensions, starting with external classifier reranking and later adding gradient guidance or cross-attention if needed.

## Guiding Principles

Evaluation drives the architecture. The main success metric is not validation loss; it is whether generated sequences scan back to the requested Pfam family with HMMER/Pfam.

The first model intervention should be small. Prefix conditioning preserves the pretrained DPLM denoising machinery and creates a clean baseline.

Classifier guidance should be treated as a first-class method. The first serious guided run should use an external family classifier as a reranker because it is plug-and-play, low-risk, and gives a clear estimate of how much conditional signal can be recovered post hoc.

Cross-attention should be included in the roadmap for text conditioning, but not as the first implementation. It is more invasive than prefix conditioning or reranking, and Pfam ID conditioning does not require it initially.

The dataset split and evaluation must be leakage-aware. Random sequence splits are not enough; cluster-aware splits and novelty checks are needed to avoid measuring memorization.

## Scope

### In Scope

- Pfam-A full or seed parsing.
- Family-ID-conditioned DPLM sequence generation.
- Prefix-conditioned DPLM baseline.
- Family classifier training for external reranking.
- HMMER/Pfam evaluation of generated samples.
- Diversity and novelty evaluation.
- Clear extension path to classifier-gradient guidance and text/cross-attention conditioning.

### Out Of Scope For First Run

- DPLM-2 multimodal sequence/structure generation.
- Full free-text conditioning.
- Cross-attention implementation as the default architecture.
- ProteinDT/ProTrek runtime dependency.
- Reliance on only neural classifier metrics without Pfam/HMMER validation.

## Data Strategy

### Primary Source

Use Pfam-A directly as the first sequence and label source.

Preferred inputs:

```bash
data-raw/pfam/Pfam-A.full.gz
data-raw/pfam/Pfam-A.seed.gz
data-raw/pfam/Pfam-A.hmm.dat.gz
data-raw/pfam/Pfam-A.hmm.gz
```

Use `Pfam-A.full.gz` for the main training corpus if feasible. Use `Pfam-A.seed.gz` for early parser testing and fast prototype runs.

### Parser Backends

Use a small parser abstraction:

```python
def iter_pfam_records(path, backend="pyhmmer"):
    ...
```

Supported backends should be:

- `pyhmmer`: preferred default if available.
- `evcouplings_lite`: local fallback inspired by EVcouplings' Stockholm parser.

`pyhmmer.easel.MSAFile` is the best default because it supports `pfam` and `stockholm` formats directly, streams MSAs, and exposes family metadata such as accession, name, and description.

EVcouplings' Stockholm parser is the best fallback design because it is pure Python, handles Stockholm annotations, and supports wrapped alignment entries.

Biopython is useful as a reference, but it should not be the primary parser until Pfam-A wrapping behavior is verified, because current Biopython Stockholm documentation warns that wrap-around alignments are not supported by its newer iterator.

### Parsed Record Schema

Each parsed sequence row should use a schema close to:

```json
{
  "id": "PF00069|P12345/34-286",
  "sequence": "M...",
  "family_id": "PF00069",
  "family_accession": "PF00069.29",
  "family_name": "Pkinase",
  "description": "Protein kinase domain",
  "clan": "CL0016",
  "source_sequence_id": "P12345/34-286",
  "length": 253,
  "source": "pfam_a_full"
}
```

The first benchmark should optimize and report `family_id`. Clan is metadata and a secondary diagnostic.

### Sequence Cleaning

For each aligned sequence:

- Remove alignment gaps: `-` and `.`.
- Uppercase residues.
- Preserve only protein residues compatible with the DPLM tokenizer.
- Either discard or map rare residues depending on config.

Initial recommendation:

- Keep canonical amino acids plus `X` if DPLM supports it cleanly.
- Map `B`, `Z`, `U`, `O`, `J` to `X` if `X` is supported.
- Otherwise discard sequences containing unsupported residues.

Initial filters:

- `min_len = 50`
- `max_len = 512`
- `min_family_size = 100`
- exact deduplication within family
- configurable maximum examples per family for balancing

### Splits

Avoid random row splits as the final benchmark split.

Preferred split:

- Cluster sequences with MMseqs2 at a configurable sequence identity threshold, initially 50-70%.
- Split clusters into train, validation, and test.
- Ensure every benchmark family exists in train for the first conditional generation task.

Initial ratios:

- train: 90%
- validation: 5%
- test: 5%

For fast prototypes, a deterministic row split can be allowed behind an explicit `--split_strategy random_debug` flag, but it should not be used for reported results.

### Dataset Outputs

Write JSONL first because it is inspectable and portable:

```text
data-bin/pfam_family/train.jsonl
data-bin/pfam_family/valid.jsonl
data-bin/pfam_family/test.jsonl
data-bin/pfam_family/family_vocab.json
data-bin/pfam_family/family_metadata.json
data-bin/pfam_family/preprocess_stats.json
```

An LMDB or Arrow backend can be added later if JSONL is too slow.

## Evaluation Strategy

### Primary Metric

The primary metric is target Pfam family hit rate under HMMER/Pfam scanning.

For generated sequences conditioned on `PFxxxxx`, run `hmmscan` against `Pfam-A.hmm` and measure whether the top valid domain hit is the target family.

Report:

- target family hit rate
- target family hit rate after E-value thresholding
- target clan hit rate as secondary diagnostic
- no-hit rate
- wrong-family hit rate
- bit score distribution
- E-value distribution
- domain coverage distribution

This is the central controllability benchmark.

### Secondary Metrics

Report sequence quality and diversity:

- pairwise identity among generated samples per family
- MMseqs2 cluster count among generated samples
- amino-acid composition statistics
- low-complexity/repeat filters
- invalid-token rate
- generated length distribution

Report novelty:

- nearest-neighbor identity to training sequences
- nearest-neighbor identity to same-family training sequences
- proportion above thresholds such as 70%, 80%, 90%, and 95%

Optional later quality metrics:

- ESMFold pLDDT
- predicted structural compactness
- family-specific motif/domain coverage checks

Later PDFBench-style function metrics:

- plausibility: protein-LM perplexity and repetitiveness
- foldability: ESMFold-based foldability
- language/function alignment: ProTrek Score, EvoLlama Score, GO Recovery, IPR Recovery, Retrieval Accuracy
- novelty: sequence novelty with MMseqs2 and structure novelty with Foldseek
- diversity: sequence and structure diversity
- similarity: ground-truth identity, ground-truth TM-score, ESMScore

For the Pfam-first milestone, HMMER/Pfam remains the primary evaluator. For function/text-conditioned DPLM, use PDFBench as the benchmark reference and export generated outputs in PDFBench-compatible JSON.

Later ProteinGym/FLIP engineering metrics:

- ProteinGym DMS Spearman, NDCG, AUC, MCC, Top-K recall, and supervised MSE
- ProteinGym clinical AUC
- FLIP Spearman and MSE on active protein-engineering splits

ProteinGym and FLIP should be used after the Pfam-first milestone to evaluate mutation-effect scoring, variant proposal quality, and engineering-relevant extrapolation.

### Baselines

Run and report at least these baselines:

- real held-out Pfam sequences
- unconditional DPLM generation with no family conditioning
- unconditional DPLM plus external family-classifier reranking
- prefix-conditioned DPLM without reranking
- prefix-conditioned DPLM plus external family-classifier reranking

This is important because reranking alone may give strong apparent control even without conditioning. We need to know whether conditional training actually moves the sample distribution.

### Candidate Generation Protocol

For each target family:

- choose target lengths from the empirical family length distribution
- generate multiple candidates per requested output sequence
- evaluate raw candidates and reranked candidates separately

Example:

```text
families: 100 benchmark families
lengths: sampled from each family empirical distribution
candidates: 64 per family-length request
reported samples: top 1, top 4, top 16 depending on evaluation mode
```

Keep generation seeds fixed for reproducibility.

## Model Strategy

### Initial Architecture: Prefix-Conditioned DPLM

Use learned family prefix tokens as the first conditional DPLM architecture.

Flow:

```text
family_id -> embedding -> projector -> K prefix embeddings
```

The DPLM denoiser receives:

```text
[prefix_1 ... prefix_K] + [noisy protein sequence tokens]
```

The diffusion loss is computed only over protein sequence positions. Prefix token positions are ignored in the loss.

Recommended initial settings:

```text
prefix_len = 8
cond_dim = 256 or d_model
projector = small MLP
conditioning target = Pfam family ID
```

Training objective:

```text
L = L_diffusion(sequence | family_id)
```

### Why Prefix Conditioning First

Prefix conditioning is the lowest-risk architectural change:

- it preserves DPLM's pretrained denoising machinery
- it avoids editing transformer blocks
- it supports frozen-base and LoRA experiments
- it is easy to ablate against unconditional generation
- it can later be generalized from Pfam ID embeddings to text-derived embeddings

### Fine-Tuning Schedule

Use staged training:

1. Frozen DPLM, train only family embedding and prefix projector.
2. If controllability is weak, add LoRA to attention and/or MLP projections.
3. If still weak and compute permits, full fine-tune with conservative learning rates.

Do not full fine-tune first unless a fast pilot shows frozen/prefix adaptation is clearly underpowered.

### Family-Balanced Training

Use family-aware sampling or capped examples per family to avoid large families dominating training.

Options:

- cap examples per family per epoch
- sample families uniformly, then sample sequences within family
- use temperature-smoothed family sampling

Family-balanced sampling should be part of the first serious run, because condition ignoring and family imbalance are tightly coupled.

## Classifier Guidance Strategy

Classifier guidance should be treated as a major branch of the project, not an afterthought.

### Phase 1: External Classifier Reranker

The first serious guided run should use an external family classifier as a reranker.

Train a sequence classifier on Pfam family labels using real Pfam data. The classifier can be:

- an ESM-style frozen encoder plus classifier head
- a DPLM encoder/hidden-state pooling classifier if convenient
- a lightweight CNN/Transformer baseline for speed

The exact classifier architecture is less important than calibration, validation accuracy, and its independence from HMMER evaluation.

Reranking protocol:

```text
target family -> generate N candidate sequences -> classifier scores candidates -> keep top k -> run HMMER evaluation
```

Report both raw and reranked metrics.

Recommended initial `N`:

```text
N = 32 or 64 candidates per requested sequence
```

Scoring:

```text
score = log p_classifier(target_family | sequence)
```

Optional combined score:

```text
score = log p_classifier(target_family | sequence) + alpha * DPLM_sample_score
```

Do not rely on classifier accuracy alone. The reranked samples must still pass HMMER/Pfam evaluation.

### Why Reranking First

Reranking is plug-and-play:

- no changes to DPLM sampling internals
- easy comparison across unconditional and conditional generators
- can reveal whether the generator already produces target-family-compatible samples at low frequency
- provides a clear upper-bound-like estimate for sampling-based selection

Reranking also de-risks gradient guidance by telling us whether classifier signal is useful before injecting it into the denoising loop.

### Phase 2: Gradient Classifier Guidance

If reranking improves target-family hit rate but requires too many candidates, add gradient guidance during denoising.

Goal:

```text
steer intermediate denoising states toward higher classifier target-family probability
```

Challenges:

- DPLM uses discrete tokens and masked/noised states.
- Classifier gradients may need to operate on token logits, relaxed embeddings, or predicted clean-token distributions.
- Over-strong guidance can harm protein realism and diversity.

Possible implementation routes:

1. Apply classifier to predicted clean token probabilities using a differentiable embedding expectation.
2. Apply classifier to soft token embeddings during selected denoising steps.
3. Use classifier-free guidance style if we later train conditional and unconditional branches together.

Initial guidance hyperparameters should include:

```text
guidance_scale
guidance_start_step
guidance_end_step
guidance_frequency
entropy_regularization
```

Gradient guidance should be evaluated against reranking, not only against prefix-only generation.

## Cross-Attention And Text-Conditioning Roadmap

Cross-attention should be planned but not implemented as the first default architecture.

### Motivation

Prefix conditioning is enough for Pfam IDs, but future text conditioning may need richer alignment between conditioning tokens and protein sequence positions.

Cross-attention can make the denoiser attend to conditioning memory such as:

- Pfam family descriptions
- InterPro descriptions
- UniProt function text
- GO terms
- EC annotations
- learned outputs from a protein-text contrastive encoder

### Planned Cross-Attention Design

Use a conditioning encoder that produces memory tokens:

```text
condition input -> condition encoder -> memory tokens
```

For Pfam IDs:

```text
family_id embedding -> small adapter -> memory tokens
```

For text later:

```text
text -> text encoder -> projected memory tokens
```

Then add cross-attention adapters to selected DPLM layers:

```text
protein hidden states attend to condition memory
```

Keep this modular:

```python
condition = condition_encoder(batch)
model(sequence_tokens, condition=condition)
```

This lets the same model API support prefix conditioning, cross-attention conditioning, and future text conditioning.

### When To Implement Cross-Attention

Implement cross-attention after the prefix plus reranker baseline unless one of these happens:

- prefix conditioning fails to improve HMMER target-family hit rate
- classifier reranking finds target-family candidates but prefix conditioning does not increase their frequency
- text-conditioning becomes the immediate project priority

Until then, cross-attention remains a documented phase rather than first-run code.

## ProDVA-Inspired Function/Text Roadmap

ProDVA should be treated as the strongest recent implementation reference for the post-Pfam function/text phase.

ProDVA uses:

- a text encoder
- a protein language model backbone
- a phrase encoder
- retrieval-backed supporting examples through FAISS
- protein fragment mappings with type, name, and description metadata
- dynamic vocabulary augmentation where retrieved protein fragments become generation candidates
- auxiliary type classification and description-phrase InfoNCE losses
- freeze, LoRA, and full fine-tuning modes

### What To Borrow Directly

The following ideas fit our DPLM plan well:

- preserve domain/function metadata during Pfam preprocessing so we can later create ProDVA-style fragment mappings
- add a generic condition encoder interface instead of hard-coding only family-prefix conditioning
- use retrieval-backed supporting examples for text/function prompts
- support freeze, LoRA, and full fine-tuning as explicit config modes
- add contrastive text/function alignment losses only after the Pfam baseline is stable

### What Not To Copy Directly

ProDVA is built around causal LM generation. Its true dynamic vocabulary mechanism is less directly compatible with DPLM because DPLM denoises fixed-length protein token grids rather than autoregressively choosing the next token.

Do not start by adding true dynamic fragment tokens to DPLM. Use lower-risk adaptations first.

### DPLM Adaptations Of Dynamic Vocabulary

Recommended progression:

1. Retrieved fragment prefixes: encode retrieved protein fragments and use them as conditioning prefix tokens.
2. Fragment-constrained infilling: use retrieved Pfam/InterPro fragments as fixed motifs or span constraints during DPLM scaffolding.
3. True dynamic fragment tokens: only if prefix and infilling approaches are insufficient.

This keeps ProDVA's core insight, retrieved functional fragments, while respecting DPLM's diffusion formulation.

### Datasets To Use Later

The ProDVA Hugging Face collection includes:

- `nwliu/CAMEO` for function-keyword design
- `nwliu/Molinst-SwissProtCLAP` for textual-description design

Each includes train, validation, test, phrase mappings, and FAISS index files. These should become the first external function/text benchmarks after the Pfam-family benchmark is working.

## Training Runs

### Run 0: Data And Evaluation Smoke Test

Purpose: validate the parser and HMMER evaluation.

Inputs:

- small Pfam-A.seed subset
- 10-50 families

Outputs:

- JSONL splits
- family vocab
- HMMER scan script results on real held-out sequences

Success criteria:

- parser extracts expected family IDs and sequences
- real held-out sequences scan back to their families at high rates
- preprocessing stats are sensible

### Run 1: Prefix-Only Frozen DPLM

Purpose: test whether soft family prefixes can steer pretrained DPLM.

Train:

- family embeddings
- prefix projector
- DPLM frozen

Evaluate:

- target family hit rate
- diversity
- novelty
- compare to unconditional DPLM

Decision:

- if target-family hit rate improves meaningfully, proceed to classifier reranking
- if not, add LoRA or auxiliary family classifier

### Run 2: Family Classifier

Purpose: create the external guidance model.

Train classifier on Pfam family labels.

Evaluate:

- validation top-1 and top-5 family accuracy
- calibration
- held-out cluster performance
- confusion by family/clan

Do not use classifier metrics as the final generation metric.

### Run 3: Reranked Generation

Purpose: test plug-and-play classifier guidance.

Evaluate these combinations:

- unconditional DPLM + reranker
- prefix-conditioned DPLM + reranker
- prefix-conditioned DPLM without reranker

Report:

- HMMER target-family hit rate before and after reranking
- number of candidates needed to obtain good hits
- diversity and novelty after reranking

### Run 4: LoRA Or Partial Fine-Tuning

Purpose: improve conditional controllability if frozen-prefix is weak.

Train:

- family embeddings
- prefix projector
- LoRA modules or selected unfrozen DPLM layers

Evaluate against Run 3.

### Run 5: Gradient Classifier Guidance

Purpose: reduce reranking waste and directly steer denoising.

Implement only after reranking has proven useful.

Evaluate:

- target-family hit rate
- diversity collapse
- invalid sequence rate
- novelty
- comparison to reranking at the same candidate budget

### Run 6: Cross-Attention Prototype

Purpose: prepare for richer text/function conditioning.

Implement only after Pfam-ID experiments clarify whether prefix conditioning is sufficient.

Compare:

- prefix conditioning
- cross-attention conditioning
- prefix plus cross-attention if needed

### Run 7: ProDVA-Style Retrieval Prefix Prototype

Purpose: test function/text readiness without implementing true dynamic vocabulary.

Inputs:

- text or keyword query
- retrieved protein examples from CAMEO or Molinst-SwissProtCLAP
- extracted Pfam/InterPro/domain fragments from retrieved examples

Conditioning:

- encode retrieved fragments as prefix or memory tokens
- generate with DPLM using the existing conditional interface

Evaluate:

- PDFBench-compatible metrics
- InterPro/IPR recovery
- ProTrek or EvoLlama alignment score
- novelty and diversity

### Run 8: PDFBench Export And Evaluation

Purpose: benchmark DPLM outputs on recent function/text conditional design tasks.

Add an export script that writes generated outputs as:

```json
{
  "instruction": "function or text prompt",
  "reference": "ground truth sequence",
  "response#1": "generated sequence",
  "response#2": "generated sequence",
  "response#3": "generated sequence"
}
```

Then run PDFBench metrics for description-guided and keyword-guided tasks.

### Run 9: ProteinGym And FLIP Scoring

Purpose: evaluate DPLM as a mutation-effect scorer and protein-engineering model.

ProteinGym inputs:

- wild-type target sequence
- mutated sequence
- mutation string
- DMS score or clinical annotation

FLIP inputs:

- sequence
- target fitness/property
- train/test split assignment

Candidate DPLM scores:

- pseudo-log-likelihood of sequence
- mutant-minus-wild-type score difference
- denoising reconstruction score at mutated positions
- family-conditioned score if a Pfam family label can be assigned

Evaluate:

- ProteinGym Spearman, NDCG, AUC, MCC, Top-K recall, MSE where applicable
- FLIP Spearman and MSE on active splits such as AAV and GB1

Use these as downstream validation metrics, not as the first controllability benchmark.

## Implementation Files

Likely new files:

```text
scripts/prepare_pfam_dataset.py
scripts/evaluate_pfam_hmmer.py
scripts/train_pfam_family_classifier.py
scripts/rerank_pfam_generations.py
scripts/export_pdfbench_results.py
scripts/score_proteingym_dplm.py
scripts/score_flip_dplm.py
generate_conditional_dplm.py
src/byprot/datamodules/dataset/family_protein.py
src/byprot/datamodules/family_protein_datamodule.py
src/byprot/models/dplm/conditional_dplm.py
src/byprot/models/dplm/conditioning.py
src/byprot/models/dplm/retrieval_conditioning.py
src/byprot/tasks/lm/conditional_dplm.py
configs/datamodule/family_protein.yaml
configs/experiment/dplm/cond_family_dplm_650m.yaml
analysis/evaluate_family_generation.py
analysis/evaluate_pdfbench_export.py
analysis/evaluate_engineering_benchmarks.py
```

The exact file list can be reduced during implementation. Prefer minimal integration into the existing DPLM task stack.

## Reproducibility Requirements

Every run should log:

- Pfam release/version
- parser backend
- preprocessing filters
- family vocabulary hash
- split strategy
- train/valid/test counts
- number of families
- per-family sequence count stats
- DPLM checkpoint
- generation seed
- requested family IDs and lengths
- HMMER database version
- HMMER thresholds

Generated outputs should include enough metadata to rerun HMMER and reranking without regenerating sequences.

## Risks And Mitigations

### Condition Ignoring

Risk: generated sequences look protein-like but do not match the requested family.

Mitigations:

- family-balanced batches
- LoRA or partial fine-tuning
- auxiliary classifier loss if needed
- external classifier reranking
- later gradient classifier guidance

### Memorization

Risk: generated sequences are near-copies of Pfam training sequences.

Mitigations:

- cluster-aware splits
- nearest-neighbor identity checks
- exact deduplication
- reporting novelty at multiple identity thresholds

### Reranker Over-Optimization

Risk: reranking selects classifier-fooling sequences rather than valid Pfam family members.

Mitigations:

- HMMER remains the primary metric
- report raw and reranked outputs
- use held-out classifier validation and calibration
- inspect disagreement between classifier and HMMER

### Length-Family Mismatch

Risk: requested lengths do not match the family domain distribution.

Mitigations:

- sample lengths from empirical family distributions
- evaluate length-conditioned and fixed-length generation separately
- report domain coverage

### Cross-Attention Complexity

Risk: cross-attention adds implementation complexity before the baseline is understood.

Mitigation:

- document it now
- keep it out of the first default run
- design conditioning APIs so it can be added later without rewriting everything

## Success Criteria

The first milestone succeeds if:

- Pfam-A parsing produces clean family-labeled sequence data.
- HMMER evaluation works and validates real held-out sequences.
- Prefix-conditioned DPLM improves target-family hit rate over unconditional DPLM.
- External classifier reranking improves target-family hit rate further without destroying diversity or novelty.
- Results are reproducible and reported against clear baselines.

The second milestone succeeds if:

- LoRA or partial fine-tuning improves conditional controllability if frozen-prefix is insufficient.
- Gradient classifier guidance can match or beat reranking at lower candidate budgets.

The third milestone succeeds if:

- The conditioning interface can support text embeddings or cross-attention memory tokens.
- Cross-attention is justified by Pfam or text-conditioning results rather than added prematurely.

## Recommended Immediate Next Steps

1. Implement `scripts/prepare_pfam_dataset.py` with `pyhmmer` backend and local fallback parser.
2. Implement HMMER evaluation on real held-out Pfam sequences.
3. Add `FamilyProteinDataset` and datamodule for JSONL Pfam records.
4. Add prefix-conditioned DPLM wrapper.
5. Run a small Pfam-A.seed smoke test.
6. Train frozen-prefix conditional DPLM on a manageable Pfam family subset.
7. Train the external family classifier.
8. Evaluate prefix-only and reranked generation using HMMER.
9. Preserve Pfam/InterPro metadata in a form that can later produce ProDVA-style fragment mappings.
10. Add a generic condition encoder interface so Pfam IDs, text prefixes, retrieved fragments, and future cross-attention memory share a common model API.
11. Add a later scoring interface for ProteinGym and FLIP so DPLM can be evaluated on mutation-effect and protein-engineering tasks.
