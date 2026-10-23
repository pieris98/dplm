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
scorer), and the **DeepGO-SE model data** (downloaded from the bio2vec.net
mirror: `https://bio2vec.net/data/deepgo/deepgo2/data.tar.gz`). Then it prints
the **one rsync TODO** that exists only locally:

```bash
# from the LOCAL workstation:
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

## 2b. Pre-flight checklist for the FIXED re-run (2026-10, post root-cause)

The first training campaign trained on a **randomly-initialized frozen encoder**
(`net.pretrain: false` skipped all weight loading — see RESEARCH_STATE.md §0).
The fix + a 23-check regression gauntlet + the Tier-1 conditioning probe are in
the repo since commits `206bf38`/`94ea51d`/`9e2f4c2`. Before (re)submitting:

1. **Sync code** (repo is bind-mounted into the sandbox — no image rebuild):
   `cd $DPLM_BASE/dplm-repo && git pull && git log --oneline -1` → expect `9e2f4c2` or newer.
2. **Quarantine the broken checkpoints** — the auto-resume scan globs
   `cond_dplm2*` under `$DPLM_LOGS`; the old random-encoder ckpts must not be
   picked up (the sbatch now also supports `FRESH=1` as a belt-and-braces guard):
   ```bash
   mkdir -p "$DPLM_BASE/quarantine_broken_ckpt"
   find "$DPLM_LOGS" -path '*cond_dplm2*' -name '*.ckpt' -not -path '*quarantine*' \
     -exec mv {} "$DPLM_BASE/quarantine_broken_ckpt/" \; -print
   ```
3. **In-container gauntlet** (interactive; `scripts/karolina/interactive.sh check`
   then `shell`, or `alloc`): run the environment-sensitive checks —
   ```bash
   $DPLM_PY scripts/debug/phase0_gauntlet.py --check pretrained-base identity grad-flow
   ```
   `pretrained-base` must report **573/573 tensors, max|diff|=0** on this
   machine's HF cache. Full suite (23 checks) if anything changed.
4. **Smoke** (DDP handshake + probe + checkpoint cadence, ~15 min on 2×8):
   ```bash
   ACCOUNT=$ACCOUNT FRESH=1 MAX_STEPS=20 \
   HYDRA_ARGS='callbacks.conditioning_probe.probe_every_n_steps=5 callbacks.model_checkpoint.every_n_train_steps=10 trainer.val_check_interval=10' \
   sbatch --export=ALL -N2 -t 00:40:00 scripts/karolina/cond_dplm2_train_multinode.sbatch
   ```
5. **Smoke tripwires** (from `logs/slurm/cond_dplm2_<id>.out`):
   - startup val loss **must start ≈ vanilla level (~1.9–2.0)** — the broken
     run started at 2.99; anything ≥ 2.5 → stop and investigate;
   - `[cond_probe] step …` lines appear every 5 steps with
     `a_frac ≈ 0.09–0.11`, `adapter_w ≈ 4.3e-3` (the 1e-5-init norm), and
     `loss_delta ≈ 0` (near-zero adapters at init);
   - `step_10-…`/`step_20-…` checkpoints written to `$DPLM_LOGS`.
6. **Resume test**: resubmit the *same* command **without** `FRESH=1` — the log
   must say `resuming from: …step_20-…` and finish with `DONE — reached 20 steps`.
7. **Full run**: `ACCOUNT=$ACCOUNT FRESH=1 sbatch --export=ALL scripts/karolina/cond_dplm2_train_multinode.sbatch`
   (100K steps, ~36 h × chunks on 2×8 A100). The self-resubmit chain does NOT
   set FRESH, so chunks resume the fixed run's own checkpoints.
   wandb: offline by default (`WANDB_MODE=offline`); sync afterwards from local:
   `wandb sync $DPLM_BASE/dplm-logs/wandb/<run>`.
8. **During the run** (probe cadence 2000): watch `probe/a_frac` (must stay
   ≈ 0.09–0.12 — real composition), `probe/loss_delta` (should lift off 0 as
   adapters learn), `probe/adapter_w` (grows slowly from 4.3e-3). Alarm fires
   at a_frac > 0.25 on two consecutive probes after step 5K (`probe/alarm=1`,
   loud log line; `alarm_abort=false` — it warns, not stops).

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
