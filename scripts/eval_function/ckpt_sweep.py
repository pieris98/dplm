"""Checkpoint sweep through the function-recovery probe (meeting point 3).

Tests whether the distribution shift is caused by training for too long:
for each checkpoint, run the reduced from-scratch eval (generation +
MMD/MRR/per-family-MMD scoring; IPS/DeepGO deliberately omitted) and
aggregate metric-vs-step curves. Resumable: steps with an existing
``results.json`` are skipped.

Requires the checkpoints on disk (e.g. the intermediate ones synced from
Meluxina). Anticipation (written 2026-10-13, from ProCALM Fig. A.4
precedent): conditioning correctness improves with training while realism
(MMD) degrades monotonically; the sweep locates the crossover if any.

  python scripts/eval_function/ckpt_sweep.py \
      --ckpt-glob 'logs/cond_dplm2_650m_cfpgen/checkpoints/*.ckpt' \
      --out eval_runs/ckpt_sweep --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent


def step_of(ckpt: Path) -> int:
    m = re.search(r"step_(\d+)", ckpt.name)
    return int(m.group(1)) if m else -1


def loss_of(ckpt: Path) -> float | None:
    m = re.search(r"loss_([\d.]+)\.ckpt", ckpt.name)
    return float(m.group(1)) if m else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt-glob", required=True)
    ap.add_argument("--out", default="eval_runs/ckpt_sweep")
    ap.add_argument("--n-labels", type=int, default=32)
    ap.add_argument("--seqs-per-label", type=int, default=8)
    ap.add_argument("--cfg-scale", type=float, default=1.0)
    ap.add_argument(
        "--arms", default="ours_cond,ours_null,real",
        help="vanilla is ckpt-independent — exclude to save GPU; add back if wanted",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ckpts = sorted(Path.glob(Path("."), args.ckpt_glob), key=step_of)
    if not ckpts:
        sys.exit(f"no checkpoints match {args.ckpt_glob!r}")
    print(f"{len(ckpts)} checkpoints: {[c.name for c in ckpts]}")

    for ckpt in ckpts:
        step = step_of(ckpt)
        run_dir = Path(args.out) / str(step)
        if (run_dir / "results.json").exists():
            print(f"[skip] step {step}: results exist")
            continue
        # torch checkpoints are zip archives — catches truncated transfers cheaply
        if not zipfile.is_zipfile(ckpt):
            print(
                f"[fail] step {step}: {ckpt.name} is not a complete zip archive "
                "(truncated/incomplete transfer?) — fix the file and re-run; skipping"
            )
            continue
        gen_cmd = [
            sys.executable, str(SCRIPTS / "generate_eval_set.py"),
            "--ckpt", str(ckpt),
            "--n-labels", str(args.n_labels),
            "--seqs-per-label", str(args.seqs_per_label),
            "--cfg-scale", str(args.cfg_scale),
            "--arms", args.arms,
            "--out", str(run_dir),
        ]
        score_cmd = [
            sys.executable, str(SCRIPTS / "score_function_eval.py"),
            "--evaldir", str(run_dir),
            "--arms", args.arms,
        ]
        print(f"[run ] step {step}: {ckpt.name}")
        if args.dry_run:
            print("  $", " ".join(gen_cmd))
            print("  $", " ".join(score_cmd))
            continue
        try:
            subprocess.run(gen_cmd, check=True)
            subprocess.run(score_cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[fail] step {step}: generation/scoring exited {e.returncode} "
                  "— fix and re-run (step stays pending)")

    if args.dry_run:
        return

    # ---- aggregate ---------------------------------------------------------
    rows = []
    for ckpt in ckpts:
        rj = Path(args.out) / str(step_of(ckpt)) / "results.json"
        if not rj.exists():
            continue
        r = json.loads(rj.read_text())
        rows.append(
            {
                "step": step_of(ckpt),
                "val_loss": loss_of(ckpt),
                "mmd_linear": r["ours_cond"]["mmd_linear_vs_real"],
                "mmd_null": r["ours_null"]["mmd_linear_vs_real"],
                "mrr": r["ours_cond"]["mrr"],
                "pfmmd_delta": r["ours_cond"]["pfmmd_linear_delta"],
                "go_f1_micro": r["ours_cond"].get("go_f1_micro"),
            }
        )
    lines = [
        "# Checkpoint sweep — metrics vs training step", "",
        "| step | val loss | MMD cond | MMD null | MRR | pfmmd delta | GO F1 micro |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: r["step"]):
        vl = f"{r['val_loss']:.2f}" if r["val_loss"] is not None else "-"
        f1 = f"{r['go_f1_micro']:.3f}" if r["go_f1_micro"] is not None else "-"
        lines.append(
            f"| {r['step']} | {vl} | {r['mmd_linear']:.3f} | {r['mmd_null']:.3f} "
            f"| {r['mrr']:.3f} | {r['pfmmd_delta']:+.4f} | {f1} |"
        )
    out_dir = Path(args.out)
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {out_dir}/summary.{{md,json}}")


if __name__ == "__main__":
    main()
