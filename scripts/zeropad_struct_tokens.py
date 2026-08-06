"""One-shot fixup: zero-pad struct_seq codes in our parquet to 4 digits.

The DPLM-2 tokenizer vocab uses zero-padded 4-digit codes ('0043', '0271',
'3160'). Our preprocessing script wrote them as raw ints ('43', '271',
'3160'), which the tokenizer can't parse for codes < 1000.

This script reads our parquet, rewrites every struct_seq with zero-padding,
and writes it back. ~30 sec for 36K rows.
"""
import sys
import pyarrow as pa
import pyarrow.parquet as pq

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/cherry/dev/phd/dplm/data-bin/cfpgen_dplm2_joined/missing_afdb_struct_tokens.parquet"
    print(f"Reading {path} ...")
    table = pq.read_table(path)
    seqs = table.column("struct_seq").to_pylist()
    print(f"  rows: {len(seqs):,}")
    fixed = []
    n_changed = 0
    for s in seqs:
        codes = s.split(",")
        new_codes = [f"{int(c):04d}" for c in codes]
        if any(len(c) != 4 for c in codes):
            n_changed += 1
        fixed.append(",".join(new_codes))

    # Replace the column in the table and write back.
    idx = table.schema.get_field_index("struct_seq")
    table = table.set_column(idx, "struct_seq", pa.array(fixed, pa.string()))
    pq.write_table(table, path)
    print(f"  rewrote {n_changed:,} rows with non-4-digit codes")
    print(f"  wrote {path}")
    # Verify
    t2 = pq.read_table(path, columns=["struct_seq"])
    sample = t2.column("struct_seq")[0].as_py()
    print(f"  sample first 30 chars: {sample[:80]}")
