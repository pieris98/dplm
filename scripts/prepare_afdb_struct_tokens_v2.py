"""Verify-tokenize AFDB structures using DPLM-2's OWN preprocessing pipeline.

Uses ``load_from_pdb`` from ``byprot.utils.protein.tokenize_pdb``, which
calls ``du.process_pdb_file`` → ``struct_tokenizer.process_chain`` → the
full OpenFold data-transform chain (atom37_to_frames, atom14, torsion
angles, pseudo-beta, backbone frames). This is the ONLY way to produce
struct tokens identical to DPLM-2's shipped data — our manual Bio.PDB
parser missed all these transforms.

This script is the correct replacement for ``prepare_afdb_struct_tokens.py``
for the verify-mode path. It processes .pdb.gz files from the AFDB v4
swissprot tarball.

Usage:
    python scripts/prepare_afdb_struct_tokens_v2.py \\
        --mode verify --max-ids 100 \\
        --afdb-dir /home/cherry/dev/phd/swissprot_pdb_v4 \\
        --cfpgen-pkl /home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/train.pkl \\
        --dplm2-parquet /home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00000-of-00002.parquet \\
        --dplm2-parquet /home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00001-of-00002.parquet \\
        --output /tmp/verify_v2.parquet
"""
import argparse
import gzip
import os
import pickle
import re
import shutil
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq


def acc(name):
    if isinstance(name, str) and name.startswith("AF-"):
        m = re.match(r"AF-([A-Z0-9]+)-F\d", name)
        return m.group(1) if m else name
    return name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--afdb-dir", required=True)
    ap.add_argument("--cfpgen-pkl", required=True)
    ap.add_argument("--dplm2-parquet", action="append", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--max-ids", type=int, default=None)
    ap.add_argument("--mode", default="verify", choices=["missing", "verify"])
    args = ap.parse_args()

    import torch
    from byprot.models.utils import get_struct_tokenizer
    from byprot.utils.protein.tokenize_pdb import load_from_pdb
    from byprot.utils import recursive_to

    torch.set_float32_matmul_precision("high")
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load CFP-Gen labels.
    cfp_entries = pickle.load(open(args.cfpgen_pkl, "rb"))
    cfp_by_id = {x["uniprot_id"]: x for x in cfp_entries}

    # Load DPLM-2 IDs + shipped struct_seq.
    dplm2_shipped = {}
    dplm2_ids = set()
    for f in args.dplm2_parquet:
        t = pq.read_table(f, columns=["pdb_name", "struct_seq", "split"])
        for n, s, sp in zip(t.column("pdb_name").to_pylist(),
                            t.column("struct_seq").to_pylist(),
                            t.column("split").to_pylist()):
            uid = acc(n)
            dplm2_ids.add(uid)
            if args.mode == "verify" and sp == "afdb_swissprot":
                dplm2_shipped[uid] = s
    print(f"dplm-2 accessions: {len(dplm2_ids):,}")
    if args.mode == "verify":
        print(f"dplm-2 afdb entries: {len(dplm2_shipped):,}")

    if args.mode == "missing":
        target_ids = [uid for uid in cfp_by_id if uid not in dplm2_ids]
    else:
        target_ids = list(dplm2_shipped.keys())

    if args.max_ids:
        import random; random.seed(0)
        target_ids = random.sample(target_ids, min(args.max_ids, len(target_ids)))
    print(f"mode={args.mode!r}  target IDs: {len(target_ids):,}")

    # Load struct tokenizer.
    print(f"Loading struct tokenizer on {device} ...")
    stok = get_struct_tokenizer()
    stok = stok.to(device).eval()

    # Process each structure: decompress .pdb.gz → temp .pdb → load_from_pdb → tokenize.
    schema = pa.schema([
        ("pdb_name", pa.string()),
        ("aa_seq", pa.string()),
        ("struct_seq", pa.string()),
        ("avg_plddt", pa.float64()),
        ("plddt_std", pa.float64()),
        ("seq_len", pa.int64()),
        ("length", pa.int64()),
        ("split", pa.string()),
        ("ipr_mapped", pa.list_(pa.int64())),
        ("go_f_mapped", pa.list_(pa.int64())),
        ("uniprot_id", pa.string()),
    ])

    rows = []
    n_ok = 0
    n_fail = 0
    n_compared = 0
    n_exact = 0

    for i, uid in enumerate(target_ids):
        pdb_gz = os.path.join(args.afdb_dir, f"AF-{uid}-F1-model_v4.pdb.gz")
        if not os.path.exists(pdb_gz):
            n_fail += 1
            continue
        try:
            # Decompress to temp file (load_from_pdb expects .pdb).
            with tempfile.NamedTemporaryFile(suffix=".pdb", delete=False, mode="wb") as tmp:
                with gzip.open(pdb_gz, "rb") as f:
                    shutil.copyfileobj(f, tmp)
                tmp_path = tmp.name

            # Use DPLM-2's own preprocessing pipeline.
            feats = load_from_pdb(tmp_path, process_chain=stok.process_chain)
            os.unlink(tmp_path)

            # Tokenize.
            batch = {k: v.unsqueeze(0) for k, v in feats.items() if hasattr(v, 'unsqueeze')}
            batch["pdb_name"] = [feats.get("pdb_name", uid)]
            batch = recursive_to(batch, device)

            with torch.no_grad():
                struct_ids = stok.tokenize(
                    batch["all_atom_positions"],
                    batch["res_mask"],
                    batch["seq_length"],
                )
            struct_tokens = struct_ids[0].cpu().tolist()
            struct_seq = ",".join(f"{int(t):04d}" for t in struct_tokens)

            # Get pLDDT from B-factors (if available in feats).
            plddts = feats.get("all_atom_positions_plddt", None)
            if plddts is not None:
                avg_plddt = float(plddts.mean())
                plddt_std = float(plddts.std())
            else:
                avg_plddt = None
                plddt_std = None

            L = len(struct_tokens)
            cfp = cfp_by_id.get(uid, {})
            row = {
                "pdb_name": f"AF-{uid}-F1-model_v4",
                "aa_seq": (cfp.get("sequence") or "")[:L],
                "struct_seq": struct_seq,
                "avg_plddt": avg_plddt,
                "plddt_std": plddt_std,
                "seq_len": L,
                "length": L,
                "split": "afdb_swissprot",
                "ipr_mapped": cfp.get("ipr_mapped", []) or [],
                "go_f_mapped": cfp.get("go_f_mapped", []) or [],
                "uniprot_id": uid,
            }
            rows.append(row)
            n_ok += 1

            # Quick verify-mode comparison.
            if args.mode == "verify":
                shipped = dplm2_shipped.get(uid, "")
                shipped_tokens = [int(x) for x in shipped.split(",") if x.strip()]
                n_compared += 1
                if struct_tokens == shipped_tokens:
                    n_exact += 1

            if (i + 1) % 10 == 0 or (i + 1) == len(target_ids):
                match_rate = f"{n_exact}/{n_compared}" if n_compared else "n/a"
                print(f"  [{i+1}/{len(target_ids)}] ok={n_ok} fail={n_fail} "
                      f"exact={match_rate}", flush=True)

        except Exception as e:
            print(f"  [ERROR] {uid}: {e}", flush=True)
            n_fail += 1

    # Write output.
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, args.output)
    print(f"\nWrote {len(rows)} rows to {args.output}")
    if args.mode == "verify" and n_compared > 0:
        print(f"VERIFY: exact match = {n_exact}/{n_compared} ({n_exact/n_compared:.1%})")


if __name__ == "__main__":
    main()
