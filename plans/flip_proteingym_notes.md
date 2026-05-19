# FLIP And ProteinGym Notes For Conditional DPLM

## Purpose

This note records how FLIP and ProteinGym should inform the conditional DPLM roadmap.

They are not replacements for the Pfam-first HMMER evaluation. They are useful later for evaluating whether generated or edited proteins are plausible, functional, and useful for protein engineering tasks.

## FLIP Status

The public repository found during review is:

```text
https://github.com/J-SNACKKB/FLIP
```

I did not find a separate public GitHub or Hugging Face artifact explicitly named `FLIP2` from targeted searches. Treat `FLIP2` as a follow-up item to verify against paper title, authors, or exact URL if available.

For now, the actionable benchmark is FLIP.

## FLIP Summary

FLIP is a benchmark suite for protein engineering tasks. It focuses on how well sequence-based models represent properties relevant to design and directed evolution.

Current active split families include:

- AAV
- GB1
- Meltome
- SCL
- Bind
- SAV
- Secondary Structure
- Conservation

FLIP splits are designed to test different extrapolation regimes, such as:

- train on low-mutational-distance variants and test on high-distance variants
- train on low-fitness variants and test on high-fitness variants
- train/test across designed versus mutationally sampled variants
- human versus mixed species settings

Example active AAV splits:

- `des_mut`
- `mut_des`
- `one_vs_many`
- `two_vs_many`
- `seven_vs_many`
- `low_vs_high`

Example active GB1 splits:

- `one_vs_rest`
- `two_vs_rest`
- `three_vs_rest`
- `low_vs_high`

Typical split files contain:

```text
sequence
target
set
validation
```

Baseline metrics include regression performance such as Spearman correlation and MSE.

## How FLIP Fits DPLM

FLIP is most relevant for evaluating DPLM as a sequence scorer or design proposal model on protein engineering tasks.

Possible uses:

1. Score FLIP variants with DPLM pseudo-likelihood or denoising likelihood and correlate with fitness.
2. Train lightweight predictors on DPLM embeddings and compare to ESM/one-hot baselines.
3. Use conditional DPLM to propose variants around FLIP wild-type sequences, then score with FLIP task predictors.
4. Evaluate whether DPLM can generate high-fitness variants in extrapolation splits such as GB1 `low_vs_high` or AAV `one_vs_many`.

FLIP should not be the first conditional-generation benchmark because it is not primarily a family-condition satisfaction benchmark. It is better for downstream fitness/usefulness evaluation.

## ProteinGym Summary

Repository:

```text
https://github.com/OATML-Markslab/ProteinGym
```

Website:

```text
https://www.proteingym.org/
```

Hugging Face dataset:

```text
OATML-Markslab/ProteinGym_v1
```

ProteinGym is a large benchmark for mutation effect prediction and design. It includes:

- DMS substitutions
- DMS indels
- clinical substitutions
- clinical indels

Scale from the README:

- about 2.7M missense variants across 217 DMS assays and 2,525 clinical proteins
- about 300k mutants across 74 DMS indel assays and 1,555 clinical proteins

Important columns:

```text
mutated_sequence
target_seq
mutant
DMS_score
DMS_score_bin
DMS_id
```

Clinical files use:

```text
mutated_sequence
target_seq
mutant
protein_id
annotation
```

ProteinGym reports metrics including:

- Spearman
- NDCG
- AUC
- MCC
- Top-K recall
- MSE for supervised settings

Metrics are aggregated by UniProt ID and functional categories to avoid over-weighting proteins with many assays.

## How ProteinGym Fits DPLM

ProteinGym is highly relevant for evaluating DPLM as a mutation-effect scorer and for validating generated designs around known proteins.

Possible uses:

1. Zero-shot DPLM variant scoring.
2. Conditional infilling/scaffolding around known target sequences.
3. Comparing generated variants against DMS fitness landscapes.
4. Evaluating whether family-conditioned DPLM learns mutation preferences that correlate with experimental fitness.

Potential DPLM score definitions:

- pseudo-log-likelihood of mutant sequence
- difference between mutant and wild-type sequence score
- denoising reconstruction score for mutated positions
- conditional score under Pfam family prefix if the target maps to a Pfam family

ProteinGym is not a primary text/function generation benchmark, but it is a strong validation benchmark for whether DPLM's learned sequence distribution aligns with measured fitness.

## Recommended Integration Order

1. Finish Pfam-first HMMER target-family generation benchmark.
2. Implement DPLM sequence scoring for wild-type and mutant sequences.
3. Run a small ProteinGym subset to validate scoring mechanics.
4. Run FLIP GB1 and AAV scoring as compact protein-engineering tasks.
5. Add conditional infilling/scaffolding experiments on ProteinGym/FLIP targets.
6. Use ProteinGym/FLIP outcomes as downstream design validation, not as the first controllability metric.

## Relationship To PDFBench

PDFBench evaluates function/text-conditioned generated sequences.

ProteinGym and FLIP evaluate fitness prediction, variant scoring, and engineering-relevant extrapolation.

Recommended benchmark roles:

- Pfam/HMMER: first controllability benchmark for family-conditioned generation.
- PDFBench: later benchmark for text/function-conditioned generation.
- ProteinGym: mutation-effect and fitness-alignment benchmark.
- FLIP: compact protein-engineering extrapolation benchmark.

## Open Follow-Up

Find the exact FLIP2 artifact if the intended benchmark is separate from FLIP:

- paper title
- author list
- GitHub URL
- Hugging Face dataset name
- benchmark website

Until then, implement around the public FLIP benchmark and keep the docs explicit that FLIP2 remains to be verified.
