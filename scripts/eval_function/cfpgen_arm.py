"""CFP-Gen arm for the function-recovery evaluation — run from the CFP-Gen repo.

IMPORTANT: this script is deliberately NOT part of the dplm python process.
CFP-Gen ships its own forked ``byprot`` package (``byprot.models.lm.cfp_gen``
exists only in the cfpgen repository) which must shadow dplm's. Run it with
the cfpgen repo first on the path, from inside the cfpgen checkout:

    cd /home/cherry/dev/phd/cfpgen
    PYTHONPATH=/home/cherry/dev/phd/cfpgen python \
        /home/cherry/dev/phd/dplm/scripts/eval_function/cfpgen_arm.py \
        --evaldir /path/to/eval_run \
        --ckpt cfpgen_650m/checkpoints/last.ckpt

It reads ``<evaldir>/manifest.json`` (arm ``real``: the held-out prompt
proteins), conditions the CFP-Gen 650M on each protein's GO+IPR labels
(the "CFP-Gen (w/ GO and IPR)" ablation variant), and writes
``<evaldir>/cfpgen/aatype.fasta`` with the same header/manifest conventions
as the other arms — so ``score_function_eval.py`` treats it as just another
arm with identical prompts and metrics.

Protocol notes (documented deviations):
  * Fixed length for every sequence (default 256) to match the dplm arms —
    CFP-Gen's own config samples random lengths in [200, 400]; a fixed length
    keeps the distribution metrics (MMD/MRR) comparable across arms.
  * No sequence-motif conditioning (``use_seq_motif=False``) — the comparison
    is the pure function-conditioning ablation "w/ GO and IPR".
  * Sampling strategy defaults to CFP-Gen's own config (gumbel_argmax) rather
    than the dplm arms' annealing schedule — the model's native sampler.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evaldir", required=True)
    ap.add_argument("--ckpt", required=True,
                    help="CFP-Gen checkpoint, e.g. cfpgen_650m/checkpoints/last.ckpt")
    ap.add_argument("--cfpgen-repo", default=None,
                    help="path to the cfpgen checkout (default: cwd)")
    ap.add_argument("--len", type=int, default=256, dest="seq_len")
    ap.add_argument("--max-iter", type=int, default=None,
                    help="denoising steps (default: seq_len, matching dplm arms)")
    ap.add_argument("--sampling", default="gumbel_argmax",
                    help="CFP-Gen's native sampler (their config default)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    # cfpgen's byprot must shadow dplm's — this process is dedicated to it.
    repo = Path(args.cfpgen_repo or Path.cwd()).resolve()
    sys.path.insert(0, str(repo))

    import torch  # noqa: E402  (import after path setup)
    from byprot import utils as cfputils  # noqa: E402
    from byprot.models.lm.cfp_gen import (  # noqa: E402
        CondDiffusionProteinLanguageModel,
    )

    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    evdir = Path(args.evaldir)
    manifest = json.load(open(evdir / "manifest.json"))
    prompts = manifest["arms"]["real"]  # held-out proteins: our prompt source
    max_iter = args.max_iter or args.seq_len

    print(f"Loading CFP-Gen from {args.ckpt} ...")
    model = CondDiffusionProteinLanguageModel.from_pretrained(args.ckpt)
    model = model.eval().to(device)
    tokenizer = model.tokenizer

    out_dir = evdir / "cfpgen"
    out_dir.mkdir(parents=True, exist_ok=True)

    recs, seqs_all = [], []
    mask_id = model.mask_id
    for i, rec in enumerate(prompts):
        go_labels = rec["prompt_go"]
        ipr_labels = rec["prompt_ipr"]

        init_seq = ["".join(["<mask>"] * args.seq_len)]
        batch = tokenizer.batch_encode_plus(
            init_seq, add_special_tokens=True, padding="longest",
            return_tensors="pt")
        out_batch = {
            "input_ids": batch["input_ids"],
            "input_mask": batch["attention_mask"].bool(),
        }
        if go_labels:
            out_batch["go_label"] = torch.tensor(go_labels)
        if ipr_labels:
            out_batch["ipr_label"] = torch.tensor(ipr_labels)
        out_batch = cfputils.recursive_to(out_batch, device)

        # All <mask> prompt: nothing partial — pure function-conditioned gen.
        partial_mask = out_batch["input_ids"].ne(mask_id).type_as(out_batch["input_mask"])

        with torch.autocast("cuda", enabled=device.startswith("cuda")):
            outputs = model.generate(
                batch=out_batch, tokenizer=tokenizer,
                max_iter=max_iter,
                sampling_strategy=args.sampling,
                partial_masks=partial_mask)
        output_tokens = outputs[0]
        decoded = tokenizer.batch_decode(output_tokens, skip_special_tokens=True)

        seq_id = f"cfpgen_{rec['uniprot_id']}"
        for seq in decoded:
            seqs_all.append(seq.replace(" ", ""))
            recs.append({
                "seq_id": seq_id,
                "uniprot_id": rec["uniprot_id"],
                "prompt_ipr": rec["prompt_ipr"],
                "prompt_go": rec["prompt_go"],
            })

        if (i + 1) % 10 == 0 or (i + 1) == len(prompts):
            print(f"  [{i+1}/{len(prompts)}] generated", flush=True)

    with open(out_dir / "aatype.fasta", "w") as f:
        for r, s in zip(recs, seqs_all):
            f.write(f">{r['seq_id']}\n{s}\n")

    # Register the arm in the manifest so the scorer sees it.
    manifest["arms"]["cfpgen"] = recs
    with open(evdir / "manifest.json", "w") as f:
        json.dump(manifest, f)

    print(f"wrote {len(recs)} sequences to {out_dir}/aatype.fasta "
          f"and registered arm 'cfpgen' in the manifest")


if __name__ == "__main__":
    main()
