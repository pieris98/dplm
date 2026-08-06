# Meluxina HPC — Conditional DPLM-2 Training

Full-scale training of ConditionalDPLM2 (parallel-adapter annotation
conditioning on a frozen DPLM-2 650M) on Meluxina's GPU partition.

## Prerequisites

### 1. Build the Docker image (local, push to registry)

The image must include the new code + wandb + the joined dataset:

```bash
# From repo root. The Dockerfile now bakes in:
#   - all new conditional modules (dplm2_conditional.py, annotated_protein.py, etc.)
#   - wandb (added to docker/dplm-pip-freeze.txt)
#   - the joined dataset at data-bin/cfpgen_dplm2_joined/ (1.1 GB)
docker build -t dplm:cu121-torch220 .
# Tag and push to the registry Meluxina can pull from:
docker tag dplm:cu121-torch220 <your-registry>/dplm:cu121-torch220
docker push <your-registry>/dplm:cu121-torch220
```

### 2. Set up wandb on Meluxina

```bash
export WANDB_API_KEY=<your-key>
export WANDB_PROJECT=CondDPLM2_650m
# Or to log offline only (no API key needed):
# export WANDB_MODE=offline
```

### 3. Apptainer/Singularity image (Meluxina runs containers via Apptainer)

Pull the Docker image into an Apptainer SIF on Meluxina's scratch:

```bash
module load Apptainer
apptainer pull dplm_cu121-torch220.sif docker://<your-registry>/dplm:cu121-torch220
```

## Submitting a training run

### Single node (4 GPUs)

```bash
sbatch scripts/meluxina/cond_dplm2_train.sbatch
```

### Multi-node (e.g. 2 nodes × 4 GPUs = 8 GPUs)

```bash
sbatch --nodes=2 scripts/meluxina/cond_dplm2_train.sbatch
```

### With custom hyperparameters

Pass Hydra overrides after `--`:

```bash
sbatch scripts/meluxina/cond_dplm2_train.sbatch -- \
  train.lr=1e-4 \
  trainer.max_steps=50000 \
  datamodule.max_tokens=6000
```

Or via env vars:

```bash
MAX_STEPS=50000 LR=1e-4 sbatch scripts/meluxina/cond_dplm2_train.sbatch
```

## Config files

| File | Purpose |
|---|---|
| `configs/trainer/meluxina_ddp_bf16.yaml` | DDP bf16 trainer, 4 GPUs/node, sync BN, parameterized `num_nodes` |
| `configs/experiment/dplm2/cond_dplm2_650m_cfpgen_meluxina.yaml` | Full training config: lr 3e-4, 100K steps, val every 500, joined_train_safe.parquet |
| `scripts/meluxina/cond_dplm2_train.sbatch` | SLURM submission script (parameterized for 1–N nodes) |

## Hyperparameter notes

- **lr = 3e-4**: between the smoke-test 1e-3 (too aggressive for long runs) and
  DPLM-2's 1e-4. Only the adapters/embedders/projectors train (19.5M params);
  the 650M base is frozen. Adjust via `train.lr`.
- **max_tokens = 4000**: 4×A100 40GB handles this (the smoke ran max_tokens=1200
  on a single 24GB 3090). Increase to 6000 if GPU memory allows.
- **100K steps**: matches DPLM-2's training length. At 4 GPUs × max_tokens 4000,
  this is ~2-3 days wall-clock on one node. Halve for 2 nodes.
- **weight_init = 1e-5**: ProCALM's near-zero adapter init. Keeps the model at
  frozen-base behaviour at step 0.

## Monitoring

```bash
# SLURM job status
squeue -u $USER

# Live logs
tail -f logs/slurm/cond_dplm2_<jobid>.out

# wandb dashboard
# https://wandb.ai/<entity>/CondDPLM2_650m
```

## Resuming from checkpoint

```bash
sbatch scripts/meluxina/cond_dplm2_train.sbatch -- \
  trainer.resume_from_checkpoint=logs/cond_dplm2_650m_cfpgen_meluxina/checkpoints/step_49999.ckpt
```

## Notes on the Docker image update

Three changes were made for the conditional run:
1. **`docker/dplm-pip-freeze.txt`**: added `wandb==0.26.1`.
2. **`Dockerfile`**: added an explicit `COPY data-bin/cfpgen_dplm2_joined/` to
   bake the 1.1 GB joined dataset into the image (it's gitignored, so the
   `COPY . .` doesn't pick it up). If you regenerate the dataset, rebuild the
   image or bind-mount over the path at runtime.
3. All new Python modules and configs are under `src/` and `configs/` which are
   tracked by git and picked up by the existing `COPY . .`.
