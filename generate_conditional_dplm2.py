"""Conditional generation for ConditionalDPLM2.

Loads a trained ConditionalDPLM2 checkpoint (Lightning .ckpt or fresh
from_pretrained), builds generation prompts for target function annotations
(GO / IPR label IDs), and generates protein sequences (and optionally
structures via co-generation) conditioned on those labels.

Outputs aatype.fasta (and struct_token.fasta for co-generation) in the same
format as generate_dplm2.py, so downstream evaluators work unchanged.

Examples
--------
Generate 8 sequences of length 128 conditioned on GO label 5, from a trained
checkpoint:

    python generate_conditional_dplm2.py \
        --ckpt logs/cond_dplm2_smoke_200step/checkpoints/step_199.0-loss_2.33.ckpt \
        --labels go:5 --num-seqs 8 --seq-len 128 \
        --saveto generation-results/cond_smoke

Unconditional baseline (null condition — measures the base model's prior):

    python generate_conditional_dplm2.py \
        --ckpt ... --labels null --num-seqs 8 --seq-len 128 --saveto ...

Co-generation (sequence + structure tokens):

    python generate_conditional_dplm2.py --ckpt ... --labels go:5 --task co_generation ...

Multiple labels (multi-label condition, as in CFP-Gen):

    python generate_conditional_dplm2.py --ckpt ... --labels go:5,27 --labels ipr:10,11,12 ...
"""
import argparse
import os

import torch
from omegaconf import OmegaConf
from Bio import SeqIO  # noqa: F401  (kept parity with generate_dplm2.py)
from tqdm import tqdm

from byprot.models.dplm2 import ConditionalDPLM2


# ---------------------------------------------------------------------------
# Checkpoint loading
# ---------------------------------------------------------------------------

def load_conditional_model(ckpt_path, device):
    """Load ConditionalDPLM2 from a Lightning checkpoint or fresh HF weights.

    Lightning checkpoints store the full task-module state under a
    ``model.`` prefix (TaskLitModule wraps the model as ``self.model``),
    and the model config (incl. ``conditioning``) in
    ``hyper_parameters.model``. We read the conditioning config from the
    ckpt so the script is self-configuring, then load base weights via
    from_pretrained and the trained adapter weights on top.
    """
    cfg_override = {}
    stripped = None
    if ckpt_path:
        if not os.path.exists(ckpt_path):
            raise FileNotFoundError(f"Checkpoint not found: {ckpt_path}")
        ckpt = torch.load(ckpt_path, map_location="cpu")
        model_hp = ckpt.get("hyper_parameters", {}).get("model", None)
        if model_hp is not None and "conditioning" in model_hp:
            cfg_override = {"conditioning": OmegaConf.to_container(
                model_hp["conditioning"], resolve=True)}
            print(f"Using conditioning config from ckpt: "
                  f"vocab_sizes={cfg_override['conditioning']['annotations']['vocab_sizes']}")
        # Strip the Lightning ``model.`` prefix.
        stripped = {}
        for k, v in ckpt["state_dict"].items():
            if k.startswith("model."):
                stripped[k[len("model."):]] = v

    model = ConditionalDPLM2.from_pretrained(
        "airkingbd/dplm2_650m", cfg_override=cfg_override)

    if stripped is not None:
        missing, unexpected = model.load_state_dict(stripped, strict=False)
        # Base weights come from from_pretrained; only adapter/embedder/
        # projector keys matter. Verify nothing trainable is missing.
        trainable_missing = [
            k for k in missing
            if k.startswith(("annotation_embedder",
                             "annotation_projectors",
                             "adapters_module",
                             "external_projector"))
        ]
        if trainable_missing:
            raise RuntimeError(
                f"Checkpoint is missing trainable (adapter) keys: "
                f"{trainable_missing[:5]} ... ({len(trainable_missing)} total)"
            )
        n_loaded = sum(1 for k in stripped
                       if k.startswith(("annotation_embedder",
                                        "annotation_projectors",
                                        "adapters_module",
                                        "external_projector")))
        print(f"Loaded {n_loaded} trained adapter/embedder/projector keys "
              f"from {ckpt_path}")
        if unexpected:
            print(f"  (ignored {len(unexpected)} unexpected keys)")
    else:
        print("No --ckpt given; using freshly-initialized adapters "
              "(near-zero — output ≈ unconditional base DPLM-2).")

    model = model.eval().to(device)
    return model


# ---------------------------------------------------------------------------
# Condition parsing
# ---------------------------------------------------------------------------

def parse_labels(label_args, required_types=None):
    """Parse ['go:5,27', 'ipr:10,11,12'] into {'go': tensor[[5,27,-1]], ...}.

    Returns a conditions dict of the shape AnnotationEmbedder expects:
    each type gets a [1, max_labels] LongTensor with -1 padding.

    If ``required_types`` is given (the model's trained annotation types),
    any type the user did NOT specify is filled with a single valid dummy
    label ``0`` — not the null token — so the embedder's per-type summation
    contributes that type's label-0 embedding. NOTE: supplying only a
    subset of types means the unspecified types still condition generation
    on label 0. For a truly unconditional run use ``--labels null``.
    """
    if not label_args or label_args == ["null"] or label_args[0] == "null":
        return None
    per_type = {}
    for spec in label_args:
        if ":" not in spec:
            raise ValueError(f"Bad label spec {spec!r}; expected 'type:id,id,...'")
        tname, ids_str = spec.split(":", 1)
        ids = [int(x) for x in ids_str.split(",") if x.strip()]
        if not ids:
            raise ValueError(f"Empty label list in {spec!r}")
        per_type[tname] = ids
    # Fill unspecified trained types with dummy label 0 so AnnotationEmbedder
    # doesn't KeyError (it requires every trained type to be present).
    for tname in (required_types or []):
        per_type.setdefault(tname, [0])
    tensors = {}
    for tname, ids in per_type.items():
        max_len = len(ids)
        t = torch.full((1, max_len), -1, dtype=torch.long)
        t[0, : len(ids)] = torch.tensor(ids, dtype=torch.long)
        tensors[tname] = t
    return {"annotations": tensors}


# ---------------------------------------------------------------------------
# Prompt construction (mirrors generate_dplm2.initialize_generation)
# ---------------------------------------------------------------------------

def build_generation_batch(task, num_seqs, seq_len, tokenizer, device,
                           batch_size):
    """Build initial all-mask input tokens, batched. Returns list of batches."""
    def create_init_seq(length):
        if task == "sequence_generation":
            aa = tokenizer.aa_mask_token * length
            aa = tokenizer.aa_cls_token + aa + tokenizer.aa_eos_token
            return None, aa
        elif task in ("co_generation", "backbone_generation"):
            # tokenizer.all_tokens[50] is the struct <mask> equivalent used
            # by generate_dplm2.py for co-generation init.
            struct = tokenizer.all_tokens[50] * length
            struct = tokenizer.struct_cls_token + struct + tokenizer.struct_eos_token
            aa = "A" * length  # placeholder aa (unmasked only at init)
            aa = tokenizer.aa_cls_token + aa + tokenizer.aa_eos_token
            return struct, aa
        else:
            raise NotImplementedError(f"task {task!r}")

    batches = []
    for start in range(0, num_seqs, batch_size):
        n = min(batch_size, num_seqs - start)
        structs, aas = [], []
        for _ in range(n):
            s, a = create_init_seq(seq_len)
            structs.append(s)
            aas.append(a)
        if task == "sequence_generation":
            encoded = tokenizer.batch_encode_plus(
                aas, add_special_tokens=False, padding=True,
                return_tensors="pt",
            )["input_ids"]
        else:
            enc_s = tokenizer.batch_encode_plus(
                structs, add_special_tokens=False, padding=True,
                return_tensors="pt",
            )["input_ids"]
            enc_a = tokenizer.batch_encode_plus(
                aas, add_special_tokens=False, padding=True,
                return_tensors="pt",
            )["input_ids"]
            encoded = torch.concat([enc_s, enc_a], dim=1)
        batches.append(encoded.to(device))
    return batches


# ---------------------------------------------------------------------------
# Output saving (same format as generate_dplm2.save_results)
# ---------------------------------------------------------------------------

def save_fasta(path, headers, seqs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for h, s in zip(headers, seqs):
            f.write(f">{h}\n{s}\n")


def save_outputs(outputs, task, tokenizer, save_dir, cond_tag, save_pdb,
                 struct_tokenizer=None):
    os.makedirs(save_dir, exist_ok=True)
    output_tokens = outputs["output_tokens"]
    headers = [f"{cond_tag}_sample{i}" for i in range(output_tokens.shape[0])]

    if task == "sequence_generation":
        aatype_tokens = output_tokens
        aa_strings = [
            "".join(s.split()) for s in tokenizer.batch_decode(
                aatype_tokens, skip_special_tokens=True)
        ]
        save_fasta(os.path.join(save_dir, "aatype.fasta"), headers, aa_strings)
        print(f"  saved {len(aa_strings)} sequences to {save_dir}/aatype.fasta")
        return aa_strings

    # co_generation / backbone_generation: split struct|aa halves
    struct_tokens, aatype_tokens = output_tokens.chunk(2, dim=-1)
    struct_strings = [
        ",".join(s.split()) for s in tokenizer.batch_decode(
            struct_tokens, skip_special_tokens=True)
    ]
    aa_strings = [
        "".join(s.split()) for s in tokenizer.batch_decode(
            aatype_tokens, skip_special_tokens=True)
    ]
    save_fasta(os.path.join(save_dir, "struct_token.fasta"), headers, struct_strings)
    save_fasta(os.path.join(save_dir, "aatype.fasta"), headers, aa_strings)
    print(f"  saved {len(aa_strings)} sequences (+struct tokens) to {save_dir}")

    if save_pdb and struct_tokenizer is not None:
        pdb_dir = os.path.join(save_dir, "pdb")
        os.makedirs(pdb_dir, exist_ok=True)
        for idx, (h, aa_str, st_str) in enumerate(
                zip(headers, aa_strings, struct_strings)):
            aatype_t, struct_t = struct_tokenizer.string_to_tensor(aa_str, st_str)
            decoder_out = struct_tokenizer.detokenize(struct_t)
            decoder_out["aatype"] = aatype_t
            decoder_out["header"] = [h]
            struct_tokenizer.output_to_pdb(decoder_out, output_dir=pdb_dir)
        print(f"  saved PDBs to {pdb_dir}")
    return aa_strings


# ---------------------------------------------------------------------------
# Main generation loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckpt", type=str, default=None,
                        help="Lightning .ckpt with trained adapters; omit for "
                             "fresh near-zero adapters (≈ unconditional base)")
    parser.add_argument("--labels", type=str, action="append", default=None,
                        help="Condition spec, e.g. --labels go:5 or "
                             "--labels ipr:10,11,12. Use 'null' for the "
                             "unconditional baseline.")
    parser.add_argument("--task", type=str, default="sequence_generation",
                        choices=["sequence_generation", "co_generation",
                                 "backbone_generation"])
    parser.add_argument("--num-seqs", type=int, default=8)
    parser.add_argument("--seq-len", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-iter", type=int, default=None,
                        help="Denoising steps; default = seq_len (full)")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--unmasking-strategy", type=str, default="stochastic1.0")
    parser.add_argument("--sampling-strategy", type=str, default="annealing@2.0:0.1")
    parser.add_argument("--saveto", type=str, default="generation-results/cond_dplm2")
    parser.add_argument("--save-pdb", action="store_true",
                        help="Decode struct tokens to PDB (co_generation only)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = load_conditional_model(args.ckpt, device)
    tokenizer = model.tokenizer

    conditions = parse_labels(args.labels, required_types=model.annotation_types)
    if conditions is None:
        cond_tag = "uncond"
        print("Mode: UNCONDITIONAL baseline (null condition)")
    else:
        tag_parts = [f"{t}-{'_'.join(map(str, v[0].tolist()))}"
                     for t, v in conditions["annotations"].items()]
        cond_tag = "cond_" + "+".join(p.replace(" ", "") for p in tag_parts)
        print(f"Mode: CONDITIONAL — {cond_tag}")

    max_iter = args.max_iter or args.seq_len
    save_dir = os.path.join(args.saveto, args.task, f"len_{args.seq_len}", cond_tag)
    print(f"Output dir: {save_dir}")
    print(f"Task={args.task}  num_seqs={args.num_seqs}  seq_len={args.seq_len}  "
          f"max_iter={max_iter}")

    batches = build_generation_batch(
        args.task, args.num_seqs, args.seq_len, tokenizer, device,
        args.batch_size,
    )

    struct_tokenizer = model.struct_tokenizer if args.save_pdb else None

    all_outputs = None
    for bi, input_tokens in enumerate(tqdm(batches, desc="Generating")):
        with torch.inference_mode(), torch.cuda.amp.autocast(
                dtype=torch.bfloat16):
            outputs = model.generate(
                input_tokens=input_tokens,
                conditions=conditions,
                max_iter=max_iter,
                temperature=args.temperature,
                unmasking_strategy=args.unmasking_strategy,
                sampling_strategy=args.sampling_strategy,
            )
        if all_outputs is None:
            all_outputs = outputs
        else:
            for k in all_outputs:
                if k in outputs:
                    all_outputs[k] = torch.concat(
                        [all_outputs[k], outputs[k]], dim=0)

    seqs = save_outputs(
        all_outputs, args.task, tokenizer, save_dir, cond_tag,
        args.save_pdb, struct_tokenizer,
    )
    print("\nSample output (first 2):")
    for s in seqs[:2]:
        print(f"  {s[:80]}{'...' if len(s) > 80 else ''}")


if __name__ == "__main__":
    main()
