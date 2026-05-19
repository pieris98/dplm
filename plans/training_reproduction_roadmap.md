# DPLM Paper Training Reproduction Roadmap

Goal: reproduce the paper training runs that this checkout can actually launch with `train.py`, starting from released DPLM checkpoints where the paper recipe does so, and make the first-pass commands usable on a 4xA100 Leonardo node.

This is a training-focused companion to `plans/pretrained-reproduction-roadmap.md`, which is evaluation-focused.

## What Is Trainable In This Checkout

| Paper capability | Training config present? | Primary config | Training interpretation |
|---|---:|---|---|
| DPLM sequence-only unconditional generation | Yes | `configs/experiment/dplm/dplm_150m.yaml`, `dplm_650m.yaml`, `dplm_3b.yaml` | Trains sequence DPLM on UniRef50. This does not start from DPLM; it is the DPLM pretraining stage, initialized from ESM2 architecture/weights depending on config. |
| DPLM-2 sequence/structure co-generation | Yes | `configs/experiment/dplm2/dplm2_150m.yaml`, `dplm2_650m.yaml`, `dplm2_3b.yaml` | One multimodal DPLM-2 training run starts from `airkingbd/dplm_*`, expands the vocabulary, loads `airkingbd/struct_tokenizer`, and trains joint structure/sequence denoising. |
| DPLM-2 forward folding | Yes, as a loss mode inside DPLM-2 | same DPLM-2 configs | `folding_loss_ratio: 0.25` trains sequence-conditioned structure prediction examples. There is no separate forward-folding training config. |
| DPLM-2 inverse folding | Yes, as a loss mode inside DPLM-2 | same DPLM-2 configs | `inverse_folding_loss_ratio: 0.25` trains structure-conditioned sequence generation examples. There is no separate DPLM-2 inverse-folding training config. |
| DPLM-2 unconditional marginals | Yes, as a loss mode inside DPLM-2 | same DPLM-2 configs | `single_modality_ratio: 0.25` and `joint_loss_ratio: 0.25` cover marginal and joint denoising used by unconditional generation/co-generation. |
| DPLM-2 any-to-any conditional generation / motif scaffolding | Partially | same DPLM-2 configs | Training supports mixed modality conditioning via the joint/marginal loss mixture. Motif scaffolding has generation scripts, but no motif-specific finetuning config. |
| DPLM-2 self-mixup training | Present but needs a local checkpoint | `configs/experiment/dplm2/dplm2_650m_selfmixup.yaml` | Intended as a second stage from a trained DPLM-2 checkpoint. The config contains a placeholder path and should be launched only after setting a real local checkpoint and verifying `training_stage`. |
| DPLM-2 Bit | Yes | `configs/experiment/dplm2/dplm2_bit_650m.yaml` | Starts from `airkingbd/dplm_650m`, uses the same PDB+SwissProt tokenized dataset, and predicts LFQ bits rather than structure-token indices. |
| Structure tokenizer | Yes | `configs/experiment/structok/structok_lfq_8k_pdb_swissprot_c512.yaml` | Trains the LFQ structure tokenizer on PDB+SwissProt metadata. This is upstream of DPLM-2 and does not start from DPLM. |
| DPLM inverse folding adapter | Yes, but config name differs from README | `configs/experiment/dplm/cond_dplm_150m.yaml`, `cond_dplm_650m.yaml`, `cond_dplm_3b.yaml` | Trains GVP encoder + DPLM conditional adapter on CATH 4.3. README says `dplm/dplm_650m_invfold`, but the checked-in config is `dplm/cond_dplm_650m`. |
| Representation learning | No in this branch | none | README says the SaProt-style representation pipeline is on a separate `representationlearning` branch. |

## Important Config Details

The DPLM-2 index model config `configs/experiment/dplm2/dplm2_650m.yaml` is the main paper training target for a 4xA100 reproduction starting from DPLM:

- Dataset: `datamodule=tokenized_protein`, `csv_file=pdb_swissprot`.
- Base checkpoint: `model.net.pretrained_model_name_or_path=airkingbd/dplm_650m`.
- Structure tokenizer: `model.struct_tokenizer.exp_path=airkingbd/struct_tokenizer`.
- Warm start mode: `model.training_stage=train_from_dplm`.
- Loss mixture: `single_modality_ratio=0.25`, `folding_loss_ratio=0.25`, `inverse_folding_loss_ratio=0.25`, `joint_loss_ratio=0.25`.
- LoRA: enabled for the index model, rank 16.
- Paper-scale steps: `trainer.max_steps=100_000`.

On 8xA100, the README uses `datamodule.max_tokens=8192` and `trainer.accumulate_grad_batches=1`, for about 64k tokens per optimizer step. On 4xA100, use `datamodule.max_tokens=8192` and `trainer.accumulate_grad_batches=2` to keep the same effective token budget.

For DPLM sequence-only training, the README target is about 1M tokens per optimizer step. On 4xA100, use `datamodule.max_tokens=8192` and `trainer.accumulate_grad_batches=32` if memory permits.

## Data Preparation

Run these once from the repo root:

```bash
bash scripts/download_pdb_swissprot_hf.sh
bash scripts/download_uniref50_hf.sh
bash scripts/download_cath.sh
```

Minimum required data by training target:

| Target | Required data |
|---|---|
| DPLM-2 / DPLM-2 Bit | `data-bin/pdb_swissprot` |
| DPLM sequence-only | `data-bin/uniref50_hf` |
| DPLM inverse-folding adapter | `data-bin/cath_4.3` |
| Structure tokenizer | PDB metadata expected by `configs/experiment/structok/structok_lfq_8k_pdb_swissprot_c512.yaml`, especially `data-bin/metadata.pdb_afdb_cameo.plddt.structok_lfq.csv` |

## Recommended 4xA100 Training Order

### Phase 0: Check training config composition

Use short dry runs before spending allocation:

```bash
bash scripts/reproduce/training/run_train.sh --recipe dplm2_650m --dry-run
bash scripts/reproduce/training/run_train.sh --recipe dplm2_bit_650m --dry-run
bash scripts/reproduce/training/run_train.sh --recipe dplm_invfold_650m --dry-run
```

Then run a single-batch Lightning smoke test:

```bash
bash scripts/reproduce/training/run_train.sh \
  --recipe dplm2_650m \
  --fast-dev-run \
  --max-steps 2 \
  --name smoke/dplm2_650m_from_dplm
```

### Phase 1: Main DPLM-2 650M from DPLM

This is the closest training reproduction target for DPLM-2 paper results on 4xA100:

```bash
bash scripts/reproduce/training/run_train.sh \
  --recipe dplm2_650m \
  --devices 4 \
  --max-tokens 8192 \
  --accumulate-grad-batches 2 \
  --max-steps 100000 \
  --name reproduce/dplm2_650m_from_dplm
```

Leonardo Slurm:

```bash
sbatch \
  --job-name=dplm2-650m \
  --output=logs/slurm/%x-%j.out \
  --error=logs/slurm/%x-%j.err \
  scripts/reproduce/training/leonardo_train.sbatch \
  --recipe dplm2_650m \
  --name reproduce/dplm2_650m_from_dplm
```

### Phase 2: DPLM-2 Bit 650M from DPLM

```bash
bash scripts/reproduce/training/run_train.sh \
  --recipe dplm2_bit_650m \
  --devices 4 \
  --max-tokens 8192 \
  --accumulate-grad-batches 2 \
  --max-steps 100000 \
  --name reproduce/dplm2_bit_650m_from_dplm
```

### Phase 3: DPLM inverse-folding adapter

This reproduces the DPLM fixed-backbone/inverse-folding training family from the README, using the checked-in config name:

```bash
bash scripts/reproduce/training/run_train.sh \
  --recipe dplm_invfold_650m \
  --devices 4 \
  --max-tokens 6000 \
  --max-steps 200000 \
  --name cath_4.3/dplm_650m/invfold
```

### Phase 4: Optional DPLM sequence-only pretraining

This is expensive and is not needed if the goal is DPLM-2 training from the released DPLM checkpoint, but the config exists:

```bash
bash scripts/reproduce/training/run_train.sh \
  --recipe dplm_650m \
  --devices 4 \
  --max-tokens 8192 \
  --accumulate-grad-batches 32 \
  --max-steps 500000 \
  --name reproduce/dplm_650m_uniref50
```

## Scripts Added

```text
scripts/reproduce/training/
  run_train.sh              # local/generic Hydra training launcher
  leonardo_train.sbatch     # Slurm wrapper for a 4xA100 Leonardo node
```

The launcher intentionally emits a full command before execution so each run has a copyable provenance trail in Slurm logs.

## Known Gaps And Risks

- `dplm2_650m_selfmixup.yaml` has a placeholder checkpoint path and should be treated as a second-stage experiment, not the first reproduction run.
- The self-mixup config says the checkpoint path should be a DPLM-2 `.ckpt`, but the checked-in `training_stage` is `train_from_dplm`; verify locally before using it for paper claims.
- Motif scaffolding and forward/inverse folding paper metrics are evaluation protocols over the multimodal checkpoint, not separate training configs in this checkout.
- Representation learning is not reproducible from this branch alone.
- Structure-tokenizer training is present, but DPLM-2 training from released `airkingbd/struct_tokenizer` is the lower-risk route for a first reproduction.
