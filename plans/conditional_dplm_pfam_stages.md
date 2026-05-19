# Conditional DPLM Pfam Plan: Executive Summary

## Stage 0: Data And Evaluation Foundation

Build the Pfam-A parser, preprocessing pipeline, cluster-aware splits, and HMMER/Pfam evaluation.

Deliverables:

- `train.jsonl`, `valid.jsonl`, `test.jsonl`
- `family_vocab.json`
- `family_metadata.json`
- `preprocess_stats.json`
- HMMER evaluation script

Success criterion:

- real held-out Pfam sequences scan back to their labeled families at high rates.

## Stage 1: Prefix-Conditioned DPLM Baseline

Add learned Pfam family prefix tokens to DPLM.

Training starts with frozen DPLM plus trainable family embeddings and prefix projector.

Success criterion:

- prefix-conditioned DPLM improves HMMER target-family hit rate over unconditional DPLM.

## Stage 2: External Classifier Reranking

Train a Pfam family classifier and use it as a plug-and-play reranker over generated candidates.

Evaluate both unconditional and prefix-conditioned DPLM with reranking.

Success criterion:

- reranking improves HMMER target-family hit rate without collapsing diversity or novelty.

## Stage 3: Stronger Conditional Fine-Tuning

If frozen-prefix conditioning is weak, add LoRA or partial DPLM fine-tuning.

Success criterion:

- conditional controllability improves beyond frozen-prefix baseline while preserving protein realism and novelty.

## Stage 4: Gradient Classifier Guidance

If classifier reranking works but requires too many candidates, add classifier-gradient guidance during denoising.

Success criterion:

- guided denoising matches or beats reranking at lower candidate budgets.

## Stage 5: ProDVA-Inspired Text And Fragment Readiness

Use ProDVA as the main design reference for the later function/text phase.

Key ideas to borrow:

- retrieval-backed supporting sequences and fragments
- dynamic protein fragment vocabulary
- text-prefix conditioning
- auxiliary fragment type and description-alignment losses
- freeze, LoRA, and full fine-tuning modes

Success criterion:

- the DPLM conditioning interface can accept Pfam IDs now and later accept text embeddings, retrieved protein fragments, or dynamic fragment candidates without rewriting the data/evaluation pipeline.

## Stage 6: Cross-Attention And Text Readiness

Plan cross-attention as a later conditioning module for Pfam descriptions, InterPro descriptions, UniProt function text, GO terms, and future protein-text encoders.

Do not implement it as the first default architecture.

Success criterion:

- cross-attention is introduced only if prefix conditioning and classifier guidance are insufficient or if text-conditioning becomes the immediate target.

## Stage 7: PDFBench-Style Function/Text Evaluation

Adopt PDFBench as the reference benchmark taxonomy for function and text conditional generation after the Pfam-first milestone.

Relevant metric groups:

- plausibility: perplexity and repetitiveness
- foldability: ESMFold-based foldability
- language/function alignment: ProTrek Score, EvoLlama Score, GO Recovery, IPR Recovery, Retrieval Accuracy
- novelty: MMseqs/Foldseek novelty
- diversity: sequence and structure diversity
- similarity: GT identity, GT TM-score, ESMScore

Success criterion:

- DPLM outputs can be exported in a PDFBench-compatible JSON format for description-guided and keyword-guided evaluation.

## Stage 8: ProteinGym And FLIP Engineering Validation

Use ProteinGym and FLIP after the Pfam-first benchmark to evaluate DPLM as a mutation-effect scorer and design proposal model.

ProteinGym roles:

- DMS substitution and indel scoring
- clinical variant scoring
- Spearman, NDCG, AUC, MCC, Top-K recall, and MSE metrics

FLIP roles:

- compact protein-engineering tasks such as AAV and GB1
- extrapolation splits such as low-to-high fitness and low-to-high mutational distance
- Spearman and MSE for fitness prediction

Success criterion:

- DPLM sequence or conditional scores correlate with measured fitness on selected ProteinGym/FLIP tasks, and generated/edited candidates can be evaluated against task-specific predictors or measured landscapes.

## Primary Metric

The main benchmark is HMMER/Pfam target-family hit rate on generated sequences.

Supporting metrics:

- target clan hit rate
- no-hit rate
- bit score and E-value distributions
- domain coverage
- generated-sample diversity
- nearest-neighbor novelty against training sequences

Later function/text metrics:

- ProTrek Score
- EvoLlama Score
- GO Recovery
- IPR Recovery
- Retrieval Accuracy
- perplexity and repetitiveness
- foldability
- sequence and structure novelty/diversity

Later protein-engineering metrics:

- ProteinGym Spearman, NDCG, AUC, MCC, Top-K recall, and MSE
- FLIP Spearman and MSE on active extrapolation splits

## First Serious Run

The first serious run should use:

- Pfam family ID as the only conditioning target
- prefix-conditioned DPLM
- family-balanced training
- empirical family length sampling
- external family classifier reranking
- HMMER/Pfam evaluation as the final judge
