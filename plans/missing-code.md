# Missing Code Audit for `dplm2_paper.md`

This report compares the full contents of `dplm2_paper.md` against the checked-out repository. I read the paper end to end and audited the source, configs, scripts, README, analysis utilities, and local data layout. I did not treat vendored OpenFold as DPLM-2 implementation code except where this repo imports it.

## Executive Summary

The repository contains a substantial DPLM-2 implementation: multimodal token modeling, LFQ structure tokenizer code, LoRA warm-up configs, self-mixup support, unconditional/co-generation/folding/inverse-folding scripts, motif-scaffolding generation, and a structure/self-consistency evaluator.

The largest missing or incomplete paper-facing elements are:

1. A fully reproducible dataset construction pipeline for the paper's PDB + SwissProt training set and evaluation metadata.
2. Complete paper experiment reproduction code for all tables/figures, especially baseline comparisons, ablations, and secondary-structure analyses.
3. Representation-learning training/evaluation code for the SaProt downstream tasks, which the README explicitly says lives on a separate `representationlearning` branch.
4. Motif-scaffolding evaluation code as scripts; generation exists, but success-rate calculation is left in notebooks.
5. Novelty (`pdb-TM`) and full diversity evaluation against the PDB/reference databases are not complete from the checked-out code alone.
6. Several configs and README commands reference filenames or paths that do not exist in this checkout.

## What Is Present

### DPLM-2 Model and Training

Present:

- `src/byprot/models/dplm2/dplm2.py` implements index-token DPLM-2 with sequence and structure streams concatenated, modality-specific masking, independent diffusion timesteps, folding/inverse/joint/single-modality training mixtures, generation, and self-mixup.
- `src/byprot/models/dplm2/dplm2_bit.py` and `src/byprot/models/dplm2/modules/dplm2_bit_modeling_esm.py` implement the later bit-based variant.
- `configs/experiment/dplm2/dplm2_150m.yaml`, `dplm2_650m.yaml`, and `dplm2_3b.yaml` configure the 150M/650M/3B DPLM-2 runs.
- `configs/experiment/dplm2/dplm2_650m_selfmixup.yaml` configures self-mixup.
- LoRA warm-up is configured under `model.lora` in DPLM-2 configs and implemented through model utilities.
- `src/byprot/tasks/lm/dplm2.py` implements the Lightning training task and logs sequence/structure losses and index accuracy.

Missing or incomplete:

- The paper says all models use AdamW with beta2 = 0.95. The DPLM-2 configs use beta2 = 0.98. This may be intentional drift, but it means the code/config does not exactly match the hyperparameter section.
- The paper says structure and sequence noising use distinct schedulers. Code supports distinct sampled timesteps, but I did not find a configurable scheduler family beyond uniform timestep sampling and linear/constant loss weights.
- The paper's ablation matrix for sequence pretraining and data augmentation is not represented as reproducible experiment configs or scripts.
- There is no single command or manifest that reproduces the 150M/650M/3B paper runs including exact checkpoint initialization, data snapshot, and evaluation.

### LFQ Structure Tokenizer

Present:

- `src/byprot/models/structok/structok_lfq.py` implements a VQ-style structure tokenizer with GVP encoder wrapper, LFQ quantizer, and ESMFold/OpenFold-style structure decoder.
- `src/byprot/models/structok/modules/lfq.py` implements lookup-free quantization.
- `src/byprot/models/structok/modules/loss.py` includes FAPE/backbone losses, violation losses, distogram-related losses, sequence cross-entropy, and codebook/perplexity logging.
- `configs/experiment/structok/structok_lfq_8k_pdb_swissprot_c512.yaml` configures an 8192-code, 13-bit tokenizer with 4 trunk blocks and crop size 512.
- `src/byprot/tasks/struct_tokenizer/structok.py` implements training and validation metrics for the tokenizer.

Missing or incomplete:

- The paper compares LFQ to conventional VQ-VAE. A VQ-VAE module exists (`src/byprot/models/structok/modules/vqvae.py`), but I did not find a matching VQ-VAE experiment config, training recipe, or evaluation script to reproduce Fig. tokenizer LFQ-vs-VQ results.
- The paper reports correlation of tokens with secondary structure. The repo has `md.compute_dssp` helpers and notebooks, but no script that reproduces the tokenizer secondary-structure plot or token/SSP statistics.
- The paper says the tokenizer is trained on the same 200K structure dataset; the config points to `metadata.pdb_afdb_cameo.plddt.structok_lfq.csv`, but that file is not present in this checkout.
- The paper says the encoder is a pretrained GVP-Transformer and frozen. The config has `encoder_config.freeze: true`, but the checkpoint/provenance for the pretrained GVP encoder is not included locally.

### PDB + SwissProt Data

Present:

- `scripts/download_pdb_swissprot_hf.sh` downloads `airkingbd/pdb_swissprot` from Hugging Face.
- `src/byprot/datamodules/dataset/tokenized_protein.py` can preprocess a CSV into HF datasets, filter AFDB/SwissProt by average pLDDT, trim low-confidence ends, and produce tokenized sequence/structure examples.
- `src/byprot/datamodules/tokenized_protein_datamodule.py` supports length cropping with probability 50% and optional cluster sampling.
- `src/byprot/datamodules/pdb_dataset/pdb_datamodule.py` supports PDB/AFDB metadata loading, filtering, confidence masking, cropping, and cluster sampling for tokenizer training.

Missing or incomplete:

- The raw construction pipeline for the exact paper training data is missing. I did not find scripts that:
  - collect the 20K clustered experimental PDB structures,
  - collect and filter the 200K AFDB SwissProt structures,
  - compute or attach `struct_seq` tokens for all structures,
  - compute metadata such as clusters, pLDDT strings, DSSP percentages, radius of gyration, and processed pickle paths,
  - produce `metadata.pdb_afdb_cameo.plddt.structok_lfq.csv`.
- The code has two thresholds for trimming low-confidence ends: paper says pLDDT < 50 at ends, but `tokenized_protein.py` and `pdb_datamodule.py` use threshold 70 in relevant preprocessing paths.
- README tells users to run `bash scripts/download_pdb_swissprot.sh`, but the checked-out script is named `scripts/download_pdb_swissprot_hf.sh`.
- The downloaded HF dataset appears under `scripts/data-bin/pdb_swissprot` in this checkout, while configs default to `${paths.data_dir}`/`data-bin`, so local paths are inconsistent unless users move files or override config.

### Unconditional Generation and Co-Generation

Present:

- `generate_dplm2.py` supports `sequence_generation`, `backbone_generation`, and `co_generation`.
- `src/byprot/utils/protein/evaluator_dplm2.py` can detokenize structures, run ESMFold and optionally ProteinMPNN, compute self-consistency RMSD/TMscore, pLDDT-derived metrics, and FoldSeek cluster counts.
- `analysis/cal_plddt_dir.py`, `analysis/cal_tmscore.py`, `analysis/TMscore`, and `analysis/TMalign` provide supporting evaluation utilities.
- `analysis/uncond_analysis.ipynb` and `analysis/plot.ipynb` exist for analysis/plotting.

Missing or incomplete:

- The paper's unconditional benchmark includes quality, novelty, and diversity for multiple baselines. The repo does not include a complete scripted pipeline that regenerates all paper tables/figures.
- `pdb-TM` novelty requires comparing generated structures against known PDB structures. I found pairwise TMscore utilities, but no included PDB reference database, prepared index, or end-to-end novelty script.
- `inner-TM` diversity exists partially through pairwise TMscore/FoldSeek helpers, but the exact paper aggregation by length/model/baseline is notebook/manual rather than a reproducible CLI.
- The symmetric oligomer case study is described in the paper. I did not find code for forcing duplicated predicted structure/sequence token patterns for symmetric oligomer design.
- The paper's length extrapolation evaluation for 600-1000 residues is not encoded as a reproducible experiment script; generation supports arbitrary `--seq_lens`, but the paper analysis is not packaged.

### Secondary Structure Statistics

Present:

- `src/byprot/modules/protein_metrics.py` and `src/byprot/utils/protein/utils.py` use `mdtraj.compute_dssp`.
- The evaluator records `helix_percent` and `strand_percent` for top samples.
- Notebooks in `analysis/` likely support some plotting.

Missing or incomplete:

- No script reproduces the paper's secondary-structure distribution analysis against natural PDB proteins.
- I did not find code for the simplex plots or natural-vs-DPLM-2-vs-ESM3-vs-RFDiffusion-vs-MultiFlow comparisons.
- Loop percentage is discussed extensively in the paper, but the evaluator records helix and strand percentages only in the top-sample CSV; loop percentage must be inferred or separately calculated.

### Forward Folding

Present:

- `generate_dplm2.py --task folding` masks structure tokens conditioned on amino-acid tokens.
- `configs/experiment/structok/inference/forward_folding.yaml` and `evaluator_dplm2.py` support evaluation against metadata-provided ground truth structures.
- README documents CAMEO 2022 and PDB date split usage.

Missing or incomplete:

- CAMEO/PDB date data are not present in the checked-out `data-bin` root. The README points to `scripts/download_metadata.sh`, which downloads from Zenodo, but the repo itself does not include the metadata.
- README says `bash script/download_metadata.sh`, but the folder is `scripts/`.
- The paper mentions supervised fine-tuning (SFT) for folding objective `log p(z | s)`. I did not find a dedicated SFT config or script that sets training exclusively to folding beyond adjusting loss ratios manually.
- There is no code to reproduce the paper table against ESMFold, FoldFlow2, MultiFlow, ESM3, and model-size variants in one evaluation harness.

### Inverse Folding

Present:

- `generate_dplm2.py --task inverse_folding` masks amino-acid tokens conditioned on structure tokens.
- `evaluator_dplm2.py` computes sequence recovery, self-consistency folding metrics, and optional ProteinMPNN comparison.
- DPLM inverse folding code exists separately in `src/byprot/models/dplm/dplm_invfold.py` and `src/byprot/tasks/lm/dplm_invfold.py`.

Missing or incomplete:

- DPLM-2 inverse-folding evaluation relies on tokenized structure FASTA and metadata that are not present without external downloads.
- The paper table includes MultiFlow and ESM3 comparisons; those baselines are not implemented or scripted here.
- The inverse-folding metrics emitted by the current evaluator use names such as `Average_seq_recovery`, `Average_bb_rmsd`, and PMPNN fields. The paper reports `AAR` and `scTM`; scTM exists in lower-level CSVs as `bb_tmscore`, but the final summary does not directly mirror the paper table.

### Motif Scaffolding

Present:

- `run/scaffold_generate_dplm2.py` implements DPLM-2 motif-scaffold generation conditioned on motif amino-acid and structure-token FASTA files.
- `src/byprot/utils/scaffold_utils.py` hard-codes the 24 motif benchmark names, motif intervals, and length ranges.
- `scripts/download_motif_scaffolds.sh` downloads motif PDB/token files from Zenodo.
- README documents generation plus ESMFold and sc-TMscore evaluation steps.

Missing or incomplete:

- The paper's motif-scaffolding success-rate evaluation is not provided as a script. README says to use `analysis/motif_analysis.ipynb`.
- I did not find a CLI implementation of motif-RMSD < 1 Angstrom against native motif coordinates.
- The paper evaluates sequence-based, structure-based, and co-generation modes; the DPLM-2 generation script focuses on co-generation. Structure-only and sequence-only DPLM-2 motif modes are not exposed as first-class options in `run/scaffold_generate_dplm2.py`.
- The benchmark references FrameFlow/Yim et al. settings, but the benchmark CSV itself is not included locally.
- The generator hard-codes motif definitions and path defaults, making it difficult to reproduce variants or new motif benchmarks without editing code.

### Representation Learning

Present:

- DPLM/DPLM-2 model classes expose forward passes and hidden states that could be used for representations.
- README includes a representation-learning results table.

Missing:

- This checked-out branch does not contain the SaProt downstream training/evaluation pipeline for Thermostability, HumanPPI, Metal Ion Binding, EC, GO-MF/BP/CC, DeepLoc-Subcellular, or DeepLoc-Binary.
- README explicitly says users should select the `representationlearning` branch for predictive-task evaluation.
- The top-level README TODO list still includes "Representation learning of DPLM-2".
- I did not find dataset download scripts, task heads, datamodules, configs, or metrics for the SaProt predictive tasks in this branch.
- The paper's DeepLoc no-pretraining/catastrophic-forgetting comparison is not reproducible from this checkout.

### Baselines and External Comparisons

Missing or external-only:

- MultiFlow training/evaluation and retraining on this paper's data.
- RFDiffusion generation/evaluation.
- ESM3 / ESM3-Open generation/evaluation.
- ESMFold baseline table reproduction beyond folding generated sequences for self-consistency.
- ProteinMPNN is referenced through `vendor/ProteinMPNN`, but this checkout only has `vendor/openfold`; ProteinMPNN itself is absent.
- FoldSeek is called as an external executable but not installed or vendored.
- SaProt is not included; representation learning is delegated to another branch/repo.

## Config and Script Mismatches

- `README.md` references `scripts/download_pdb_swissprot.sh`; repository has `scripts/download_pdb_swissprot_hf.sh`.
- `README.md` references `script/download_metadata.sh`; repository has `scripts/download_metadata.sh`.
- `README.md` references `anylasis/plddt_calculate.sh`; repository has `analysis/plddt_calculate.sh`.
- `configs/experiment/structok/inference/*.yaml` use absolute or placeholder paths such as `/root/research/projects/ByProt`, `/path/to/fasta/dir`, and `path/to/colabfold-conda/bin/colabfold_batch`.
- `configs/experiment/structok/inference/*.yaml` reference `vendor/ProteinMPNN`, but the repository has no `vendor/ProteinMPNN` directory.
- Several configs expect metadata under `data-bin/metadata/...`; this checkout's local visible data under `data-bin` is mostly Pfam, while PDB/SwissProt and UniRef downloads are under `scripts/data-bin`.
- `generate_dplm2.py` has default `--model_name airkingbd/dplm_150m`, which is a DPLM model name, not a DPLM-2 model name.

## Data and Artifacts Not Present Locally

The following paper-relevant artifacts are not present in the checked-out repository root:

- Final PDB + SwissProt training CSV/metadata with structure tokens and all filters applied.
- `data-bin/metadata/pdb_afdb_cameo.csv`.
- `metadata.pdb_afdb_cameo.plddt.structok_lfq.csv`.
- `data-bin/cameo2022/aatype.fasta`.
- `data-bin/cameo2022/struct.fasta`.
- `data-bin/PDB_date/struct.fasta`.
- Motif scaffold files under `data-bin/scaffolding-pdbs` unless downloaded.
- PDB reference database or prepared list for novelty (`pdb-TM`) evaluation.
- Baseline outputs for ESM3, RFDiffusion, MultiFlow, ProteinMPNN, and natural PDB secondary-structure distributions.
- Pretrained GVP encoder provenance/checkpoint if required separately from released tokenizer checkpoints.

## Priority Fix List

### High Priority

1. Add a reproducible `scripts/prepare_dplm2_dataset.py` pipeline or documented Makefile that creates the exact DPLM-2 PDB/SwissProt metadata, structure tokens, train/valid/test splits, clusters, pLDDT filters, and CAMEO/PDB-date metadata.
2. Add script versions of all paper evaluations:
   - unconditional quality/diversity/novelty,
   - secondary-structure distributions,
   - folding table,
   - inverse-folding table,
   - motif-scaffolding success table,
   - tokenizer reconstruction table/plots.
3. Bring representation learning into this branch or add a clear submodule/branch checkout script with exact commands and configs.
4. Vendor or clearly install-check external executables/models used by the paper pipeline: ProteinMPNN, FoldSeek, ESMFold dependencies, and TMscore/TMalign.

### Medium Priority

1. Add configs for the paper ablations: no DPLM warm-up, PDB-only, PDB+SwissProt, self-mixup on/off, LFQ vs VQ-VAE.
2. Add a dedicated folding SFT config rather than requiring manual loss-ratio overrides.
3. Add a CLI for motif-RMSD and success-rate aggregation; stop relying on `analysis/motif_analysis.ipynb`.
4. Add a novelty pipeline with a documented PDB reference source and caching.
5. Normalize metric names in final CSV summaries to match paper terms (`scTM`, `scRMSD`, `AAR`, `pdb-TM`, `inner-TM`, cluster ratio).

### Low Priority

1. Clean README typos and path mismatches.
2. Replace hard-coded motif dictionaries with a benchmark CSV parser.
3. Add smoke tests for `generate_dplm2.py` task initialization, tokenizer detokenization, and evaluator config loading.
4. Add a single `paper_reproduce/` directory containing commands and expected output schema for each table/figure.

## Bottom Line

The core DPLM-2 and structure-tokenizer mechanisms are present, but the repository is not a complete paper-reproduction artifact. Most missing elements are around exact data creation, external baseline integration, scripted metrics/plots, and downstream representation-learning experiments.
