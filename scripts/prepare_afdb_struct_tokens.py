"""One-shot preprocessing pipeline: AFDB v4 PDB → struct tokens + labels.

For each CFP-Gen training ID that is missing from DPLM-2's pdb_swissprot
parquet, this script:

  1. Loads AF-{uid}-F1-model_v4.pdb.gz from /home/cherry/dev/phd/swissprot_pdb_v4/
  2. Parses backbone N/CA/C/O coords + per-residue pLDDT (from B-factors).
  3. Applies DPLM-2's exact filter:
       - avg pLDDT > 85
       - end-crop low-pLDDT (<50) segments at both ends
       - length in [60, 512] after cropping
  4. Runs the frozen DPLM-2 LFQ structure tokenizer over batched survivors
     to produce the discrete struct-token string.
  5. Joins each survivor's struct tokens with its CFP-Gen GO/IPR labels and
     writes a parquet matching DPLM-2's pdb_swissprot schema (with two new
     label columns: ``ipr_mapped`` and ``go_f_mapped``).

The output parquet can be concatenated with DPLM-2's existing train split
to form the unified ~100K annotated dataset.

Usage (from repo root):
    python scripts/prepare_afdb_struct_tokens.py \
        --afdb-dir /home/cherry/dev/phd/swissprot_pdb_v4 \
        --cfpgen-pkl /home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/train.pkl \
        --dplm2-parquet /home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00000-of-00002.parquet \
        --dplm2-parquet /home/cherry/dev/phd/dplm/data-bin/pdb_swissprot/train/train-00001-of-00002.parquet \
        --output /home/cherry/dev/phd/dplm/data-bin/cfpgen_dplm2_joined/train.parquet \
        --batch-size 16

Run on a single GPU; CPU parsing is the bottleneck so the script
parallelises parsing across workers via concurrent.futures.
"""
import argparse
import gzip
import multiprocessing
import os
import pickle
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# IMPORTANT: all heavy imports (numpy, Bio.PDB, torch, pyarrow) are done
# lazily inside the functions that need them. This script uses the ``spawn``
# start method so worker processes import the module fresh — having heavy
# imports at module scope would be re-executed in every worker, but the
# real reason for spawn is that the default ``fork`` start method deadlocks
# when the parent has already initialised CUDA via torch.


def parse_afdb_pdb(pdb_gz_path):
    """Return (uid, backbone_coords [L,4,3], plddts [L]) or (uid, None, None)."""
    import numpy as np  # lazy
    from Bio.PDB import PDBParser  # lazy
    uid = re.search(r"AF-([A-Z0-9]+)-F\d", os.path.basename(pdb_gz_path))
    uid = uid.group(1) if uid else os.path.basename(pdb_gz_path)
    try:
        parser = PDBParser(QUIET=True)
        with gzip.open(pdb_gz_path, "rt") as f:
            struct = parser.get_structure(uid, f)
        coords = []
        plddts = []
        # GVP encoder expects exactly 3 backbone atoms (N, CA, C) — see
        # src/byprot/models/structok/modules/gvp_encoder.py:72 which slices
        # backb_positions[:, :, :3, :]. Including O garbles the representation.
        BACKBONE = ("N", "CA", "C")
        for res in struct.get_residues():
            if res.id[0] != " ":
                continue  # skip hetero/water
            if "CA" not in res:
                continue
            plddts.append(res["CA"].get_bfactor())
            row = []
            for atom_name in BACKBONE:
                if atom_name in res:
                    row.append(res[atom_name].get_coord().astype(np.float32))
                else:
                    row.append(np.zeros(3, dtype=np.float32))
            coords.append(row)
        if not coords:
            return uid, None, None
        coords = np.asarray(coords, dtype=np.float32)  # [L, 3, 3] = N/CA/C
        # Center coords at the CA centroid — DPLM-2's preprocessing does this
        # at src/byprot/datamodules/pdb_dataset/utils.py:388-391 (bb_center
        # computed from CA, then atom_positions -= bb_center). Without
        # centering, the GVP encoder sees absolute Å positions and produces
        # completely different (and wrong) embeddings.
        ca_coords = coords[:, 1, :]  # [L, 3]
        bb_center = ca_coords.sum(axis=0) / (len(ca_coords) + 1e-5)
        coords = coords - bb_center[None, None, :]
        return uid, coords, np.asarray(plddts, dtype=np.float32)
    except Exception as e:
        print(f"  [parse error] {uid}: {e}", file=sys.stderr)
        return uid, None, None


# ----------------------------------------------------------------------
# 1. PARSE + FILTER  (CPU, parallelisable across processes)
# ----------------------------------------------------------------------

BACKBONE_ATOMS = ("N", "CA", "C", "O")
MIN_LEN = 60
MAX_LEN = 512
PLDDT_AVG_THRESHOLD = 85.0
# 70 (NOT 50 as the paper text at dplm2_paper.md:812 claims) — verified
# against DPLM-2's actual preprocessing code.
PLDDT_END_CROP_THRESHOLD = 70.0


def crop_low_plddt_ends(plddts, threshold=PLDDT_END_CROP_THRESHOLD):
    """DPLM-2 rule: crop trailing low-pLDDT segments at both ends."""
    L = len(plddts)
    start = 0
    while start < L and plddts[start] < threshold:
        start += 1
    end = L
    while end > start and plddts[end - 1] < threshold:
        end -= 1
    return start, end


def parse_and_filter(pdb_gz_path):
    """Parse + apply DPLM-2 filters. Returns dict or None if filtered out."""
    uid, coords, plddts = parse_afdb_pdb(pdb_gz_path)
    if coords is None:
        return uid, None
    if plddts.mean() <= PLDDT_AVG_THRESHOLD:
        return uid, {"reason": f"avg_plddt={plddts.mean():.1f}<=85"}
    s, e = crop_low_plddt_ends(plddts)
    coords_c = coords[s:e]
    plddts_c = plddts[s:e]
    L = len(plddts_c)
    if L < MIN_LEN or L > MAX_LEN:
        return uid, {"reason": f"len={L} out of [{MIN_LEN},{MAX_LEN}]"}
    return uid, {
        "coords": coords_c,
        "plddts": plddts_c,
        "avg_plddt": float(plddts_c.mean()),
        "plddt_std": float(plddts_c.std()),
    }


def parse_and_filter_no_filter(pdb_gz_path):
    """Verify-mode parser: parse only, no DPLM-2 filtering. Used to
    re-tokenize DPLM-2's existing afdb entries so we can diff against
    their shipped struct_seq."""
    uid, coords, plddts = parse_afdb_pdb(pdb_gz_path)
    if coords is None:
        return uid, None
    # Still end-crop low-pLDDT (<50) ends because DPLM-2 does this at the
    # DATA level (the shipped struct_seq was produced AFTER end-cropping),
    # so we must mirror it to compare apples to apples.
    s, e = crop_low_plddt_ends(plddts)
    coords_c = coords[s:e]
    plddts_c = plddts[s:e]
    return uid, {
        "coords": coords_c,
        "plddts": plddts_c,
        "avg_plddt": float(plddts_c.mean()),
        "plddt_std": float(plddts_c.std()),
    }


# ----------------------------------------------------------------------
# 2. LFQ TOKENIZE  (GPU, batched)
# ----------------------------------------------------------------------

def load_struct_tokenizer(device):
    """Load DPLM-2's frozen LFQ structure tokenizer."""
    import torch  # lazy
    from byprot.models.utils import get_struct_tokenizer
    stok = get_struct_tokenizer("airkingbd/struct_tokenizer", eval_mode=True)
    stok = stok.to(device)
    return stok


def iter_tokenize(stok, uids, coords_list, device, batch_size=16):
    """Generator yielding (uid, token_array) per input, batch-by-batch.

    Used so the caller can flush each batch to disk as soon as it's produced,
    losing at most one batch on a crash. Also the natural place to emit
    progress prints.
    """
    import torch  # lazy
    stok.eval()
    n = len(coords_list)
    n_batches = (n + batch_size - 1) // batch_size
    with torch.no_grad():
        for bi, i in enumerate(range(0, n, batch_size)):
            batch_uids = uids[i : i + batch_size]
            batch = coords_list[i : i + batch_size]
            Ls = [c.shape[0] for c in batch]
            Lmax = max(Ls)
            B = len(batch)
            coords_pad = torch.zeros(B, Lmax, 3, 3, dtype=torch.float32, device=device)
            mask_pad = torch.zeros(B, Lmax, dtype=torch.float32, device=device)
            for j, c in enumerate(batch):
                Lj = c.shape[0]
                coords_pad[j, :Lj] = torch.from_numpy(c)
                mask_pad[j, :Lj] = 1.0
            seq_length = torch.tensor(Ls, dtype=torch.long, device=device)
            struct_tokens = stok.tokenize(
                atom_positions=coords_pad,
                res_mask=mask_pad,
                seq_length=seq_length,
            )  # [B, Lmax]
            for j, Lj in enumerate(Ls):
                yield batch_uids[j], struct_tokens[j, :Lj].cpu().numpy()
            if bi % 50 == 0 or bi == n_batches - 1:
                print(
                    f"  tokenize batch {bi+1}/{n_batches} "
                    f"({i+B}/{n} structs, {100*(i+B)/n:.1f}%)",
                    flush=True,
                )


# ----------------------------------------------------------------------
# 3. MAIN
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--afdb-dir", required=True)
    ap.add_argument("--cfpgen-pkl", required=True)
    ap.add_argument("--dplm2-parquet", action="append", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--workers", type=int, default=8,
                    help="CPU workers for PDB parsing")
    ap.add_argument("--max-ids", type=int, default=None,
                    help="limit # IDs processed (for smoke tests)")
    ap.add_argument("--device", default=None,
                    help="cuda or cpu; default auto-detects after imports")
    ap.add_argument("--mode", default="missing",
                    choices=["missing", "verify"],
                    help="'missing' (default) processes CFP-Gen IDs absent "
                         "from DPLM-2's parquet. 'verify' re-tokenizes the "
                         "afdb_swissprot IDs that are IN DPLM-2's parquet, "
                         "applies no filter, and includes the shipped "
                         "struct_seq for later comparison.")
    args = ap.parse_args()

    # Lazy imports: keep these out of the forked workers.
    import torch
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Resolve --device now that torch is available.
    if args.device is None:
        args.device = "cuda" if torch.cuda.is_available() else "cpu"

    # Use the spawn start method for ProcessPoolExecutor: with fork, workers
    # inherit the parent's CUDA/thread state and deadlock silently. spawn
    # re-imports this module per worker, which is why all heavy imports
    # above are lazy.
    ctx = multiprocessing.get_context("spawn")

    # ---- 3a. Determine the ID set + DPLM-2 shipped tokens (if verify). ----
    print("Loading CFP-Gen train pkl ...")
    cfp_entries = pickle.load(open(args.cfpgen_pkl, "rb"))
    cfp_by_id = {x["uniprot_id"]: x for x in cfp_entries}
    print(f"  cfpgen entries: {len(cfp_by_id):,}")

    def acc(name):
        if isinstance(name, str) and name.startswith("AF-"):
            m = re.match(r"AF-([A-Z0-9]+)-F\d", name)
            return m.group(1) if m else name
        return name

    # Load DPLM-2 afdb_swissprot rows (we need both pdb_name + struct_seq
    # for verify mode, pdb_name only for missing mode).
    dplm2_shipped = {}  # uid -> shipped struct_seq string (verify mode only)
    dplm2_ids = set()
    load_cols = ["pdb_name"] + (["struct_seq", "split", "seq_len"] if args.mode == "verify" else [])
    for f in args.dplm2_parquet:
        t = pq.read_table(f, columns=load_cols)
        rows = [{c: v for c, v in zip(load_cols, row)} for row in zip(*[t.column(c).to_pylist() for c in load_cols])]
        for r in rows:
            uid = acc(r["pdb_name"])
            dplm2_ids.add(uid)
            if args.mode == "verify" and r.get("split") == "afdb_swissprot":
                dplm2_shipped[uid] = r.get("struct_seq")
    print(f"  dplm-2 accessions: {len(dplm2_ids):,}")
    if args.mode == "verify":
        print(f"  dplm-2 afdb_swissprot entries with shipped struct_seq: {len(dplm2_shipped):,}")

    if args.mode == "missing":
        target_ids = [uid for uid in cfp_by_id if uid not in dplm2_ids]
    else:  # verify
        target_ids = list(dplm2_shipped.keys())
    if args.max_ids:
        import random; random.seed(0)
        target_ids = random.sample(target_ids, min(args.max_ids, len(target_ids)))
    print(f"  mode={args.mode!r}  target IDs to process: {len(target_ids):,}")

    # ---- 3b. Parse + filter in parallel. ----
    # In verify mode we DON'T filter (these entries already passed DPLM-2's
    # filter at preprocessing time; we want to tokenize the full structure
    # to match DPLM-2's shipped tokens).
    worker_fn = parse_and_filter_no_filter if args.mode == "verify" else parse_and_filter
    print(f"\nParsing with {args.workers} workers (mode={args.mode}) ...")
    survivors = {}  # uid -> entry dict
    rejected = {}
    todo = [os.path.join(args.afdb_dir, f"AF-{uid}-F1-model_v4.pdb.gz") for uid in target_ids]
    uid_by_path = {p: uid for uid, p in zip(target_ids, todo)}
    todo_existing = [p for p in todo if os.path.exists(p)]
    print(f"  files present on disk: {len(todo_existing):,} / {len(todo):,}")

    with ProcessPoolExecutor(max_workers=args.workers, mp_context=ctx) as ex:
        futures = {ex.submit(worker_fn, p): p for p in todo_existing}
        done = 0
        for fut in as_completed(futures):
            p = futures[fut]
            uid = uid_by_path[p]
            try:
                _, result = fut.result()
            except Exception as e:
                rejected[uid] = {"reason": f"exception: {e}"}
                continue
            done += 1
            if done % 500 == 0:
                print(f"    parsed {done}/{len(todo_existing)} ...")
            if result is None or "reason" in result:
                rejected[uid] = result or {"reason": "parse returned None"}
            else:
                survivors[uid] = result
    print(f"  survivors: {len(survivors):,}")
    print(f"  rejected : {len(rejected):,}")
    # Brief reject-reason histogram
    import collections
    reasons = collections.Counter()
    for r in rejected.values():
        reasons[r.get("reason", "unknown").split("=")[0].split()[0]] += 1
    print(f"  reject reasons: {dict(reasons)}")

    # ---- 3c-d. LFQ tokenize, writing each batch to a shard immediately. ----
    # Streaming: each batch is converted to a small parquet "shard" and
    # flushed to disk. A crash loses at most the current batch. At the end
    # all shards are concatenated into the final output parquet.
    schema = pa.schema([
        ("pdb_name", pa.string()),
        ("aa_seq", pa.string()),
        ("struct_seq", pa.string()),
        ("avg_plddt", pa.float64()),
        ("plddt_std", pa.float64()),
        ("plddt", pa.list_(pa.float64())),
        ("seq_len", pa.int64()),
        ("modeled_seq_len", pa.int64()),
        ("length", pa.int64()),
        ("split", pa.string()),
        ("num_chains", pa.int64()),
        ("oligomeric_detail", pa.string()),
        ("quaternary_category", pa.string()),
        ("coil_percent", pa.float64()),
        ("helix_percent", pa.float64()),
        ("strand_percent", pa.float64()),
        ("radius_of_gyration", pa.float64()),
        ("resolution", pa.float64()),
        ("structure_method", pa.string()),
        ("oligomeric_count", pa.string()),
        ("lddt_ca", pa.float64()),
        ("rmsd", pa.float64()),
        ("cluster", pa.string()),
        ("processed_path", pa.string()),
        ("gvp_feat_path", pa.string()),
        ("domain_num", pa.float64()),
        ("domain_split", pa.string()),
        ("ipr_mapped", pa.list_(pa.int64())),
        ("go_f_mapped", pa.list_(pa.int64())),
        ("uniprot_id", pa.string()),
    ])

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    shard_dir = Path(args.output + ".shards")
    shard_dir.mkdir(parents=True, exist_ok=True)
    # Clear stale shards so we don't accidentally concat old + new.
    for old in shard_dir.glob("shard-*.parquet"):
        old.unlink()

    print(f"\nTokenizing on {args.device} (batch_size={args.batch_size}), "
          f"streaming shards to {shard_dir} ...")
    stok = load_struct_tokenizer(args.device)
    surv_uids = list(survivors.keys())
    surv_coords = [survivors[u]["coords"] for u in surv_uids]

    def build_row(uid, tokens):
        entry = survivors[uid]
        cfp = cfp_by_id.get(uid, {})
        plddt = entry["plddts"].tolist()
        L = len(plddt)
        return {
            "pdb_name": f"AF-{uid}-F1-model_v4",
            "aa_seq": (cfp.get("sequence") or "")[:L],
            "struct_seq": ",".join(str(int(t)) for t in tokens),
            "avg_plddt": entry["avg_plddt"],
            "plddt_std": entry["plddt_std"],
            "plddt": plddt,
            "seq_len": L, "modeled_seq_len": L, "length": L,
            "split": "afdb_swissprot",
            "num_chains": 1,
            "oligomeric_detail": "monomeric",
            "quaternary_category": None,
            "coil_percent": None, "helix_percent": None, "strand_percent": None,
            "radius_of_gyration": None, "resolution": None,
            "structure_method": None, "oligomeric_count": None,
            "lddt_ca": None, "rmsd": None,
            "cluster": f"AF-{uid}-F1-model_v4",
            "processed_path": None, "gvp_feat_path": None,
            "domain_num": None, "domain_split": None,
            "ipr_mapped": cfp.get("ipr_mapped", []) or [],
            "go_f_mapped": cfp.get("go_f_mapped", []) or [],
            "uniprot_id": uid,
        }

    # Walk the generator in batches-of-batches: each tokenize batch yields
    # batch_size rows; accumulate them and flush as one shard per tokenize
    # batch. Using a counter for shard names keeps them sorted.
    n_total_written = 0
    shard_idx = 0
    buf_rows = []
    for uid, tokens in iter_tokenize(
        stok, surv_uids, surv_coords, args.device, args.batch_size
    ):
        buf_rows.append(build_row(uid, tokens))
        if len(buf_rows) >= args.batch_size:
            shard_path = shard_dir / f"shard-{shard_idx:06d}.parquet"
            t = pa.Table.from_pylist(buf_rows, schema=schema)
            pq.write_table(t, shard_path)
            n_total_written += len(buf_rows)
            shard_idx += 1
            buf_rows = []
    # Flush remainder.
    if buf_rows:
        shard_path = shard_dir / f"shard-{shard_idx:06d}.parquet"
        t = pa.Table.from_pylist(buf_rows, schema=schema)
        pq.write_table(t, shard_path)
        n_total_written += len(buf_rows)

    print(f"\n  wrote {shard_idx + (1 if buf_rows else 0):,} shards, "
          f"{n_total_written:,} rows total")

    # Concatenate shards into the final output parquet.
    print(f"Concatenating shards into {args.output} ...")
    shard_paths = sorted(shard_dir.glob("shard-*.parquet"))
    tables = [pq.read_table(p) for p in shard_paths]
    if tables:
        final = pa.concat_tables(tables)
        pq.write_table(final, args.output)
        print(f"  wrote {len(final):,} rows to {args.output}")
    else:
        print(f"  no shards found — nothing to write")

    print("Done.")


if __name__ == "__main__":
    main()
