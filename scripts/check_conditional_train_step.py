"""End-to-end training smoke test for ConditionalDPLM2.

Builds the model from the experiment config (no trainer needed), loads one
batch from the datamodule, runs one forward+backward, and reports:
  - loss is finite
  - base DPLM-2 params have zero grad
  - adapter/embedder/projector params have non-zero grad
  - adapter fires (loss differs from no-conditions run)

Run from repo root:
    python scripts/check_conditional_train_step.py
"""
import torch
from omegaconf import OmegaConf

from byprot.models.dplm2 import ConditionalDPLM2
from byprot.datamodules.dataset.annotated_protein import (
    AnnotatedProteinDataset, AnnotatedProteinCollater,
)
from byprot.datamodules.dataset.tokenized_protein import DPLM2Tokenizer


COND_CFG = {
    "annotations": {"vocab_sizes": {"ipr": 1154, "go": 375}, "embed_dim": 128, "p_dropout_uncond": 0.1},
    "adapter": {"c_s": 128, "c_hidden": 16, "weight_init": 1e-5},
    "external": {"enable": False},
    "freeze_base": True,
}
PARQUET = "/home/cherry/dev/phd/dplm/data-bin/cfpgen_dplm2_joined/missing_afdb_struct_tokens.parquet"


def main():
    print("Loading ConditionalDPLM2 from airkingbd/dplm2_650m ...")
    model = ConditionalDPLM2.from_pretrained(
        "airkingbd/dplm2_650m", cfg_override={"conditioning": COND_CFG}
    )
    model.train()
    print(f"  trainable params: {sum(p.numel() for p in model.parameters() if p.requires_grad):,}")
    print(f"  base frozen     : {all(not p.requires_grad for p in model.net.parameters())}")

    # Build a batch from the datamodule.
    tok = DPLM2Tokenizer.from_pretrained("airkingbd/dplm2_650m")
    ds = AnnotatedProteinDataset(
        parquet_path=PARQUET, vocab_file="airkingbd/dplm2_650m",
        max_len=512, split="train",
    )
    coll = AnnotatedProteinCollater(tok)
    batch = coll([ds[i] for i in range(2)])
    device = next(model.parameters()).device
    # Move tensors in struct_tokens/aatype_tokens to device
    for mod in ("struct_tokens", "aatype_tokens"):
        for k in batch[mod]:
            batch[mod][k] = batch[mod][k].to(device)
    annotations = batch.pop("annotations")
    conditions = {"annotations": {k: v.to(device) for k, v in annotations.items()}}

    # Step 1: loss with no conditions (sanity, should match base DPLM-2)
    with torch.no_grad():
        out_no = model.compute_loss(batch)
        def scalar_loss(o):
            return torch.nn.functional.cross_entropy(
                o[0]["aatype"].reshape(-1, o[0]["aatype"].shape[-1]),
                o[1]["aatype"].reshape(-1),
            )
        loss_no = scalar_loss(out_no).item()

    # Step 2: loss WITH conditions, then backward
    model.zero_grad(set_to_none=True)
    out = model.compute_loss(batch, conditions=conditions)
    loss = scalar_loss(out)
    loss.backward()
    loss_val = loss.item()

    print(f"\nLoss, no conditions : {loss_no:.4f}")
    print(f"Loss, with cond    : {loss_val:.4f}  (should differ)")

    # Grad isolation
    base_grad = sum(
        1 for n, p in model.named_parameters()
        if p.requires_grad and p.grad is not None and p.grad.abs().max().item() > 0
        and not (n.startswith("annotation_embedder") or n.startswith("annotation_projectors")
                 or n.startswith("adapters_module") or n.startswith("external_projector"))
    )
    adapter_grad = sum(
        1 for n, p in model.named_parameters()
        if p.requires_grad and p.grad is not None and p.grad.abs().max().item() > 0
        and (n.startswith("annotation_embedder") or n.startswith("annotation_projectors")
             or n.startswith("adapters_module") or n.startswith("external_projector"))
    )
    print(f"\nParams with non-zero grad after backward:")
    print(f"  base params (should be 0): {base_grad}")
    print(f"  adapter/embedder/projector: {adapter_grad}")

    print("\n=== VERDICT ===")
    ok = (
        torch.isfinite(torch.tensor(loss_val)).item()
        and abs(loss_no - loss_val) > 0
        and base_grad == 0
        and adapter_grad > 0
    )
    print("PASS" if ok else "FAIL — see diagnostics above")


if __name__ == "__main__":
    main()
