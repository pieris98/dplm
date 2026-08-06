"""Build a joined dataset using only DPLM-2's shipped struct_seq (correct path).

Takes the intersection of CFP-Gen general train + DPLM-2 pdb_swissprot/train
(~46K proteins) and writes a parquet that:
  - uses DPLM-2's SHIPPED struct_seq verbatim (correct, matches the model's
    pretraining distribution)
  - uses DPLM-2's aa_seq verbatim
  - joins CFP-Gen's ipr_mapped + go_f_mapped labels by UniProt accession

This is the "safe" dataset for the smoke run while we debug the
tokenization-mismatch issue with the AFDB-recovered entries.
"""
import os
import pickle
import re
import sys

import pyarrow as pa
import pyarrow.parquet as pq


def acc(name):
    if isinstance(name, str) and name.startswith("AF-"):
        m = re.match(r"AF-([A-Z0-9]+)-F\d", name)
        return m.group(1) if m else name
    return name


def main():
    cfpgen_pkl = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/train.pkl"
    dplm2_parquets = sys.argv[2:-1] if len(sys.argv) > 3 else [
        "/home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00000-of-00002.parquet",
        "/home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00001-of-00002.parquet",
    ]
    out_path = sys.argv[-1] if len(sys.argv) > 2 else \
        "/home/cherry/dev/phd/dplm/data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet"

    print(f"Loading CFP-Gen labels from {cfpgen_pkl} ...")
    cfp = pickle.load(open(cfpgen_pkl, "rb"))
    cfp_by_id = {x["uniprot_id"]: x for x in cfp}
    print(f"  cfpgen entries: {len(cfp_by_id):,}")

    print(f"\nLoading DPLM-2 parquet (struct_seq + aa_seq) ...")
    rows = []
    for f in dplm2_parquets:
        t = pq.read_table(f, columns=[
            "pdb_name", "struct_seq", "aa_seq", "length", "seq_len",
            "avg_plddt", "plddt_std", "split",
        ])
        n = t.num_rows
        for r in zip(*[t.column(c).to_pylist() for c in t.column_names]):
            row = {c: v for c, v in zip(t.column_names, r)}
            uid = acc(row["pdb_name"])
            if uid in cfp_by_id:
                cfp_row = cfp_by_id[uid]
                row["uniprot_id"] = uid
                row["ipr_mapped"] = cfp_row.get("ipr_mapped", []) or []
                row["go_f_mapped"] = cfp_row.get("go_f_mapped", []) or []
                # Drop unannotated rows
                if row["ipr_mapped"] or row["go_f_mapped"]:
                    rows.append(row)
        print(f"  {f}: {n:,} rows")
    print(f"\n  joined + annotated rows: {len(rows):,}")

    # Build schema (subset of DPLM-2's + the two label cols + uniprot_id).
    schema = pa.schema([
        ("pdb_name", pa.string()),
        ("aa_seq", pa.string()),
        ("struct_seq", pa.string()),
        ("length", pa.int64()),
        ("seq_len", pa.int64()),
        ("avg_plddt", pa.float64()),
        ("plddt_std", pa.float64()),
        ("split", pa.string()),
        ("ipr_mapped", pa.list_(pa.int64())),
        ("go_f_mapped", pa.list_(pa.int64())),
        ("uniprot_id", pa.string()),
    ])
    # Reorder columns to match schema
    table_rows = [{c: r.get(c) for c in schema.names} for r in rows]
    table = pa.Table.from_pylist(table_rows, schema=schema)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    pq.write_table(table, out_path)
    print(f"\nWrote {len(table):,} rows to {out_path}")

    # Quick sanity: per-label survival
    import collections
    go_count = collections.Counter()
    ipr_count = collections.Counter()
    for r in rows:
        for g in r["ipr_mapped"]: ipr_count[g] += 1
        for g in r["go_f_mapped"]: go_count[g] += 1
    print(f"\n  # IPR labels with ≥20 seqs: {sum(1 for c in ipr_count.values() if c >= 20)} / {len(ipr_count)}")
    print(f"  # GO  labels with ≥20 seqs: {sum(1 for c in go_count.values() if c >= 20)} / {len(go_count)}")


if __name__ == "__main__":
    main()
