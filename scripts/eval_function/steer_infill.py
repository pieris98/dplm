"""Anchored steering experiment: masked-infill generation over real anchors.

Implements the generation stage of plans/anchored_steering_experiment.md:
real anchor sequences (family-membership strata from build_strata.py) are
partially masked (fraction ``p`` of aa positions) and denoised with
  - ``vanilla``    pretrained DPLM-2, no conditions
  - ``cond``       our conditional ckpt, prompt = the family itself
  - ``cond_null``  our conditional ckpt, null condition
The p=1.0 bridge cell (from-scratch equivalence check) is added automatically
for the ``low`` strata. Cells are resumable: existing FASTAs are skipped.

  python scripts/eval_function/steer_infill.py \
      --ckpt logs/cond_dplm2_650m_cfpgen/checkpoints/step_99999.0-loss_1.95.ckpt \
      --anchors eval_runs/steer_v1/anchors --out eval_runs/steer_v1/gen
"""
from __future__ import annotations

import argparse
import json
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
from byprot.models.dplm2 import ConditionalDPLM2  # noqa: E402,F401 (registry)
from byprot.eval.function import sanitize_sequence  # noqa: E402

DEFAULT_IPR_MAP = "/home/cherry/dev/phd/cfpgen/ipr_mapping.pkl"


def read_fasta(path: Path) -> list[tuple[str, str]]:
    out, cur_id, cur = [], None, []
    with open(path) as f:
        for line in f:
            if line.startswith(">"):
                if cur_id is not None:
                    out.append((cur_id, "".join(cur)))
                cur_id, cur = line[1:].split()[0], []
            else:
                cur.append(line.strip())
    if cur_id is not None:
        out.append((cur_id, "".join(cur)))
    return out


def build_family_conditions(fam_int: int, batch: int, device) -> dict:
    """Prompt = the family itself (NOT the anchor's own labels — that would
    leak the answer). Same {-1}-padded layout as the from-scratch driver."""
    return {
        "annotations": {
            "ipr": torch.full((batch, 1), fam_int, dtype=torch.long, device=device),
            "go": torch.full((batch, 1), -1, dtype=torch.long, device=device),
        }
    }


@torch.no_grad()
def run_cell(model, arm, seqs, pm, tok, device, max_iter, temperature,
             unmasking, sampling, conds, cfg_scale):
    """One denoising call over an (already padded) batch of anchors."""
    with torch.inference_mode(), torch.cuda.amp.autocast(dtype=torch.bfloat16):
        if arm == "vanilla":
            out = model.generate(
                input_tokens=pm["tokens"], max_iter=max_iter,
                temperature=temperature, unmasking_strategy=unmasking,
                sampling_strategy=sampling, partial_masks=pm["partial"],
            )
        else:
            out = model.generate(
                input_tokens=pm["tokens"], max_iter=max_iter,
                temperature=temperature, unmasking_strategy=unmasking,
                sampling_strategy=sampling, partial_masks=pm["partial"],
                conditions=conds, cfg_scale=cfg_scale,
            )
    return [
        sanitize_sequence("".join(s.split()))
        for s in tok.batch_decode(out["output_tokens"], skip_special_tokens=True)
    ]


def build_batch(anchor_seqs, p, tok, rng, device):
    """Encode anchors, choose p-fraction of aa positions to edit per sequence.

    Returns tokens [B, L] and partial_masks [B, L] with True = fixed
    (cls/eos/pad and the un-edited aa positions), False = editable. Padding
    uses the tokenizer's special ``<pad>`` token: it is excluded from editing
    by the model's non-special mask and dropped by ``skip_special_tokens``.
    """
    enc = tok.batch_encode_plus(
        [tok.aa_cls_token + s + tok.aa_eos_token for s in anchor_seqs],
        add_special_tokens=False, padding=True, return_tensors="pt",
    )["input_ids"].to(device)

    partial = torch.ones_like(enc, dtype=torch.bool)
    for i, s in enumerate(anchor_seqs):
        n_edit = max(1, int(round(p * len(s))))
        for j in rng.sample(range(1, len(s) + 1), n_edit):  # skip cls at 0
            partial[i, j] = False
    return {"tokens": enc, "partial": partial}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--anchors", default="eval_runs/steer_v1/anchors")
    ap.add_argument("--out", default="eval_runs/steer_v1/gen")
    ap.add_argument("--families", default="IPR036291,IPR012340,IPR002052")
    ap.add_argument("--strata", default="low,high,none")
    ap.add_argument("--arms", default="vanilla,cond,cond_null")
    ap.add_argument("--ps", default="0.3,0.8",
                    help="edit fractions; p=1.0 is auto-added for the low strata")
    ap.add_argument("--seeds", default="0")
    ap.add_argument("--cfg-scale", type=float, default=1.0)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--token-budget", type=int, default=None,
                    help="max tokens (batch_len x max_len) per denoising chunk; "
                         "default batch-size*256. Long-anchor chunks are split "
                         "by this budget to stay in VRAM")
    ap.add_argument("--shard", type=int, default=0,
                    help="process only cells with index %% num-shards == shard")
    ap.add_argument("--num-shards", type=int, default=1,
                    help="total shards (one per GPU on a multi-GPU node)")
    ap.add_argument("--max-iter", type=int, default=None)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--unmasking", default="stochastic1.0")
    ap.add_argument("--sampling", default="annealing@2.0:0.1")
    ap.add_argument("--ipr-mapping", default=DEFAULT_IPR_MAP)
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    fams = [f.strip() for f in args.families.split(",")]
    strata = [s.strip() for s in args.strata.split(",")]
    arms = [a.strip() for a in args.arms.split(",")]
    ps = [float(x) for x in args.ps.split(",")]
    seeds = [int(x) for x in args.seeds.split(",")]

    with open(args.ipr_mapping, "rb") as f:
        ipr_map = pickle.load(f)

    need_ours = any(a in ("cond", "cond_null") for a in arms)
    need_vanilla = "vanilla" in arms
    ours = vanilla = None
    if need_ours:
        print("loading our trained ckpt ...")
        from generate_conditional_dplm2 import load_conditional_model
        ours = load_conditional_model(args.ckpt, device)
        tok = ours.tokenizer
    if need_vanilla:
        print("loading vanilla airkingbd/dplm2_650m ...")
        vanilla = DPLM2.from_pretrained("airkingbd/dplm2_650m").to(device).eval()
        tok = vanilla.tokenizer

    cells = []
    for fam in fams:
        for stratum in strata:
            # build_strata writes the length-matched non-member set as
            # nonmembers.fasta; the stratum label for it is "none"
            fname = "nonmembers.fasta" if stratum == "none" else f"{stratum}.fasta"
            fasta = Path(args.anchors) / fam / fname
            if not fasta.exists():
                print(f"[skip] missing {fasta}")
                continue
            anchors = read_fasta(fasta)
            fam_ps = ps + ([1.0] if stratum == "low" and 1.0 not in ps else [])
            for arm in arms:
                for p in fam_ps:
                    for seed in seeds:
                        cells.append((fam, stratum, arm, p, seed, anchors))
    print(f"{len(cells)} cells queued")
    if args.num_shards > 1:
        cells = [c for i, c in enumerate(cells) if i % args.num_shards == args.shard]
        print(f"shard {args.shard}/{args.num_shards}: {len(cells)} cells")

    for fam, stratum, arm, p, seed, anchors in tqdm(cells, desc="cells"):
        cell_dir = Path(args.out) / fam / stratum / arm
        cell_dir.mkdir(parents=True, exist_ok=True)
        fasta_path = cell_dir / f"p{p:g}_s{seed}.fasta"
        meta_path = cell_dir / f"p{p:g}_s{seed}.json"
        if fasta_path.exists() and meta_path.exists():
            continue

        torch.manual_seed(seed)
        rng = random.Random((seed, fam, stratum, arm, p))
        model = vanilla if arm == "vanilla" else ours
        if model is None:
            print(f"[skip] arm {arm} requested but its model was not loaded")
            continue

        # length-sorted chunks keep padding (and its fixed eos) minimal; a
        # token budget (not a fixed count) keeps long-anchor batches in VRAM
        anchors_sorted = sorted(anchors, key=lambda a: len(a[1]))
        token_budget = args.token_budget or args.batch_size * 256
        chunks, cur = [], []
        for a in anchors_sorted:
            cur.append(a)
            cur_max = max(len(x[1]) for x in cur)
            if len(cur) == args.batch_size or len(cur) * cur_max >= token_budget:
                chunks.append(cur)
                cur = []
        if cur:
            chunks.append(cur)
        written = []
        for chunk in chunks:
            ids = [a[0] for a in chunk]
            seqs_in = [a[1] for a in chunk]
            pm = build_batch(seqs_in, p, tok, rng, device)
            conds = (
                build_family_conditions(ipr_map[fam], len(chunk), device)
                if arm == "cond" else None
            )
            seqs = run_cell(
                model, arm, seqs_in, pm, tok, device,
                args.max_iter or pm["tokens"].shape[1],
                args.temperature, args.unmasking, args.sampling,
                conds, args.cfg_scale,
            )
            written.extend(zip(ids, seqs))

        with open(fasta_path, "w") as f:
            for uid, s in written:
                f.write(f">{uid} fam={fam} stratum={stratum} arm={arm} "
                        f"p={p:g} seed={seed}\n{s}\n")
        meta_path.write_text(json.dumps({
            "family": fam, "stratum": stratum, "arm": arm, "p": p, "seed": seed,
            "n": len(written), "cfg_scale": args.cfg_scale,
        }))
        tqdm.write(f"[done] {fam}/{stratum}/{arm} p={p:g} s={seed} n={len(written)}")


if __name__ == "__main__":
    main()
