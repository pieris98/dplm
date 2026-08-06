# DPLM cloud/HPC image.
#
# Environment captured from local conda env `dplm` on 2026-06-08,
# then reproduced in this image with a plain Python virtualenv:
#   Python: 3.9.23
#   PyTorch: 2.2.0+cu121
#   PyTorch CUDA runtime: 12.1
#   cuDNN reported by torch: 8902
#   NVCC/toolkit in conda env: CUDA 12.9, V12.9.86
#   Local editable pip package: ByProt from /home/cherry/dev/phd/dplm
#   Local third-party build: vendor/openfold, installed from the local checkout
#   Root env.yml: checked, but not used because it is a legacy
#     Python 3.7 / Torch 1.12 / CUDA 11.3-era environment. See
#     docker/env-yml-comparison.md.
#
# Build:
#   docker build -t dplm:cu121-torch220 .
#
# Run with NVIDIA Container Toolkit:
#   docker run --rm --gpus all -it dplm:cu121-torch220

FROM nvidia/cuda:12.9.1-devel-ubuntu22.04 AS runtime-base

ARG DEBIAN_FRONTEND=noninteractive
ARG VENV_DIR=/opt/venv
ARG DPLM_HF_MODELS="airkingbd/dplm_150m airkingbd/dplm_650m airkingbd/dplm2_650m airkingbd/dplm2_bit_650m airkingbd/struct_tokenizer"
ARG UV_VERSION=0.8.15

ENV PATH="${VENV_DIR}/bin:/root/.local/bin:${PATH}" \
    CUDA_HOME=/usr/local/cuda \
    DPLM_HF_MODELS="${DPLM_HF_MODELS}" \
    HF_HOME=/opt/huggingface \
    HF_HUB_CACHE=/opt/huggingface/hub \
    HF_HUB_DOWNLOAD_TIMEOUT=300 \
    HF_HUB_ETAG_TIMEOUT=60 \
    HF_HUB_DISABLE_XET=1 \
    TRANSFORMERS_CACHE=/opt/huggingface/hub \
    TORCH_HOME=/opt/torch \
    XDG_CACHE_HOME=/tmp/dplm-xdg-cache \
    TRITON_CACHE_DIR=/tmp/dplm-triton-cache \
    MPLCONFIGDIR=/tmp/dplm-matplotlib-cache \
    TORCH_CUDA_ARCH_LIST="7.0;8.0" \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        build-essential \
        pkg-config \
        libffi-dev \
        libssl-dev \
        libaio-dev \
        libxml2 \
        libxrender1 \
        libxext6 \
        libsm6 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o /tmp/uv-install.sh \
    && sh /tmp/uv-install.sh \
    && rm /tmp/uv-install.sh \
    && uv python install 3.9.23 \
    && uv venv --python 3.9.23 "${VENV_DIR}" \
    && uv pip install --python "${VENV_DIR}/bin/python" pip==25.2 setuptools==80.9.0 wheel==0.45.1

WORKDIR /workspace/dplm

COPY docker/dplm-pip-freeze.txt docker/dplm-pip-freeze.txt

RUN python -m pip install --no-build-isolation \
        torch==2.2.0+cu121 \
        torchvision==0.17.0+cu121 \
        torchaudio==2.2.0+cu121 \
        --index-url https://download.pytorch.org/whl/cu121 \
    && python -m pip install -r docker/dplm-pip-freeze.txt

FROM runtime-base AS model-cache

RUN python - <<'PY'
import os
import shutil
import time
from pathlib import Path

from huggingface_hub import snapshot_download

models = os.environ.get("DPLM_HF_MODELS", "").split()
cache_dir = Path(os.environ.get("HF_HUB_CACHE", "/opt/huggingface/hub"))

def repo_cache_dir(repo_id):
    return cache_dir / f"models--{repo_id.replace('/', '--')}"

for repo_id in models:
    print(f"Caching Hugging Face model: {repo_id}")
    last_error = None
    for attempt in range(1, 4):
        try:
            snapshot_download(
                repo_id=repo_id,
                repo_type="model",
                max_workers=1,
                resume_download=True,
                force_download=attempt > 1,
            )
            break
        except Exception as exc:
            last_error = exc
            print(f"Attempt {attempt}/3 failed for {repo_id}: {exc!r}")
            shutil.rmtree(repo_cache_dir(repo_id), ignore_errors=True)
            time.sleep(10 * attempt)
    else:
        raise SystemExit(f"Failed to cache {repo_id}: {last_error!r}")
PY

RUN mkdir -p "${TORCH_HOME}/hub/checkpoints" "${XDG_CACHE_HOME}" "${TRITON_CACHE_DIR}" "${MPLCONFIGDIR}" \
    && python - <<'PY'
import gc

import esm

fair_esm_loaders = [
    ("esm_if1_gvp4_t16_142M_UR50", esm.pretrained.esm_if1_gvp4_t16_142M_UR50),
    ("esm2_t6_8M_UR50D", esm.pretrained.esm2_t6_8M_UR50D),
    ("esm2_t36_3B_UR50D", esm.pretrained.esm2_t36_3B_UR50D),
]

for name, loader in fair_esm_loaders:
    print(f"Caching Fair-ESM torch-hub model: {name}")
    model, alphabet = loader()
    del model, alphabet
    gc.collect()
PY

RUN mkdir -p "${TORCH_HOME}/hub/checkpoints" \
    && curl --fail --location --retry 5 --retry-delay 10 --connect-timeout 30 --speed-time 300 --speed-limit 1024 \
        https://dl.fbaipublicfiles.com/fair-esm/models/esmfold_3B_v1.pt \
        --output "${TORCH_HOME}/hub/checkpoints/esmfold_3B_v1.pt"

FROM runtime-base AS final

WORKDIR /workspace/dplm

COPY . .

# Bake in the conditional-training dataset (gitignored under data-bin/).
# ~1.1 GB: CFP-Gen labels joined onto DPLM-2's shipped struct tokens.
# If you regenerate this dataset, rebuild the image or mount over it at
# /workspace/dplm/data-bin/cfpgen_dplm2_joined.
COPY data-bin/cfpgen_dplm2_joined/ /workspace/dplm/data-bin/cfpgen_dplm2_joined/

RUN python -m pip install --no-build-isolation -e .

RUN python -m pip install --no-build-isolation -e vendor/openfold

COPY --from=model-cache /opt/huggingface /opt/huggingface
COPY --from=model-cache /opt/torch /opt/torch

RUN python - <<'PY'
import sys
import torch

print("python", sys.version.replace("\n", " "))
print("torch", torch.__version__)
print("torch_cuda", torch.version.cuda)
print("cuda_available", torch.cuda.is_available())
print("cudnn", torch.backends.cudnn.version())
PY

CMD ["/bin/bash"]
