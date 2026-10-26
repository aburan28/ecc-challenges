---
name: ecc-challenges-worker
description: Install prerequisites and set up, inspect, resume, or troubleshoot an ECC Challenges worker on CPU, NVIDIA CUDA, or macOS Metal. Use for contributing compute to ecc-challenges.com with Docker or the native Linux, macOS, and Windows installers.
---

# ECC Challenges worker

Help the user run a persistent worker and verify its connection to the public
gateway. The worker does the curve search. You handle setup, monitoring and
recovery.

## Choose the setup

Read the current [worker guide](https://ecc-challenges.com/docs/running-a-worker)
and [live campaign catalog](https://ecc-challenges.com/v1/campaigns). The
catalog's `worker_image` and `cpu_worker_image` are the current immutable image
pins. Use the dashboard's command for the selected challenge rather than
guessing an image digest or protocol.

Before starting anything, inspect existing workers, their state directories,
and the user's chosen resource limits. Resume an existing run when possible.
Use existing hardware within the requested scope; obtain authorization before
renting capacity or increasing paid resources.

| Host | Setup |
| --- | --- |
| Linux NVIDIA GPU with Docker | Dashboard's NVIDIA command; one worker per GPU |
| Linux x86-64 or arm64 CPU with Docker | Dashboard's CPU image; usually one worker per host; set `OMP_NUM_THREADS` to the agreed CPU limit |
| macOS | Native installer. Metal uses the Mac GPU; Docker on macOS runs a Linux VM on the CPU |
| Linux without Docker | Native installer; CPU or CUDA when the toolkit is installed |
| Windows x64 or arm64 | Native PowerShell installer; CPU, or CUDA on supported x64 hosts |
| Existing managed or cloud host | Read the guide's matching Compose, Kubernetes, systemd, Slurm or cloud recipe; keep persistent storage |

Check the chosen challenge in the catalog. A solved challenge suits a
deliberate pipeline check; pick an open challenge for ongoing contributions.
The native installers default to `eccp-131`, so set `ECC_CAMPAIGN` explicitly
to keep the user's choice.

## Install missing prerequisites

Carry setup through dependency installation. The bundled
[prerequisite guide](references/prerequisites.md) has the commands and vendor
sources; if you are reading this skill at `/skill.md`, use the
[web copy](https://ecc-challenges.com/docs/worker-prerequisites). First inspect
the target host's OS, architecture, privileges, existing workers and Docker
context. Reuse working tools and install only what the selected path needs:

- **Linux Docker:** install a supported Docker Engine, start its daemon and
  verify access as the worker's user. Add Compose only for a Compose recipe.
  A Docker CLI without a reachable daemon is an incomplete setup.
- **NVIDIA Docker:** also check the host driver and install and configure the
  NVIDIA Container Toolkit. Prebuilt images need no host CUDA compiler. Verify
  the allocated GPU inside the pinned worker image with `ECC_BACKEND=cuda` and
  `--check`, so a failure shows instead of silently falling back to CPU.
- **Native Linux and macOS:** install Python 3.10+, a C++17 compiler, curl and
  tar. On macOS, finish the Xcode command-line tools setup. On native CUDA
  hosts, also install a compatible CUDA toolkit.
- **Native Windows:** install Python 3.10+, CMake 3.21+ and Visual Studio 2022
  C++ Build Tools for the machine's architecture. Finish each installer, reopen
  the terminal if needed, and verify the tools before running the worker
  installer.

A request to set up the worker includes installing its normal prerequisites on
the designated machine, within the session's permissions. Preserve daemon
settings and existing workloads. A reboot, driver replacement or Docker restart
that interrupts unrelated jobs needs maintenance approval; without it, report
that step as unfinished. Do not weaken Docker socket permissions or discard
state to get past a setup failure.

Native workers do not need Docker. Node and npm are needed only for the Skills
CLI; an agent can read this URL directly. Once the tools are ready, continue
through worker installation, offline checks and connection verification below.
Report missing privileges, unfinished interactive installers and unavailable
backends as they are. A dependency list alone does not complete setup.

## Credentials and installation

The user signs in with GitHub at [the dashboard](https://ecc-challenges.com/dashboard)
and creates a revocable API key. They enter the key locally in the installer's
hidden prompt or save it to the private key file
(`~/.config/ecc-challenges/api-key`). Do not ask for the key in chat, print it,
or put it in commands, logs, screenshots, commits or public issue reports. The
key does not sign an agent into the private dashboard.

On Linux or macOS, the public installer builds the worker in the user's home
directory and prompts before starting it:

```sh
curl -fsSL https://ecc-challenges.com/install.sh | ECC_CAMPAIGN=eccp-131 sh
```

On Windows, in PowerShell:

```powershell
$env:ECC_CAMPAIGN = 'eccp-131'
irm https://ecc-challenges.com/install.ps1 | iex
```

Finish the prerequisite setup above before running these installers. Set
`ECC_START=no` to install without starting a worker. In an unattended agent
session, run the printed start command after the user has saved the key. The
absence of an interactive terminal is not permission to ask for the key in
chat.

## Start and verify

For Docker, use a stable container name, `--stop-timeout 120`, the chosen
immutable image, a read-only key mount and a persistent `/data`. Follow the
dashboard's UID and GID settings so the mode 0600 key and the state directory
are accessible. Start one bounded worker before expanding.

For a new installation, `ecc-worker --check` (or the image's `--check`) runs
finite all-curve checks without credentials or run registration. The guide
covers backend selection and receipts. A pass shows correctness on the tested
backend, not production upload or throughput.

Verify registration and record the campaign, protocol, run ID, selected
backend, state directory and image digest or native version. Check heartbeat
and health in the user's dashboard. Confirm accepted uploads when they appear;
large challenges can take a long time to produce the first point, so a missing
upload alone does not mean the worker is disconnected. Dashboard health and the
archived and verified counts measure different things.

Report what was checked: installation, offline checks, running process,
heartbeat, accepted upload, and verification and index status. Name the checks
still pending. A running process alone is not a successful contribution.

## Stop, resume, and recover

- Keep one state directory per worker, campaign and protocol. Never run two
  processes on the same directory or delete it to resolve a registration,
  checkpoint, cursor, namespace or lock error.
- Stop Docker workers with `docker stop CONTAINER`; restart the same container
  with `docker start CONTAINER`. Inspect logs with `docker logs --tail 100 -f
  CONTAINER`. Keep its image and mounted state when resuming.
- Stop native workers with Ctrl-C or SIGTERM and let the current round finish.
  Restart with the same campaign, state directory and backend.
- Generic protocol 2 retires a run with `ECC_ACTION=close` once pending
  records are uploaded. Do not use it on an original protocol 1 worker. Closing
  a run retires it for good; it is not a pause.
- On 429 or 503, back off: keep pending records and let the worker retry.
  On 401 or 403, check the key file and whether the key was revoked. After a
  permanent rejection or an identity mismatch, keep the state and point the
  user to [help](https://ecc-challenges.com/help).
- Original `ecc2k-130` sigma workers use protocol 1 with their registered
  distinguished point (DP) threshold: DP30, DP32 or DP34. Generic protocol 2
  has a separate walk and state. Do not change a saved run's protocol or
  threshold to match a recommendation.
- Timeouts and unresolved server audits need operator review. They are not
  evidence of invalid points, and they are no reason to discard worker state.

For a handoff, give the user the exact restart and log commands, the state
path, resource usage and worker identity, and links to their dashboard and the
[public status page](https://ecc-challenges.com/status). Redact credentials.
