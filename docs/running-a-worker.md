# Running a worker

A worker is one process that walks one curve and uploads distinguished points.
It is the same program everywhere; only the packaging differs. Pick the row
that matches what you have:

| You have | Use | Files |
| --- | --- | --- |
| A Linux box with an NVIDIA GPU and Docker | [Docker](#docker) | command on your dashboard |
| Several workers on one machine | [Docker Compose](#docker-compose) | `deploy/docker/` |
| A Kubernetes cluster (CPU or GPU nodes) | [Kubernetes](#kubernetes) | `deploy/kubernetes/` |
| A server where you'd rather not run containers | [Native install + systemd](#native-install-and-systemd) | `deploy/native/`, `deploy/systemd/` |
| Docker, but managed by systemd | [systemd + Docker](#systemd-with-docker) | `deploy/systemd/ecc-worker-docker@.service` |
| Rootless Podman | [Podman Quadlet](#podman) | `deploy/podman/` |
| A university or lab cluster with Slurm | [Apptainer + Slurm](#apptainer-and-slurm) | `deploy/hpc/` |
| No hardware, a cloud GPU account | [Modal](modal-workers.md), RunPod, any container host | |

> **Which curves are open right now?** The live site lists them at
> [`/v1/campaigns`](https://ecc-challenges.com/v1/campaigns). Until the
> all-curves release is switched on (`multicurve_enabled: true` there), only
> ECC2K-130 on the GPU image with `ECC_PROTOCOL=1` is accepted, and the CPU
> image and other curves get HTTP 503. Everything below works unchanged once it
> is on.

## Before you start

1. Sign in at [ecc-challenges.com](https://ecc-challenges.com) and create an
   API key. Save it where only you can read it:

   ```sh
   mkdir -p ~/.config/ecc-challenges
   (umask 077; cat > ~/.config/ecc-challenges/api-key)   # paste, then Ctrl-D
   ```

2. Pick a curve from the catalog, for example `eccp-131` or `ecc2-131`.

3. Know the rules every recipe follows:
   - **One state directory per worker, kept forever.** The worker stores its run
     ID and checkpoint there and locks it. Don't share it between workers, and
     don't wipe it while the run is active, or the work since the last upload
     is lost.
   - **At most 8 active runs per API key.** Ask for more keys (up to three per
     account) if you need more workers.
   - **Stop with SIGTERM and give it up to two minutes.** The worker finishes the
     current round, uploads what it has and saves. Every recipe here is set up
     that way.
   - To retire a worker for good, run it once with `ECC_ACTION=close`. This
     closes the run on the server and keeps the data directory as a record.

## Images

| Image | For | Architectures |
| --- | --- | --- |
| `ghcr.io/aburan28/ecc-challenges-worker@sha256:…` | All 24 curves on CPU or NVIDIA GPUs (Turing through Blackwell); ECC2K-130 protocol 1 resume | amd64 |
| `ghcr.io/aburan28/ecc-challenges-worker-cpu` | Smaller CPU-only build of the same generic 24-curve worker | amd64, arm64 |

The combined image built from this source includes both the CUDA engine and a
parallel CPU engine, so its pinned digest works on an amd64 machine with or
without a GPU. The
smaller CPU-only image is available for arm64 or hosts that cannot pull the
CUDA-sized image. Use the digest shown on your dashboard and pin it for anything
long-running so a new release doesn't change the code under a run.

The CPU engine in new combined builds uses every core it is given. One worker
per machine is the right shape for CPUs; set `OMP_NUM_THREADS` to limit new
builds. For GPUs, run one worker per card.

## Docker

The dashboard shows the exact GPU command, with your image digest. On an amd64
CPU host, use that same digest without `--gpus all` and `ECC_GPU`:

```sh
ECC_WORKER_IMAGE='ghcr.io/aburan28/ecc-challenges-worker@sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee'
mkdir -p "$PWD/ecc-data-eccp-131"
docker run -d --restart unless-stopped --name ecc-eccp-131 \
  --user "$(id -u):$(id -g)" --stop-timeout 120 \
  -e ECC_CAMPAIGN=eccp-131 \
  -v "$HOME/.config/ecc-challenges/api-key:/run/secrets/ecc_api_key:ro" \
  -v "$PWD/ecc-data-eccp-131:/data" \
  "$ECC_WORKER_IMAGE"
docker logs -f ecc-eccp-131
```

Replace the example digest with the current one shown on your dashboard. The
published digest above supports all 24 curves on CPU; the OpenMP CPU build in
this source is included in the next image built from it.

## Docker Compose

`deploy/docker/compose.yaml` runs CPU and GPU workers from one
`ECC_WORKER_IMAGE` digest, with a read-only root filesystem, no capabilities
and your own UID, so the key can stay mode 0600.

```sh
cd deploy/docker
cp example.env .env                      # set ECC_UID/ECC_GID (id -u / id -g), curve, image
install -m 0600 ~/.config/ecc-challenges/api-key api-key
docker compose --profile cpu up -d       # or --profile gpu
docker compose logs -f
```

For more GPUs, copy the `gpu0` service to `gpu1` and change `ECC_GPU`,
`device_ids` and the data directory.

## Kubernetes

Workers run as a **StatefulSet**, not a Deployment: each replica gets its own
persistent volume (`data-ecc-worker-cpu-0`, `-1`, …), so a rescheduled pod
resumes its own run. A Deployment with a shared volume would break the
one-directory-per-worker rule.

```sh
printf '%s' "$(cat ~/.config/ecc-challenges/api-key)" > deploy/kubernetes/base/api-key
kubectl apply -k deploy/kubernetes/cpu      # or deploy/kubernetes/gpu
kubectl -n ecc-challenges logs -f statefulset/ecc-worker-cpu
```

Edit the overlay's patch to change the curve, `replicas` (8 or fewer per key) or
the CPU limit. The CPU engine's thread count follows the pod's CPU limit. The
GPU overlay requests `nvidia.com/gpu: 1` per pod and needs the NVIDIA device
plugin or GPU Operator. Pods run as UID 10001 with a read-only root
filesystem, no service-account token and the `RuntimeDefault` seccomp profile.

Scaling down leaves the higher-numbered volumes in place, so scaling back up
resumes those runs. To retire one for good, close its run first (see above).

## Native install and systemd

For hosts without containers. Needs Python 3.10+, `g++` (or `clang++`) with
OpenMP, and optionally `nvcc` for a CUDA build.

```sh
git clone https://github.com/aburan28/ecc-challenges && cd ecc-challenges
sudo deploy/native/install.sh
sudo install -m 0600 ~/.config/ecc-challenges/api-key /etc/ecc-challenges/api-key
sudoedit /etc/ecc-challenges/gpu0.env        # curve, backend, GPU index
sudo systemctl enable --now ecc-worker@gpu0
journalctl -fu ecc-worker@gpu0
```

The installer builds the engine on your machine, runs its self-test, puts
everything under `/opt/ecc-challenges` and adds the `ecc-worker@.service`
template. Each instance (`gpu0`, `gpu1`, `cpu`, …) reads
`/etc/ecc-challenges/<instance>.env` and keeps state in
`/var/lib/ecc-challenges/<instance>`. The key stays root-only; systemd passes
the service a private copy. Without root, use
`deploy/native/install.sh --prefix ~/.local/ecc-challenges --no-systemd` and run
`~/.local/ecc-challenges/bin/ecc-worker` yourself.

## systemd with Docker

`deploy/systemd/ecc-worker-docker@.service` wraps `docker run` in a unit, with
the same `/etc/ecc-challenges/<instance>.env` files plus `ECC_IMAGE` and, for a
GPU, `ECC_DOCKER_ARGS=--gpus all`. The container runs as UID 10001, so make
the key readable by that group: `install -m 0640 -g 10001 key /etc/ecc-challenges/api-key`.

## Podman

`deploy/podman/ecc-worker.container` is a
[Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
unit for rootless Podman. The key is a Podman secret, delivered as an
environment variable that the worker removes before starting the engine.

```sh
podman secret create ecc-api-key ~/.config/ecc-challenges/api-key
mkdir -p ~/.config/containers/systemd
cp deploy/podman/ecc-worker.container ~/.config/containers/systemd/
systemctl --user daemon-reload && systemctl --user start ecc-worker
loginctl enable-linger "$USER"
```

## Apptainer and Slurm

Most HPC clusters run Apptainer (formerly Singularity) instead of Docker.
`deploy/hpc/ecc-worker.sbatch` runs one worker per Slurm array task:

```sh
apptainer pull ecc-worker.sif docker://ghcr.io/aburan28/ecc-challenges-worker@sha256:<digest>
ECC_CAMPAIGN=eccp-131 sbatch --array=0-3 deploy/hpc/ecc-worker.sbatch
```

Slurm sends SIGTERM two minutes before the time limit, so the worker saves
before the job ends. Each array index keeps its own state directory under
`~/ecc-data`, so resubmitting the same array resumes the same runs. Check your
cluster's policy on long-running jobs and outbound HTTPS before you start.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `ECC_CAMPAIGN` | `ecc2k-130` | Curve ID from the catalog |
| `ECC_BACKEND` | `auto` (CPU-only image: `cpu`) | `cuda`, `cpu`, or `auto` (CUDA when `nvidia-smi` sees a GPU) |
| `ECC_GPU` | unset | GPU index for this worker |
| `OMP_NUM_THREADS` | all available cores | CPU engine threads |
| `ECC_LANES` | GPU 64, CPU 4 × threads | Parallel walks. Fixed for a run once it starts |
| `ECC_STATE_DIR` | `/data` | Persistent state directory |
| `ECC_API_KEY_FILE` | `/run/secrets/ecc_api_key` | Key file |
| `ECC_API_KEY` | unset | Key as an environment variable instead of a file |
| `ECC_PROTOCOL` | `2` for new state | `1` only for the original ECC2K-130 GPU walk |
| `ECC_ACTION` | `work` | `close` retires the run in this state directory |
| `ECC_API_URL` | `https://ecc-challenges.com` | Gateway |

## How each recipe was checked

Every recipe except Apptainer/Slurm was run against the
[local stack](local-stack.md), with uploads verified and indexed by the
server:

| Recipe | Check |
| --- | --- |
| CPU image | Built for amd64; engine self-test; arm64 engine cross-built and matched amd64 output on five curves under QEMU |
| Docker Compose | `docker compose config`; CPU service uploaded ecc2k-95 points with read-only rootfs and dropped capabilities |
| Kubernetes | Both overlays render and pass `kubeconform -strict` for Kubernetes 1.34; the pod's security settings were reproduced under Docker, including stop and resume of the same run |
| Native install | `install.sh` in a clean Debian container; worker uploaded eccp-89 and eccp-97 points; SIGTERM and SIGINT stop cleanly and resume |
| systemd units | `systemd-analyze verify` |
| Podman Quadlet | Quadlet generator output checked; the same `podman run` uploaded eccp-109 points |
| Apptainer + Slurm | Not run: no Slurm cluster available. Reviewed only |
| GPU paths | Not run here (no GPU). The GPU image itself has its own L4 release checks in `verification/` |
