# Pretrained DPLM-2 Paper Reproduction Roadmap

Goal: reproduce the paper's reported results using released pretrained models, while reusing as much existing repository code as possible. This roadmap intentionally starts with the lowest-friction evaluation surfaces before moving into experiments that need more external tools, reference databases, or custom aggregation.

## Recommendation

Start with **forward folding on CAMEO 2022 / PDB-date**.

Why this is the easiest useful component:

- It uses the released pretrained DPLM-2 model directly.
- Existing code already supports the generation path: `generate_dplm2.py --task folding`.
- Existing code already supports the evaluation path: `src/byprot/utils/protein/evaluator_dplm2.py -cn forward_folding`.
- It has ground-truth structures, so the primary metrics are direct RMSD/TMscore against references.
- It avoids the hardest dependencies at first: ProteinMPNN, FoldSeek clustering, PDB novelty search, baseline model execution, and motif-RMSD parsing.
- It exercises the most important DPLM-2 machinery: tokenizer, structure detokenizer, multimodal mask-predict generation, metadata loading, and metric aggregation.

After forward folding works, the natural next step is **inverse folding**, because it uses the same downloaded metadata/testsets and same generation/evaluation skeleton, but flips the conditioning direction.

## Prioritized Roadmap

### Phase 0: Reproduction Harness and Environment Checks

Objective: create a small, boring, repeatable shell/Python harness that catches path and dependency problems before running expensive jobs.

Existing code to reuse:

- `generate_dplm2.py`
- `scripts/download_metadata.sh`
- `scripts/download_motif_scaffolds.sh`
- `scripts/download_pdb_swissprot_hf.sh`
- `src/byprot/utils/protein/tokenize_pdb.py`
- `src/byprot/utils/protein/evaluator_dplm2.py`
- `test_dplm2_bit_basic.py` as rough precedent for model-load smoke tests

Tasks:

1. Add `scripts/reproduce/check_env.sh`.
2. Check that Python can import `torch`, `byprot`, `transformers`, `esm`, `openfold`, `mdtraj`, and `Bio`.
3. Check that CUDA is visible when expected.
4. Check that `analysis/TMscore` and `analysis/TMalign` exist and are executable.
5. Check optional external tools separately: `foldseek`, `colabfold_batch`, and `vendor/ProteinMPNN`.
6. Add a tiny model-load smoke test for:
   - `airkingbd/dplm2_650m`
   - `airkingbd/dplm2_bit_650m`
   - `airkingbd/struct_tokenizer`
7. Fix README/script path mismatches as they block reproduction:
   - `scripts/download_pdb_swissprot.sh` -> `scripts/download_pdb_swissprot_hf.sh`
   - `script/download_metadata.sh` -> `scripts/download_metadata.sh`
   - `anylasis/plddt_calculate.sh` -> `analysis/plddt_calculate.sh`

Deliverable:

- `scripts/reproduce/check_env.sh`
- `scripts/reproduce/smoke_dplm2_load.py`
- A short `plans/reproduction-notes.md` recording exact hardware, package versions, model names, and commit hash.

Success criterion:

- A command fails early with a clear missing dependency/path message.
- Loading the pretrained model and structure tokenizer works before any full benchmark is attempted.

## Phase 1: Forward Folding Reproduction

Objective: reproduce the paper's sequence-conditioned structure prediction numbers for DPLM-2 with pretrained models.

Existing code to reuse:

- `generate_dplm2.py --task folding`
- `configs/experiment/structok/inference/forward_folding.yaml`
- `src/byprot/utils/protein/evaluator_dplm2.py`
- `scripts/download_metadata.sh`

Tasks:

1. Add `scripts/reproduce/download_eval_data.sh` that wraps `scripts/download_metadata.sh` and verifies:
   - `data-bin/cameo2022/aatype.fasta`
   - `data-bin/cameo2022/struct.fasta`
   - `data-bin/PDB_date/aatype.fasta` if present
   - `data-bin/PDB_date/struct.fasta` if present
   - `data-bin/metadata/pdb_afdb_cameo.csv`
2. Add `scripts/reproduce/run_folding.sh` with arguments:
   - model name, default `airkingbd/dplm2_650m`
   - split, default `cameo2022`
   - output root, default `generation-results/reproduce`
   - max iterations, default `100`
3. The script should run:
   - `python generate_dplm2.py --task folding --input_fasta_path ... --sampling_strategy argmax --unmasking_strategy deterministic --max_iter 100`
   - `python src/byprot/utils/protein/evaluator_dplm2.py -cn forward_folding inference.input_fasta_dir=...`
4. Add `scripts/reproduce/summarize_folding.py` to normalize evaluator outputs into a paper-like CSV:
   - model
   - split
   - n
   - mean RMSD
   - median RMSD
   - mean TMscore
   - median TMscore
   - length bins if available
5. Run first on a tiny subset, then full CAMEO.

Deliverable:

- `results/reproduce/folding/cameo2022/dplm2_650m/summary.csv`
- `results/reproduce/folding/cameo2022/dplm2_650m/run_config.yaml`

Success criterion:

- The summary produces RMSD/TMscore columns comparable to the folding table.
- The same command can be rerun without manual notebook steps.

## Phase 2: Inverse Folding Reproduction

Objective: reproduce structure-conditioned sequence generation with pretrained DPLM-2.

Existing code to reuse:

- `generate_dplm2.py --task inverse_folding`
- `configs/experiment/structok/inference/inverse_folding.yaml`
- `src/byprot/utils/protein/evaluator_dplm2.py`

Tasks:

1. Add `scripts/reproduce/run_inverse_folding.sh`.
2. Use `data-bin/cameo2022/struct.fasta` as the first target.
3. Generate with:
   - `--sampling_strategy argmax`
   - `--unmasking_strategy deterministic`
   - `--max_iter 100`
4. Evaluate with:
   - `evaluator_dplm2.py -cn inverse_folding`
5. Add `scripts/reproduce/summarize_inverse_folding.py`.
6. Normalize names to paper metrics:
   - `Average_seq_recovery` -> `AAR`
   - `bb_tmscore` or equivalent top-sample TM column -> `scTM`
   - `mean_plddt` where available -> `pLDDT`

Deliverable:

- `results/reproduce/inverse_folding/cameo2022/dplm2_650m/summary.csv`

Success criterion:

- AAR and scTM are generated from pretrained DPLM-2 without notebook steps.

## Phase 3: Unconditional Co-Generation Quality

Objective: reproduce the core DPLM-2 co-generation quality metrics first, before full novelty/diversity.

Existing code to reuse:

- `generate_dplm2.py --task co_generation`
- `configs/experiment/structok/inference/unconditional_codesign.yaml`
- `src/byprot/utils/protein/evaluator_dplm2.py`
- `analysis/cal_plddt_dir.py` if needed

Tasks:

1. Add `scripts/reproduce/run_cogeneration.sh`.
2. Support lengths `100 200 300 400 500` and later `600 700 800 900 1000`.
3. Start with small `--num_seqs 5` smoke runs, then paper-scale `--num_seqs 100`.
4. Evaluate generated sequences/structures through `unconditional_codesign`.
5. Add `scripts/reproduce/summarize_cogeneration_quality.py`.
6. Emit:
   - length
   - number of samples
   - mean/median `scTM`
   - mean/median `scRMSD`
   - mean pLDDT
   - designability rate under paper thresholds

Defer:

- PDB novelty (`pdb-TM`)
- full pairwise `inner-TM`
- FoldSeek cluster ratio
- baseline comparisons

Deliverable:

- `results/reproduce/cogeneration/dplm2_650m/quality_summary.csv`

Success criterion:

- Co-generated structures and sequences are saved, detokenized, folded/evaluated, and summarized by length from one command.

## Phase 4: Diversity and Novelty

Objective: extend co-generation from quality-only to the paper's diversity/novelty metrics.

Existing code to reuse:

- `analysis/cal_tmscore.py`
- `analysis/TMscore`
- `analysis/TMalign`
- `src/byprot/utils/protein/utils.py` FoldSeek helpers
- `evaluator_dplm2.py` diversity hooks

Tasks:

1. Add `scripts/reproduce/prepare_pdb_reference.sh`.
2. Decide and document the PDB reference snapshot. This is essential because `pdb-TM` depends on database contents.
3. Add `scripts/reproduce/run_pairwise_tmscore.py` or wrap `analysis/cal_tmscore.py` in a stable interface.
4. Add `scripts/reproduce/run_foldseek_clusters.sh`.
5. Add `scripts/reproduce/summarize_diversity_novelty.py`.
6. Emit:
   - `inner-TM`
   - `pdb-TM`
   - FoldSeek cluster count
   - normalized cluster ratio

Deliverable:

- `results/reproduce/cogeneration/dplm2_650m/diversity_novelty_summary.csv`

Success criterion:

- Quality, novelty, and diversity can be reproduced for DPLM-2 generated samples with a documented reference database.

## Phase 5: Motif Scaffolding

Objective: replace notebook/manual motif evaluation with a reproducible CLI.

Existing code to reuse:

- `scripts/download_motif_scaffolds.sh`
- `run/scaffold_generate_dplm2.py`
- `src/byprot/utils/scaffold_utils.py`
- `analysis/cal_plddt_dir.py`
- `src/byprot/utils/protein/evaluator_dplm2.py`
- `analysis/motif_analysis.ipynb` as behavioral reference only

Tasks:

1. Add `scripts/reproduce/run_motif_scaffolding.sh`.
2. Generate 100 samples per motif using pretrained `airkingbd/dplm2_650m`.
3. Add `scripts/reproduce/calc_motif_rmsd.py`.
4. Add `scripts/reproduce/summarize_motif_scaffolding.py`.
5. Implement paper thresholds:
   - motif preservation: motif-RMSD < 1 Angstrom
   - co-generation quality: scTM > 0.8
   - sequence-only quality: pLDDT > 70 if reproducing DPLM baseline
6. Replace hard-coded assumptions where possible with a CSV manifest:
   - motif name
   - PDB name
   - chain
   - motif residue ranges
   - allowed scaffold length range

Deliverable:

- `results/reproduce/motif_scaffolding/dplm2_650m/problem_summary.csv`
- `results/reproduce/motif_scaffolding/dplm2_650m/overall_summary.csv`

Success criterion:

- The 24-problem motif table can be regenerated without opening `analysis/motif_analysis.ipynb`.

## Phase 6: Secondary Structure Statistics

Objective: reproduce the natural-like secondary-structure analysis.

Existing code to reuse:

- `src/byprot/modules/protein_metrics.py`
- `src/byprot/utils/protein/utils.py`
- `analysis/plot.ipynb` as reference

Tasks:

1. Add `scripts/reproduce/calc_secondary_structure.py`.
2. Compute DSSP percentages for:
   - DPLM-2 generated structures
   - natural PDB reference structures
   - any available baseline outputs
3. Emit helix, sheet, loop percentages.
4. Add `scripts/reproduce/plot_secondary_structure.py` or at least CSVs suitable for plotting.
5. Add simplex plot support only after CSVs are correct.

Deliverable:

- `results/reproduce/secondary_structure/dplm2_650m/ssp_summary.csv`

Success criterion:

- DPLM-2 vs natural PDB secondary-structure percentages are reproducible from a command.

## Phase 7: Baseline Comparisons

Objective: reproduce paper tables against external baselines only after DPLM-2 itself is stable.

Baselines:

- ESMFold
- ProteinMPNN
- MultiFlow
- RFDiffusion
- ESM3 / ESM3-Open
- DPLM sequence-only where relevant

Tasks:

1. Create a baseline output schema:
   - generated sequence FASTA
   - generated structure PDB
   - per-sample metrics CSV
   - aggregate summary CSV
2. Do not wire every baseline directly into the DPLM repo at first. Start by supporting imported baseline outputs.
3. Add adapters under `scripts/reproduce/baselines/` that convert each baseline's outputs into the shared schema.
4. Reuse the same evaluator/summarizers from Phases 1-6.

Deliverable:

- `results/reproduce/tables/table_unconditional.csv`
- `results/reproduce/tables/table_folding.csv`
- `results/reproduce/tables/table_inverse_folding.csv`
- `results/reproduce/tables/table_motif.csv`

Success criterion:

- Tables are regenerated from saved baseline outputs and DPLM-2 outputs with one aggregation command.

## Phase 8: Representation Learning

Objective: reproduce downstream predictive-task results.

Recommendation:

Do this late. It is not the easiest pretrained-model reproduction target because this checkout does not contain the SaProt-based pipeline. The README says the modified representation-learning code is on a separate `representationlearning` branch.

Tasks:

1. Inspect the `representationlearning` branch.
2. Decide whether to merge that branch into this checkout or treat it as a separate runner.
3. Add a bridge script that exports DPLM/DPLM-2 representations in the format expected by the SaProt tasks.
4. Reproduce one small task first, likely DeepLoc-Binary or Thermostability.
5. Scale to all paper tasks:
   - Thermostability
   - HumanPPI
   - Metal Ion Binding
   - EC
   - GO-MF
   - GO-BP
   - GO-CC
   - DeepLoc-Subcellular
   - DeepLoc-Binary

Deliverable:

- `results/reproduce/representation_learning/all_tasks_summary.csv`

Success criterion:

- At least one downstream task can be reproduced from pretrained DPLM-2 representations without manual branch switching during the run.

## Proposed File Layout

```text
scripts/reproduce/
  check_env.sh
  download_eval_data.sh
  smoke_dplm2_load.py
  run_folding.sh
  summarize_folding.py
  run_inverse_folding.sh
  summarize_inverse_folding.py
  run_cogeneration.sh
  summarize_cogeneration_quality.py
  prepare_pdb_reference.sh
  summarize_diversity_novelty.py
  run_motif_scaffolding.sh
  calc_motif_rmsd.py
  summarize_motif_scaffolding.py
  calc_secondary_structure.py
  baselines/

results/reproduce/
  folding/
  inverse_folding/
  cogeneration/
  motif_scaffolding/
  secondary_structure/
  representation_learning/
  tables/
```

## Milestone Order

1. Environment and pretrained-load smoke tests.
2. Forward folding on a tiny CAMEO subset.
3. Full forward folding on CAMEO.
4. Inverse folding on CAMEO.
5. Co-generation quality at lengths 100-500.
6. Co-generation quality at lengths 600-1000.
7. Diversity and novelty metrics.
8. Motif-scaffolding generation and scripted success-rate evaluation.
9. Secondary-structure statistics.
10. External baseline adapters.
11. Representation-learning branch integration.

## Why This Order

Forward folding gives the fastest meaningful win because it is deterministic, uses a prepared benchmark, and requires the fewest external model/tool dependencies. Inverse folding reuses almost the same pipeline. Co-generation comes next because it is central to the paper but requires folding/self-consistency evaluation. Motif scaffolding and diversity/novelty follow because their metrics require more custom glue. Representation learning comes last because the code is not in this branch.
