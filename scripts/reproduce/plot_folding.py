#!/usr/bin/env python
"""Plot and optionally log phase-1 forward-folding reproduction results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--result-dir",
        type=Path,
        help=(
            "Directory containing summary.csv and all_top_samples.csv. "
            "Example: results/reproduce/folding/cameo2022/dplm2_650m"
        ),
    )
    parser.add_argument("--summary-csv", type=Path)
    parser.add_argument("--details-csv", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--tensorboard-logdir", type=Path)
    parser.add_argument("--wandb", action="store_true")
    parser.add_argument("--wandb-project", default="dplm2-reproduction")
    parser.add_argument("--wandb-run-name")
    return parser.parse_args()


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    if args.result_dir:
        summary_csv = args.summary_csv or args.result_dir / "summary.csv"
        details_csv = args.details_csv or args.result_dir / "all_top_samples.csv"
        out_dir = args.out_dir or args.result_dir / "plots"
    else:
        if not args.summary_csv:
            raise SystemExit("ERROR: provide --result-dir or --summary-csv")
        summary_csv = args.summary_csv
        details_csv = args.details_csv or summary_csv.parent / "all_top_samples.csv"
        out_dir = args.out_dir or summary_csv.parent / "plots"
    return summary_csv, details_csv, out_dir


def require_columns(df: pd.DataFrame, columns: list[str], source: Path) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise SystemExit(
            f"ERROR: {source} is missing columns: {', '.join(missing)}"
        )


def save_length_bin_plot(summary: pd.DataFrame, out_dir: Path) -> Path:
    binned = summary[summary["length_bin"] != "all"].copy()
    if binned.empty:
        binned = summary.copy()

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    axes[0].bar(binned["length_bin"], binned["mean_rmsd"], color="#4C78A8")
    axes[0].set_title("Mean RMSD by Length Bin")
    axes[0].set_ylabel("RMSD")
    axes[0].tick_params(axis="x", rotation=30)

    axes[1].bar(binned["length_bin"], binned["mean_tmscore"], color="#54A24B")
    axes[1].set_title("Mean TMscore by Length Bin")
    axes[1].set_ylabel("TMscore")
    axes[1].set_ylim(0, max(1.0, float(binned["mean_tmscore"].max()) * 1.05))
    axes[1].tick_params(axis="x", rotation=30)

    path = out_dir / "length_bin_metrics.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def save_histograms(details: pd.DataFrame, out_dir: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    axes[0].hist(details["bb_rmsd_to_gt"], bins=min(30, max(5, len(details))), color="#4C78A8")
    axes[0].set_title("RMSD Distribution")
    axes[0].set_xlabel("RMSD")
    axes[0].set_ylabel("Targets")

    axes[1].hist(
        details["bb_tmscore_to_gt"],
        bins=min(30, max(5, len(details))),
        color="#54A24B",
    )
    axes[1].set_title("TMscore Distribution")
    axes[1].set_xlabel("TMscore")
    axes[1].set_ylabel("Targets")

    path = out_dir / "metric_distributions.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def save_length_scatter(details: pd.DataFrame, out_dir: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    axes[0].scatter(details["length"], details["bb_rmsd_to_gt"], s=24, color="#4C78A8")
    axes[0].set_title("Length vs RMSD")
    axes[0].set_xlabel("Length")
    axes[0].set_ylabel("RMSD")

    axes[1].scatter(details["length"], details["bb_tmscore_to_gt"], s=24, color="#54A24B")
    axes[1].set_title("Length vs TMscore")
    axes[1].set_xlabel("Length")
    axes[1].set_ylabel("TMscore")
    axes[1].set_ylim(0, 1.02)

    path = out_dir / "length_vs_metrics.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    return path


def load_data(summary_csv: Path, details_csv: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not summary_csv.exists():
        raise SystemExit(f"ERROR: summary CSV not found: {summary_csv}")
    if not details_csv.exists():
        raise SystemExit(f"ERROR: details CSV not found: {details_csv}")

    summary = pd.read_csv(summary_csv)
    details = pd.read_csv(details_csv)
    require_columns(
        summary,
        ["model", "split", "length_bin", "n", "mean_rmsd", "mean_tmscore"],
        summary_csv,
    )
    require_columns(
        details,
        ["length", "bb_rmsd_to_gt", "bb_tmscore_to_gt"],
        details_csv,
    )
    return summary, details


def log_tensorboard(
    logdir: Path,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    image_paths: list[Path],
) -> None:
    try:
        from torch.utils.tensorboard import SummaryWriter
    except Exception as exc:
        raise SystemExit(
            "ERROR: TensorBoard logging requested, but torch.utils.tensorboard "
            f"could not be imported: {exc}"
        ) from exc

    logdir.mkdir(parents=True, exist_ok=True)
    all_row = summary[summary["length_bin"] == "all"].iloc[0]
    with SummaryWriter(str(logdir)) as writer:
        writer.add_scalar("folding/mean_rmsd", float(all_row["mean_rmsd"]), 0)
        writer.add_scalar("folding/median_rmsd", float(all_row["median_rmsd"]), 0)
        writer.add_scalar("folding/mean_tmscore", float(all_row["mean_tmscore"]), 0)
        writer.add_scalar(
            "folding/median_tmscore", float(all_row["median_tmscore"]), 0
        )
        writer.add_scalar("folding/n", int(all_row["n"]), 0)
        for _, row in summary[summary["length_bin"] != "all"].iterrows():
            prefix = f"folding_by_length/{row['length_bin']}"
            writer.add_scalar(f"{prefix}/mean_rmsd", float(row["mean_rmsd"]), 0)
            writer.add_scalar(
                f"{prefix}/mean_tmscore", float(row["mean_tmscore"]), 0
            )
        writer.add_text("folding/summary_json", summary.to_json(orient="records"))
        writer.add_text(
            "folding/details_head_json",
            details.head(20).to_json(orient="records"),
        )
        for image_path in image_paths:
            image = plt.imread(image_path)
            writer.add_image(
                f"plots/{image_path.stem}", image, 0, dataformats="HWC"
            )


def log_wandb(
    project: str,
    run_name: str | None,
    summary: pd.DataFrame,
    details: pd.DataFrame,
    image_paths: list[Path],
) -> None:
    try:
        import wandb
    except Exception as exc:
        raise SystemExit(
            "ERROR: W&B logging requested, but wandb is not installed. "
            "Install it with `pip install wandb` in the active environment."
        ) from exc

    all_row = summary[summary["length_bin"] == "all"].iloc[0].to_dict()
    with wandb.init(project=project, name=run_name, config=all_row) as run:
        run.log(
            {
                "mean_rmsd": float(all_row["mean_rmsd"]),
                "median_rmsd": float(all_row["median_rmsd"]),
                "mean_tmscore": float(all_row["mean_tmscore"]),
                "median_tmscore": float(all_row["median_tmscore"]),
                "n": int(all_row["n"]),
                "summary": wandb.Table(dataframe=summary),
                "per_target": wandb.Table(dataframe=details),
            }
        )
        for image_path in image_paths:
            run.log({f"plots/{image_path.stem}": wandb.Image(str(image_path))})


def main() -> int:
    args = parse_args()
    summary_csv, details_csv, out_dir = resolve_paths(args)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary, details = load_data(summary_csv, details_csv)
    image_paths = [
        save_length_bin_plot(summary, out_dir),
        save_histograms(details, out_dir),
        save_length_scatter(details, out_dir),
    ]

    manifest = {
        "summary_csv": str(summary_csv),
        "details_csv": str(details_csv),
        "plots": [str(path) for path in image_paths],
    }
    manifest_path = out_dir / "plot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    if args.tensorboard_logdir:
        log_tensorboard(args.tensorboard_logdir, summary, details, image_paths)
        print(f"Wrote TensorBoard logs to {args.tensorboard_logdir}")

    if args.wandb:
        log_wandb(
            args.wandb_project,
            args.wandb_run_name,
            summary,
            details,
            image_paths,
        )

    print(f"Wrote plots to {out_dir}")
    for path in image_paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
