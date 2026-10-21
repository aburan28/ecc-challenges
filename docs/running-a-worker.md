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
| A Mac (Apple silicon or Intel), using its GPU through Metal | [macOS](#macos) | `deploy/native/install.sh`, `deploy/launchd/` |
| A Windows PC (x64 or ARM64) | [Windows](#windows) | `deploy/native/install.ps1` |
| Docker, but managed by systemd | [systemd + Docker](#systemd-with-docker) | `deploy/systemd/ecc-worker-docker@.service` |
| Rootless Podman | [Podman Quadlet](#podman) | `deploy/podman/` |
| A university or lab cluster with Slurm | [Apptainer + Slurm](#apptainer-and-slurm) | `deploy/hpc/` |
| No hardware, a cloud GPU account | [Modal](modal-workers.md), RunPod, any container host | |

> **Which curves are open right now?** The live site lists them at
> [`/v1/campaigns`](https://ecc-challenges.com/v1/campaigns). The 19 October 2026
> release enables protocol 2 for all 24 curves. Existing ECC2K-130 protocol 1
> workers can keep resuming their bound checkpoints.

## Quickest start

On a Mac or a Linux machine without Docker, one command downloads the source,
builds the native worker into `~/.local/ecc-challenges`, asks for your API key
and offers to start it:

```sh
curl -fsSL https://ecc-challenges.com/install.sh | sh
```

On Windows, in PowerShell (it installs to `%LOCALAPPDATA%\ecc-challenges`):

```powershell
irm https://ecc-challenges.com/install.ps1 | iex
```

Both take `ECC_CAMPAIGN` (default `eccp-131`) from the environment, for
example `curl -fsSL https://ecc-challenges.com/install.sh | ECC_CAMPAIGN=ecc2-131 sh`,
and print the command to start the worker again. They wrap the
`deploy/native` installers described below; the scripts themselves are
`src/ecc_challenges/install/`. Run the same command to update.

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
   - **At most 8 active runs per account.** Additional keys share this limit.
     Closing a run releases an active slot; the lifetime run limit still applies.
   - **Stop with SIGTERM and give it up to two minutes.** The worker finishes the
     current round, uploads what it has and saves. Every recipe here is set up
     that way.
   - To retire a worker for good, run it once with `ECC_ACTION=close`. This
     closes the run on the server and keeps the data directory as a record.

## Images

| Image | For | Architectures |
| --- | --- | --- |
| `ghcr.io/aburan28/ecc-challenges-worker` | All 24 curves on CPU or NVIDIA GPUs (Turing through Blackwell); ECC2K-130 protocol 1 resume | amd64 |
| `ghcr.io/aburan28/ecc-challenges-worker-cpu` | Smaller CPU-only build of the same generic 24-curve worker | amd64, arm64 |

Both public packages are linked to the [ecc-challenges repository](https://github.com/aburan28/ecc-challenges/packages).
The combined image built from this source includes both the CUDA engine and a
parallel CPU engine, so its pinned digest works on an amd64 machine with or
without a GPU. The
smaller CPU-only image is available for arm64 or hosts that cannot pull the
CUDA-sized image. Use the digest shown on your dashboard and pin it for anything
long-running so a new release doesn't change the code under a run.

The CPU engine in new combined builds uses every core it is given. One worker
per machine is the right shape for CPUs; set `OMP_NUM_THREADS` to limit new
builds. For GPUs, run one worker per card.

### Hardware coverage

Every implemented backend handles all 24 pinned Certicom instances, including
prime and binary fields through 359 bits. Hardware acceleration depends on the
backend and host:

| Host | Worker |
| --- | --- |
| Linux x86-64 CPU | Combined image or CPU-only image |
| Linux arm64 CPU, including Graviton | CPU-only image |
| Linux x86-64 NVIDIA GPU, Turing through Blackwell | Combined image with CUDA and a compatible host driver |
| macOS GPU, Apple silicon or Intel Mac with Metal | Native worker; Docker's Linux VM does not expose Metal |
| Linux AMD or Intel GPU | CPU image uses the host CPU; no ROCm, OpenCL or SYCL backend is implemented |
| FPGA instance | CPU image can use its host CPU; FPGA acceleration is not implemented |

The all-curve CUDA image requires a CUDA 13-capable host driver (Linux R580 or
newer). Its CUDA 13.0 engine includes native code for the supported generations;
it uses [CUDA minor-version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html).
The NVIDIA driver requirement check remains enabled.

An FPGA backend needs a board-specific kernel or bitstream and host runtime.
Mounting an FPGA into a container does not provide the curve arithmetic.
Docker images here target Linux; native Windows and macOS workers use the same
arithmetic core. A supported backend does not enable a campaign at the gateway:
check the live catalog before starting an uploading worker.

### Check a new image on your hardware

New builds include a finite offline check for all 24 curves. It needs no API key,
does not register a run or alter a checkpoint, and checks actual lane states and
distinguished points against the Python reference, including invalid-input
rejections. Each backend runs 168 checks. A GPU check checks both CPU and the
requested GPU backend; an unavailable requested backend fails.

```sh
# Use the image you built or a release that includes --check.
docker run --rm --network none -e ECC_BACKEND=cpu "$ECC_WORKER_IMAGE" --check
docker run --rm --network none --gpus all -e ECC_BACKEND=cuda "$ECC_WORKER_IMAGE" --check
```

Set `ECC_CHECK_OUT` to a new directory in a mounted writable volume to retain
inputs, outputs and a receipt binding the results to each engine's SHA-256.
Without it, temporary check artifacts are removed. These are bounded
correctness checks, not throughput measurements or challenge solutions.
CI checks both final images and runs the CPU image natively on amd64 and arm64.
The publishing workflow requires the CPU checks and verifies that anonymous
users can retrieve the published manifests.

## Docker

The dashboard shows the exact GPU command, with your image digest. On an amd64
CPU host, use that same digest without `--gpus all` and `ECC_GPU`:

```sh
ECC_WORKER_IMAGE='ghcr.io/aburan28/ecc-challenges-worker@sha256:c7b58f784a8d8ea62d1649062387e76558d627c166fef14f185b6dab417a6f2e'
mkdir -p "$PWD/ecc-data-eccp-131"
docker run -d --restart unless-stopped --name ecc-eccp-131 \
  --user "$(id -u):$(id -g)" --stop-timeout 120 \
  -e ECC_CAMPAIGN=eccp-131 \
  -v "$HOME/.config/ecc-challenges/api-key:/run/secrets/ecc_api_key:ro" \
  -v "$PWD/ecc-data-eccp-131:/data" \
  "$ECC_WORKER_IMAGE"
docker logs -f ecc-eccp-131
```

The published digest above supports all 24 curves on CPU and CUDA and includes
the OpenMP CPU engine. For the smaller CPU-only image, including ARM64, use
`ghcr.io/aburan28/ecc-challenges-worker-cpu@sha256:3e64885e81d55cbe67552e664ca46a38cc5f7276e6041d211b03fc8d2b0a30d8`.
The dashboard shows the current pinned images.

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

## macOS

Apple silicon and Intel Macs run the native worker. The engine is built with
both a CPU backend and a Metal backend, and `ECC_BACKEND=auto` picks Metal
when the engine sees a Metal device. Needs the Xcode command line tools
(`xcode-select --install`) and Python 3.10+ (python.org or Homebrew; the
`/usr/bin/python3` that ships with macOS can be older).

```sh
git clone https://github.com/aburan28/ecc-challenges && cd ecc-challenges
deploy/native/install.sh --prefix "$HOME/.local/ecc-challenges"
ECC_CAMPAIGN=eccp-131 ECC_STATE_DIR="$HOME/ecc-data/eccp-131" \
  ECC_API_KEY_FILE="$HOME/.config/ecc-challenges/api-key" \
  "$HOME/.local/ecc-challenges/bin/ecc-worker"
```

The installer prints the engine's `--probe` report, which lists the Metal
devices it found. It also writes a launchd agent to
`~/.local/ecc-challenges/com.ecc-challenges.worker.plist`; edit the curve and
state directory, copy it to `~/Library/LaunchAgents/` and load it with
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ecc-challenges.worker.plist`.
`launchctl bootout` sends SIGTERM and allows 150 seconds for the worker to save.
On a Mac with several GPUs, `ECC_GPU` is an index into the probe's
`metal_devices` list. The Metal kernel is compiled from the same source as
the CPU and CUDA engines and must match the CPU backend bit for bit on all 24
curves before it is trusted (see the checks below). Prefer `cmake -S worker -B
build` if you want a universal (`arm64;x86_64`) engine or Homebrew's OpenMP.

## Windows

Windows 10/11 on x64 or ARM64 runs the native worker with the CPU backend, and
with CUDA when the CUDA toolkit is installed (x64). Needs Python 3.10+ from
python.org, and Visual Studio 2022 or its Build Tools with the "Desktop
development with C++" workload (which includes CMake). In PowerShell:

```powershell
git clone https://github.com/aburan28/ecc-challenges; cd ecc-challenges
powershell -ExecutionPolicy Bypass -File deploy\native\install.ps1
mkdir $env:USERPROFILE\.config\ecc-challenges
notepad $env:USERPROFILE\.config\ecc-challenges\api-key    # paste the key, save
icacls $env:USERPROFILE\.config\ecc-challenges\api-key /inheritance:r /grant:r "${env:USERNAME}:R"
$env:ECC_CAMPAIGN = 'eccp-131'
$env:ECC_STATE_DIR = "$env:USERPROFILE\ecc-data\eccp-131"
& "$env:LOCALAPPDATA\ecc-challenges\bin\ecc-worker.cmd"
```

Stop it with Ctrl-C; the worker finishes the round, uploads and saves (cmd may
then ask "Terminate batch job?"; either answer is fine). The wrapper defaults
`ECC_API_KEY_FILE` to `%USERPROFILE%\.config\ecc-challenges\api-key`. Each
round's checkpoint is written atomically, so a hard stop (closing the window,
Task Manager, a reboot) loses at most the round in progress.

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
| `ECC_BACKEND` | `auto` (CPU-only image: `cpu`) | `cuda`, `metal`, `cpu`, or `auto` (Metal on a Mac whose engine sees a Metal device, CUDA when `nvidia-smi` sees a GPU, else CPU) |
| `ECC_GPU` | unset | GPU index for this worker (CUDA device, or Metal device from `generic-engine --probe`) |
| `OMP_NUM_THREADS` | all available cores | CPU engine threads (also honored by builds without OpenMP) |
| `ECC_LANES` | GPU (CUDA, Metal) 64, CPU 4 × threads; ECC2-131 and ECCp-131 (walk 4): GPU 4096, CPU 32 × threads | Parallel walks. Fixed for a run once it starts |
| `ECC_STEPS_PER_ROUND` | 256; ECC2-131 and ECCp-131 (walk 4): 4096 | Steps between checkpoints. Fixed for a run once it starts |
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
| macOS (arm64, x86_64) | CI `Worker platforms`: `install.sh` and CMake builds with Metal, self-test, all-24-curve CPU walk against a Python reference, Metal-vs-CPU parity on all 24 curves where the runner exposes a Metal device, universal binary, `plutil -lint` on the launchd agent, installed wrapper start-up |
| Windows (x64, ARM64) | CI `Worker platforms`: `install.ps1` under Windows PowerShell 5.1, self-test, all-24-curve CPU walk against a Python reference, file lock and atomic checkpoint, installed wrapper start-up |
| Linux arm64 native | CI `Worker platforms`: as Windows, plus CMake and `install.sh` builds |
| Metal kernel source | Type-checked on every CI host with clang's C++ for OpenCL, which enforces address spaces as Metal does; compiled with `xcrun metal` where the Metal toolchain is installed |

## API key activity

The private dashboard shows activity for each API key across all campaigns and
worker protocols. Workers send an authenticated `POST /v1/heartbeat` immediately
when their work loop starts and every 60 seconds thereafter, independently of
point discovery, long compute rounds, checkpoints, and upload retries. Requests
have a 10-second timeout; failures are retried on the next interval and do not
interrupt the search. Closing a run or running `--check` does not send heartbeats.

**Active** means a worker using that key pinged within the past three minutes;
otherwise it shows **Offline**. Multiple workers can share a key: stopping one
does not mark the others offline. Revoked keys and suspended accounts are always
offline. This reports worker connectivity, not proof that a GPU is computing.
Status is calculated when the page loads; use **Refresh status** to update it.

**Last ping** is the last accepted heartbeat, using the server's clock.
**Last used** is the last other authenticated API request that passed the key's
rate limit, even if the later operation was rejected. Both timestamps use UTC.
Heartbeats do not change run cursors, point counts, checkpoints, or last used.

Existing workers need an update and restart before they send heartbeats. For
Docker, stop the worker gracefully and recreate it with the dashboard's current
image, preserving the same state volume, protocol and threshold. For native
installs, stop the worker and rerun the installer, then resume the same state
directory. No heartbeat history is inferred from earlier point uploads.

**Points received** sums the committed record counters for the key's runs.
Re-sending a batch does not increase the count. The count includes records
awaiting verification, can include repeated endpoints in different batches, and
does not include unuploaded local points or unattributed historical imports.
Revoked keys retain their history; unused keys show “Never” and zero points.

## ECC2K-130 legacy sigma thresholds

To start a sigma DP34 run that can match the old DP30/DP32 archive, use the
combined GPU image built with threshold compatibility and a **new, dedicated
state directory**. Set:

```sh
ECC_PROTOCOL=1
ECC_CAMPAIGN=ecc2k-130
ECC_DP_WEIGHT=34
```

Pass these as environment variables to the worker alongside your existing API
key and state-volume configuration. This sigma implementation requires an
NVIDIA GPU. The gateway advertises supported weights in `/v1/config`.
`ECC_DP_WEIGHT` accepts 30, 32 or 34. DP34 is recommended for new NVIDIA sigma
workers and is preselected on the dashboard. New clients use the gateway's
`recommended_dp_weight` (currently 34) unless `ECC_DP_WEIGHT` is set. The chosen
weight becomes part of the run and checkpoint binding and cannot be changed
when resuming. Existing sigma checkpoints retain their original threshold;
old checkpoints without threshold metadata remain DP32. Pending registrations
also retain their threshold across retries. Use a separate state directory
when starting a different threshold.

These are Hamming-weight thresholds: DP34 emits more frequently than DP32.
This recommendation is not a measured claim of the fastest time to a solution;
GPU throughput, upload/index capacity and continuation costs also matter.
Protocol 2's `dp_bits=34` belongs to a different walk and does not query the
legacy archive. The operator's bounded continuation jobs described in
[operations](operations.md#reusing-legacy-dp30dp32-records-with-sigma-dp34-runs)
can extend sigma DP34 trails to the old, rarer endpoints.
