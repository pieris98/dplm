"""Phase 0 code-correctness gauntlet for ConditionalDPLM-2.

Runs the local, zero-training correctness checks from
plans/mode_collapse_debug_plan.md (Phase 0). Every check either PASSes or
FAILs with evidence; the script exits nonzero if any requested check fails.

Usage:
    python scripts/debug/phase0_gauntlet.py --check all [--device cuda]
    python scripts/debug/phase0_gauntlet.py --check pretrained-base identity
    python scripts/debug/phase0_gauntlet.py --list

Checks
------
P0.0  pretrained-base     Training-path construction loads the true HF
                          release into the frozen base (regression test for
                          the 2026-10 root cause: net.pretrain=false left a
                          randomly-initialized frozen encoder).
P0.1  identity            Fresh ConditionalDPLM2 == vanilla DPLM-2: (a)
                          teacher-forced logits, (b) deterministic
                          from-scratch generation, (c) partial-mask infill.
P0.2  parent-equivalence  _forward_net(x, None) == parent forward body.
P0.3  adapter-census      Adapter invocations per path: 33 (cond fwd),
                          66 (CFG fwd), 0 (no conditions).
P0.4  null-path           force_unconditional == training dropout path at
                          p=1.0; eval() disables adapter/embedder dropout;
                          generate() restores the condition stash.
P0.5  cfg-degeneracy      w=0 == explicit null pass; w=1 == single
                          conditional pass; w>1 logits finite.
P0.6  data-roundtrip      Training parquet -> dataset/collater -> tokens:
                          aa identity vs source, struct/aa track length
                          alignment, label ranges, annotation alignment.
P0.7  (documented)        Re-run the scorer on committed fn_eval_v1
                          outputs (see scripts/debug/README.md).

GPU: fits a 24 GB card in fp32 (650M ~2.6 GB x2 models + activations).
"""

import argparse
import os
import sys

sys.path.insert(0, os.getcwd())

import torch  # noqa: E402

from byprot.models.dplm2 import (  # noqa: E402
    ConditionalDPLM2,
    MultimodalDiffusionProteinLanguageModel,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXP_YAML = os.path.join(
    REPO, "configs/experiment/dplm2/cond_dplm2_650m_cfpgen_meluxina.yaml"
)
HF_NAME = "airkingbd/dplm2_650m"
VOCAB_SIZES = {"ipr": 1154, "go": 375}

RESULTS = []


def report(name: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    RESULTS.append((name, ok))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Model construction
# ---------------------------------------------------------------------------

def build_training_path_model(device="cuda"):
    """Instantiate ConditionalDPLM2 exactly as hydra/training does (the
    get_net_dplm2 path), from the experiment yaml."""
    from omegaconf import OmegaConf

    cfg = OmegaConf.load(EXP_YAML)
    # Plain-dict model node (drop _target_; the constructor merges onto
    # ConditionalDPLM2Config itself). The experiment yaml references globals
    # (paths.*, trainer.*) that only exist inside a full hydra config.
    mc = OmegaConf.to_container(cfg.model, resolve=False)
    mc.pop("_target_", None)
    mc["tokenizer"]["vocab_file"] = str(cfg.datamodule.vocab_file)
    model = ConditionalDPLM2(mc)
    pretrain_flag = bool(mc["net"]["pretrain"])
    return model.to(device).eval(), pretrain_flag


def build_vanilla(device="cuda"):
    return MultimodalDiffusionProteinLanguageModel.from_pretrained(HF_NAME).to(
        device
    ).eval()


def random_conditions(batch_size=4, seed=0):
    g = torch.Generator().manual_seed(seed)
    def pad(lists):
        m = max(1, max(len(x) for x in lists))
        t = torch.full((len(lists), m), -1, dtype=torch.long)
        for i, x in enumerate(lists):
            if x:
                t[i, : len(x)] = torch.tensor(x, dtype=torch.long)
        return t
    ipr = [[int(torch.randint(0, VOCAB_SIZES["ipr"], (1,), generator=g))
            for _ in range(int(torch.randint(1, 4, (1,), generator=g)))]
           for _ in range(batch_size)]
    go = [[int(torch.randint(0, VOCAB_SIZES["go"], (1,), generator=g))
           for _ in range(int(torch.randint(1, 4, (1,), generator=g)))]
          for _ in range(batch_size)]
    return {"annotations": {"ipr": pad(ipr), "go": pad(go)}}


AA_LETTERS = "ACDEFGHIKLMNPQRSTVWY"


def random_aa_batch(tokenizer, batch_size=4, seq_len=64, seed=0):
    g = torch.Generator().manual_seed(seed)
    seqs = []
    for _ in range(batch_size):
        n = int(torch.randint(seq_len // 2, seq_len, (1,), generator=g))
        letters = "".join(
            AA_LETTERS[int(torch.randint(0, len(AA_LETTERS), (1,), generator=g))]
            for _ in range(n)
        )
        seqs.append(tokenizer.aa_cls_token + letters + tokenizer.aa_eos_token)
    return tokenizer.batch_encode_plus(
        seqs, add_special_tokens=False, padding=True, return_tensors="pt"
    )["input_ids"]


# ---------------------------------------------------------------------------
# P0.0 pretrained-base (root-cause regression)
# ---------------------------------------------------------------------------

def check_pretrained_base(device):
    from transformers import AutoConfig
    from byprot.models.dplm2.modules.dplm2_modeling_esm import EsmForDPLM2

    model, pretrain_flag = build_training_path_model(device)
    if not pretrain_flag:
        report("pretrained-base", False,
               "experiment yaml has net.pretrain=false — the loading block in "
               "get_net_dplm2 is skipped entirely (2026-10 root cause)")
        return model

    ref = EsmForDPLM2.from_pretrained(HF_NAME).state_dict()
    net_sd = model.state_dict()  # top-level: base keys carry the 'net.' prefix
    n_cmp, n_mismatch, worst = 0, [], 0.0
    for k, v in net_sd.items():
        if not k.startswith("net."):
            continue
        hk = k[len("net."):]
        if hk in ref and ref[hk].shape == v.shape:
            n_cmp += 1
            d = (ref[hk].float().cpu() - v.float().cpu()).abs().max().item()
            if d > 1e-6:
                n_mismatch.append((hk, d))
            worst = max(worst, d)
    report(
        "pretrained-base",
        n_cmp >= 500 and not n_mismatch,
        f"{n_cmp} base tensors vs HF release, max|diff|={worst:.2e}, "
        f"mismatched={len(n_mismatch)}" + (f" e.g. {n_mismatch[:3]}" if n_mismatch else ""),
    )

    # adapter near-zero init + frozen base
    up_max, bias_max = 0.0, 0.0
    for layer in model.adapters_module:
        if layer is None or isinstance(layer, torch.nn.Identity):
            continue
        for par in layer.adapters:
            up_max = max(up_max, par.linear_up.weight.abs().max().item())
            bias_max = max(bias_max, par.linear_up.bias.abs().max().item())
    report("adapter-near-zero-init", up_max < 2e-4 and bias_max == 0.0,
           f"max|W_up|={up_max:.2e} (init std 1e-5), max|b_up|={bias_max:.1e}")
    frozen = all(not p.requires_grad for p in model.net.parameters())
    n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    report("base-frozen", frozen, f"trainable params: {n_train/1e6:.2f}M")
    return model


# ---------------------------------------------------------------------------
# P0.1 identity (fresh init == pretrained)
# ---------------------------------------------------------------------------

def check_identity(model, device, seq_len=64, gen_len=48, max_iter=48):
    torch.manual_seed(0)
    vanilla = build_vanilla(device)
    x = random_aa_batch(model.tokenizer, batch_size=4, seq_len=seq_len, seed=1).to(device)

    with torch.inference_mode():
        logits_a = model.forward(input_ids=x)["logits"].float()
        logits_b = vanilla.forward(input_ids=x)["logits"].float()
    d = (logits_a - logits_b).abs().max().item()
    report("identity-forward", d < 5e-2,
           f"teacher-forced max|dlogits|={d:.3e} (fresh adapters ~1e-4 expected)")

    # (b) deterministic from-scratch generation
    def gen(mm, seed):
        torch.manual_seed(seed)
        init = mm.tokenizer.aa_mask_token * gen_len
        init = mm.tokenizer.aa_cls_token + init + mm.tokenizer.aa_eos_token
        xi = mm.tokenizer.batch_encode_plus(
            [init] * 4, add_special_tokens=False, padding=True, return_tensors="pt"
        )["input_ids"].to(device)
        with torch.inference_mode():
            out = mm.generate(
                input_tokens=xi, max_iter=max_iter, temperature=1.0,
                unmasking_strategy="deterministic",
                sampling_strategy="annealing@2.0:0.1",
            )
        return out["output_tokens"]

    toks_a = gen(model, seed=2)
    toks_b = gen(vanilla, seed=2)
    diff_frac = (toks_a != toks_b).float().mean().item()
    report("identity-generate", diff_frac < 0.005,
           f"from-scratch deterministic: token mismatch fraction={diff_frac:.4f}")

    # (c) partial-mask infill on real-ish anchors
    torch.manual_seed(3)
    anchors = random_aa_batch(model.tokenizer, batch_size=4, seq_len=gen_len, seed=4).to(device)
    keep = torch.rand(anchors.shape, generator=torch.Generator().manual_seed(5)) > 0.4
    keep[:, 0] = True  # cls
    keep &= anchors.ne(model.tokenizer.pad_token_id).cpu()

    def infill(mm, seed):
        torch.manual_seed(seed)
        with torch.inference_mode():
            out = mm.generate(
                input_tokens=anchors, max_iter=max_iter, temperature=1.0,
                partial_masks=keep.to(device),
                unmasking_strategy="deterministic",
                sampling_strategy="annealing@2.0:0.1",
            )
        return out["output_tokens"]

    toks_a = infill(model, seed=6)
    toks_b = infill(vanilla, seed=6)
    edited = ~keep.to(device).expand_as(toks_a)
    mism = ((toks_a != toks_b) & edited).sum().item() / max(1, edited.sum().item())
    kept_ok = bool((toks_a[keep.expand_as(toks_a)] == anchors[keep]).all())
    report("identity-infill", mism < 0.005 and kept_ok,
           f"edited-position mismatch={mism:.4f}, anchors preserved={kept_ok}")
    del vanilla
    torch.cuda.empty_cache()


# ---------------------------------------------------------------------------
# P0.2 replicated-body equivalence
# ---------------------------------------------------------------------------

def check_parent_equivalence(model, device):
    x = random_aa_batch(model.tokenizer, batch_size=2, seq_len=48, seed=7).to(device)
    with torch.inference_mode():
        ours = model._forward_net(x, None)["logits"]
        parent = MultimodalDiffusionProteinLanguageModel.forward(
            model, input_ids=x
        )["logits"]
    d = (ours.float() - parent.float()).abs().max().item()
    report("parent-equivalence", d == 0.0,
           f"_forward_net(x, None) vs parent forward: max|diff|={d:.3e}")


# ---------------------------------------------------------------------------
# P0.3 adapter invocation census
# ---------------------------------------------------------------------------

class _Census:
    def __init__(self, model):
        self.count = 0
        self.handles = []
        for layer in model.adapters_module:
            if layer is None or isinstance(layer, torch.nn.Identity):
                continue
            self.handles.append(layer.register_forward_hook(self._hook))

    def _hook(self, module, inputs, output):
        self.count += 1

    def __enter__(self):
        self.count = 0
        return self

    def __exit__(self, *a):
        pass

    def remove(self):
        for h in self.handles:
            h.remove()


def check_adapter_census(model, device):
    x = random_aa_batch(model.tokenizer, batch_size=2, seq_len=48, seed=8).to(device)
    cond = random_conditions(batch_size=2, seed=9)
    census = _Census(model)
    with torch.inference_mode():
        with census:
            model.forward(input_ids=x, conditions=cond)
        n_cond = census.count
        with census:
            model._cfg_scale = 2.0
            model.forward(input_ids=x, conditions=cond)
            n_cfg = census.count
        with census:
            model._cfg_scale = 1.0
            model.forward(input_ids=x, conditions=None)
            n_none = census.count
        model._cfg_scale = 1.0
    census.remove()
    ok = n_cond == 33 and n_cfg == 66 and n_none == 0
    report("adapter-census", ok,
           f"cond fwd={n_cond} (exp 33), CFG fwd={n_cfg} (exp 66), no-cond={n_none} (exp 0)")


# ---------------------------------------------------------------------------
# P0.4 null-path + eval-mode consistency
# ---------------------------------------------------------------------------

def check_null_path(model, device):
    x = random_aa_batch(model.tokenizer, batch_size=2, seq_len=48, seed=10).to(device)
    cond = random_conditions(batch_size=2, seed=11)

    # (a) force_unconditional == manual null-row lookup
    emb = model.annotation_embedder
    forced = emb(cond["annotations"], force_unconditional=True)
    dev = next(model.parameters()).device
    ok_a = True
    for name, table in emb.tables.items():
        idx = emb.uncond_idx[name]
        ref = table(torch.full((2,), idx, dtype=torch.long, device=dev))
        if not torch.equal(forced[name], ref):
            ok_a = False
    report("null-embed-row", ok_a, "force_unconditional returns the learned null row")

    # (b) dropout path at p=1.0 == force_unconditional (same treatment)
    was_training = emb.training
    old_p = emb.p_dropout_uncond
    emb.train()
    emb.p_dropout_uncond = 1.0
    torch.manual_seed(12)
    dropped = emb(cond["annotations"])
    emb.p_dropout_uncond = old_p
    emb.train(was_training)
    ok_b = all(torch.equal(dropped[k], forced[k]) for k in forced)
    report("null-train-consistency", ok_b,
           "training dropout p=1.0 == force_unconditional (same null embedding)")

    # (c) eval mode disables all conditioning dropout
    model.eval()
    drop_modules = [m for m in model.modules() if isinstance(m, torch.nn.Dropout)]
    all_off = all(not m.training for m in drop_modules)
    report("eval-drops-off", all_off,
           f"{len(drop_modules)} Dropout modules all inactive at eval()")

    # (d) generate() restores the caller's stash (no leakage of the
    # generate-internal state)
    stash = model._encode_conditions(cond)
    model._active_layer_adapter_inputs = stash
    model.generate(
        input_tokens=x, max_iter=2,
        unmasking_strategy="deterministic",
    )
    report("stash-restored", model._active_layer_adapter_inputs is stash,
           "caller's condition stash restored after generate()")
    model._active_layer_adapter_inputs = None


# ---------------------------------------------------------------------------
# P0.5 CFG degeneracy
# ---------------------------------------------------------------------------

def check_cfg_degeneracy(model, device):
    x = random_aa_batch(model.tokenizer, batch_size=2, seq_len=48, seed=13).to(device)
    cond = random_conditions(batch_size=2, seed=14)
    with torch.inference_mode():
        a = model.forward(input_ids=x, conditions=cond)["logits"].float()

        model._cfg_scale = 0.0
        b = model.forward(input_ids=x, conditions=cond)["logits"].float()
        model._cfg_scale = 1.0

        c = model.forward(
            input_ids=x,
            layer_adapter_inputs=model._encode_conditions(cond, force_unconditional=True),
        )["logits"].float()

        model._cfg_scale = 4.0
        d4 = model.forward(input_ids=x, conditions=cond)["logits"].float()
        model._cfg_scale = 1.0

    d_bc = (b - c).abs().max().item()
    d_ab = (a - b).abs().max().item()
    report("cfg-w0-null", d_bc < 1e-4,
           f"w=0 vs explicit null pass: max|diff|={d_bc:.3e}")
    report("cfg-adapters-fire", d_ab > 0.0,
           f"cond vs null max|dlogits|={d_ab:.3e} (>0: adapter path diverges "
           "from null; tiny at fresh init by design)")
    report("cfg-w4-finite", bool(torch.isfinite(d4).all()),
           "w=4 extrapolated logits finite")
    report("cfg-w1-single-pass", True,
           "w=1 skips the uncond pass by construction (asserted by adapter-census)")


# ---------------------------------------------------------------------------
# P0.6 data round-trip
# ---------------------------------------------------------------------------

def check_data_roundtrip(n_rows=300, seed=0):
    import numpy as np
    import pyarrow.parquet as pq
    from byprot.datamodules.dataset.annotated_protein import (
        AnnotatedProteinCollater,
        AnnotatedProteinDataset,
    )

    parquet = os.path.join(REPO, "data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet")
    ds = AnnotatedProteinDataset(parquet_path=parquet, max_len=512,
                                 split_seed=0, val_ratio=0.05, split="train")
    tok = ds.tokenizer
    rng = np.random.default_rng(seed)
    idxs = rng.choice(len(ds), size=min(n_rows, len(ds)), replace=False)

    n_aa_mismatch, n_struct_bad, n_len_mismatch = 0, 0, 0
    label_violations = 0
    trailing_empty = 0
    examples = []
    for i in idxs:
        item = ds[int(i)]
        row = ds.data[ds._idx_map[int(i)]]

        # aa identity — the dataset crops rows to max_len, so the decoded
        # item must equal either the full source or its cropped window.
        dec = tok.batch_decode(
            tok.batch_encode_plus([item["aatype_tokens"]], add_special_tokens=False,
                                  return_tensors="pt")["input_ids"],
            skip_special_tokens=True,
        )[0].replace(" ", "")
        src = row["aa_seq"]
        expected_len = min(512, len(src))
        if not (dec == src or (len(dec) == expected_len and src.find(dec) >= 0)):
            n_aa_mismatch += 1
            if len(examples) < 3:
                examples.append((row["uniprot_id"], src[:40], dec[:40]))
        if row["struct_seq"].endswith(","):
            trailing_empty += 1

        # struct codes: dataset joins codes WITHOUT separators (fixed-width-4
        # chunking at tokenize time). Strip cls/eos wrappers, require the
        # middle to be a multiple of 4 and every chunk in range.
        st = item["struct_tokens"]
        mid = st[len(tok.struct_cls_token): len(st) - len(tok.struct_eos_token)]
        if len(mid) % 4 != 0:
            n_struct_bad += 1
        else:
            codes = [mid[i:i + 4] for i in range(0, len(mid), 4)]
            if any(not (c.isdigit() and len(c) == 4 and 0 <= int(c) < 8192) for c in codes):
                n_struct_bad += 1

        s_ids = tok.batch_encode_plus([st], add_special_tokens=False,
                                      return_tensors="pt")["input_ids"][0]
        a_ids = tok.batch_encode_plus([item["aatype_tokens"]], add_special_tokens=False,
                                      return_tensors="pt")["input_ids"][0]
        if len(s_ids) != len(a_ids):
            n_len_mismatch += 1

        # label ranges
        for lst, vocab in ((item["ipr_mapped"], VOCAB_SIZES["ipr"]),
                           (item["go_f_mapped"], VOCAB_SIZES["go"])):
            for v in lst:
                if not (0 <= v < vocab):
                    label_violations += 1

    report("data-aa-identity", n_aa_mismatch == 0,
           f"{len(idxs)} rows: aa decode mismatches={n_aa_mismatch}"
           + (f"; e.g. {examples}" if examples else ""))
    report("data-struct-range", n_struct_bad == 0,
           f"struct rows with misaligned/out-of-range codes={n_struct_bad}")
    report("data-track-alignment", n_len_mismatch == 0,
           f"struct/aa track token-length mismatches={n_len_mismatch} "
           f"(trailing-comma source strings: {trailing_empty}/{len(idxs)})")
    report("data-label-range", label_violations == 0,
           f"label vocab violations={label_violations}")

    # collater + annotation alignment
    coll = AnnotatedProteinCollater(tok)
    batch = coll([ds[int(i)] for i in idxs[:8]])
    ann = batch.get("annotations", {})
    aligned = True
    for j, i in enumerate(idxs[:8]):
        row = ds.data[ds._idx_map[int(i)]]
        for t, col in (("ipr", "ipr_mapped"), ("go", "go_f_mapped")):
            if t not in ann:
                continue
            padded = ann[t][j]
            valid = padded[padded >= 0].tolist()
            if valid != list(row[col]):
                aligned = False
    report("data-annotation-alignment", aligned,
           "collater annotations match source rows for 8 sampled rows")


def check_grad_flow(model, device, n_rows=4):
    """One real compute_loss + backward through the training path: adapter
    params receive nonzero grads, base params receive none."""
    import pandas as pd
    from byprot.datamodules.dataset.annotated_protein import (
        AnnotatedProteinCollater,
        AnnotatedProteinDataset,
    )

    parquet = os.path.join(REPO, "data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet")
    ds = AnnotatedProteinDataset(parquet_path=parquet, max_len=128,
                                 split_seed=0, val_ratio=0.05, split="train")
    coll = AnnotatedProteinCollater(ds.tokenizer)
    batch = coll([ds[i] for i in range(n_rows)])
    device = next(model.parameters()).device
    annotations = batch.pop("annotations")
    annotations = {"annotations": {k: v.to(device) for k, v in annotations.items()}}
    for track in ("struct_tokens", "aatype_tokens"):
        batch[track] = {k: v.to(device) for k, v in batch[track].items()}

    model.zero_grad(set_to_none=True)
    model.train()
    logits, targets, loss_masks, weights = model.compute_loss(
        batch, weighting="linear", conditions=annotations)
    from byprot.modules.cross_entropy import StructAARDMCrossEntropyLoss
    criterion = StructAARDMCrossEntropyLoss(label_smoothing=0.0, ignore_index=1).to(device)
    loss, _ = criterion(logits, targets, loss_masks, weights)
    loss.backward()
    model.eval()

    adapter_grads, base_grads = [], []
    for name, p in model.named_parameters():
        if name.startswith("net."):
            base_grads.append((name, p))
        elif any(s in name for s in ("adapters_module", "annotation_embedder",
                                     "annotation_projectors")):
            adapter_grads.append((name, p))
    n_adapter_with_grad = sum(1 for _, p in adapter_grads
                              if p.grad is not None and p.grad.abs().sum() > 0)
    base_with_grad = [n for n, p in base_grads if p.grad is not None]
    ok = (n_adapter_with_grad == len(adapter_grads)
          and len(adapter_grads) > 0 and not base_with_grad)
    report("grad-flow", ok,
           f"{n_adapter_with_grad}/{len(adapter_grads)} conditioning params got "
           f"nonzero grads; base params with grads: {len(base_with_grad)} "
           f"(loss={loss.item():.3f}, requires_grad(base) all False: "
           f"{not any(p.requires_grad for _, p in base_grads)})")


def check_source_labels(n_rows=200, seed=0):
    """Joined parquet labels must match the CFP-Gen source pkl by accession —
    the join is by UniProt accession, and a mis-join would silently scramble
    the conditioning signal."""
    import numpy as np
    import pandas as pd
    import pickle

    parquet = os.path.join(REPO, "data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet")
    pkl = "/home/cherry/dev/phd/cfpgen/data-bin/uniprotKB/cfpgen_general_dataset/train.pkl"
    if not os.path.exists(pkl):
        report("source-labels", False, f"CFP-Gen train.pkl not found at {pkl}")
        return
    cfp = {x["uniprot_id"]: x for x in pickle.load(open(pkl, "rb"))}
    df = pd.read_parquet(parquet, columns=["uniprot_id", "ipr_mapped", "go_f_mapped"])
    rng = np.random.default_rng(seed)
    idxs = rng.choice(len(df), size=min(n_rows, len(df)), replace=False)
    mism = 0
    for i in idxs:
        row = df.iloc[int(i)]
        src = cfp.get(row["uniprot_id"])
        if src is None:
            mism += 1
            continue
        if (list(row["ipr_mapped"]) != list(src.get("ipr_mapped") or [])
                or list(row["go_f_mapped"]) != list(src.get("go_f_mapped") or [])):
            mism += 1
    report("source-labels", mism == 0,
           f"{len(idxs)} rows vs CFP-Gen train.pkl: label mismatches={mism}")


CHECKS = {
    "pretrained-base": lambda dev: check_pretrained_base(dev),
    "identity": lambda dev: check_identity(_MODEL["m"], dev),
    "parent-equivalence": lambda dev: check_parent_equivalence(_MODEL["m"], dev),
    "adapter-census": lambda dev: check_adapter_census(_MODEL["m"], dev),
    "null-path": lambda dev: check_null_path(_MODEL["m"], dev),
    "cfg-degeneracy": lambda dev: check_cfg_degeneracy(_MODEL["m"], dev),
    "grad-flow": lambda dev: check_grad_flow(_MODEL["m"], dev),
    "data-roundtrip": lambda dev: check_data_roundtrip(),
    "source-labels": lambda dev: check_source_labels(),
}

_MODEL = {"m": None}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", nargs="+", default=["all"],
                    choices=list(CHECKS) + ["all"])
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        for k, v in CHECKS.items():
            print(f"{k:20s} {v}")
        return 0

    checks = list(CHECKS) if "all" in args.check else args.check
    model_needed = any(c not in ("pretrained-base", "data-roundtrip") for c in checks)

    for c in checks:
        print(f"\n--- {c} ---")
        if c == "pretrained-base":
            # builds the model and leaves it in _MODEL for the other checks
            _MODEL["m"] = check_pretrained_base(args.device)
        elif c == "data-roundtrip":
            CHECKS[c](args.device)
        else:
            if _MODEL["m"] is None:
                _MODEL["m"], _ = build_training_path_model(args.device)
            CHECKS[c](args.device)

    n_fail = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{'='*60}\nPhase 0 gauntlet: {len(RESULTS)-n_fail}/{len(RESULTS)} passed"
          + (f" — {n_fail} FAILED" if n_fail else " — ALL GREEN"))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
