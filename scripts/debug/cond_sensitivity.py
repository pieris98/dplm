"""Q10: offline condition-sensitivity check for a ConditionalDPLM2 checkpoint.

Answers the question the live loss_delta sensor could not (its bug kept the
value at exactly 0 for the first 14K steps): **do the trained adapters make
the model condition-sensitive at all?**

Three measurements on real batches from the training parquet, all with
identical seeding so only the condition varies:

  1. **Loss delta** — training loss with real GO/IPR conditions vs with the
     learned null embedding (force_unconditional path). Conditioning that
     helps denoising → delta < 0. Seed-matched pairs, so diffusion-noise
     variance cancels.
  2. **Logit sensitivity** — same inputs, real vs null adapter pathway:
     mean/max |Δlogits| over all positions, and the number of argmax flips.
     Function-level sensitivity, independent of the loss reduction.
  3. **Condition-space spread** — pairwise cosine distances of the projected
     condition vectors s across random label sets. Near-zero spread means the
     condition pathway collapsed to a constant (capacity/learning issue)
     even if the plumbing is intact.

Diagnostic, not a gate — always exits 0. Run inside the sandbox:

    srun ... bash -c "source scripts/meluxina/common.sh && \
      run_in_container \"\${DPLM_PY}\" scripts/debug/cond_sensitivity.py \
      --ckpt <path or 'auto'>"
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.getcwd())

import torch  # noqa: E402

AA = "ACDEFGHIKLMNPQRSTVWY"


def random_conditions(batch_size, device, seed, vocab_sizes):
    g = torch.Generator().manual_seed(seed)

    def pad(lists):
        m = max(1, max(len(x) for x in lists))
        t = torch.full((len(lists), m), -1, dtype=torch.long)
        for i, x in enumerate(lists):
            if x:
                t[i, : len(x)] = torch.tensor(x, dtype=torch.long)
        return t.to(device)

    anns = {}
    for t, vocab in vocab_sizes.items():
        lists = [
            [int(torch.randint(0, vocab, (1,), generator=g))
             for _ in range(int(torch.randint(1, 4, (1,), generator=g)))]
            for _ in range(batch_size)
        ]
        anns[t] = pad(lists)
    return {"annotations": anns}


def random_aa_batch(tokenizer, batch_size, seq_len, device, seed):
    g = torch.Generator().manual_seed(seed)
    seqs = []
    for _ in range(batch_size):
        n = int(torch.randint(seq_len // 2, seq_len, (1,), generator=g))
        seqs.append(
            tokenizer.aa_cls_token
            + "".join(AA[int(torch.randint(0, len(AA), (1,), generator=g))] for _ in range(n))
            + tokenizer.aa_eos_token
        )
    return tokenizer.batch_encode_plus(
        seqs, add_special_tokens=False, padding=True, return_tensors="pt"
    )["input_ids"].to(device)


def k_of(item):
    return item.key or "/".join(str(x) for x in item.nested_key)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", required=True, help="checkpoint path")
    ap.add_argument("--parquet", default="data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet")
    ap.add_argument("--n-batches", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--seq-len", type=int, default=128)
    ap.add_argument("--seeds", type=int, default=3, help="seed-matched repetitions per batch")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    from generate_conditional_dplm2 import load_conditional_model
    from byprot.datamodules.dataset.annotated_protein import (
        AnnotatedProteinCollater,
        AnnotatedProteinDataset,
    )
    from byprot.modules.cross_entropy import StructAARDMCrossEntropyLoss

    model = load_conditional_model(args.ckpt, args.device)
    model.eval()

    ds = AnnotatedProteinDataset(parquet_path=args.parquet, max_len=args.seq_len,
                                 split_seed=0, val_ratio=0.05, split="train")
    coll = AnnotatedProteinCollater(ds.tokenizer)
    device = next(model.parameters()).device
    criterion = StructAARDMCrossEntropyLoss(label_smoothing=0.0, ignore_index=1).to(device)

    print(f"[cond_sensitivity] ckpt={args.ckpt}")
    print(f"[cond_sensitivity] {args.n_batches} batches x {args.batch_size} seqs, "
          f"{args.seeds} seed-matched repetitions\n")

    # ---- 3. condition-space spread -----------------------------------------------
    vocab_sizes = model.cond_cfg.annotations.vocab_sizes
    conds = [random_conditions(args.batch_size, device, seed=1000 + i, vocab_sizes=vocab_sizes)
             for i in range(16)]
    with torch.inference_mode():
        encs = [model._encode_conditions(c) for c in conds]
    flat = []
    for enc in encs:
        for layer_inputs in enc:
            if layer_inputs:
                flat.append(torch.cat(layer_inputs, dim=-1)[0])  # first sample's s
    if len(flat) > 1:
        S = torch.stack(flat)
        S = S / (S.norm(dim=-1, keepdim=True) + 1e-8)
        cos = S @ S.T
        n = cos.shape[0]
        off = cos[~torch.eye(n, dtype=torch.bool)]
        print(f"[spread] condition vectors: {n} label sets | pairwise cosine "
              f"mean={off.mean().item():.4f} min={off.min().item():.4f} max={off.max().item():.4f}")
        print("         (≈1.0 everywhere → condition pathway collapsed to a constant)\n")

    # ---- 1+2. loss delta & logit sensitivity --------------------------------------
    all_deltas, all_lmean, all_lmax = [], [], []
    total_flips = total_pos = 0
    for b in range(args.n_batches):
        idxs = range(b * args.batch_size, (b + 1) * args.batch_size)
        batch = coll([ds[i] for i in idxs])
        annotations = batch.pop("annotations")
        annotations = {"annotations": {k: v.to(device) for k, v in annotations.items()}}
        for track in ("struct_tokens", "aatype_tokens"):
            batch[track] = {k: v.to(device) for k, v in batch[track].items()}
        x = torch.cat([batch["struct_tokens"]["targets"],
                       batch["aatype_tokens"]["targets"]], dim=1).to(device)

        cond_enc = model._encode_conditions(annotations)
        null_enc = model._encode_conditions(annotations, force_unconditional=True)

        # 2. logit sensitivity: same input, real vs null adapter pathway
        l_means, l_maxs, flips, n_pos = [], [], 0, 0
        for s in range(args.seeds):
            torch.manual_seed(9000 + b * 100 + s)
            with torch.inference_mode():
                out_r = model.forward(input_ids=x, layer_adapter_inputs=cond_enc)["logits"].float()
                out_n = model.forward(input_ids=x, layer_adapter_inputs=null_enc)["logits"].float()
            d = (out_r - out_n).abs()
            l_means.append(d.mean().item())
            l_maxs.append(d.max().item())
            flips += int((out_r.argmax(-1) != out_n.argmax(-1)).sum())
            n_pos += d.numel()
        all_lmean += l_means
        all_lmax += l_maxs
        total_flips += flips
        total_pos += n_pos

        # 1. seeded loss delta (patch wraps ONLY the null pass)
        d_losses = {"total": [], "aatype": [], "struct": []}
        orig = model._encode_conditions

        def _track_val(log, track):
            for k, v in log.items():
                if k.startswith(track) and "nll" in k and "fullseq" not in k:
                    return float(v)
            for k, v in log.items():
                if k.startswith(track) and "loss" in k and "index" not in k:
                    return float(v)
            return None

        for s in range(args.seeds):
            torch.manual_seed(7000 + b * 100 + s)
            with torch.inference_mode():
                logits_r, targets, masks, w = model.compute_loss(
                    {k: batch[k] for k in ("struct_tokens", "aatype_tokens")},
                    weighting="linear", conditions=annotations)
                loss_r, log_r = criterion(logits_r, targets, masks, w)
                model._encode_conditions = (
                    lambda c, force_unconditional=False, _o=orig:
                    _o(c, force_unconditional=True))
                try:
                    logits_n, targets, masks, w = model.compute_loss(
                        {k: batch[k] for k in ("struct_tokens", "aatype_tokens")},
                        weighting="linear", conditions=annotations)
                finally:
                    model._encode_conditions = orig
                loss_n, log_n = criterion(logits_n, targets, masks, w)
            d_losses["total"].append((loss_r - loss_n).item())
            for track in ("aatype", "struct"):
                vr, vn = _track_val(log_r, track), _track_val(log_n, track)
                if vr is not None and vn is not None:
                    d_losses[track].append(vr - vn)
        all_deltas += d_losses["total"]

        print(f"[batch {b}] loss delta (real-null): total mean={statistics.mean(d_losses['total']):+.5f} "
              f"range=[{min(d_losses['total']):+.5f}, {max(d_losses['total']):+.5f}]")
        for track in ("aatype", "struct"):
            if d_losses[track]:
                print(f"           {track:6s} delta mean={statistics.mean(d_losses[track]):+.5f} "
                      f"range=[{min(d_losses[track]):+.5f}, {max(d_losses[track]):+.5f}]")
        print(f"           logit |Δ| mean={statistics.mean(l_means):.5f} "
              f"max={max(l_maxs):.4f} | argmax flips={flips}/{n_pos}")

    print("\n[verdict]")
    print(f"  loss delta:        mean={statistics.mean(all_deltas):+.5f} "
          f"(negative = conditioning helping denoising; ~0 = adapters ignore labels)")
    print(f"  logit sensitivity: mean|Δ|={statistics.mean(all_lmean):.5f} "
          f"max|Δ|={max(all_lmax):.4f} | argmax flips {total_flips}/{total_pos}")
    print("  interpret with probe/adapter_w: large W + zero sensitivity = adapters "
          "learned condition-INDEPENDENT refinements only.")


if __name__ == "__main__":
    main()
