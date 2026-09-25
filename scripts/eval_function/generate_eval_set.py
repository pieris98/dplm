"""Generate the function-recovery evaluation set (all arms).

Protocol (CFP-Gen-style, adapted):
  * Select N held-out labels from CFP-Gen's ``test.pkl`` that have at least
    K real sequences (needed for the positive control and MRR reference).
  * For each selected label, take K real proteins; their own GO+IPR label
    sets become the conditioning prompts.
  * Arms:
      ours_cond   — our trained ckpt, conditioned on each protein's labels
      ours_null   — our trained ckpt, null condition
      vanilla     — pretrained airkingbd/dplm2_650m, unconditional
      real        — the real sequences (positive control / reference)

Outputs (under --out):
  <arm>/aatype.fasta           generated or real sequences
  manifest.json                prompts + metadata per arm
  label_map.json               int label id ↔ accession maps (for scorers)

Example:
  python scripts/eval_function/generate_eval_set.py \
      --ckpt logs/cond_dplm2_650m_cfpgen/checkpoints/step_99999.0-loss_1.95.ckpt \
      --n-labels 32 --seqs-per-label 8 --len 256 --out eval_runs/fn_eval_v1
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import random
import sys
from pathlib import Path

import torch
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from byprot.models.dplm2 import (  # noqa: E402
    MultimodalDiffusionProteinLanguageModel as DPLM2,
)
from byprot.models.dplm2 import ConditionalDPLM2  # noqa: E402,F401  (import side effects)
from byprot.eval.function import sanitize_sequence  # noqa: E402


def load_pickles(pkl_dir):
    with open(os.path.join(pkl_dir, "test.pkl"), "rb") as f:
        test = pickle.load(f)
    return test


def load_mappings(go_pkl, ipr_pkl):
    with open(go_pkl, "rb") as f:
        go_map = pickle.load(f)  # 'GO:XXXX' -> int
    with open(ipr_pkl, "rb") as f:
        ipr_map = pickle.load(f)  # 'IPRxxxxx' -> int
    return go_map, ipr_map, {v: k for k, v in go_map.items()}, {v: k for k, v in ipr_map.items()}


def select_labels(test, seqs_per_label, n_labels, seed, go_inv, ipr_inv):
    """Pick labels with >= seqs_per_label real test sequences.

    Preference order: GO-F labels (primary axis of CFP-Gen GO eval), then
    IPR labels if GO runs short.
    """
    rng = random.Random(seed)
    go_count, ipr_count = {}, {}
    for e in test:
        for g in e.get("go_f_mapped", []) or []:
            go_count[g] = go_count.get(g, 0) + 1
        for i in e.get("ipr_mapped", []) or []:
            ipr_count[i] = ipr_count.get(i, 0) + 1

    def eligible(counts, inv):
        return sorted(
            [lab for lab, c in counts.items() if c >= seqs_per_label and lab in inv],
        )

    go_ok, ipr_ok = eligible(go_count, go_inv), eligible(ipr_count, ipr_inv)
    rng.shuffle(go_ok)
    rng.shuffle(ipr_ok)

    selected = []  # (label_type, label_id)
    for lab in go_ok:
        if len(selected) >= n_labels:
            break
        selected.append(("go", lab))
    for lab in ipr_ok:
        if len(selected) >= n_labels:
            break
        selected.append(("ipr", lab))

    # For each label, pick the first seqs_per_label test proteins carrying it
    by_label = {}
    for e in test:
        for ltype, lab in selected:
            key = "go_f_mapped" if ltype == "go" else "ipr_mapped"
            if lab in (e.get(key, []) or []):
                bucket = by_label.setdefault((ltype, lab), [])
                if len(bucket) < seqs_per_label:
                    bucket.append(e)
    return selected, by_label


def build_conditions(entries, device):
    """{ipr/go: [B, max_labels] LongTensor (-1 pad)} from test.pkl entries."""
    ipr_lists = [e.get("ipr_mapped", []) or [] for e in entries]
    go_lists = [e.get("go_f_mapped", []) or [] for e in entries]

    def pad(lists):
        m = max(1, max(len(x) for x in lists))
        t = torch.full((len(lists), m), -1, dtype=torch.long)
        for i, x in enumerate(lists):
            if x:
                t[i, : len(x)] = torch.tensor(x, dtype=torch.long)
        return t.to(device)

    return {"annotations": {"ipr": pad(ipr_lists), "go": pad(go_lists)}}


def encode_init(aa_len, tokenizer, n):
    seq = tokenizer.aa_mask_token * aa_len
    seq = tokenizer.aa_cls_token + seq + tokenizer.aa_eos_token
    return tokenizer.batch_encode_plus(
        [seq] * n, add_special_tokens=False, padding=True, return_tensors="pt"
    )["input_ids"]


def decode_seqs(tokens, tokenizer):
    return [sanitize_sequence("".join(s.split()))
            for s in tokenizer.batch_decode(tokens, skip_special_tokens=True)]


@torch.no_grad()
def generate_arm(model, arm, entries, aa_len, tokenizer, device, max_iter,
                 temperature, unmasking, sampling, cfg_scale):
    """Generate one batch: all entries of this arm/label in a single batch."""
    x = encode_init(aa_len, tokenizer, len(entries)).to(device)
    conditions = build_conditions(entries, device) if arm == "ours_cond" else None
    with torch.inference_mode(), torch.cuda.amp.autocast(dtype=torch.bfloat16):
        if arm == "vanilla":
            out = model.generate(
                input_tokens=x, max_iter=max_iter, temperature=temperature,
                unmasking_strategy=unmasking, sampling_strategy=sampling)
        else:
            out = model.generate(
                input_tokens=x, max_iter=max_iter, temperature=temperature,
                unmasking_strategy=unmasking, sampling_strategy=sampling,
                conditions=conditions, cfg_scale=cfg_scale)
    return decode_seqs(out["output_tokens"], tokenizer)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", required=True, help="our trained ConditionalDPLM2 ckpt")
    ap.add_argument("--test-pkl",
                    default="/home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/test.pkl")
    ap.add_argument("--go-mapping", default="/home/cherry/dev/phd/cfpgen/go_mapping.pkl")
    ap.add_argument("--ipr-mapping", default="/home/cherry/dev/phd/cfpgen/ipr_mapping.pkl")
    ap.add_argument("--n-labels", type=int, default=32)
    ap.add_argument("--seqs-per-label", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=64,
                    help="sequences per denoising batch — batch across labels; "
                         "raise until GPU memory is nearly full")
    ap.add_argument("--len", type=int, default=256, dest="seq_len")
    ap.add_argument("--cfg-scale", type=float, default=1.0)
    ap.add_argument("--max-iter", type=int, default=None)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--unmasking", default="stochastic1.0")
    ap.add_argument("--sampling", default="annealing@2.0:0.1")
    ap.add_argument("--arms", default="ours_cond,ours_null,vanilla,real")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="eval_runs/fn_eval_v1")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    max_iter = args.max_iter or args.seq_len

    test = load_pickles(os.path.dirname(args.test_pkl))
    go_map, ipr_map, go_inv, ipr_inv = load_mappings(args.go_mapping, args.ipr_mapping)
    selected, by_label = select_labels(
        test, args.seqs_per_label, args.n_labels, args.seed, go_inv, ipr_inv)
    print(f"selected {len(selected)} labels: "
          f"{[(t, l) for t, l in selected[:8]]} ...")

    # Flatten: one generation batch per selected label.
    batches = []
    for (ltype, lab) in selected:
        entries = by_label[(ltype, lab)]
        batches.append((ltype, lab, entries))
    flat_entries = [e for _, _, es in batches for e in es]
    print(f"total prompt proteins: {len(flat_entries)}")

    Path(args.out).mkdir(parents=True, exist_ok=True)
    # Merge with any existing manifest: re-running the driver for a subset of
    # arms must not wipe the records of arms generated earlier.
    mpath = os.path.join(args.out, "manifest.json")
    if os.path.exists(mpath):
        with open(mpath) as f:
            manifest = json.load(f)
        manifest.setdefault("arms", {})
        manifest["args"] = vars(args)
    else:
        manifest = {"args": vars(args), "arms": {}}

    # ---- real arm (reference) ----
    if "real" in arms:
        Path(args.out).joinpath("real").mkdir(parents=True, exist_ok=True)
        recs = []
        for _, _, entries in batches:
            for e in entries:
                recs.append({
                    "seq_id": f"real_{e['uniprot_id']}",
                    "uniprot_id": e["uniprot_id"],
                    "prompt_ipr": e.get("ipr_mapped", []) or [],
                    "prompt_go": e.get("go_f_mapped", []) or [],
                })
        with open(os.path.join(args.out, "real", "aatype.fasta"), "w") as f:
            for r in recs:
                e = next(x for x in test if x["uniprot_id"] == r["uniprot_id"])
                f.write(f">{r['seq_id']}\n{e['sequence']}\n")
        manifest["arms"]["real"] = recs

    # Flatten all prompt proteins across labels, then chunk into
    # --batch-size batches. The denoising loop is sequential (max_iter
    # steps), so throughput scales with batch size; conditions are per-row
    # (padded label tensors), so batching across labels is safe.
    flat = [(ltype, lab, e) for (ltype, lab), entries in batches for e in entries]
    chunks = [flat[i : i + args.batch_size] for i in range(0, len(flat), args.batch_size)]
    print(f"{len(flat)} prompt proteins → {len(chunks)} batch(es) of ≤{args.batch_size}")

    # ---- ours_cond / ours_null ----
    if any(a.startswith("ours") for a in arms):
        print("loading our trained ckpt ...")
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from generate_conditional_dplm2 import load_conditional_model
        ours = load_conditional_model(args.ckpt, device)
        tok_ours = ours.tokenizer

        for arm in [a for a in arms if a.startswith("ours")]:
            recs, seqs_all = [], []
            for chunk in tqdm(chunks, desc=f"gen:{arm}"):
                entries = [e for _, _, e in chunk]
                seqs = generate_arm(
                    ours, arm, entries, args.seq_len, tok_ours, device,
                    max_iter, args.temperature, args.unmasking, args.sampling,
                    args.cfg_scale)
                seqs_all.extend(seqs)
                for (_lt, lab, e), s in zip(chunk, seqs):
                    recs.append({
                        "seq_id": f"{arm}_{ltype}{lab}_{e['uniprot_id']}",
                        "uniprot_id": e["uniprot_id"],
                        "prompt_ipr": e.get("ipr_mapped", []) or [],
                        "prompt_go": e.get("go_f_mapped", []) or [],
                        "primary_label": [ltype, lab],
                    })
            d = os.path.join(args.out, arm)
            Path(d).mkdir(parents=True, exist_ok=True)
            with open(os.path.join(d, "aatype.fasta"), "w") as f:
                for r, s in zip(recs, seqs_all):
                    f.write(f">{r['seq_id']}\n{s}\n")
            manifest["arms"][arm] = recs

    # ---- vanilla arm ----
    if "vanilla" in arms:
        print("loading vanilla airkingbd/dplm2_650m ...")
        vanilla = DPLM2.from_pretrained("airkingbd/dplm2_650m").to(device).eval()
        tok_v = vanilla.tokenizer
        recs, seqs_all = [], []
        for chunk in tqdm(chunks, desc="gen:vanilla"):
            entries = [e for _, _, e in chunk]
            seqs = generate_arm(
                vanilla, "vanilla", entries, args.seq_len, tok_v, device,
                max_iter, args.temperature, args.unmasking, args.sampling,
                cfg_scale=1.0)
            seqs_all.extend(seqs)
            for (_lt, lab, e), s in zip(chunk, seqs):
                recs.append({
                    "seq_id": f"vanilla_{ltype}{lab}_{e['uniprot_id']}",
                    "uniprot_id": e["uniprot_id"],
                    "prompt_ipr": e.get("ipr_mapped", []) or [],
                    "prompt_go": e.get("go_f_mapped", []) or [],
                })
        d = os.path.join(args.out, "vanilla")
        Path(d).mkdir(parents=True, exist_ok=True)
        with open(os.path.join(d, "aatype.fasta"), "w") as f:
            for r, s in zip(recs, seqs_all):
                f.write(f">{r['seq_id']}\n{s}\n")
        manifest["arms"]["vanilla"] = recs

    manifest["label_map"] = {"go": go_map, "ipr": ipr_map,
                             "selected": [[t, l] for t, l in selected]}
    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    print(f"\nwrote {args.out}/manifest.json + per-arm FASTAs for arms={arms}")


if __name__ == "__main__":
    main()
