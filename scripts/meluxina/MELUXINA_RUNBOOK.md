# Meluxina Runbook — Conditional DPLM-2 Smoke Test & Full Run

Copy-paste commands for everything on Meluxina, in order. Assumes you land
on a **login node** with access to the `gpu` partition.

**Project paths (this project has no `$SCRATCH`):**

| Var | Path | Use |
|---|---|---|
| `$HOME` | `/home/users/u104556` | small stuff only (quota-limited) |
| `$PROJECT` | `/project/home/p201418` | **the SIF, HF cache, run artifacts** (large quota) |

**Key idea:** the Apptainer jobs bind-mount only your repo's *code*
(`src/`, `configs/`, `scripts/`, the CLI scripts) over the image — so a
`git pull` updates code without rebuilding the image — while the image's
**compiled openfold CUDA kernels** and **baked-in 45K dataset** remain
visible. Do NOT bind the whole repo dir over `/workspace/dplm`; that
shadows the compiled artifacts and breaks openfold
(`ModuleNotFoundError: attn_core_inplace_cuda`).

---

## Step 0 — One-time environment prep (login node)

```bash
module load Apptainer

# Writable dirs on PROJECT
mkdir -p $PROJECT/dplm-runs $PROJECT/hf-cache

# wandb — export once per shell (or add to ~/.bashrc)
export WANDB_API_KEY=<your-key>
# If you prefer no online logging: instead `export WANDB_MODE=offline`

# Sanity: check GPU partition access
sinfo -p gpu

# Confirm the paths
echo "HOME=$HOME  PROJECT=$PROJECT"
```

## Step 1 — Pull the Docker image into an Apptainer SIF (login node, ~10-20 min)

```bash
cd $PROJECT
apptainer pull dplm_cond.sif docker://pieris98/dplm:cu121-torch220-cond
ls -lh $PROJECT/dplm_cond.sif   # expect ~57 GB
```

## Step 2 — Clone the repo (login node)

```bash
mkdir -p $PROJECT/dplm-repo && cd $PROJECT/dplm-repo
git clone https://github.com/<your-account>/dplm.git .
git checkout main
```

> ⚠️ From your LOCAL machine first — make sure everything is on GitHub:
> ```bash
> cd ~/dev/phd/dplm
> git status                 # should be clean
> git log --oneline -3       # conditional-run + meluxina commits on top
> git push origin main
> ```

## Step 3 — Quick container sanity check (login node, no GPU)

```bash
cd $PROJECT/dplm-repo

apptainer exec \
  --bind $PROJECT/dplm-repo/src:/workspace/dplm/src \
  --bind $PROJECT/dplm-repo/configs:/workspace/dplm/configs \
  $PROJECT/dplm_cond.sif \
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

Expected last lines: `dataset rows: 45696` and `ALL IMPORTS + DATASET OK`.
(The CUDA-not-found / CPU-accelerator warnings are normal on a login node —
compute nodes get GPUs via `--nv`. The `df: ~/.triton/...` lines are
harmless noise.)

---

## Step 4 — Submit the smoke test (the actual validation)

```bash
cd $PROJECT/dplm-repo
export DPLM_SIF=$PROJECT/dplm_cond.sif
export WANDB_API_KEY=<your-key>   # if not already in ~/.bashrc

sbatch scripts/meluxina/cond_dplm2_smoke.sbatch
```

What it does (30 min, 1 node × 4 A100s):
1. GPU visibility inside the container (`nvidia-smi`)
2. Dataset visibility (row-count check)
3. **40 training steps** of 4-GPU DDP bf16 with wandb logging
4. **Conditional generation** (4 sequences, GO label 5) from the smoke ckpt

Watch it:

```bash
squeue -u $USER                                   # job status
tail -f logs/slurm/cond_dplm2_smoke_<JOBID>.out   # live log
```

Note: SLURM's stdout (`logs/slurm/...`) is written by the *host* shell into
the repo checkout; training checkpoints / wandb / generation outputs are
written by the *container* into `$PROJECT/dplm-logs`, `$PROJECT/dplm-wandb`,
`$PROJECT/dplm-gen` respectively (bind-mounted — see reference below).

### Smoke-test pass criteria (in the `.out` file)

| Check | Look for |
|---|---|
| GPUs visible | `nvidia-smi` table listing 4× A100 |
| Dataset | `dataset rows: 45696` and `dataset OK` |
| DDP initialized | no NCCL/RANK errors |
| Training ran | `Validation Info @ ... global step 20` and `step 40`, **finite** val/loss (≈2.5-3.0) |
| wandb | run URL printed; metrics in the dashboard |
| Generation | `Loaded 802 ... keys`, 4 sequences saved, `generation OK` |
| Final line | `SMOKE TEST PASSED — safe to submit the full run` |

If anything fails, the traceback is in the `.out`/`.err` — bring it back and we debug.

---

## Step 5 — Submit the FULL training run

```bash
cd $PROJECT/dplm-repo

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
# checkpoints land in: $PROJECT/dplm-logs/<run-name>/checkpoints/

# After ~1 hour — loss should be trending down from ~2.9:
grep "Validation Info" logs/slurm/cond_dplm2_<JOBID>.out | tail -5
```

---

## How the container mounting works (reference)

The sbatches source `scripts/meluxina/common.sh`, which runs:

```
apptainer exec --nv \
  --bind $PROJECT/dplm-repo/src:/workspace/dplm/src \        # fresh code
  --bind $PROJECT/dplm-repo/configs:/workspace/dplm/configs \
  --bind $PROJECT/dplm-repo/scripts:/workspace/dplm/scripts \
  --bind $PROJECT/dplm-repo/train.py:/workspace/dplm/train.py \
  --bind $PROJECT/dplm-repo/generate_conditional_dplm2.py:... \
  --bind $PROJECT/dplm-logs:/workspace/dplm/logs \           # persistent ckpts
  --bind $PROJECT/dplm-wandb:/workspace/dplm/wandb \         # wandb local logs
  --bind $PROJECT/dplm-gen:/workspace/dplm/generation-results \
  --bind $PROJECT/dplm_run_<jobid>/hf:/opt/huggingface \     # writable HF cache
  --bind $PROJECT/dplm_run_<jobid>/tmp:/tmp \
  --pwd /workspace/dplm \
  $PROJECT/dplm_cond.sif \
  python train.py ...
```

- `--nv` exposes the NVIDIA drivers (GPU access).
- **What stays from the image:** compiled `vendor/openfold` (CUDA kernels),
  the 45K dataset, all Python packages, the pretrained model caches.
- **What comes from your repo:** `src/`, `configs/`, `scripts/`, CLI scripts.
- Storage base resolution: `DPLM_BASE` (default `$PROJECT`, falls back to
  `$HOME`). Override with `export DPLM_BASE=...` if needed.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: attn_core_inplace_cuda` | You bound the whole repo over `/workspace/dplm` — use `common.sh`'s per-directory binds (or re-run via the sbatch scripts) |
| `ERROR: Apptainer image not found` | `export DPLM_SIF=$PROJECT/dplm_cond.sif` (or do Step 1) |
| Dataset rows = 0 / file not found | The sbatch runs with `--pwd /workspace/dplm` inside the image — the baked-in dataset should be found; if not, check `apptainer exec ... ls /workspace/dplm/data-bin/cfpgen_dplm2_joined/` |
| NCCL errors in DDP | Stale port; `export MASTER_PORT=29501` and resubmit |
| wandb 401/403 | `WANDB_API_KEY` not exported in the submission shell |
| OOM during training | `sbatch ... -- datamodule.max_tokens=3000` |
| Code changes not picked up | `git pull` in `$PROJECT/dplm-repo` |
| Read-only filesystem errors for logs | Expected inside the image; logs/wandb/gen go to the `$PROJECT` binds — submit via the sbatch scripts, which set them up |
| `$PROJECT` quota exceeded | SIF (57G) + HF cache are the big items; `du -sh $PROJECT/*` |
