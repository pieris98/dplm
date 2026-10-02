# RESEARCH_STATE.md — handoff snapshot

**Date:** 2026-09-29 · **Repo:** `dplm` (branch `main`) · **Author context:** PhD project — function-conditioned protein generation on DPLM-2.

This file is the single entry point for resuming work. It summarizes what exists, what we learned, what is broken/pending, and where everything lives. Companion documents are linked in §2; numbers cite the committed reports.

---

## 1. Repository overview and what we built

### 1.1 Base project (pre-existing)

DPLM / DPLM-2 — discrete diffusion protein language models ([byprot](src/byprot/)). DPLM-2 ([src/byprot/models/dplm2/dplm2.py](src/byprot/models/dplm2/dplm2.py), `modules/dplm2_modeling_esm.py`) extends DPLM with a structure-token modality: unified vocab of 33 AA + 8,192 LFQ structure tokens + 4 specials = 8,229, trained in 4 modes (single-sequence / folding / inverse-folding / joint, 25% each) with timestep-weighted cross-entropy summed over the aa-track and struct-track. The user-facing entrypoints are `train.py` and `generate_dplm2.py` (vanilla).

### 1.2 Our contribution: ConditionalDPLM-2 (function conditioning)

**Goal:** GO / InterPro function-annotation conditioning of frozen pretrained DPLM-2 650M, following ProCALM-style parallel adapters + CFP-Gen-style annotation embedding. Architecture ([technical reference](reports/conditional_dplm2_technical_reference.md) has full detail + data-flow diagrams):

| Piece | File | What it does |
|---|---|---|
| `ConditionalDPLM2` | [src/byprot/models/dplm2/dplm2_conditional.py](src/byprot/models/dplm2/dplm2_conditional.py) | subclasses the DPLM-2 LightningModule; encodes label sets, stashes adapter inputs, replicates the parent `forward` body (the parent hardcodes net kwargs — adapters would silently never fire otherwise), CFG double-pass `logits = uncond + w·(cond − uncond)` |
| Parallel adapters | [conditioning/parallel_adapter.py](src/byprot/models/dplm2/conditioning/parallel_adapter.py) | per-layer low-rank bottleneck (LN → down → **concat condition per position** → MLP → up), near-zero-init output, N independent adapters summed per layer; applied at all 33 layers (`hidden + adapter(h, s)`) |
| AnnotationEmbedder | [conditioning/annotation_embedder.py](src/byprot/models/dplm2/conditioning/annotation_embedder.py) | per-type `nn.Embedding(vocab+1)` (GO molecular-function + IPR), CFG null token at index `vocab_size`, −1 padding masked, multi-label sum |
| Threading adapters through the encoder | [modules/dplm2_modeling_esm.py](src/byprot/models/dplm2/modules/dplm2_modeling_esm.py) | `layer_adapters` / `layer_adapter_inputs` kwargs through ModifiedEsmEncoder/Model/EsmForDPLM2 |

**Training** (frozen base, 19.6M trainable = 2.9%): 45K CFP-Gen proteins (GO-F + IPR labels), CFG label dropout 0.1, 100K steps on 4×A100 DDP bf16 on Meluxina → **final val/loss 1.95**. Config: [configs/experiment/dplm2/cond_dplm2_650m_cfpgen_meluxina.yaml](configs/experiment/dplm2/cond_dplm2_650m_cfpgen_meluxina.yaml). Checkpoint: `logs/cond_dplm2_650m_cfpgen/checkpoints/step_99999.0-loss_1.95.ckpt` (also on Meluxina). No loss changes vs vanilla — function is conditioning-only (this matters for checkpoint selection, see §3.4).

**Conditioning granularity:** the label vector is **global/protein-level** — broadcast to every residue position by the adapters. There is no residue/domain-level pathway.

### 1.3 Evaluation stack (this project's second big deliverable)

All under [scripts/eval_function/](scripts/eval_function/) + [src/byprot/eval/](src/byprot/eval/): 4/7-arm generation driver, predictor runners/parsers (InterProScan, DeepGO-SE), metric ports (MRR, MMD, per-family MMD, set-match, CAFA Fmax, GO-DAG propagation), scorer, IPS audit tool, UMAP visualization, metric sanity checks. Details in §5.

### 1.4 HPC infrastructure

[scripts/meluxina/](scripts/meluxina/) — containerized SLURM training/generation on Meluxina (Apptainer sandbox dir, bind-mount strategy, QOS handling). Read [MELUXINA_RUNBOOK.md](scripts/meluxina/MELUXINA_RUNBOOK.md) before touching it. Non-git eval assets placement commands were delivered 2026-09-29 (in chat; summary in §4.3) and are **not yet executed**.

---

## 2. Document map (where to look)

| Document | Contents |
|---|---|
| [reports/weekly_meeting_2026-09-22.md](reports/weekly_meeting_2026-09-22.md) | **Methodology + first results.** Metric primer (what each metric measures/can't tell you, §4), 7-arm results tables, per-branch GO diagnostic, IPS zero-result audit, per-family MMD, positive/negative results, diagnosis. The densest single document. |
| [reports/weekly_meeting_2026-09-29.md](reports/weekly_meeting_2026-09-29.md) | **This week's report:** complete final tables (GO+IPR+per-family MMD+UMAP), issue log with root causes/fixes, why-no-training-this-week rationale, next steps. |
| [reports/conditional_dplm2_technical_reference.md](reports/conditional_dplm2_technical_reference.md) | Architecture reference: modules, shapes, forward/generate paths, CFG, adapter plumbing, doxygen-style diagrams. |
| [reports/conditional_dplm2_progress_report.md](reports/conditional_dplm2_progress_report.md) | Earlier progress narrative (implementation history, tokenization mismatch story, dataset merging with CFP-Gen). |
| [reports/conditional_dplm2_session_impl.md](reports/conditional_dplm2_session_impl.md) | Session-level implementation log. |
| [reports/cfpgen_dplm2_meeting_synthesis.md](reports/cfpgen_dplm2_meeting_synthesis.md) | CFP-Gen vs DPLM-2 methodology comparison (recap document for the 09-15 meeting). |
| [reports/figures/](reports/figures/) | Committed UMAP figures (`umap_by_arm.png`, `umap_centroids.png`, `umap_labels.png`). |
| [plans/meetings.md](plans/meetings.md) | **Meeting log** — 2026-09-15 and 2026-09-29 entries: professor feedback verbatim + evidence-based resolutions + prioritized next steps. The task queue in §4 comes from here. |
| [scripts/meluxina/MELUXINA_RUNBOOK.md](scripts/meluxina/MELUXINA_RUNBOOK.md) | HPC operations: sbatch, containers, binds, known failure modes. |
| CFP-Gen side | Local repo `/home/cherry/dev/phd/cfpgen` (papers/notes: `cfpgen_paper.md`, `dplm2_paper.md` were deleted from this repo's root — see `papers/` dir). CFP-Gen is *not* integrated; it runs in its own env/repo via [scripts/eval_function/cfpgen_arm.py](scripts/eval_function/cfpgen_arm.py). |

---

## 3. Key empirical findings

### 3.1 Setup (all numbers from `eval_runs/fn_eval_v1/`, committed reports)

7 arms × ~250 sequences: `ours_cond` (w=1), CFG sweep `ours_cond_cfg{2,4,8}`, `ours_null` (null-conditioned, same trained weights), `vanilla` (pretrained DPLM-2, no conditioning), `real` (positive control = the real held-out sequences). Prompts: 32 held-out GO-F labels × 8 proteins from CFP-Gen `test.pkl`; len-256 generation, 256 denoising steps. Predictors: InterProScan 5.78-109.0, DeepGO-SE (GPU). GO ground truth is **ancestor-expanded** over the GO DAG.

### 3.2 Results table (the reference numbers)

| Arm | GO Fmax ↑ | GO recall micro | IPR F1 micro | MRR ↑ | per-family Δ (lin) ↑ | frac own-closest ↑ | MMD-linear ↓ | \|ESM centroid−real\| ↓ |
|---|---|---|---|---|---|---|---|---|
| ours_cond w=1 | 0.0139 | 0.0052 | 0.0000 | 0.1053 | +0.0045 | 0.53 | 0.897 | 0.366 |
| ours_cond w=2 | 0.0277 | 0.0420 | 0.0000 | **0.1277** | **+0.0185** | **0.71** | 0.492 | 0.319 |
| ours_cond w=4 | **0.0369** | 0.0745 | 0.0000 | 0.0865 | +0.0122 | 0.66 | 0.376 | 0.328 |
| ours_cond w=8 | 0.0337 | 0.0953 | 0.0000 | 0.0860 | +0.0066 | 0.61 | 0.407 | 0.402 |
| ours_null | 0.0310 | 0.0022 | 0.0000 | 0.0816 | −0.0009 | 0.54 | 0.854 | 0.367 |
| vanilla | 0.0591 | 0.0966 | 0.0037 | 0.0745 | −0.0011 | 0.51 | **0.160** | **0.086** |
| **real (ceiling)** | **0.2228** | 0.3043 | **0.9782** | 0.8099* | +0.8006 | 1.00 | 0 | 0 |

\* 0.81 = MRR's sample-size noise floor at 8 seqs/label (not 1.0). DeepGO-SE's published natural-protein MFO Fmax is 0.386; our real-arm 0.223 is the ceiling for this predictor+GT+set.

### 3.3 What works (converged)

1. **A real conditioning signal exists.** MRR 0.105 (w=1) → 0.128 (w=2) vs controls 0.075–0.082 (chance ≈ 0.068). Independently confirmed by per-family MMD (Δ peaks +0.0185 at w=2, 71% of labels own-closest; controls at chance). Per-label: some labels rank 1st–4th/59 with Δ up to +0.14 (NAD binding), others near bottom (Δ −0.23).
2. **CFG machinery works exactly as designed.** Dose-response on realism (MMD 0.897→0.376), on GO recovery (Fmax 0.014→0.037, beats null at w≥4), on diversity (MF terms 32→62; ESM intra-arm spread 0.178→0.30), peaks/decays at sane w.
3. **First non-zero function recovery — GO only.** GO Fmax/recall rise with guidance; recovered terms are GO-DAG neighbours of prompts (exact-term overlap stays 0–1).
4. **The eval stack is self-validating.** Real arm: IPR F1 0.978, GO Fmax 0.223, MRR 0.81 (= its own noise floor), per-family Δ = cross (own = 0). The positive control caught one metric-invalidating bug (§3.5) and motivated the IPS audit.
5. **Pipeline economics.** Full eval loop ≈ 1 h on one RTX 3090 (generation + IPS + DeepGO-SE + scoring). DeepGO-SE ≈ 1 min/arm on GPU (`-d cuda`) with a persisted ESM-2 3B cache — it was hours on CPU.

### 3.4 What failed (negative results)

1. **Zero IPR recovery everywhere** — F1 = 0.0000 in all 1,536 generated sequences vs 0.978 real ceiling. Audited (§3.5): genuine, not a bug.
2. **GO recovery far below both ceilings** — best 0.037 vs vanilla-unconditional 0.059 and real 0.223. Adapter-trained outputs are less function-annotatable than the frozen base's random samples.
3. **Mode collapse in function space** — ours w=1 annotates to 32 unique MF terms vs 243 (real) / 369 (vanilla); ESM intra-arm spread 0.178, *tighter than the real arm's own spread* (0.181). Collapse lives in the trained weights (null shows it too); CFG only partially lifts it.
4. **Sequence-realism regression** — MMD 0.37–0.90 vs vanilla 0.16; UMAP: all adapter-trained arms (incl. null) occupy a **disjoint ESM-space region** (centroid-to-real 0.32–0.40 vs vanilla 0.086). Displacement is a property of the trained weights, not of conditioning.
5. **CFG does not approach the real manifold in ESM space** — its MMD gain is composition-level only (k-mer space); semantically the outputs remain off-manifold. This reconciles MMD improvement with IPS seeing only a low-complexity artifact.
6. **No GO loss term + "best-total-loss" checkpoint selection is function-blind** — total val loss = aa-track + struct-track CE; the aatype component plateaued at ~2K steps while struct kept improving, so "best" checkpoints are struct-selected. Overtraining hypothesis (adapters steered too long) is open — testable via checkpoint sweep (§4.2).

### 3.5 Diagnosis (two separable layers)

1. **Capacity/mode-collapse layer (dominant):** 19.6M broadcast adapters transmit weak label information — enough for MRR/per-family-Δ signal, far too little to shape low-complexity outputs into diverse, domain-bearing sequences.
2. **Granularity layer (secondary, measurable):** given the collapse, GO (global) shows graded guidance-responsive recovery while IPR (local domains) is identically zero — consistent with global labels being expressible through position-broadcast adapters and local placement not. **The planned LoRA run doubles as this hypothesis's test**: if GO recovers with restored diversity but IPR stays ~0, the limitation is architectural → motivates position-aware conditioning.

### 3.6 Issues encountered → mitigations (the war stories)

| Issue | Mitigation (permanent, committed) |
|---|---|
| EBI FTP refused all connections (no IPS 6, no direct downloads) | IPS **5.78-109.0 GitHub release tarball** (bundles all member DBs); local install at `eval/interproscan-5.78-109.0/` |
| DeepGO-SE `data.tar.gz` KAUST link dead (only data source) | Full dataset recovered via **web archive** (16 GB, `eval/deepgo2/data/`); runner = docker `coolmaksat/deepgose`; issue filed upstream |
| Scorer compared prompt label **ints** vs predictor **accessions** → F1=0 incl. positive control | Convert GT via manifest `label_map` inverse maps; positive control now 0.978 (the bug was caught *by* the control) |
| GO GT never ancestor-expanded — `obonet` missing and `import obonet` sits inside the `--obo` block, so the scorer **silently skipped** expansion (all-zeros GO columns) | Installed `obonet` (pinned in `docker/dplm-pip-freeze.txt`); re-scored with `--obo`; verified expansion + node-attr `is_a` extraction on the real ontology. **Lesson: guard against silently-skipped optional stages** |
| DeepGO-SE output `.tsv` files contained only progress spam | It writes **gzipped per-branch TSVs** (`aatype_preds_{mf,bp,cc}.tsv.gz`) next to the input; parser globs those |
| DeepGO-SE re-downloaded ESM-2 3B (5.7 GB) every container run; CPU pass took hours | Persisted torch-hub cache dir bind-mounted at `/root/.cache/torch/hub/checkpoints` (local: `~/.cache/esm_torch_hub`); `-d cuda` ≈ 1 min/arm |
| Null-conditioned samples contain gap tokens (`-`, `.`) → IPS rejects | `sanitize_sequence()` at FASTA-write time (both drivers) |
| Duplicate FASTA headers crashed DeepGO-SE (protein selected via multiple labels) | Dedup by UniProt ID — one generation per unique protein |
| Subset re-runs wiped other arms from `manifest.json` | Manifest merged per-arm |
| Eval generation GPU ~5% utilized | Flat label-crossing batching, `--batch-size` (default 64) |
| `ipr_aupr_*` misleading: micro-AP on **binary** matrices has nonzero baseline at TP=0 (ours "AUPR" 0.029 with zero overlap; vanilla 0.007 *with* overlap) | Read only P/R/F1 for set-valued predictors; AUPR only meaningful for DeepGO-SE's per-term scores. (Consider dropping the columns or annotating them.) |
| Meluxina batch shell: no apptainer on PATH / spool-path breaks `$BASH_SOURCE` / QOS rejects / openfold shadowing when binding whole repo / container python invisible | Hardcoded EasyBuild apptainer prefix; `SLURM_SUBMIT_DIR` root; `--account=p201418 --qos=…`; bind only `src/configs/scripts/train.py/.git`; **sandbox dir** (not SIF) + `--cleanenv` + absolute `/opt/venv/bin/python` (`DPLM_PY`); retry wrapper for mount flakes. Full list in the runbook |

---

## 4. Outstanding bugs, active experiments, pending work

### 4.1 Known open issues (no known *code* bugs right now)

- `ipr_aupr_*` / `auc_roc` columns are artifacts for binary predictor output (§3.6) — either drop from tables or footnote everywhere they appear.
- CFG-sweep arms were generated on the **pre-dedup** prompt set (256 seqs) vs 250 for the main arms — sub-0.01 cross-arm deltas should not be over-read. A clean re-generation of the sweep on the deduped set would remove the caveat.
- `mrr_ranks` / `pfmmd_detail` in `results.md` are noisy (dicts in markdown); they live properly in `results.json` (markdown writer now filters nested dicts).
- Macro AUC/AUPR emit `nan` (labels without positives) — cosmetic.
- The real arm is not length-matched to generation (mean 367 vs 256): the ceiling is validated but not at generation length. Vanilla (len 256) still annotates to 517 accessions, so length is a weak effect — noted, not fixed.

### 4.2 Active experiments / designed-but-not-run

1. **`cfpgen` comparison arm** — [cfpgen_arm.py](scripts/eval_function/cfpgen_arm.py) written, never executed. Runs CFP-Gen 650M in its own repo/env on our exact prompts (manifest real-arm records) → adds the paper-method column to the table.
2. **Checkpoint sweep × recovery probe** (tests overtraining hypothesis, feedback 4): the Meluxina run saved `top_k=2 every 2000` checkpoints; sweep 3–5 of them through the eval driver (32 prompts × 8, maybe reduced seqs/arm) and plot recovery-vs-step. Expect: function recovery (if any) peaks early while realism degrades late.
3. **LoRA on attention+FFN** — the priority training change. Same 45K set, identical eval protocol. Success criterion: IPR recovery > 0 at w=1–2 with MMD not worse than vanilla. Doubles as the granularity-layer test (§3.5).
4. **Validation callback** (for future training runs) — **architecture decided 2026-10-02, two-tier split** (external predictors must never run inside the training loop: IPS takes ~25 min/arm on our degenerate sequences and DeepGO-SE needs ~6 GB GPU beside training):
   - *Tier 1 — in-training callback (cheap, every validation, same GPU/process):* loss-delta proxy (fixed batches, null vs real-conditioned forward → condition-sensitivity curve); generation probe every N steps (8 fixed labels × 4 seqs → `eval_probes/step_<k>/aatype.fasta`); **predictor-free metrics in-process → wandb** (probe MRR, per-family Δ, MMD vs a cached real-reference subset). These are the checkpoint-selection signal.
   - *Tier 2 — decoupled predictor recovery:* [eval_pipeline.sbatch](scripts/meluxina/eval_pipeline.sbatch) with `SKIP_GENERATE=1` (and `RUN_NAME`/`EVALDIR` pointed at a probe dir), submitted `--dependency=afterok:<job>` or manually on chosen checkpoints. Only here do IPS/DeepGO-SE/Fmax run.
   - Deferred: PDB/pLDDT/scTM qualitative table (needs ESMFold in-container — separate decision).

### 4.3 Pending task queue (from [plans/meetings.md](plans/meetings.md), priority order)

- [x] **Meluxina artifact placement** — synced 2026-10-02 (deepgo2 data, cfpgen eval pkl/mappings, ESM-2 3B cache, deepgose sandbox; IPS+go.obo were already there). Note: everything eval-related only runs **inside a GPU allocation** (login nodes block user namespaces + modules). One-time remaining: `python3 -m pip install --target <repo>/eval/pylibs obonet==1.2.0` on the login node.
- [x] **`scripts/meluxina/eval_pipeline.sbatch`** (2026-10-02) — end-to-end eval job: generation → IPS → DeepGO-SE → scoring, stage-idempotent, `RUN_NAME/CKPT/SWEEP/SKIP_GENERATE` env-configurable; `common.sh` now auto-binds `eval/` (ro), `eval_runs/` (rw) and adds `eval/pylibs` to `PYTHONPATH`. See the runbook's eval section.
- [ ] Feedback 3 — **benchmark survey**: eval protocols of other function-conditional / multimodal PLMs (ProGen-family conditional evals, ESM3 function-annotation evals, ZymCTRL EC, CAFA) beyond CFP-Gen's.
- [ ] Feedback 6 — **combined structural+sequence+functional comparison** per arm/w, incl. the degradation check: co-generation designability (identical settings: pretrained vs our ckpt null) + CAMEO 2022 forward folding + paper-numbers table.
- [ ] UMAP extension (optional): structure-token / pLDDT coloring; per-family centroid grid for all 32 labels.
- [ ] Smin (CAFA semantic distance) via CAFA-evaluator — deferred, needs information-content weights.
- [ ] Docs: loss-decomposition section + Mermaid training/inference diagrams in the technical reference (09-15 meeting items still open).
- [ ] Data scaling path (gated on LoRA result): tokenize the ~49K missing CFP-Gen proteins with the corrected v2 pipeline (82–104K total), LoRA on the combined set.

---

## 5. Reference: entrypoints, data formats, metrics

### 5.1 Script entrypoints

| Script | Purpose | Key flags |
|---|---|---|
| [generate_conditional_dplm2.py](generate_conditional_dplm2.py) | Function-conditioned sampling from a ConditionalDPLM2 ckpt (self-configuring from Lightning hparams; `model.` prefix stripped) | `--ckpt`, `--labels type:id,id`, `--cfg-scale`, `--cfg-sweep`, `--save-pdb` |
| [scripts/eval_function/generate_eval_set.py](scripts/eval_function/generate_eval_set.py) | 4-arm eval-set driver (ours_cond/ours_null/vanilla/real) over CFP-Gen `test.pkl`; per-cfg output dirs; manifest merged per-arm; sanitizes sequences | `--test-pkl` (default local cfpgen path), `--go-mapping`, `--ipr-mapping`, `--n-labels 32`, `--seqs-per-label 8`, `--batch-size 64`, `--len 256`, `--cfg-sweep` |
| [scripts/eval_function/score_function_eval.py](scripts/eval_function/score_function_eval.py) | Scorer → `results.json` + `results.md` (MRR/MMD/per-family MMD always; IPR from `--ips-tsv`; GO from `--deepgose-tsv` glob) | `--evaldir`, `--arms`, `--ips-tsv arm=path` (repeatable), `--deepgose-tsv 'arm=dir/aatype_preds_*.tsv.gz'` (repeatable, globs ok), `--obo eval/deepgo2/data/go.obo`, `--mrr-label-type go|ipr` |
| [scripts/eval_function/diagnose_ips.py](scripts/eval_function/diagnose_ips.py) | Scorer-free IPS audit: join integrity, raw overlap, set_match_metrics reproduction, what-IPS-finds, per-label recovery; shuffled-control writer | `--evaldir`, `--arms`, `--shuffle-arm ARM`, `--shuffle-real DIR` |
| [scripts/eval_function/plot_umap.py](scripts/eval_function/plot_umap.py) | Shared UMAP over arms' ESM embeddings + raw-space separation stats (`umap_summary.json`) + per-label panels | `--evaldir`, `--arms`, `--n-label-panels 6`, `--n-neighbors/--min-dist/--seed` |
| [scripts/eval_function/check_function_metrics.py](scripts/eval_function/check_function_metrics.py) | Metric sanity suite (18 checks incl. per-family MMD controls). **Run after touching `function.py`.** | — |
| [scripts/eval_function/cfpgen_arm.py](scripts/eval_function/cfpgen_arm.py) | CFP-Gen 650M comparison arm (own env/repo; PYTHONPATH shadowing; reads manifest real-arm prompts) | run from cfpgen checkout |
| [scripts/meluxina/common.sh](scripts/meluxina/common.sh) | Container launcher (apptainer discovery, sandbox/SIF, binds, `DPLM_PY=/opt/venv/bin/python`, retry) | sourced by sbatch/interactive |
| [scripts/meluxina/cond_dplm2_train.sbatch](scripts/meluxina/cond_dplm2_train.sbatch) / [cond_dplm2_smoke.sbatch](scripts/meluxina/cond_dplm2_smoke.sbatch) | Full training / 4-stage smoke validation | `sbatch --account=p201418 --qos=long|test` |
| [scripts/meluxina/interactive.sh](scripts/meluxina/interactive.sh) | alloc/shell/check helpers; [checks.py](scripts/meluxina/checks.py) = 8-point GPU/dataset/binds gauntlet | `./interactive.sh alloc` |

Canonical full-eval invocation (local, from repo root, dplm env):

```bash
python scripts/eval_function/score_function_eval.py --evaldir eval_runs/fn_eval_v1 \
  --arms ours_cond,ours_cond_cfg2,ours_cond_cfg4,ours_cond_cfg8,ours_null,vanilla,real \
  --ips-tsv ARM=eval_runs/fn_eval_v1/ARM/ips.tsv         # …repeat per arm \
  --deepgose-tsv 'ARM=eval_runs/fn_eval_v1/ARM/aatype_preds_*.tsv.gz'   # …repeat per arm \
  --obo eval/deepgo2/data/go.obo
```

DeepGO-SE runner (docker locally / apptainer on Meluxina — same binds):

```bash
docker run --gpus all \
  -v $PWD/eval/deepgo2/data:/workspace/deepgo2/data \
  -v ~/.cache/esm_torch_hub:/root/.cache/torch/hub/checkpoints \
  -v $PWD/eval_runs/fn_eval_v1:/workspace/deepgo2/eval \
  coolmaksat/deepgose \
  python predict.py -if eval/<arm>/aatype.fasta -dr data -d cuda
```

InterProScan: `eval/interproscan-5.78-109.0/interproscan.sh -i <fasta> -f TSV -o <out>.tsv -goterms --disable-precalc -cpu 8` (writes `<out>.tsv`).

### 5.2 Dataset / artifact formats

| Artifact | Format |
|---|---|
| CFP-Gen eval data | `test.pkl` (held-out proteins: seq + GO/IPR label **ints**), `go_mapping.pkl` / `ipr_mapping.pkl` (int ↔ accession). Local: `/home/cherry/dev/phd/cfpgen/…` |
| `manifest.json` (per eval run) | `{label_map: {go: {acc: int}, ipr: {acc: int}}, arms: {arm: [{seq_id, prompt_go: [int], prompt_ipr: [int]}]}}` — **the int↔accession bridge for all GT conversion** |
| Per-arm dir (`eval_runs/fn_eval_v1/<arm>/`) | `aatype.fasta` (headers `>arm_go<lbl>_<UniProt>`, sanitized), `ips.tsv` (IPS output), `aatype_preds_{mf,bp,cc}.tsv.gz` (DeepGO-SE), `aatype_esm.pkl` (torch dict `{data: Tensor[n,2560], proteins: [ids]}` — order-aligned to the FASTA *at prediction time*; may carry stale extra ids after regeneration → align by id) |
| IPS TSV | tab-separated; col 0 protein id, col 3 analysis, col 5 signature description, **col 11 InterPro accession** (only on InterPro rows), col 13+ `GO:…` with `-goterms` |
| DeepGO-SE TSVs | gzipped, one per GO branch, lines `protein_id \t GO:XXXXXXX \t score` |
| Scorer outputs | `<evaldir>/results.json` (everything incl. nested per-label dicts) + `results.md` (scalar columns only); `<evaldir>/umap_summary.json`; figures in `<evaldir>/figs/` |
| Checkpoint | `logs/cond_dplm2_650m_cfpgen/checkpoints/step_99999.0-loss_1.95.ckpt` (self-configuring; contains conditioning hparams) |
| Non-git eval assets (local) | `eval/interproscan-5.78-109.0/` (35 GB), `eval/deepgo2/data/` (16 GB), `eval/go.obo` (36 MB), `~/.cache/esm_torch_hub/` (5.7 GB) |

### 5.3 Metrics (all in [src/byprot/eval/function.py](src/byprot/eval/function.py), sanity-checked)

| Metric | What it measures | Read it as |
|---|---|---|
| **MRR** (`mrr`) | label-conditionality: mean spectrum-embedding of generated-for-L ranked among all real group means; reciprocal rank averaged | already label-grouped — do **not** "adjust" for per-family; ceiling ≈ 0.81 at 8 seqs/label |
| **MMD linear/gaussian** (`mmd`) | arm-level distributional realism in k-mer space vs real arm (condition-blind) | lower better; interpret vs vanilla 0.16, not 0 |
| **per-family MMD** (`per_family_mmd`) | own = MMD(gen-for-L, real-L) vs cross = mean MMD(gen-for-L, real-L′≠L); **delta = cross−own**; frac own-closest | Δ>0 ⇒ label-specific proximity beyond realism; controls ≈ 0; real arm: own=0, frac=1 |
| **set-match P/R/F1 micro+macro** (`set_match_metrics`) | predicted vs prompted accession sets (IPR from IPS; GO from DeepGO-SE ≥0.5) | the honest recovery numbers for set-valued predictors |
| **GO Fmax** (`fmax`) | CAFA threshold-free max-F1 over DeepGO-SE per-term probabilities vs **DAG-expanded** GT (`propagate_ancestors` + obonet) | headline GO metric; real ceiling here 0.223, DeepGO-SE paper 0.386 |
| AUPR/AUC | from binarized pred matrix | **meaningless for IPS (binary sets)** — nonzero baseline at TP=0; use only for DeepGO-SE scores |
| **UMAP stats** (`plot_umap.py`) | raw ESM-space \|centroid−real\| + intra-arm spread (UMAP itself is visualization-only) | vanilla 0.086 = on-manifold; ours 0.32–0.40; spread < real's 0.181 ⇒ collapse |
| Smin | CAFA semantic distance | deferred (needs IC weights) |

---

## 6. One-paragraph status

ConditionalDPLM-2 (frozen DPLM-2 650M + 19.6M broadcast adapters + GO/IPR annotation embedding, CFG) trained 100K steps on 45K CFP-Gen proteins and fully evaluated on a self-validating 7-arm stack: **the conditioning signal is real but weak** (MRR/per-family-Δ peak at w=2, controls at chance), **function recovery is essentially absent** (IPR exactly 0 everywhere — audited genuine, dominated by a low-complexity antifreeze-repeat artifact; GO ≤0.037 vs 0.059 vanilla-unconditional and 0.223 real ceiling), and **adapter training displaced generation into a collapsed, composition-degenerate ESM-space region** that CFG only compositionally repairs. Diagnosis: capacity/mode-collapse first (→ LoRA run, which also tests the global-vs-local granularity asymmetry that GO-vs-IPR exposes), training-signal/checkpoint-selection second (→ checkpoint sweep + validation callback), data scale third. The eval stack, its controls, and the full issue history are committed and reproducible (~1 h/iteration on one 3090).
