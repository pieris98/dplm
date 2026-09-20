"""CFG sampling correctness checks for ConditionalDPLM2.

Verifies the classifier-free-guidance combination implemented in
``ConditionalDPLM2.forward``:

  * w=0   → logits equal the null-conditioned (uncond) pass exactly
  * w=1   → logits equal the conditional pass exactly (shortcut path)
  * w=2   → equals ``uncond + 2*(cond - uncond)`` exactly
  * guidance distance from the conditional logits grows monotonically with w
  * ``generate`` runs end-to-end with w=1 and w=4; same seed, different
    sequences (guidance must change sampling)

Run from repo root (uses the local 200-step smoke ckpt so the conditional
signal is measurable):

    python scripts/check_conditional_cfg.py
"""
import argparse
import os

import torch

from byprot.models.dplm2 import ConditionalDPLM2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", type=str,
                    default="logs/cond_dplm2_smoke_200step/checkpoints/step_199.0-loss_2.33.ckpt")
    ap.add_argument("--labels", type=str, action="append", default=None,
                    help="optional trained-label override, e.g. --labels go:5")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ConditionalDPLM2.from_pretrained(
        "airkingbd/dplm2_650m",
        cfg_override={"conditioning": {
            "annotations": {"vocab_sizes": {"ipr": 1154, "go": 375},
                            "embed_dim": 128, "p_dropout_uncond": 0.0},
            "adapter": {"c_s": 128, "c_hidden": 16, "weight_init": 1e-5},
            "external": {"enable": False}, "freeze_base": True}},
    )
    if args.ckpt and os.path.exists(args.ckpt):
        ckpt = torch.load(args.ckpt, map_location="cpu")
        stripped = {k[len("model."):]: v for k, v in ckpt["state_dict"].items()
                    if k.startswith("model.")}
        model.load_state_dict(stripped, strict=False)
        print(f"loaded trained ckpt: {args.ckpt}")
    model = model.eval().to(device)

    # Fixed inputs + a condition whose labels exist in the training vocab.
    torch.manual_seed(0)
    x = torch.randint(0, 33, (2, 16), device=device)
    cond = {"annotations": {"ipr": torch.tensor([[10, 11, -1]], device=device),
                            "go": torch.tensor([[5, -1, -1]], device=device)}}

    enc_cond = model._encode_conditions(cond)
    enc_uncond = model._encode_conditions(cond, force_unconditional=True)

    with torch.no_grad():
        ref_cond = model._forward_net(x, enc_cond)["logits"]
        ref_uncond = model._forward_net(x, enc_uncond)["logits"]
    gap = (ref_cond - ref_uncond).abs().max().item()
    print(f"\n|cond - uncond| logits gap: {gap:.4f}")
    assert gap > 0, "adapter fired with no conditional signal — nothing to guide"

    # Stash both passes, then walk the guidance scale.
    model._active_layer_adapter_inputs = enc_cond
    model._active_layer_adapter_inputs_uncond = enc_uncond

    failures = []

    def expect(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
        if not ok:
            failures.append(name)

    with torch.no_grad():
        model._cfg_scale = 0.0
        out0 = model.forward(x)["logits"]
        expect("w=0 reproduces the null-conditioned pass exactly",
               torch.allclose(out0, ref_uncond, atol=1e-4),
               f"max diff {(out0 - ref_uncond).abs().max().item():.2e}")

        model._cfg_scale = 1.0  # shortcut path — single pass
        out1 = model.forward(x)["logits"]
        expect("w=1 reproduces the conditional pass exactly (shortcut)",
               torch.allclose(out1, ref_cond, atol=1e-5),
               f"max diff {(out1 - ref_cond).abs().max().item():.2e}")

        ref2 = ref_uncond + 2.0 * (ref_cond - ref_uncond)
        model._cfg_scale = 2.0
        out2 = model.forward(x)["logits"]
        expect("w=2 equals uncond + 2*(cond - uncond)",
               torch.allclose(out2, ref2, atol=1e-4),
               f"max diff {(out2 - ref2).abs().max().item():.2e}")

        model._cfg_scale = 4.0
        out4 = model.forward(x)["logits"]
        d2 = (out2 - ref_cond).abs().max().item()
        d4 = (out4 - ref_cond).abs().max().item()
        expect("guidance distance grows monotonically with w (2 → 4)", d4 > d2,
               f"d(w=2)={d2:.3f}  d(w=4)={d4:.3f}")

        # Restore clean state.
        model._cfg_scale = 1.0
        model._active_layer_adapter_inputs = None
        model._active_layer_adapter_inputs_uncond = None

    # End-to-end: guided generation runs and differs from vanilla at same seed.
    aa_bos, aa_eos = model.aa_bos_id, model.aa_eos_id
    tok = model.tokenizer
    init = tok.aa_mask_token * 16
    init = tok.aa_cls_token + init + tok.aa_eos_token
    input_tokens = tok.batch_encode_plus(
        [init, init], add_special_tokens=False, padding=True,
        return_tensors="pt")["input_ids"].to(device)

    seqs_by_w = {}
    for w in (1.0, 4.0):
        torch.manual_seed(42)
        with torch.inference_mode(), torch.cuda.amp.autocast(dtype=torch.bfloat16):
            out = model.generate(
                input_tokens=input_tokens, conditions=cond, max_iter=8,
                cfg_scale=w)
        toks = out["output_tokens"]
        # tokens must be valid AA vocab entries (< 33 for the aa half)
        assert toks.min() >= 0 and toks.max() < 33
        seqs_by_w[w] = toks
        expect(f"generate w={w:g} runs and returns tokens", True,
               f"shape {tuple(toks.shape)}")

    same = torch.equal(seqs_by_w[1.0], seqs_by_w[4.0])
    expect("same seed: w=1 and w=4 produce different sequences", not same)

    print("\n=== VERDICT ===")
    if failures:
        print(f"FAIL — {len(failures)} check(s): {failures}")
        sys_exit(1)
    print("ALL CFG CHECKS PASSED")


def sys_exit(code):
    import sys
    sys.exit(code)


if __name__ == "__main__":
    main()
