# Root `env.yml` comparison

Checked against the active local conda environment `dplm` on 2026-06-08 while
building the Docker lock in `docker/dplm-pip-freeze.txt`.

## Decision

Do not install the root `env.yml` into the Docker image. It is a legacy
environment for Python 3.7, Torch 1.12, and CUDA 11.3-era packages, while the
active `dplm` environment uses Python 3.9.23, Torch 2.2.0+cu121, and a CUDA
12.1 PyTorch runtime.

The Docker image follows the active conda environment, not `env.yml`.

## Major version conflicts

- Python: `env.yml=3.7.16`, active env `3.9.23`
- pip: `env.yml=22.3.1`, active env `25.2`
- setuptools: `env.yml=65.6.3`, active env `80.9.0`
- wheel: `env.yml=0.38.4`, active env `0.45.1`
- torch: `env.yml=1.12.0`, active env `2.2.0+cu121`
- torchtext: `env.yml=0.13.0`, active env `0.17.0`
- torchdata: `env.yml=0.4.0`, active env `0.7.1`
- pytorch-lightning: `env.yml=1.7.3`, active env `2.2.0`
- torch-geometric: `env.yml=2.3.1`, active env `2.6.1`
- torch-scatter: `env.yml=2.1.1`, active env `2.1.2`
- numpy: `env.yml=1.21.6`, active env `1.26.4`
- pandas: `env.yml=1.3.5`, active env `2.3.3`
- scipy: `env.yml=1.7.3`, active env `1.13.1`
- pydantic: `env.yml=1.10.9`, active env `2.13.3`
- protobuf: `env.yml=3.20.3`, active env `6.33.6`
- transformers: same major line is used, but active env remains the authority:
  `env.yml` does not pin every transitive package used by the current install.

The structured comparison found 114 pip version conflicts in total.

## Packages present in `env.yml` but absent from active `dplm`

The structured comparison found 82 missing package names. The notable groups
are:

- Legacy CUDA 11.3 / Torch 1.12 graph stack: `dgl-cu113`, `dglgo`,
  `torch-cluster`, `torch-sparse`, `torch-spline-conv`
- Legacy distributed stack: `byted-torch`, `byteps`
- Optional chemistry/graph extras: `rdkit-pypi`, `ogb`
- Notebook and documentation tooling: `ipykernel`, `ipython`, `jupyter-client`,
  `jupyter-server`, `notebook`, `nbconvert`, `sphinx`, `numpydoc`, and related
  transitive packages

Source scan result: these legacy-only package names were not imported by the
main repo code paths under `src`, `run`, `scripts`, top-level training/generate
entrypoints, or tests. They appear in `env.yml`, notebook references, or docs.

## Docker implication

The Dockerfile intentionally keeps:

- `torch==2.2.0+cu121`
- `torchvision==0.17.0+cu121`
- `torchaudio==2.2.0+cu121`
- the active pip freeze from `docker/dplm-pip-freeze.txt`
- local `ByProt` installed editable with `--no-build-isolation`
- local `vendor/openfold` built from the checkout with `--no-build-isolation`

Adding the missing `env.yml` graph/distributed packages directly would mix
CUDA 11.3/Torch 1.12 assumptions into a CUDA 12.1/Torch 2.2 image.
