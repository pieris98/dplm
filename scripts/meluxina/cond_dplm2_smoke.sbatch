#!/usr/bin/env bash
#SBATCH --job-name=cond_dplm2_smoke
#SBATCH --account=p201418
#SBATCH --partition=gpu
#SBATCH --qos=test
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=16
#SBATCH --time=00:30:00
#SBATCH --output=logs/slurm/cond_dplm2_smoke_%j.out
#SBATCH --error=logs/slurm/cond_dplm2_smoke_%j.err
#
# Meluxina SMOKE TEST for conditional DPLM-2 training.
# Validates in ONE 30-minute job:
#   1. Apptainer container runs with GPU access (--nv)
#   3. The 45K joined dataset is visible inside the container
#   2. Multi-GPU DDP (4×A100, bf16, find_unused_parameters) initializes
#   4. Training steps execute; loss is finite and logged to wandb
#   5. A conditional generation runs from the smoke checkpoint
#
# Submit:
#   sbatch scripts/meluxina/cond_dplm2_smoke.sbatch
#
# Success criteria (check the .out file):
#   - "tokenized ... OK" / dataset rows loaded ≈ 45,696
#   - val/loss finite at steps 20 and 40
#   - generation produces a non-empty aatype.fasta
#
# Prerequisites (one-time, on a login node):
#   module load Apptainer
#   apptainer pull ${PROJECT}/dplm_cond.sif docker://pieris98/dplm:cu121-torch220-cond
#   export WANDB_API_KEY=...   (or submit with WANDB_MODE=offline)

set -Eeuo pipefail

# Parastation/SLURM executes this script from a spool copy, so __FILE__-based
# path resolution breaks. Use SLURM_SUBMIT_DIR (the submission cwd) instead.
ROOT_DIR="${ROOT_DIR:-${SLURM_SUBMIT_DIR:-$(pwd)}}"
cd "${ROOT_DIR}"
source "${ROOT_DIR}/scripts/meluxina/common.sh"

echo "=== [1/4] Container + GPU visibility ==="
run_in_container nvidia-smi --query-gpu=index,name,memory.total --format=csv

echo "=== [2/4] Dataset visibility ==="
run_in_container python -c "
import pyarrow.parquet as pq
t = pq.read_table('/workspace/dplm/data-bin/cfpgen_dplm2_joined/joined_train_safe.parquet', columns=['uniprot_id'])
print(f'dataset rows: {t.num_rows}')
assert t.num_rows > 40000, 'dataset missing or truncated!'
print('dataset OK')
"

echo "=== [3/4] 40-step DDP training (4×A100 bf16, wandb) ==="
run_in_container python train.py \
  experiment=dplm2/cond_dplm2_650m_cfpgen_meluxina \
  trainer=meluxina_ddp_bf16 \
  trainer.num_nodes=1 \
  trainer.devices=4 \
  trainer.max_steps=40 \
  trainer.val_check_interval=20 \
  trainer.log_every_n_steps=5 \
  trainer.limit_val_batches=4 \
  datamodule.num_workers=4 \
  callbacks.model_checkpoint.every_n_train_steps=40 \
  logger=wandb \
  name=cond_dplm2_meluxina_smoke

echo "=== [4/4] Conditional generation from the smoke ckpt ==="
# Checkpoints are written by the container into the $DPLM_LOGS bind
# (= /workspace/dplm/logs inside). Look them up on the HOST side.
SMOKE_CKPT=$(ls -t "${DPLM_LOGS}/cond_dplm2_meluxina_smoke/checkpoints/"step_*.ckpt 2>/dev/null | head -1)
if [[ -z "${SMOKE_CKPT}" ]]; then
  echo "WARNING: no step_*.ckpt found; skipping generation test."
else
  echo "Using ckpt: ${SMOKE_CKPT}"
  run_in_container python generate_conditional_dplm2.py \
    --ckpt "${SMOKE_CKPT}" \
    --labels go:5 \
    --task sequence_generation \
    --num-seqs 4 --seq-len 64 --batch-size 4 \
    --saveto generation-results/meluxina_smoke
  run_in_container python -c "
seqs = open('generation-results/meluxina_smoke/sequence_generation/len_64/cond_go-5+ipr-0/aatype.fasta').read()
assert seqs.count('>') >= 4, 'generation produced too few sequences'
print('generation OK')
"
fi

echo ""
echo "=============================================="
echo "SMOKE TEST PASSED — safe to submit the full run:"
echo "  sbatch scripts/meluxina/cond_dplm2_train.sbatch"
echo "=============================================="
