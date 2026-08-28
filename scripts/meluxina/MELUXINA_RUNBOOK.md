# Meluxina Runbook — Conditional DPLM-2 Smoke Test & Full Run

Copy-paste commands for everything you need to do on Meluxina, in order.
Assumes you land on a **login node** with access to the `gpu` partition.

Throughout, replace:
- `$SCRATCH` — your Meluxina scratch dir (usually pre-set; check with `echo $SCRATCH`)
- `~/_dplm` — wherever you want the repo (anywhere on the shared FS)

---

## Step 0 — One-time environment prep (login node)

```bash
# Modules
module load Apptainer
module load Python   # only needed if you want git; otherwise skip

# Writable dirs on scratch
mkdir -p $SCRATCH/dplm-runs $SCRATCH/hf-cache

# wandb — export once per shell (or add to ~/.bashrc)
export WANDB_API_KEY=<your-key>
# If you prefer no online logging: instead `export WANDB_MODE=offline`

# Sanity: check GPU partition access
sinfo -p gpu
```

---

## Step 1 — Pull the Docker image into an Apptainer SIF (login node, ~10-20 min)

```bash
cd $SCRATCH
apptainer pull dplm_cond.sif docker://pieris98/dplm:cu121-torch220-cond
```

Notes:
- The image is ~57 GB — pulls to scratch, one-time cost.
- Verify it landed: `ls -lh $SCRATCH/dplm_cond.sif`

## Step 2 — Clone the repo (login node)

```bash
cd ~/_dplm   # or wherever you keep code; create it first: mkdir -p ~/_dplm
git clone https://github.com/<your-account>/dplm.git .
git checkout main
# If your local work isn't pushed yet, push it from your local machine first!
```

> ⚠️ Before this step, from your LOCAL machine, make sure everything is on GitHub:
> ```bash
> cd ~/dev/phd/dplm
> git status                 # should be clean
> git log --oneline -3       # the conditional-run commits should be on top
> git push origin main
> ```

## Step 3 — Quick container sanity check (login node, no GPU)

```bash
cd ~/_dplm

# The image has the code + data baked in at /workspace/dplm. We bind our
# repo checkout OVER it so the container runs the freshly-cloned code while
# keeping everything else (models, libs) from the image.
apptainer exec \
  --bind ~/_dplm:/workspace/dplm \
  $SCRATCH/dplm_cond.sif \
  python -c "
import wandb, torch
from byprot.models.dplm2 import ConditionalDPLM2
from byprot.datamodules.dataset.annotated_protein import AnnotatedProteinDataset
import pyarrow.parquet as pq
t = pq.read_table('/workspace/dplm/data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet', columns=['uniprot_id'])
print('wandb', wandb.__version__)
print('dataset rows:', t.num_rows)   # expect 45696
print('ALL IMPORTS + DATASET OK')
"
```

Expected output ends with: `ALL IMPORTS + DATASET OK` and `dataset rows: 45696`.
(No GPU on the login node — `torch.cuda.is_available()` will be False. That's fine; the compute nodes get GPUs via `--nv`.)

---

## Step 4 — Submit the smoke test (the actual validation)

```bash
cd ~/_dplm
export DPLM_SIF=$SCRATCH/dplm_cond.sif
export WANDB_API_KEY=<your-key>   # if not already in ~/.bashrc

sbatch scripts/meluxina/cond_dplm2_smoke.sbatch
```

What it does (30 min, 1 node × 4 A100s):
1. GPU visibility inside the container (`nvidia-smi`)
2. Dataset visibility (row count check)
3. **40 training steps** of 4-GPU DDP bf16 with wandb logging
4. **Conditional generation** (4 sequences, GO label 5) from the smoke checkpoint

Watch it:

```bash
squeue -u $USER                                          # job status
tail -f logs/slurm/cond_dplm2_smoke_<JOBID>.out          # live log
# or once done:
cat logs/slurm/cond_dplm2_smoke_<JOBID>.out
```

### Smoke-test pass criteria (check in the .out file)

| Check | Look for |
|---|---|
| GPUs visible | `nvidia-smi` table listing 4× A100 |
| Dataset | `dataset rows: 45696` and `dataset OK` |
| DDP initialized | no NCCL/RANK errors; `Initializing distributed` lines |
| Training ran | `Validation Info @ ... global step 20` and `step 40` with **finite** val/loss (≈2.5-3.0) |
| wandb | a run URL printed; metrics visible in the dashboard |
| Generation | `Using conditioning config from ckpt`, `Loaded 802 ... keys`, 4 sequences saved, `generation OK` |
| Final line | `SMOKE TEST PASSED — safe to submit the full run` |

If any step fails, the log will contain the traceback — send it my way and we debug.

---

## Step 5 — Submit the FULL training run

```bash
cd ~/_dplm

# 1 node (4 GPUs), 24h wall time, ~100K steps:
sbatch scripts/meluxina/cond_dplm2_train.sbatch

# OR 2 nodes (8 GPUs) — halved wall time:
sbatch --nodes=2 scripts/meluxina/cond_dplm2_train.sbatch

# OR with overrides:
sbatch scripts/meluxina/cond_dplm2_train.sbatch -- train.lr=1e-4 trainer.max_steps=50000
```

Monitor:

```bash
squeue -u $USER
tail -f logs/slurm/cond_dplm2_<JOBID>.out
# wandb dashboard: https://wandb.ai/<entity>/CondDPLM2_650m
```

Useful checks after ~1 hour of full-run training:

```bash
grep "Validation Info" logs/slurm/cond_dplm2_<JOBID>.out | tail -5
# val/loss should be trending down from ~2.9
grep -c "train/nll" logs/slurm/cond_dplm2_<JOBID>.out    # metric lines present
nvidia-smi                                                # (from a compute node) GPU util ~100%
```

---

## How the container mounting works (reference)

The sbatch scripts source `scripts/meluxina/common.sh`, which builds this
`apptainer exec` invocation:

```
apptainer exec --nv \
  --bind <repo>:/workspace/dplm \        # your fresh git clone shadows the baked-in code
  --bind $SCRATCH/dplm_run_<jobid>/hf:/opt/huggingface \   # writable HF cache
  --bind $SCRATCH/dplm_run_<jobid>/tmp:/tmp \              # writable /tmp
  --env WANDB_API_KEY=... --env HF_HOME=/opt/huggingface ... \
  $SCRATCH/dplm_cond.sif \
  python train.py ...
```

- `--nv` exposes the NVIDIA drivers (GPU access).
- The repo bind means **code changes only need a `git pull`**, not an image rebuild.
- The dataset lives inside the image at
  `/workspace/dplm/data-bin/cfpgen_dplm2_joined/` — but the repo bind shadows
  `/workspace/dplm` entirely! **So the dataset must also exist in your repo
  checkout**, OR be present via the data_dir path. Two options:
  1. Copy it out of the image once (see below), or
  2. Rely on Hydra's `paths.data_dir = $PROJECT_ROOT/data-bin` → your checkout's data-bin.

**Copy the dataset out of the image (one-time, ~1.1 GB):**

```bash
cd ~/_dplm
mkdir -p data-bin/cfpgen_dplm2_joined
apptainer exec $SCRATCH/dplm_cond.sif \
  cat /workspace/dplm/data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet \
  > data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet
ls -lh data-bin/cfpgen_dplm2_joined/
```

Run this BEFORE the smoke test (Step 4) — the dataloader reads
`${paths.data_dir}/cfpgen_dplm2_joined/joined_train_safe.parquet` from the
bind-mounted repo, not the image's copy.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ERROR: Apptainer image not found` | `export DPLM_SIF=$SCRATCH/dplm_cond.sif` (or pull the SIF, Step 1) |
| Dataset rows = 0 / file not found | Do the dataset copy-out (above); check `data-bin/cfpgen_dplm2_joined/` exists in the repo checkout |
| NCCL errors in DDP | Usually a stale port; `export MASTER_PORT=29501` and resubmit |
| wandb 401/403 | `WANDB_API_KEY` not exported in the submission shell |
| OOM during training | `sbatch ... -- datamodule.max_tokens=3000` |
| Code changes not picked up | You edited locally but didn't `git push` + `git pull` on Meluxina |
