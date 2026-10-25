# Docker launches

Run commands on the selected Linux host, as the account that will own its
runner state. Read `docs/running-a-worker.md` in the repository first. This
recipe starts one new **protocol-2** runner; preserve the original configuration
when resuming. For legacy sigma use the dashboard's protocol-1 command instead.

## Prepare

Resolve these non-secret variables from host inspection and the user's request:

| Variable | Value |
| --- | --- |
| `ECC_CAMPAIGN` | Admitted curve ID, usually `eccp-131` when unspecified |
| `ECC_WORKER_IMAGE` | Current CPU-only or combined image pinned by digest |
| `ECC_KEY_FILE` | Absolute path to the existing protected API-key file |
| `ECC_STATE_DIR` | Absolute durable path dedicated to this runner |
| `ECC_RUNNER_NAME` | Unique Docker name, e.g. `ecc-eccp-131-cpu` or `ecc-eccp-131-gpu0` |
| `ECC_CPU_THREADS` | Positive integer within the allocated CPU budget, for CPU |
| `ECC_DEVICE_ID` | Allocated physical GPU ID/index on the host, for CUDA |

Never put a key value in a variable interpolation or command. In this recipe
`ECC_KEY_FILE` is a path, not the key. Do not put keys or state in this repository.
For `--mount`, select absolute paths without commas. Check for an existing runner
before creating a new one:

```sh
test -n "${ECC_RUNNER_NAME:?set a runner name}"
docker ps -a --filter "name=^/${ECC_RUNNER_NAME}$" \
  --format '{{.Names}} {{.Status}} {{.Image}}'
```

If present, inspect the image/state mount, then `docker start` the stopped
container to resume it. Do not run the creation recipe over an existing runner.
If absent, verify that the intended state directory is new, or inspect its saved
metadata and use the saved run's configuration. Keep pending registration files.

```sh
test -f "${ECC_KEY_FILE:?set an absolute key-file path}"
test -s "$ECC_KEY_FILE"
case "${ECC_WORKER_IMAGE:?set a pinned image}" in
  *@sha256:*) ;;
  *) echo 'Use the current published image@sha256 digest' >&2; exit 1 ;;
esac
umask 077
mkdir -p "${ECC_STATE_DIR:?set an absolute persistent state path}"
docker pull "$ECC_WORKER_IMAGE"
```

The launching UID must be able to read the key and write the state directory.
Check mode/ownership without reading the key. Do not solve a permissions failure
by making a key world-readable or running all worker code as root.

## CPU: offline check, then persistent start

Use the CPU-only digest from `cpu_worker_image`, especially on arm64. The
combined digest also supports CPU on amd64. Use Docker's CPU limit as well as
the engine thread count; do not consume more cores than allocated.

```sh
test "${ECC_CPU_THREADS:?set allocated CPU threads}" -gt 0
docker run --rm --network none --cpus "$ECC_CPU_THREADS" \
  -e ECC_BACKEND=cpu -e OMP_NUM_THREADS="$ECC_CPU_THREADS" \
  "$ECC_WORKER_IMAGE" --check
```

After the check succeeds:

```sh
docker run -d --restart unless-stopped --name "$ECC_RUNNER_NAME" \
  --user "$(id -u):$(id -g)" --stop-timeout 120 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  --cpus "$ECC_CPU_THREADS" \
  -e ECC_API_URL=https://ecc-challenges.com -e ECC_PROTOCOL=2 \
  -e ECC_CAMPAIGN="$ECC_CAMPAIGN" -e ECC_BACKEND=cpu \
  -e OMP_NUM_THREADS="$ECC_CPU_THREADS" \
  -e ECC_API_KEY_FILE=/run/secrets/ecc_api_key -e ECC_STATE_DIR=/data \
  --mount "type=bind,src=$ECC_KEY_FILE,dst=/run/secrets/ecc_api_key,readonly" \
  --mount "type=bind,src=$ECC_STATE_DIR,dst=/data" \
  "$ECC_WORKER_IMAGE"
```

## CUDA: offline check, then one persistent runner per allocated card

Use the combined amd64 image, a compatible NVIDIA driver, and NVIDIA Container
Toolkit. Restrict the container to one allocated device. Inside that container
the single visible CUDA device is index `0`, irrespective of its host index.

```sh
test -n "${ECC_DEVICE_ID:?set an allocated host GPU}"
docker run --rm --network none --gpus "device=$ECC_DEVICE_ID" \
  -e ECC_BACKEND=cuda -e ECC_GPU=0 "$ECC_WORKER_IMAGE" --check
```

After the check succeeds:

```sh
docker run -d --restart unless-stopped --name "$ECC_RUNNER_NAME" \
  --user "$(id -u):$(id -g)" --stop-timeout 120 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true \
  --gpus "device=$ECC_DEVICE_ID" \
  -e ECC_API_URL=https://ecc-challenges.com -e ECC_PROTOCOL=2 \
  -e ECC_CAMPAIGN="$ECC_CAMPAIGN" -e ECC_BACKEND=cuda -e ECC_GPU=0 \
  -e ECC_API_KEY_FILE=/run/secrets/ecc_api_key -e ECC_STATE_DIR=/data \
  --mount "type=bind,src=$ECC_KEY_FILE,dst=/run/secrets/ecc_api_key,readonly" \
  --mount "type=bind,src=$ECC_STATE_DIR,dst=/data" \
  "$ECC_WORKER_IMAGE"
```

Repeat for each requested card with a different host device, container name,
and state directory, within the account's active-run capacity. Alternatively use
`deploy/docker/compose.yaml`, setting the actual UID/GID, pinned image, campaign,
device IDs, and per-service state paths; run only the requested profile/services.
Do not use `docker compose down -v` or erase bind-mounted data to restart.

## Observe and manage

```sh
docker ps -a --filter "name=^/${ECC_RUNNER_NAME}$" \
  --format '{{.Names}} {{.Status}} {{.Image}}'
docker logs --tail 80 "$ECC_RUNNER_NAME"
docker stats --no-stream "$ECC_RUNNER_NAME"
```

Inspect safe checkpoint metadata on the host as described in the [skill](../SKILL.md), then
check the run's private dashboard heartbeat and upload receipts. A container can
be running while registration or uploads are failing. Do not dump `docker
inspect` output containing its environment; select only needed fields.

Commands to include in the handoff (with the actual container name):

```sh
docker logs --tail 80 -f "$ECC_RUNNER_NAME"
docker stop -t 120 "$ECC_RUNNER_NAME"
docker start "$ECC_RUNNER_NAME"
```

For an image update, stop gracefully and inspect the exit before recreating the
container with the new pinned image and **the same** mounts and run settings.
Keep the old launch definition/digest for recovery. Never use `docker kill` as a
routine stop. If a round exceeds the grace period, diagnose it and allow a
longer graceful stop rather than repeatedly forcing termination.
