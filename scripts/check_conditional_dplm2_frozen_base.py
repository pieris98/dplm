"""Frozen-base equivalence smoke check for ConditionalDPLM2.

Loads plain DPLM-2 and ConditionalDPLM2 from the SAME pretrained checkpoint
(`airkingbd/dplm2_650m`) via from_pretrained, then compares logits on a
fixed batch. Pass criteria:
  * With conditions=None, logits must match base DPLM-2 within tolerance
    (adapters are near-zero-init, base is byte-identical).
  * With a Pfam annotation, logits should still be close to base but
    non-identical (proves the adapter is actually being applied).
  * Base params must have zero grad after one backward; only adapter /
    embedder / projector params should have non-zero grad.

Run from repo root:
    python scripts/check_conditional_dplm2_frozen_base.py
"""
import torch
from byprot.models.dplm2 import ConditionalDPLM2, MultimodalDiffusionProteinLanguageModel

MODEL = "airkingbd/dplm2_650m"
COND_CFG = {
    "annotations": {"vocab_sizes": {"pfam": 100}, "embed_dim": 128, "p_dropout_uncond": 0.0},
    "adapter": {"c_s": 128, "c_hidden": 16, "weight_init": 1e-5},
    "external": {"enable": False},
    "freeze_base": True,
}


def main():
    torch.manual_seed(0)
    print(f"Loading base DPLM-2 from {MODEL} ...")
    base = MultimodalDiffusionProteinLanguageModel.from_pretrained(MODEL).eval()
    print(f"Loading ConditionalDPLM2 from {MODEL} ...")
    cond = ConditionalDPLM2.from_pretrained(MODEL, cfg_override={"conditioning": COND_CFG}).eval()

    # Confirm base params are actually frozen in `cond`.
    base_frozen = all(not p.requires_grad for p in cond.net.parameters())
    print(f"  cond.net params all frozen: {base_frozen}")
    n_trainable = sum(p.numel() for n, p in cond.named_parameters() if p.requires_grad)
    print(f"  trainable params (adapter+embedder+projector): {n_trainable:,}")

    # Confirm the pretrained weights loaded identically into both.
    base_emb = base.net.esm.embeddings.word_embeddings.weight
    cond_emb = cond.net.esm.embeddings.word_embeddings.weight
    print(f"  shared embedding table identical: {torch.allclose(base_emb, cond_emb)}")

    # Synthetic batch: amino-acid tokens only, pad on the right.
    pad_id = base.pad_id
    B, L = 2, 16
    torch.manual_seed(42)
    input_ids = torch.randint(0, 33, (B, L))
    input_ids[:, -1] = base.aa_eos_id
    input_ids[1, -3:] = pad_id

    with torch.no_grad():
        base_out = base.forward(input_ids=input_ids)
        cond_no_cond = cond.forward(input_ids=input_ids, conditions=None)
        labels = {"pfam": torch.tensor([[0, 1, -1], [2, 3, -1]])}
        cond_with = cond.forward(
            input_ids=input_ids, conditions={"annotations": labels}
        )

    base_logits = base_out["logits"]
    d_none = (cond_no_cond["logits"] - base_logits).abs().max().item()
    d_with = (cond_with["logits"] - base_logits).abs().max().item()
    print(f"\nMax abs logit diff, no conditions : {d_none:.2e}  (expect ~0)")
    print(f"Max abs logit diff, with Pfam cond: {d_with:.2e}  (expect small but > 0)")

    # Gradient check: one backward, then inspect which params moved.
    cond.zero_grad(set_to_none=True)
    out = cond.forward(input_ids=input_ids, conditions={"annotations": labels})
    out["logits"].sum().backward()
    base_grad = sum(
        1 for n, p in cond.named_parameters()
        if p.requires_grad and p.grad is not None and p.grad.abs().max().item() > 0
        and not (n.startswith("annotation_embedder")
                 or n.startswith("annotation_projectors")
                 or n.startswith("adapters_module")
                 or n.startswith("external_projector"))
    )
    adapter_grad = sum(
        1 for n, p in cond.named_parameters()
        if p.requires_grad and p.grad is not None and p.grad.abs().max().item() > 0
        and (n.startswith("annotation_embedder")
             or n.startswith("annotation_projectors")
             or n.startswith("adapters_module")
             or n.startswith("external_projector"))
    )
    print(f"\nParams with non-zero grad:")
    print(f"  base params with grad (should be 0): {base_grad}")
    print(f"  adapter/embedder/projector params with grad: {adapter_grad}")

    print("\n=== VERDICT ===")
    ok = (
        torch.allclose(base_emb, cond_emb)
        and d_none < 1e-3
        and d_with > 0
        and base_grad == 0
        and adapter_grad > 0
    )
    print("PASS" if ok else "FAIL — see diagnostics above")


if __name__ == "__main__":
    main()
