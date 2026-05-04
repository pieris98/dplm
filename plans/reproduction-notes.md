# Reproduction Notes

## Checkout

- Repository: `/home/cherry/dev/phd/dplm`
- Commit: `8a2e15e53416b4536f03f79ad1f6f6a9cbd5e19d`
- Working tree at phase 0 start: dirty; unrelated local edits and data artifacts were already present.

## Environment

- Conda environment: `dplm`
- Activation command: `source /home/cherry/miniconda/bin/activate dplm`
- Python: `3.9.23`
- PyTorch: `2.2.0+cu121`
- Key package versions observed by `scripts/reproduce/check_env.sh`:
  - `transformers==4.39.2`
  - `esm==2.0.0`
  - `mdtraj==1.10.3`
  - `biopython==1.79`
  - `fairscale==0.4.6`
  - `byprot` and `openfold`: importable local packages, version metadata unavailable
- Hardware observed with escalated CUDA access: `1 x NVIDIA GeForce RTX 3090`
- Note: sandboxed checks may report `cuda_available=False`; the escalated smoke run reported `cuda_available=True`.
- Phase 0 environment fix applied: installed repo-declared dependency `fairscale==0.4.6` into the `dplm` conda environment.
- Phase 0 preflight: `bash scripts/reproduce/check_env.sh`
- Full model-load smoke test: `RUN_MODEL_SMOKE=1 bash scripts/reproduce/check_env.sh`

Record package and hardware details by rerunning:

```bash
source /home/cherry/miniconda/bin/activate dplm
python - <<'PY'
import importlib
import platform
import torch

print("platform:", platform.platform())
print("python:", platform.python_version())
for name in ["torch", "transformers", "esm", "openfold", "fairscale", "mdtraj", "Bio", "byprot"]:
    module = importlib.import_module(name)
    print(f"{name}:", getattr(module, "__version__", "unknown"))
print("cuda_available:", torch.cuda.is_available())
print("cuda_device_count:", torch.cuda.device_count() if torch.cuda.is_available() else 0)
for idx in range(torch.cuda.device_count() if torch.cuda.is_available() else 0):
    print(f"cuda_device[{idx}]:", torch.cuda.get_device_name(idx))
PY
```

## Pretrained Artifacts

- DPLM-2: `airkingbd/dplm2_650m`
- DPLM-2 Bit: `airkingbd/dplm2_bit_650m`
- Structure tokenizer: `airkingbd/struct_tokenizer`

## External Tools

- Required for phase 1 folding evaluation: `analysis/TMscore`, `analysis/TMalign`
- Optional later phases: `foldseek`, `colabfold_batch`, `vendor/ProteinMPNN`
