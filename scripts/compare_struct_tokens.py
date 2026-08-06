"""Compare our freshly-tokenized struct_seq against DPLM-2's shipped struct_seq.

Standalone — no GPU, no PDB parsing. Reads:
  --ours    : the parquet produced by prepare_afdb_struct_tokens.py
              (with our tokens in column ``struct_seq``)
  --theirs  : one or more DPLM-2 pdb_swissprot parquet shards
              (column ``struct_seq`` is the shipped reference)

For every UniProt accession that appears in BOTH, compares the struct-token
sequences token-by-token and reports:
  * exact-match rate
  * length-mismatch rate
  * token-level mismatch rate (same length, ≥1 differing position)
  * stats on per-sequence diffs for the mismatch cases
  * first few sample mismatches

Run:
    python scripts/compare_struct_tokens.py \
        --ours   /home/cherry/dev/phd/dplm/data-bin/cfpgen_dplm2_joined/verify_afdb_struct_tokens.parquet \
        --theirs /home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00000-of-00002.parquet \
        --theirs /home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00001-of-00002.parquet
"""
import argparse
import re
import statistics
from collections import Counter

import pyarrow.parquet as pq


def acc(name):
    """Extract UniProt accession from AF-{ACCESSION}-F1-model_v4 names."""
    if isinstance(name, str) and name.startswith("AF-"):
        m = re.match(r"AF-([A-Z0-9]+)-F\d", name)
        return m.group(1) if m else name
    return name


def load_struct_seq_map(parquet_paths, label):
    """uid -> struct_seq string, from one or more parquet files."""
    out = {}
    for p in parquet_paths:
        t = pq.read_table(p, columns=["pdb_name", "struct_seq"])
        names = t.column("pdb_name").to_pylist()
        seqs = t.column("struct_seq").to_pylist()
        for n, s in zip(names, seqs):
            out[acc(n)] = s
        print(f"  [{label}] loaded {len(names):,} rows from {p}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", required=True)
    ap.add_argument("--theirs", action="append", required=True)
    args = ap.parse_args()

    print("Loading ours ...")
    ours = load_struct_seq_map([args.ours], "ours")
    print(f"  unique uids (ours): {len(ours):,}")
    print("\nLoading theirs (DPLM-2 shipped) ...")
    theirs = load_struct_seq_map(args.theirs, "theirs")
    print(f"  unique uids (theirs): {len(theirs):,}")

    common = set(ours) & set(theirs)
    print(f"\nIntersection: {len(common):,}")

    n_exact = 0
    n_len_mismatch = 0
    n_token_mismatch = 0
    n_no_seq = 0
    first_mismatches = []
    per_seq_diff_counts = []

    for uid in common:
        ours_seq = ours[uid]
        theirs_seq = theirs[uid]
        if not ours_seq or not theirs_seq:
            n_no_seq += 1
            continue
        ours_tokens = [int(x) for x in ours_seq.split(",") if x.strip()]
        theirs_tokens = [int(x) for x in theirs_seq.split(",") if x.strip()]
        if len(ours_tokens) != len(theirs_tokens):
            n_len_mismatch += 1
            if len(first_mismatches) < 10:
                first_mismatches.append(
                    (uid, len(ours_tokens), len(theirs_tokens), "length")
                )
            continue
        n_diff = sum(1 for a, b in zip(ours_tokens, theirs_tokens) if a != b)
        if n_diff == 0:
            n_exact += 1
        else:
            n_token_mismatch += 1
            per_seq_diff_counts.append(n_diff)
            if len(first_mismatches) < 10:
                first_mismatches.append(
                    (uid, len(ours_tokens), len(theirs_tokens), f"{n_diff} differ")
                )

    total = n_exact + n_len_mismatch + n_token_mismatch
    print("\n=== Comparison ===")
    print(f"  total compared            : {total:,}")
    print(f"  EXACT match               : {n_exact:,} ({n_exact/max(total,1):.2%})")
    print(f"  token-level mismatches    : {n_token_mismatch:,} ({n_token_mismatch/max(total,1):.2%})")
    print(f"  length mismatches         : {n_len_mismatch:,} ({n_len_mismatch/max(total,1):.2%})")
    print(f"  skipped (no seq on one side): {n_no_seq:,}")
    if per_seq_diff_counts:
        print(
            f"  for token-mismatch cases: diffs/seq min/median/max = "
            f"{min(per_seq_diff_counts)}/"
            f"{int(statistics.median(per_seq_diff_counts))}/"
            f"{max(per_seq_diff_counts)}"
        )
    if first_mismatches:
        print(f"  first few mismatches (uid, ours_len, theirs_len, note):")
        for m in first_mismatches:
            print(f"    {m}")


if __name__ == "__main__":
    main()
