#!/bin/bash
# Slurm job running the worker under Apptainer (or Singularity).
#
# One-time setup on a login node:
#   apptainer pull ecc-worker.sif docker://ghcr.io/aburan28/ecc-challenges-worker@sha256:<digest>
#   install -m 0600 /dev/stdin ~/.config/ecc-challenges/api-key
# Then:  sbatch --array=0-3 deploy/hpc/ecc-worker.sbatch
#
# Each array task keeps its own state directory, so resubmitting the same
# array resumes the same runs. Keep the array size <= 8 per API key.
#SBATCH --job-name=ecc-worker
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=4G
#SBATCH --time=24:00:00
# Ask Slurm for SIGTERM two minutes before the limit so the worker can save.
#SBATCH --signal=B:TERM@120
#SBATCH --requeue

set -euo pipefail
campaign=${ECC_CAMPAIGN:-eccp-131}
image=${ECC_SIF:-$PWD/ecc-worker.sif}
state=${ECC_STATE_ROOT:-$HOME/ecc-data}/$campaign-${SLURM_ARRAY_TASK_ID:-0}
mkdir -p "$state"

# For a CPU-only partition: drop --gres, use the CPU image, ECC_BACKEND=cpu,
# and remove --nv.
exec apptainer run --nv --cleanenv --containall \
  --bind "$state:/data" \
  --bind "$HOME/.config/ecc-challenges/api-key:/run/secrets/ecc_api_key:ro" \
  --env ECC_CAMPAIGN="$campaign",ECC_BACKEND=cuda,ECC_PROTOCOL=2,OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}" \
  "$image"
