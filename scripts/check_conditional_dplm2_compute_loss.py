"""Training-time plumbing check for ConditionalDPLM2.

Confirms that:
  1. compute_loss(..., conditions=...) flows conditions into the forward
     pass (loss differs from the no-conditions run).
  2. After backward, only adapter/embedder/projector params have non-zero
     grad; base DPLM-2 params have zero grad.
  3. Loss is finite.

Builds a synthetic DPLM-2 batch (struct + aa tokens) so we don't need real
data on disk. Token id ranges: <33 = AA, 33..8224 = struct, 8225..8228 =
struct specials. We use only struct tokens in the struct track and AA tokens
in the aa track, with the proper <cls>/<eos> wrapping via the model's
special-token ids.

Run from repo root:
    python scripts/check_conditional_dplm2_compute_loss.py
"""
import torch
from byprot.models.dplm2 import ConditionalDPLM2

MODEL = "airkingbd/dplm2_650m"
COND_CFG = {
    "annotations": {"vocab_sizes": {"pfam": 100}, "embed_dim": 128, "p_dropout_uncond": 0.0},
    "adapter": {"c_s": 128, "c_hidden": 16, "weight_init": 1e-5},
    "external": {"enable": False},
    "freeze_base": True,
}


def make_batch(model, B=2, L=12):
    """Minimal DPLM-2 batch with struct + aa token tracks."""
    # AA track: random AA ids, wrapped with aa_bos ... aa_eos.
    aa_bos, aa_eos = model.aa_bos_id, model.aa_eos_id
    struct_bos, struct_eos = model.struct_bos_id, model.struct_eos_id
    inner = L - 2
    aa_inner = torch.randint(0, 20, (B, inner))  # standard AAs only
    aa = torch.cat([torch.full((B, 1), aa_bos), aa_inner, torch.full((B, 1), aa_eos)], dim=1)
    # Struct track: random struct ids in the 33..8224 range.
    struct_inner = torch.randint(33, 33 + 8192, (B, inner))
    struct = torch.cat([torch.full((B, 1), struct_bos), struct_inner, torch.full((B, 1), struct_eos)], dim=1)
    return {
        "struct_tokens": {"targets": struct, "attention_mask": torch.ones_like(struct)},
        "aatype_tokens": {"targets": aa, "attention_mask": torch.ones_like(aa)},
    }


def main():
    print(f"Loading ConditionalDPLM2 from {MODEL} ...")
    model = ConditionalDPLM2.from_pretrained(
        MODEL, cfg_override={"conditioning": COND_CFG}
    )
    model.train()  # enable dropout so annotation dropout can act; not strictly needed
    torch.manual_seed(0)
    batch = make_batch(model)
    labels = {"pfam": torch.tensor([[0, 1, -1], [2, 3, -1]])}

    # 1) Loss with no conditions vs with conditions — must differ.
    with torch.no_grad():
        loss_none = model.compute_loss(batch)
    # compute_loss returns (logits, targets, masks, weights) — we just need
    # a scalar for backward. Build a simple CE from the logits.
    def scalar_loss(out):
        logits, targets = out[0], out[1]
        return torch.nn.functional.cross_entropy(
            logits["aatype"].reshape(-1, logits["aatype"].shape[-1]),
            targets["aatype"].reshape(-1),
        )
    with torch.no_grad():
        loss_none_scalar = scalar_loss(loss_none).item()

    model.zero_grad(set_to_none=True)
    out_with = model.compute_loss(batch, conditions={"annotations": labels})
    loss_with = scalar_loss(out_with)
    loss_with.backward()
    loss_with_scalar = loss_with.item()

    print(f"Loss, no conditions : {loss_none_scalar:.6f}")
    print(f"Loss, with Pfam cond: {loss_with_scalar:.6f}")
    print(f"  (these should differ — proves conditions flow into compute_loss)")

    # 2) Gradient isolation.
    base_grad = sum(
        1 for n, p in model.named_parameters()
        if p.requires_grad and p.grad is not None and p.grad.abs().max().item() > 0
        and not (n.startswith("annotation_embedder")
                 or n.startswith("annotation_projectors")
                 or n.startswith("adapters_module")
                 or n.startswith("external_projector"))
    )
    adapter_grad = sum(
        1 for n, p in model.named_parameters()
        if p.requires_grad and p.grad is not None and p.grad.abs().max().item() > 0
        and (n.startswith("annotation_embedder")
             or n.startswith("annotation_projectors")
             or n.startswith("adapters_module")
             or n.startswith("external_projector"))
    )
    print(f"\nParams with non-zero grad after backward:")
    print(f"  base params (should be 0): {base_grad}")
    print(f"  adapter/embedder/projector: {adapter_grad}")

    print("\n=== VERDICT ===")
    ok = (
        abs(loss_none_scalar - loss_with_scalar) > 0
        and base_grad == 0
        and adapter_grad > 0
        and torch.isfinite(torch.tensor(loss_with_scalar)).item()
    )
    print("PASS" if ok else "FAIL — see diagnostics above")


if __name__ == "__main__":
    main()
