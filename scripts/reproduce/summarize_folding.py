#!/usr/bin/env python
"""Summarize DPLM-2 forward-folding evaluator output.

The evaluator writes one top_sample.csv per target and an aggregate
forward_fold_metrics.csv. This script normalizes those per-target files into a
compact CSV with paper-facing RMSD and TMscore columns.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_LENGTH_BINS = (
    (60, 128),
    (128, 256),
    (256, 512),
    (512, None),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--details-csv", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--run-config", type=Path)
    parser.add_argument("--input-fasta", type=Path)
    parser.add_argument("--generation-dir", type=Path)
    return parser.parse_args()


def read_top_samples(eval_dir: Path) -> pd.DataFrame:
    if not eval_dir.exists():
        raise SystemExit(f"ERROR: evaluator directory does not exist: {eval_dir}")

    aggregate = eval_dir / "all_top_samples.csv"
    if aggregate.exists():
        df = pd.read_csv(aggregate)
    else:
        csv_paths = sorted(eval_dir.glob("*/*/top_sample.csv"))
        if not csv_paths:
            raise SystemExit(
                "ERROR: no top_sample.csv files found under "
                f"{eval_dir}; expected evaluator output like length_*/target/top_sample.csv"
            )
        frames = []
        for csv_path in csv_paths:
            frame = pd.read_csv(csv_path)
            frame["top_sample_csv"] = str(csv_path)
            frames.append(frame)
        df = pd.concat(frames, ignore_index=True)

    unnamed = [column for column in df.columns if column.startswith("Unnamed:")]
    if unnamed:
        df = df.drop(columns=unnamed)

    required = {"bb_rmsd_to_gt", "bb_tmscore_to_gt", "length"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise SystemExit(
            "ERROR: evaluator output is missing required columns: "
            + ", ".join(missing)
        )

    df["bb_rmsd_to_gt"] = pd.to_numeric(df["bb_rmsd_to_gt"], errors="coerce")
    df["bb_tmscore_to_gt"] = pd.to_numeric(
        df["bb_tmscore_to_gt"], errors="coerce"
    )
    df["length"] = pd.to_numeric(df["length"], errors="coerce")
    df = df.dropna(subset=["bb_rmsd_to_gt", "bb_tmscore_to_gt", "length"])
    if df.empty:
        raise SystemExit("ERROR: no valid metric rows remained after parsing")
    return df


def summarize_subset(
    df: pd.DataFrame, model: str, split: str, length_bin: str
) -> dict[str, object]:
    return {
        "model": model,
        "split": split,
        "length_bin": length_bin,
        "n": int(len(df)),
        "mean_rmsd": df["bb_rmsd_to_gt"].mean(),
        "median_rmsd": df["bb_rmsd_to_gt"].median(),
        "mean_tmscore": df["bb_tmscore_to_gt"].mean(),
        "median_tmscore": df["bb_tmscore_to_gt"].median(),
    }


def build_summary(df: pd.DataFrame, model: str, split: str) -> pd.DataFrame:
    rows = [summarize_subset(df, model, split, "all")]

    for lower, upper in DEFAULT_LENGTH_BINS:
        if upper is None:
            mask = df["length"] >= lower
            label = f"{lower}+"
        else:
            mask = (df["length"] >= lower) & (df["length"] < upper)
            label = f"{lower}-{upper - 1}"
        subset = df[mask]
        if not subset.empty:
            rows.append(summarize_subset(subset, model, split, label))

    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    top_samples = read_top_samples(args.eval_dir)
    summary = build_summary(top_samples, args.model, args.split)

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_csv, index=False)

    if args.details_csv:
        args.details_csv.parent.mkdir(parents=True, exist_ok=True)
        top_samples.to_csv(args.details_csv, index=False)

    print(f"Loaded {len(top_samples)} top-sample rows from {args.eval_dir}")
    print(f"Wrote summary to {args.output_csv}")
    if args.details_csv:
        print(f"Wrote per-target details to {args.details_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
