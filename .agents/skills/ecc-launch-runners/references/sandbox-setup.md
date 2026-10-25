# Install and use gVisor or Kata

Read the repo's [sandbox checks](../../../../docs/sandbox-runtimes.md) and the
current upstream installation docs linked below. These are optional Linux
runtime choices selected by the user. Install both only when both were
requested, and test each separately. Keep the host's default runtime unchanged.
Use [host setup](host-setup.md) for Docker and configuration backups first.
The Docker snippets assume access to the selected daemon; use sudo consistently
when required. Run shell examples with error checking.

## gVisor (`runsc`)

Use the [official install guide](https://gvisor.dev/docs/user_guide/install/)
and [Docker integration](https://gvisor.dev/docs/user_guide/quick_start/docker/).
Check the current kernel/architecture requirements (currently Linux 5.6+ on
x86-64/arm64). Reuse a compatible registered runtime. For a new Debian/Ubuntu
install, after verifying existing repository entries:

```sh
set -eu
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg
umask 077
ECC_GVISOR_SETUP=$(mktemp -d)
curl -fsSL https://gvisor.dev/archive.key -o "$ECC_GVISOR_SETUP/archive.key"
gpg --batch --dearmor -o "$ECC_GVISOR_SETUP/archive.gpg" "$ECC_GVISOR_SETUP/archive.key"
sudo install -m 0644 "$ECC_GVISOR_SETUP/archive.gpg" /usr/share/keyrings/gvisor-archive-keyring.gpg
printf 'deb [arch=%s signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main\n' \
  "$(dpkg --print-architecture)" | sudo tee /etc/apt/sources.list.d/gvisor.list >/dev/null
sudo apt-get update
sudo apt-get install -y --no-install-recommends runsc
runsc --version
```

The package may register Docker automatically. Inspect `docker info` and the
configuration diff before doing more. If registration is missing, `sudo runsc
install` adds the runtime; validate the daemon config and reload/restart under
the host-setup rules. Confirm `runsc` is present in `docker info` and actually
executes the smoke below. If apt is unavailable, use the upstream verified
release archive; modern releases include sidecar files (`gvisor-bin/`), so
copying just the `runsc` binary is not a complete installation.

## Kata Containers (runtime-rs with QEMU)

Use the [official installation guide](https://kata-containers.github.io/kata-containers/installation/).
For the single-box Docker recipe require Docker 26+, working CPU virtualization,
accessible `/dev/kvm`, and the needed vhost devices/modules. Inside a VM, verify
nested virtualization is exposed. Do not attempt to manufacture KVM by adding
`--privileged` to a worker container.

Inspect the host before downloading: check `/dev/kvm`, its accessibility to the
daemon/runtime account, and virtualization flags on x86-64. Load `kvm_intel` or
`kvm_amd` only for the actual CPU when needed, plus `vhost_vsock`/`vhost_net`;
verify `/dev/vhost-vsock` and make required module loading persistent. Firmware,
hypervisor, missing nested virtualization, and unavailable kernel modules are
host constraints; report them instead of claiming an installed package fixes
them. This QEMU/KVM recipe does not cover a different hypervisor's `/dev/mshv`.

On a new compatible host, choose a fixed stable release from the official
release metadata and retain it. The example resolves the latest stable release
once, then uses that exact asset URL and its published digest. Requires `jq`,
`curl`, `tar`, and `zstd`; install missing packages through the host package
manager. Do not unpack over an existing `/opt/kata`; inspect/plan its upgrade.

```sh
set -eu
umask 077
ECC_KATA_SETUP=$(mktemp -d)
case "$(uname -m)" in
  x86_64) ECC_KATA_ARCH=amd64 ;;
  aarch64) ECC_KATA_ARCH=arm64 ;;
  *) echo 'Select a supported worker/runtime architecture before installing' >&2; exit 1 ;;
esac
curl -fsSL https://api.github.com/repos/kata-containers/kata-containers/releases/latest \
  -o "$ECC_KATA_SETUP/release.json"
ECC_KATA_VERSION=$(jq -er '.tag_name' "$ECC_KATA_SETUP/release.json")
ECC_KATA_ASSET="kata-static-$ECC_KATA_VERSION-$ECC_KATA_ARCH.tar.zst"
ECC_KATA_URL=$(jq -er --arg name "$ECC_KATA_ASSET" \
  '.assets[] | select(.name == $name) | .browser_download_url' "$ECC_KATA_SETUP/release.json")
ECC_KATA_SHA=$(jq -er --arg name "$ECC_KATA_ASSET" \
  '.assets[] | select(.name == $name) | .digest | select(startswith("sha256:")) | ltrimstr("sha256:")' \
  "$ECC_KATA_SETUP/release.json")
test "${#ECC_KATA_SHA}" -eq 64
curl -fL "$ECC_KATA_URL" -o "$ECC_KATA_SETUP/kata.tar.zst"
printf '%s  %s\n' "$ECC_KATA_SHA" "$ECC_KATA_SETUP/kata.tar.zst" | sha256sum -c -
tar --zstd -tf "$ECC_KATA_SETUP/kata.tar.zst" > "$ECC_KATA_SETUP/archive-paths.txt"
```

If an older release lacks a digest field, verify its upstream-published checksum
or signature before continuing; do not skip verification. Inspect archive paths
and links before extraction. Stage the verified archive, inspect its expected
`opt/kata` tree and free space, then copy that tree to a new `/opt/kata` rather
than extracting unrelated files as root. Use runtime-rs, not the deprecated Go
shim; retain the selected version, architecture, and checksum.

Locate and verify the executable shim and packaged QEMU configuration. Current
packages use the following paths; stop and consult the selected release docs if
they differ. Merge only the named runtime entry into a fresh snapshot of the
actual daemon configuration, after its previous changes have been applied:

```sh
set -eu
test -n "${ECC_KATA_SETUP:?set the retained setup directory}"
ECC_KATA_SHIM=/opt/kata/runtime-rs/bin/containerd-shim-kata-v2
ECC_KATA_CONFIG=/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-qemu-runtime-rs.toml
test -x "$ECC_KATA_SHIM"
test -f "$ECC_KATA_CONFIG"
if sudo test -f /etc/docker/daemon.json; then
  sudo cat /etc/docker/daemon.json > "$ECC_KATA_SETUP/daemon.before.json"
else
  printf '{}\n' > "$ECC_KATA_SETUP/daemon.before.json"
fi
jq --arg shim "$ECC_KATA_SHIM" --arg config "$ECC_KATA_CONFIG" \
  '.runtimes = (.runtimes // {}) | .runtimes.kata = {runtimeType: $shim, options: {ConfigPath: $config}}' \
  "$ECC_KATA_SETUP/daemon.before.json" > "$ECC_KATA_SETUP/daemon.kata.json"
sudo dockerd --validate --config-file "$ECC_KATA_SETUP/daemon.kata.json"
```

Before replacing an existing `kata` entry, inspect whether it serves other
workloads; choose a distinct name if needed and use that name consistently.
Back up the original config durably, check that it has not changed since the
snapshot, install the merged file with protected permissions, then apply a
supported daemon reload/restart within scope. Retain all other settings and
the old config for rollback. Verify the shim version, Docker's registered
runtime, and real execution using the CPU smoke below.

## Require the selected runtime through checks and live launch

Set `ECC_CONTAINER_RUNTIME` to `runsc`, `kata`, or the actual configured name;
set `ECC_WORKER_IMAGE` to the selected pinned CPU worker digest and
`ECC_RUNTIME_RECEIPT` to a new absolute receipt path outside temporary/worker
state directories. Run from the repository root:

```sh
test -n "${ECC_CONTAINER_RUNTIME:?select the installed runtime}"
test -n "${ECC_RUNTIME_RECEIPT:?set a new absolute receipt path}"
python3 scripts/check_sandbox_runtime.py \
  --runtime "$ECC_CONTAINER_RUNTIME" --image "$ECC_WORKER_IMAGE" \
  --all-curves --receipt "$ECC_RUNTIME_RECEIPT"
```

This uses no API key and checks all 24 curves plus an unprivileged mode-0600
checkpoint written/resumed from separate containers. It deletes only its own
disposable smoke volume. Require the actual exit code and retained receipt;
registered runtime names and `hello-world` alone do not validate the ECC worker.
Repeat for both runtimes if both were requested, keeping separate receipts.

Then launch the contributor using [Docker launches](docker.md), retaining
`--runtime "$ECC_CONTAINER_RUNTIME"` in the persistent definition. Verify the
live container's `HostConfig.Runtime`, checkpoint progression, run heartbeat,
and eventual acknowledged uploads. Describe offline correctness, live
connectivity, uploads, and server verification separately. For live runtime
qualification, use a bounded canary authorized for that gateway, exercise
restart/resume on the same state and flush/close as appropriate; retain server
receipts before declaring the whole lifecycle verified.

## GPU sandboxing is a separate device setup

For gVisor CUDA follow its [GPU guide](https://gvisor.dev/docs/user_guide/gpu/):
check the installed `runsc` supported-driver list against the exact host driver,
configure an explicitly named nvproxy-enabled runtime, and use the supported
NVIDIA injection/CDI path. Preserve existing NVIDIA configuration and other
workloads. Do not bypass unknown/unsupported-driver checks. Run the **CUDA**
ECC image check on the allocated GPU with that runtime; the CPU smoke above
does not establish GPU support.

For Kata CUDA follow the [NVIDIA passthrough guide](https://github.com/kata-containers/kata-containers/blob/main/docs/use-cases/NVIDIA-GPU-passthrough-and-Kata-QEMU.md)
for the actual release: host IOMMU/VFIO, allocated passthrough devices, guest
driver/toolkit image, and GPU-specific VMM configuration are required. Do not
unbind a GPU in use or treat adding `--runtime kata --gpus all` as a complete
passthrough setup. The normal host-driver-backed CUDA image may not be the
required guest image. Verify the intended curve engine, persistence, upload,
and restart on that exact stack before claiming success. If the host cannot
satisfy it, report the specific blocker and keep completed unaffected setup.
