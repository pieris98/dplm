# Debug tooling

## phase0_gauntlet.py

Code-correctness gauntlet for ConditionalDPLM-2 (see
[plans/mode_collapse_debug_plan.md](../../plans/mode_collapse_debug_plan.md)).
Run from the repo root:

```bash
python scripts/debug/phase0_gauntlet.py --check all          # everything
python scripts/debug/phase0_gauntlet.py --list               # list checks
python scripts/debug/phase0_gauntlet.py --check pretrained-base   # the root-cause regression
```

All checks are local-GPU (fits a 24 GB card, fp32). Exit code 1 on any failure.
Current state: **23/23 green** (2026-10-20, after the pretrained-loading fix).

**Run this gauntlet after any change to** `get_net_dplm2`, the conditional model
classes, the configs, the collater/dataset, or before launching any training run.

## P0.7 — scorer regression (not automated)

Re-run the scorer on the committed fn_eval_v1 outputs and confirm the headline
numbers reproduce (real arm IPS F1 0.978 etc.):

```bash
python scripts/eval_function/score_function_eval.py --evaldir eval_runs/fn_eval_v1 \
  --arms ours_cond,ours_cond_cfg2,ours_cond_cfg4,ours_cond_cfg8,ours_null,vanilla,real \
  --ips-tsv ours_cond=eval_runs/fn_eval_v1/ours_cond/ips.tsv \
  --deepgose-tsv 'ours_cond=eval_runs/fn_eval_v1/ours_cond/aatype_preds_*.tsv.gz' \
  --obo eval/deepgo2/data/go.obo
# repeat --ips-tsv/--deepgose-tsv per arm; see RESEARCH_STATE.md §5.1
```

## Broken checkpoints (artifacts of the 2026-10-20 incident)

`logs/cond_dplm2_650m_cfpgen/checkpoints/step_{79999,99999}.0-loss_{1.9*.ckpt}`
were trained on a randomly-initialized frozen encoder (pretrained weights never
loaded — see RESEARCH_STATE.md §0). Keep for forensics only; **do not** use them
for evaluation, paper claims, or as a resume base.
