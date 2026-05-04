#!/usr/bin/env python
"""Load released DPLM-2 checkpoints before running reproduction jobs."""

from __future__ import annotations

import argparse
import gc
import sys
import time
from dataclasses import dataclass

import torch


@dataclass(frozen=True)
class Target:
    name: str
    kind: str


DEFAULT_TARGETS = (
    Target("airkingbd/dplm2_650m", "dplm2"),
    Target("airkingbd/dplm2_bit_650m", "dplm2_bit"),
    Target("airkingbd/struct_tokenizer", "struct_tokenizer"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        action="append",
        choices=[target.kind for target in DEFAULT_TARGETS],
        help="Target kind to load. May be repeated. Defaults to all targets.",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="Move loaded models to this device when possible.",
    )
    return parser.parse_args()


def resolve_device(requested: str) -> torch.device:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("--device cuda requested but CUDA is not available")
        return torch.device("cuda")
    if requested == "auto" and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def parameter_count(model: torch.nn.Module) -> int:
    return sum(param.numel() for param in model.parameters())


def load_target(target: Target, device: torch.device) -> None:
    start = time.monotonic()
    print(f"Loading {target.name} ({target.kind})...")

    if target.kind == "dplm2":
        from byprot.models.dplm2 import MultimodalDiffusionProteinLanguageModel

        model = MultimodalDiffusionProteinLanguageModel.from_pretrained(target.name)
        model = model.to(device).eval()
        print(f"OK: {target.name} params={parameter_count(model):,} device={device}")
    elif target.kind == "dplm2_bit":
        from byprot.models.dplm2 import DPLM2Bit

        model = DPLM2Bit.from_pretrained(target.name)
        model = model.to(device).eval()
        print(f"OK: {target.name} params={parameter_count(model):,} device={device}")
    elif target.kind == "struct_tokenizer":
        from byprot.models.utils import get_struct_tokenizer

        model = get_struct_tokenizer(target.name).to(device).eval()
        print(f"OK: {target.name} params={parameter_count(model):,} device={device}")
    else:
        raise ValueError(f"Unknown target kind: {target.kind}")

    elapsed = time.monotonic() - start
    print(f"OK: loaded {target.name} in {elapsed:.1f}s")
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def main() -> int:
    args = parse_args()
    selected_kinds = set(args.target or [target.kind for target in DEFAULT_TARGETS])
    targets = [target for target in DEFAULT_TARGETS if target.kind in selected_kinds]
    device = resolve_device(args.device)

    print(f"Python: {sys.version.split()[0]}")
    print(f"Torch: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    print(f"Smoke-test device: {device}")

    for target in targets:
        load_target(target, device)

    print("OK: all requested model-load smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
