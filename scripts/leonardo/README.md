# Leonardo Login-Node Setup

These scripts prepare a DPLM-2 reproduction environment on a Leonardo login node
where internet is available, then validate that compute nodes can run offline.
They are separate from the training Slurm launcher.

## One-Shot Login Setup

From the repository root:

```bash
export CONDA_PATH = "${WORK}/miniforge3"
export DPLM_CONDA_ACTIVATE="${CONDA_PATH}/bin/activate"
export DPLM_CONDA_ENV=dplm
export DPLM_INSTALL_ROOT="${WORK}/dplm_reproduce"
export DPLM_DATA_DIR="${WORK}/dplm_reproduce/data-bin"

bash scripts/leonardo/07_run_all_login_setup.sh
```

Defaults download the DPLM-2 training dataset `pdb_swissprot`, evaluation
metadata, and these model snapshots:

- `airkingbd/dplm_650m`
- `airkingbd/dplm2_650m`
- `airkingbd/dplm2_bit_650m`
- `airkingbd/struct_tokenizer`

Optional datasets:

```bash
DOWNLOAD_CATH=1 DOWNLOAD_UNIREF50=1 bash scripts/leonardo/04_download_training_data.sh
```

## Individual Steps

```bash
bash scripts/leonardo/00_write_runtime_env.sh
bash scripts/leonardo/01_create_conda_env.sh
bash scripts/leonardo/02_install_repo_editable.sh
bash scripts/leonardo/03_download_model_checkpoints.sh
bash scripts/leonardo/04_download_training_data.sh
bash scripts/leonardo/05_login_node_smoke_test.sh
```

Set `RUN_MODEL_SMOKE=1` for the login smoke test if the login node has enough
memory to load the released models.

## Compute-Node Offline Test

Inside an interactive allocation or a small Slurm test job:

```bash
source scripts/leonardo/env.sh
bash scripts/leonardo/06_compute_node_offline_test.sh
```

The compute test sets:

```bash
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_DATASETS_OFFLINE=1
```

It verifies imports, local data, Hugging Face cache or local model mirrors,
Hydra config composition, and CUDA visibility. Set `RUN_HEAVY_MODEL_LOAD=1` to
also load cached DPLM-2 checkpoints on the compute node.

## Training After Setup

For compute-node training, source the generated environment file before running
the existing training launcher:

```bash
source scripts/leonardo/env.sh
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1

bash scripts/reproduce/training/run_train.sh \
  --recipe dplm2_650m \
  --devices 4 \
  --max-tokens 8192 \
  --accumulate-grad-batches 2 \
  --max-steps 100000 \
  --name reproduce/dplm2_650m_from_dplm
```

`run_train.sh` now honors `DPLM_DATA_DIR` from `scripts/leonardo/env.sh`, so the
Hydra `paths.data_dir` override is passed automatically.
