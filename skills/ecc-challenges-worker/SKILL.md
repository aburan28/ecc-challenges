---
name: ecc-challenges-worker
description: Install prerequisites and set up, inspect, resume, or troubleshoot an ECC Challenges contributor worker on CPU, NVIDIA CUDA, or macOS Metal. Use for contributing compute to ecc-challenges.com with Docker or the native Linux, macOS, and Windows installers.
---

# ECC Challenges worker

Help the user run a persistent contributor worker and verify its connection to
the public gateway. The worker performs the curve search; an agent helps with
setup, monitoring, and recovery.

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
| Linux x86-64 or ARM64 CPU with Docker | Dashboard's CPU image; usually one worker per host; set `OMP_NUM_THREADS` to the agreed CPU limit |
| macOS | Native installer; Metal uses the Mac GPU, whereas Docker on macOS uses a Linux VM's CPU |
| Linux without Docker | Native installer; CPU or CUDA when the toolkit is installed |
| Windows x64 or ARM64 | Native PowerShell installer; CPU, or CUDA on supported x64 hosts |
| Existing managed or cloud host | Read the guide's matching Compose, Kubernetes, systemd, Slurm or cloud recipe; keep persistent storage |

Check the chosen challenge in the catalog. Previously solved challenges are
useful for a deliberate pipeline check; select an open challenge for ongoing
contributions. Native installers default to `eccp-131`; set `ECC_CAMPAIGN`
explicitly to preserve the user's choice.

## Install missing prerequisites

Carry setup through dependency installation. Read the bundled
[prerequisite guide](references/prerequisites.md) for commands and current
vendor sources. When reading this skill at `/skill.md`, use the
[web copy](https://ecc-challenges.com/docs/worker-prerequisites). Inspect the actual target host's OS,
architecture, privileges, existing workers and Docker context first. Reuse
working tools; install only what the selected path needs:

- **Linux Docker:** install a supported Docker Engine, start its daemon and
  verify access as the worker's user. Add Compose only for a Compose recipe.
  A Docker CLI without a reachable daemon is incomplete setup.
- **NVIDIA Docker:** also check the host driver and install/configure the NVIDIA
  Container Toolkit. A host CUDA compiler is unnecessary for prebuilt images.
  Verify the allocated GPU inside the pinned worker image with
  `ECC_BACKEND=cuda` and `--check`; keep failures visible instead of silently
  switching to CPU.
- **Native Linux/macOS:** install Python 3.10+, a C++17 compiler, and the
  download/extraction tools. On macOS, complete Xcode command-line tools setup;
  on native CUDA hosts also install the compatible CUDA toolkit.
- **Native Windows:** install Python 3.10+, CMake 3.21+, and Visual Studio 2022
  C++ Build Tools for the machine's architecture. Finish any installer, reopen
  the terminal when needed and verify the tools before running the worker installer.

A request to set up the worker includes its normal prerequisite installation
on the designated machine, within that session's permissions. Preserve daemon
settings and existing workloads. A reboot, driver replacement or Docker restart
that interrupts unrelated jobs needs the corresponding maintenance scope; if
it is unavailable, report that specific unfinished step. Do not weaken Docker
socket permissions or discard state to bypass setup failures.

Docker is optional for native workers. Node/npm is optional for the Skills CLI;
an agent can read this URL directly. Once the tools are ready, continue through
worker installation, offline checks and connection verification below. Report
missing privileges, unfinished interactive installers and unavailable backends
accurately; a dependency list alone does not complete setup.

## Credentials and installation

The user signs in with GitHub at [the dashboard](https://ecc-challenges.com/dashboard)
and creates a revocable contributor API key. Have them enter the key locally in
the installer's hidden prompt or save it to the documented private key file.
Avoid requesting the key in chat, printing it, or including it in commands,
logs, screenshots, commits, or public issue reports. A worker key does not sign
an agent into the private dashboard.

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

Complete the prerequisite setup above before invoking these installers.
Use `ECC_START=no` to install without starting a worker. For an unattended
agent session, use the printed start command after the user has saved the key;
an absent interactive terminal is not permission to request the key in chat.

## Start and verify

For Docker, use a stable container name, `--stop-timeout 120`, the chosen
immutable image, a read-only key mount, and persistent `/data`. Follow the
dashboard's UID/GID settings so a mode-0600 key and the state directory are
accessible. Start one bounded worker before expanding the deployment.

For a new installation, `ecc-worker --check` (or the image's `--check`) runs
finite all-curve checks without credentials or run registration. See the
guide for backend selection and receipts. Passing this check establishes
correctness on the tested backend, not production upload or throughput.

Verify registration and record the campaign, protocol, run ID, selected
backend, state directory and image digest or native version. Check heartbeat
and health in the user's dashboard. Confirm accepted uploads when they occur;
large challenges can take substantial time to find the first point. A missing
upload alone does not establish a disconnected worker. Dashboard health and
archived/verified counts have different meanings.

Report what was checked: installation, offline checks, running process,
heartbeat, accepted upload, and verification/index status. State which checks
remain pending rather than claiming a successful contribution from a running
process alone.

## Stop, resume, and recover

- Keep one state directory per worker, campaign and protocol. Never run two
  processes on the same directory or delete it to resolve a registration,
  checkpoint, cursor, namespace or lock error.
- Stop Docker workers with `docker stop CONTAINER`; restart the same container
  with `docker start CONTAINER`. Inspect logs with `docker logs --tail 100 -f
  CONTAINER`. Keep its image and mounted state when resuming.
- Stop native workers with Ctrl-C or SIGTERM and allow the current round to
  finish. Restart using the same campaign, state directory and backend setup.
- Generic protocol 2 has an explicit retirement action, `ECC_ACTION=close`,
  after pending records have been uploaded. Do not apply it to an original
  protocol-1 worker. Closing a run is a deliberate retirement, not a pause.
- Treat 429/503 as backoff: keep pending records and let the worker retry.
  For 401/403, check the key file and revocation state. Preserve state on a
  permanent rejection or identity mismatch and use [help](https://ecc-challenges.com/help).
- Original ECC2K-130 sigma workers use protocol 1 with their registered DP30,
  DP32 or DP34 threshold. Generic protocol 2 has a separate walk and state.
  Do not switch a saved run's protocol or threshold to follow a recommendation.
- Timeouts and unresolved server audits need operator review; they are not
  evidence of invalid points or an instruction to discard contributor state.

For a handoff, give the user the exact restart/log commands and state path,
resource usage and worker identity, and links to their dashboard and the
[public status page](https://ecc-challenges.com/status). Redact credentials.
