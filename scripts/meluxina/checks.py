"""Interactive environment checks for the Meluxina conditional-DPLM2 job.

Run INSIDE the container (via scripts/meluxina/interactive.sh check) before
trusting the sbatch scripts. Verifies every precondition the smoke/full-run
sbatch path depends on; exits non-zero if any hard check fails.

Usage (from the login node — it allocates a GPU itself):
    scripts/meluxina/interactive.sh check
Or from inside an existing allocation:
    scripts/meluxina/interactive.sh check
"""
import os
import sys

# Offline HF before any HF import: models are baked into the image; without
# this, from_pretrained does etag checks that stall on egress-less nodes.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

RESULTS = []


def check(name, fn, required=True):
    try:
        detail = fn()
        RESULTS.append((True, name, detail))
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""), flush=True)
    except Exception as e:
        RESULTS.append((not required, name, f"{type(e).__name__}: {e}"))
        tag = "FAIL" if required else "WARN"
        print(f"{tag}  {name}  [{type(e).__name__}: {e}]", flush=True)


def c_python():
    assert sys.executable == "/opt/venv/bin/python", sys.executable
    return sys.version.split()[0]


def c_torch_cuda():
    import torch
    n = torch.cuda.device_count()
    assert torch.cuda.is_available(), "CUDA not available"
    assert n > 0, "no GPUs visible"
    return f"torch {torch.__version__}, cuda {torch.version.cuda}, {n} GPU(s)"


def c_dataset():
    import pyarrow.parquet as pq
    t = pq.read_table(
        "/workspace/dplm/data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet",
        columns=["uniprot_id"],
    )
    assert t.num_rows == 45696, f"rows={t.num_rows}, expected 45696"
    return f"{t.num_rows} rows"


def c_imports():
    # This import chain pulls in vendor/openfold -> attn_core_inplace_cuda
    # (the compiled kernel that broke under whole-repo binds).
    from byprot.models.dplm2 import ConditionalDPLM2  # noqa: F401
    from byprot.datamodules.dataset.annotated_protein import (  # noqa: F401
        AnnotatedProteinDataset, AnnotatedProteinCollater)
    from byprot.tasks.lm.conditional_dplm2 import (  # noqa: F401
        ConditionalDPLM2TrainingTask)
    return "ConditionalDPLM2 + dataset + task import (incl. openfold kernels)"


def c_hf_cache():
    from huggingface_hub import snapshot_download
    p = snapshot_download("airkingbd/dplm2_650m", local_files_only=True)
    return p


def c_wandb_egress():
    import socket
    socket.create_connection(("api.wandb.ai", 443), timeout=5).close()
    return "api.wandb.ai reachable (online logging OK)"


def c_write_binds():
    out = []
    for d in ("/workspace/dplm/logs", "/workspace/dplm/wandb",
              "/workspace/dplm/generation-results"):
        p = os.path.join(d, ".write_check")
        with open(p, "w") as f:
            f.write("x")
        os.unlink(p)
        out.append(d.rsplit("/", 1)[1])
    return "writable: " + ", ".join(out)


def c_model_forward():
    """The heavyweight end-to-end check: 650M load from image cache + GPU
    forward with conditions (proves HF cache, GPU, and our model code)."""
    import torch
    from byprot.models.dplm2 import ConditionalDPLM2
    cfg = {"conditioning": {
        "annotations": {"vocab_sizes": {"ipr": 1154, "go": 375},
                        "embed_dim": 128, "p_dropout_uncond": 0.0},
        "adapter": {"c_s": 128, "c_hidden": 16, "weight_init": 1e-5},
        "external": {"enable": False}, "freeze_base": True}}
    m = ConditionalDPLM2.from_pretrained("airkingbd/dplm2_650m",
                                         cfg_override=cfg).eval().cuda()
    x = torch.randint(0, 33, (2, 32), device="cuda")
    cond = {"annotations": {"ipr": torch.tensor([[1, 2, -1]]),
                            "go": torch.tensor([[3, -1, -1]])}}
    with torch.no_grad():
        out = m.forward(input_ids=x, conditions=cond)
    assert torch.isfinite(out["logits"]).all()
    n_train = sum(p.numel() for p in m.parameters() if p.requires_grad)
    return (f"logits {tuple(out['logits'].shape)} finite; "
            f"{n_train/1e6:.1f}M trainable; base frozen="
            f"{all(not p.requires_grad for p in m.net.parameters())}")


def main():
    print(f"interpreter: {sys.executable}")
    check("python interpreter is the image venv", c_python)
    check("torch + CUDA visible", c_torch_cuda)
    check("joined dataset present (45696 rows)", c_dataset)
    check("byprot/openfold imports (compiled kernels)", c_imports)
    check("HF model cache (airkingbd/dplm2_650m)", c_hf_cache)
    check("wandb egress", c_wandb_egress, required=False)
    check("output binds writable", c_write_binds)
    print("loading 650M model from image cache (1-2 min) ...", flush=True)
    check("model loads + GPU forward with conditions", c_model_forward)

    n_hard_fail = sum(1 for ok, name, _ in RESULTS if not ok)
    print()
    if n_hard_fail == 0:
        print("ALL CHECKS PASSED — the sbatch scripts should work.")
        sys.exit(0)
    print(f"{n_hard_fail} HARD FAILURE(S) — fix before submitting sbatch.")
    sys.exit(1)


if __name__ == "__main__":
    main()
