# Karolina Runbook — training + eval from scratch

Migration target after Meluxina access ended. IT4I Karolina: 72 GPU nodes ×
8× A100 40GB; SLURM; Lmod modules; Apptainer available.

**Key differences vs Meluxina**

| | Meluxina | Karolina |
|---|---|---|
| GPU partitions | `gpu` (whole nodes, 6-day wall) | `qgpu` = 1-GPU slices (48 h max) · `qgpu_big` = whole 8-GPU nodes (**12 h max**) |
| Training strategy | one 4-GPU job, 3 days | `qgpu_big`, 8 GPUs, `max_tokens=2000` (batch parity with 4×4000), **self-resubmitting 12 h chunks** |
| Storage | `$PROJECT` (Parastation) | `/scratch/project/<ACCOUNT>` (Lustre, 20 TB, **90-day purge** → back up checkpoints/results) |
| Modules | hidden from batch shells (hardcoded paths) | Lmod works normally; `common.sh` auto-discovers (Apptainer/Java) |
| wandb from compute | online OK | **no compute egress → default `WANDB_MODE=offline`**; sync later from local: `wandb sync $DPLM_BASE/dplm-logs/wandb/<run>` |
| Account | `--account=p201418` | `--account=OPEN-XX-XX` / `DD-XX-XX` (export `ACCOUNT`) |
| Quirk | — | if apptainer misbehaves: `ml -f XALT` and retry |

## 0. Prerequisites

```bash
ssh <user>@karolina.it4i.cz                     # IT4I account + active project with GPU allocation
export ACCOUNT=OPEN-XX-XX                       # your project id
export DPLM_BASE=/scratch/project/$ACCOUNT      # everything big lives here
# keep apptainer's layer cache OFF home (25 GB quota):
export APPTAINER_CACHEDIR=$DPLM_BASE/apptainer_cache
export APPTAINER_TMPDIR=$DPLM_BASE/apptainer_tmp
git clone <your-remote>/dplm.git /scratch/project/$ACCOUNT/dplm-repo
cd /scratch/project/$ACCOUNT/dplm-repo && git checkout main
# (or rsync the working tree from the local machine, excluding logs/, eval_runs/, outputs/, work/)
```

## 1. One-time setup (login node)

```bash
bash scripts/karolina/setup_data.sh
```

Idempotent; does: dplm sandbox (`docker://pieris98/dplm:cu121-torch220-cond`,
~57 GB — contains the pretrained HF caches, the 45K joined training dataset,
and openfold's compiled kernels, so no other model/data downloads are needed),
deepgose sandbox, ESM-2 3B torch-hub cache (5.7 GB), InterProScan 5.78 tarball
+ extraction + a validation run, `go.obo`, `eval/pylibs` (obonet for the
scorer). Then it prints the **two rsync TODOs** that only exist locally:

```bash
# from the LOCAL workstation:
rsync -avP /home/cherry/dev/phd/dplm/eval/deepgo2/data/ \
  <user>@karolina.it4i.cz:/scratch/project/$ACCOUNT/dplm-repo/eval/deepgo2/data/   # 16 GB
rsync -avP /home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/test.pkl \
           /home/cherry/dev/phd/cfpgen/go_mapping.pkl \
           /home/cherry/dev/phd/cfpgen/ipr_mapping.pkl \
  <user>@karolina.it4i.cz:/scratch/project/$ACCOUNT/dplm-repo/eval/cfpgen_eval/    # 14 MB
```

Re-run `setup_data.sh` after — it should end with `COMPLETE`.

Sanity check (auto-allocates 1 GPU):
```bash
ACCOUNT=$ACCOUNT scripts/karolina/interactive.sh check
```

## 2. Training from scratch

```bash
ACCOUNT=$ACCOUNT sbatch --export=ALL scripts/karolina/cond_dplm2_train.sbatch
squeue -u $USER
```

- `qgpu_big` whole node, 8×A100, `max_tokens=2000` → effective 16K tokens/step,
  identical optimizer schedule to the Meluxina 100K baseline, ~2× wall speed.
- The job chains itself across the 12 h limit: on the pre-timeout SIGTERM (or
  a normal exit below `MAX_STEPS`) it resubmits and resumes from the
  highest-step checkpoint (every-2000-step ckpts → ≤2000 steps lost per
  chunk). Expect ~3 chunks for 100K steps. `MAX_STEPS` / `LR` /
  `MAX_RESUBMITS` are env-overridable; Hydra overrides go after `--`.
- Curves: offline wandb → `wandb sync` later, or watch
  `$DPLM_BASE/dplm-logs/**/checkpoints` + the slurm `.out`.
- **Purge discipline**: scratch purges after 90 idle days — copy final
  checkpoints (and `eval_runs/` results) to PROJECT storage or local.

Monitor: `tail -f logs/slurm/cond_dplm2_<id>.out`

## 3. Evaluation

```bash
# after (or during) training, with the produced checkpoint:
ACCOUNT=$ACCOUNT sbatch --export=ALL scripts/karolina/eval_pipeline.sbatch
# CKPT=<path> RUN_NAME=fn_eval_v1 sbatch --export=ALL ...   # explicit ckpt/name
```

Runs generation (4 arms) → InterProScan → DeepGO-SE → scoring in one `qgpu`
1-GPU job (~2–4 h; IPS on degenerate arms is the long pole at ~25 min/arm).
Stages skip finished outputs — resubmit to resume. Results:
`eval_runs/fn_eval_v1/results.{json,md}`.

Interactive debugging instead:

```bash
scripts/karolina/interactive.sh evalsh      # 1 GPU, helpers loaded (eval_generate/eval_ips/eval_dg/eval_score)
scripts/karolina/interactive.sh alloc       # whole node for training debugging
```

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| **`disk quota exceeded` during an apptainer build/pull** | Apptainer caches docker layers in `~/.apptainer/cache` (HOME = 25 GB; the dplm image's layers are ~20–30 GB). Free it: `rm -rf ~/.apptainer/cache ~/.singularity/cache`, then point cache+temp at scratch — `export APPTAINER_CACHEDIR=$DPLM_BASE/apptainer_cache APPTAINER_TMPDIR=$DPLM_BASE/apptainer_tmp` — and retry (`setup_data.sh` now does this itself) |
| `sbatch: error: Invalid account or partition` | wrong `ACCOUNT` or no GPU allocation left on the project |
| apptainer oddities (mount/exec flakes) | `ml -f XALT` in that shell, retry; sandbox dirs (not SIF) are the preferred image format here too |
| Training job ends at 12 h without resubmit | check `.err`/`.out` for the resubmit lines; resubmit manually — it auto-resumes from the newest ckpt |
| IPS stage slow (>10 min/arm) | expected on our degenerate outputs (PANTHER vs low-complexity); `hmmsearch` running = healthy |
| `FATAL: sklearn/obonet unavailable` | re-run `setup_data.sh` step 6 (login node) |
| GO columns missing from `results.md` | `import obonet` failing → step 6 above; **the scorer silently skips GO-DAG expansion without it** |
| Scratch purge warning | `lfs quota -h /scratch/project/$ACCOUNT`; move checkpoints/results off scratch |
