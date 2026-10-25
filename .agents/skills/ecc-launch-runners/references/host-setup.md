# Install the host stack

Carry a request to get a box running through installation as well as launch.
Use only the selected host and compute budget. The commands here target a new
supported Ubuntu/Debian Linux host with systemd and root/sudo; check the current
vendor instructions for the actual OS before execution. On other distributions
use their supported package repositories. On macOS/Windows use the native
prerequisites in [native launches](native.md); Docker Desktop is not a way to
make Metal or Linux KVM passthrough available.

## Inspect, reuse, and preserve

Inspect `/etc/os-release`, architecture, kernel, available disk/memory, daemon
context, package versions, and existing workloads. Ensure `docker` commands
target the intended daemon: a remote Docker context is not the local host.
Use a working compatible installation as-is. An installed CLI without a running
reachable Engine does not complete Docker setup.

Before changing a daemon, retain its exact configuration and service flags in a
protected backup outside the repo. Do not print proxy credentials or replace
`daemon.json` wholesale. Keep other runtimes, the default runtime, registry/CA,
proxy, network, logging, and storage settings. Inspect conflicts with existing
`docker.io`, `containerd`, `runc`, or cluster packages before any migration;
do not use a blanket removal command on a machine with existing workloads.
Runtime package post-install hooks may also change/restart Docker.

Use reload when the installed daemon supports the changed setting. A required
restart must fit the agreed maintenance scope; otherwise retain the prepared
change and explain the specific interruption before requesting that scope.
Do not stop unrelated containers to make setup look successful. Use sudo for
Docker commands when needed rather than making its socket world-writable or
silently changing users' groups. The recipes' `--user` must still identify the
intended runner account, not accidentally root because the shell uses sudo.

## Docker Engine and Compose

Sources: Docker's [Ubuntu](https://docs.docker.com/engine/install/ubuntu/) and
[Debian](https://docs.docker.com/engine/install/debian/) installation guides.
Only do this when a compatible Engine is absent and package conflicts have
been resolved within scope. Skip already configured vendor keys/repositories.
Use the detected distribution and codename, not a guessed Ubuntu release.
Run the examples with error checking; carry required variables/absolute paths
explicitly between execution cells that do not share a shell.

```sh
set -eu
. /etc/os-release
case "$ID" in
  ubuntu) ECC_DOCKER_DISTRO=ubuntu; ECC_DOCKER_SUITE=${UBUNTU_CODENAME:-$VERSION_CODENAME} ;;
  debian) ECC_DOCKER_DISTRO=debian; ECC_DOCKER_SUITE=$VERSION_CODENAME ;;
  *) echo 'Use the Docker installation guide for this distribution' >&2; exit 1 ;;
esac
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg jq python3 git
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL "https://download.docker.com/linux/$ECC_DOCKER_DISTRO/gpg" \
  -o /etc/apt/keyrings/docker.asc
sudo chmod 0644 /etc/apt/keyrings/docker.asc
printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "$ECC_DOCKER_DISTRO" "$ECC_DOCKER_SUITE" "$(dpkg --print-architecture)" \
  | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker info --format '{{json .Runtimes}}'
sudo docker compose version
```

Record installed versions. Verify actual container execution using the selected
pinned ECC image's offline check in [Docker launches](docker.md), not just
`docker --version`. Configure the requested sandbox before that check when
applicable. Runner containers need outbound HTTPS and their state/key mounts;
no inbound published ports are required.

## NVIDIA driver and Container Toolkit

Check `nvidia-smi` first. On a blank dedicated GPU host, install a compatible
distribution-packaged driver when absent, following NVIDIA's current driver
guide, and account for any required reboot before continuing. Do not replace a
working shared-host driver by inference. A pending reboot or unavailable driver
means CUDA setup is incomplete.

For a compatible existing driver and missing toolkit, use NVIDIA's
[Container Toolkit installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
On Ubuntu/Debian, add its signed production repository, install the stable
`nvidia-container-toolkit` package and dependencies, then configure Docker:

```sh
sudo nvidia-ctk runtime configure --runtime=docker
```

Back up and inspect the daemon configuration diff before applying the required
daemon restart. Keep the default runtime unchanged. Confirm the registered
`nvidia` runtime and run the ECC image's offline **CUDA** check on the allocated
device with `ECC_CONTAINER_RUNTIME=nvidia`. A successful `nvidia-smi` alone is
not an ECC engine check. For gVisor/Kata CUDA, continue with the sandbox-specific
GPU requirements; installing this toolkit alone is insufficient.

## Native prerequisites

For the Linux native path, install the missing Python 3.10+, compiler, and build
tools through the distribution package manager (for example `python3`,
`build-essential`, and `cmake` on supported Ubuntu/Debian). Add a compatible CUDA
toolkit only when a native CUDA build is requested; `nvcc` is unnecessary for
prebuilt CUDA containers. On macOS install missing Xcode command-line tools and
a supported Python. On Windows install the documented Python/CMake/Visual
Studio C++ build prerequisites. Observe the actual installers' completion;
interactive OS installers or required reboots remain incomplete until finished.

Then follow [native launches](native.md) through backend check, persistent
supervisor configuration, startup, and dashboard verification. Merely installing
the build dependencies is not the end of the task.
