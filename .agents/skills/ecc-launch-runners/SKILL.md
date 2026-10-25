---
name: ecc-launch-runners
description: Launch, resume, inspect, or stop ECC Challenges contributor runners on a specified local or SSH box using aburan28/ecc-challenges. Use for CPU, NVIDIA CUDA, or macOS Metal workers, persistent services, checkpoints, and runner health. These are ECC compute workers, not GitHub Actions runners. Excludes deploying the gateway and provisioning cloud capacity.
---

# Launch ECC Challenges runners

Take a runner request through host inspection, installation, startup, and verified
handoff. Use [aburan28/ecc-challenges](https://github.com/aburan28/ecc-challenges)
and its existing deployment recipes; do not invent a worker client or protocol.
If asked only to create or install this skill, do not start compute jobs.

Run repository commands from the checkout root. Resolve relative references
against this skill's directory. Read the current
[worker guide](../../../docs/running-a-worker.md) and the selected recipe before
launching; they and the checked-out client are the implementation authority.

## 1. Select the machine and inspect existing work

Use the user's designated host, SSH alias, or current box. If the target is
unclear, ask for it; do not silently launch in the assistant's execution sandbox.
Reuse the configured SSH connection with normal host-key verification. Inspect
the target, not just the machine making the SSH connection.

Collect only the facts needed to choose a recipe:

- OS, architecture, available CPU affinity/quota, memory, disk space, and privilege
  level; respect any requested CPU, GPU, memory, or runtime budget.
- Docker/Compose availability, or Python 3.10+ and a C++17 compiler for native
  builds. For NVIDIA, inspect GPU IDs, driver, and toolkit/container support.
  Current CUDA images need a CUDA 13-compatible driver (Linux R580+).
- Existing `ecc-*` containers, `ecc-worker@*`/`ecc-worker-docker@*` services,
  launchd agents, installs, and state mounts. Inspect selected fields rather than
  dumping container environments or process environments, which may hold keys.
- Existing key file/secret reference, campaign, image/source revision, backend,
  state path, and run ID, when resuming an existing worker.

A launch request on a named existing machine covers the normal bounded install
and start. Continue without an extra confirmation for those steps. Ask only for
missing inputs or materially different scope, such as buying another machine.
Do not provision instances, alter drivers, or replace other jobs by inference.

## 2. Resolve campaign, build, credentials, and state

Fetch `https://ecc-challenges.com/v1/campaigns` without authentication. Read its
`campaigns`, `multicurve_enabled`, `worker_image`, and `cpu_worker_image` fields.
Check that the chosen row advertises the required protocol in `protocols`; for
generic workers also check `/v2/config?campaign=CAMPAIGN`. A compiled backend is
not evidence that the live gateway admits that campaign.

Honor a requested campaign. For a new generic worker with no preference, use
`eccp-131` if the live gateway admits protocol 2 and state that choice. For an
existing worker, keep its bound campaign and protocol.

For containers, select the current digest from the live catalog/dashboard and
record the exact `image@sha256:...`. Do not copy an old example digest or use
`latest` for a persistent runner. For native installs, record the actual source
commit; do not overwrite a dirty checkout or rebuild an active install in place.

Use an existing API-key file or configured secret binding. If absent, ask the
user to create a key at `https://ecc-challenges.com/dashboard` and make it
available as a protected file/secret reference. Do not ask for a pasted key or
print one. Keep it out of shell arguments, committed files, logs, and reports;
prefer a mode-0600 file mounted read-only or systemd `LoadCredential`.
Check account capacity before adding runs: the worker guide currently specifies
at most eight active runs per account, shared across its keys. Do not create keys
or discard checkpoints to get around a quota.

Assign one absolute, persistent state directory per runner, outside the checkout
and temporary directories. One CPU worker per machine is the normal shape; cap
`OMP_NUM_THREADS` to the allocated cores. For GPUs, use one worker per allocated
card with a distinct device selection, service/container name, and state path.
Do not add a CPU worker alongside GPU workers unless that compute was requested.

Resume the original state directory and its saved protocol, namespace, lane
count, round size, and (for sigma) threshold. Never delete or share a checkpoint,
copy it into two simultaneously running workers, or change its bindings to make
startup succeed. `generic-state.json` is protocol 2; `state.json`/`state.ck` are
legacy sigma state. Preserve pending registration files as well.

## 3. Choose and execute the recipe

| Machine | Recipe |
| --- | --- |
| Linux amd64 with NVIDIA and Docker | Combined worker image, explicit CUDA backend and selected GPU |
| Linux amd64/arm64 CPU with Docker | CPU-only image matching the host, explicit CPU backend |
| Linux without Docker | Native installer and systemd when available |
| macOS with Metal | Native installer and launchd; Docker does not expose Metal |
| Windows | Native PowerShell installer; use the requested supervision method |
| AMD/Intel GPU or FPGA host | CPU backend only; those accelerator backends are not implemented |

Read [Docker launches](references/docker.md) or
[native launches](references/native.md), then execute the applicable recipe.
Use existing Compose/systemd/launchd templates for multiple or persistent
workers. Install only missing prerequisites needed for the chosen path. If the
user requested a GPU, do not silently substitute CPU after a failed GPU check.

New generic runs use `ECC_PROTOCOL=2`. A requested ECC2K-130 **legacy sigma**
runner uses the combined NVIDIA image with `ECC_PROTOCOL=1`; consult the worker
guide's sigma-threshold section and `/v1/config` for supported/recommended
weights. Retain the saved weight for resumes. Protocol 2 on ECC2K-130 does not
search the legacy sigma archive. CPU-only and native generic recipes do not
provide the legacy sigma engine.

Before registering any new uploading runner, run the selected build's finite
offline `--check` on the intended backend/device, without an API key. A successful
CPU check does not validate CUDA or Metal. On a managed cloud executor, follow
the cloud-environment runtime skill's proxy/CA instructions before using Docker;
do not work around a denied route or disable TLS verification.

Start workers under the agreed persistent supervisor. Set explicit campaign,
backend, protocol, key path, and state path. Give SIGTERM at least 120 seconds
to finish a round and save (the supplied systemd/launchd templates use 150).
If a matching container/service already exists, inspect it and resume it; do not
launch a duplicate or remove it merely to resolve a name conflict.

## 4. Verify and hand off

Do not equate a successful start command with a working contributor. Check:

1. The intended container/service remains running after initial startup, uses
   the expected image/build and device, and has no repeated startup failures.
2. State is on the intended durable mount. Read only safe metadata such as run
   ID, protocol, namespace, cursor, file size, and modification time; do not dump
   the full checkpoint. Observe checkpoint advancement when a round completes.
3. On the private dashboard, the expected run/key receives a fresh heartbeat
   (sent immediately and every 60 seconds). Inspect the run detail page for
   backend and health; a key-level ping alone cannot identify one of many runs.
   Do not collect or publish private dashboard data unnecessarily.
4. When points are found, logs/receipts show acknowledged uploads rather than
   just attempts. Sparse points on a larger curve can take time: do not claim
   upload verification without a receipt, or wait indefinitely for a point.

Bound startup observation to a few minutes. If dashboard access is unavailable,
state that the host checks passed and remote heartbeat/upload verification is
still unconfirmed. Distinguish computing, connected, uploaded, and independently
verified; report no invented throughput or solution estimate.

On failure, preserve state, inspect bounded logs, and fix the actual cause.
Honor 429/5xx backoff; investigate 401/403 credentials or 409 namespace/config
errors without opening new runs or resetting state. Never bypass driver or
namespace checks. Stop and retain failed/quarantined work for diagnosis.

For a requested stop, use the supervisor's graceful stop and retain all state;
stopping does not close the server run. For explicit permanent retirement of a
protocol-2 runner, first stop its writer, flush pending records by resuming if
necessary, then run the same build/key/state once with `ECC_ACTION=close`.
That action is implemented by the generic client, not the legacy sigma client;
do not present it as a legacy close command.

Finish with the host, runner names, campaign/protocol/backend/device, pinned
image or source commit, durable paths (never key contents), run IDs, checks
actually observed, and exact log/stop/resume commands. Record any unverified
heartbeat/upload condition and a user-specified end time or resource limit.

Example invocation: `$ecc-launch-runners launch one eccp-131 CPU runner on
my SSH host lab-box using my existing API-key file, limited to four cores`.
