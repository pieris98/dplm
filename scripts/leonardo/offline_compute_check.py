#!/usr/bin/env python
"""Offline validation for Leonardo compute nodes.

The check intentionally avoids network calls in offline mode. It verifies local
imports, DPLM-2 Hydra config composition, required data paths, Hugging Face cache
state, and optionally loads cached checkpoints.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = (
    ("airkingbd/dplm_650m", "model"),
    ("airkingbd/dplm2_650m", "model"),
    ("airkingbd/dplm2_bit_650m", "model"),
    ("airkingbd/struct_tokenizer", "model"),
)


def ok(message: str) -> None:
    print(f"OK: {message}")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["online", "offline"], default="offline")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--skip-heavy-model-load", action="store_true")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("DPLM_DATA_DIR", str(ROOT / "data-bin")),
        help="Data directory to verify.",
    )
    return parser.parse_args()


def require_imports() -> None:
    modules = [
        "torch",
        "lightning",
        "pytorch_lightning",
        "transformers",
        "datasets",
        "huggingface_hub",
        "esm",
        "fairscale",
        "mdtraj",
        "Bio",
        "byprot",
        "openfold",
    ]
    for name in modules:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "unknown")
        ok(f"import {name} version={version}")


def require_offline_env(mode: str) -> None:
    if mode != "offline":
        return
    required = {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    }
    for key, expected in required.items():
        actual = os.environ.get(key)
        if actual != expected:
            fail(f"{key}={actual!r}; expected {expected!r}")
        ok(f"{key}={actual}")


def require_data(data_dir: Path) -> None:
    required = [
        data_dir / "pdb_swissprot",
    ]
    recommended = [
        data_dir / "cameo2022" / "aatype.fasta",
        data_dir / "cameo2022" / "struct.fasta",
        data_dir / "metadata" / "pdb_afdb_cameo.csv",
    ]
    for path in required:
        if not path.exists():
            fail(f"required data path missing: {path}")
        ok(f"required data path exists: {path}")
    for path in recommended:
        if path.exists() and path.stat().st_size > 0:
            ok(f"evaluation file exists: {path}")
        else:
            print(f"WARN: evaluation file missing or empty: {path}", file=sys.stderr)


def require_hf_cache() -> None:
    from huggingface_hub import scan_cache_dir

    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    if not hf_home.exists():
        fail(f"HF_HOME does not exist: {hf_home}")
    ok(f"HF_HOME exists: {hf_home}")

    cache_info = scan_cache_dir()
    repos = {repo.repo_id for repo in cache_info.repos}
    missing = [repo_id for repo_id, _ in DEFAULT_MODELS if repo_id not in repos]
    if missing:
        print("WARN: these repos were not found by scan_cache_dir:", file=sys.stderr)
        for repo_id in missing:
            print(f"  - {repo_id}", file=sys.stderr)
        local_root = Path(os.environ.get("DPLM_INSTALL_ROOT", ROOT)) / "models"
        for repo_id in missing:
            mirror = local_root / repo_id.replace("/", "__")
            if not mirror.exists():
                fail(f"model not found in HF cache or local mirror: {repo_id}")
            ok(f"model mirror exists: {mirror}")
    else:
        ok("all expected model repos found in Hugging Face cache")


def require_hydra_config() -> None:
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf

    config_dir = str(ROOT / "configs")
    with initialize_config_dir(config_dir=config_dir, version_base=None):
        cfg = compose(
            config_name="config",
            overrides=[
                "experiment=dplm2/dplm2_650m",
                "trainer=ddp_bf16",
                "logger=tensorboard",
                "datamodule.max_tokens=8192",
                "trainer.devices=4",
                "trainer.accumulate_grad_batches=2",
                "trainer.max_steps=2",
                "model.net.name=airkingbd/dplm_650m",
                "model.net.pretrained_model_name_or_path=airkingbd/dplm_650m",
                "model.training_stage=train_from_dplm",
            ],
        )
    resolved = OmegaConf.to_container(cfg, resolve=False)
    model = resolved["model"]
    if model["training_stage"] != "train_from_dplm":
        fail("Hydra config did not preserve model.training_stage=train_from_dplm")
    ok("Hydra DPLM-2 training config composes")


def resolve_device(requested: str):
    import torch

    if requested == "cuda":
        if not torch.cuda.is_available():
            fail("--device cuda requested but CUDA is unavailable")
        return torch.device("cuda")
    if requested == "auto" and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def load_cached_models(device_name: str) -> None:
    import torch

    device = resolve_device(device_name)
    ok(f"model-load device={device}")

    from byprot.models.dplm2 import DPLM2Bit
    from byprot.models.dplm2 import MultimodalDiffusionProteinLanguageModel as DPLM2
    from byprot.models.utils import get_struct_tokenizer

    targets = [
        ("airkingbd/dplm2_650m", lambda name: DPLM2.from_pretrained(name)),
        ("airkingbd/dplm2_bit_650m", lambda name: DPLM2Bit.from_pretrained(name)),
        ("airkingbd/struct_tokenizer", get_struct_tokenizer),
    ]
    for name, loader in targets:
        model = loader(name).to(device).eval()
        params = sum(p.numel() for p in model.parameters())
        ok(f"loaded {name} params={params:,}")
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def main() -> int:
    args = parse_args()
    os.chdir(ROOT)

    require_offline_env(args.mode)
    require_imports()
    require_data(Path(args.data_dir))
    require_hf_cache()
    require_hydra_config()

    if args.skip_heavy_model_load:
        ok("skipped heavy model load")
    else:
        load_cached_models(args.device)

    ok("offline compute validation completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
