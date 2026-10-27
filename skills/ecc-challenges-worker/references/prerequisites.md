# Install worker prerequisites

The [worker skill](https://ecc-challenges.com/skill.md) installs the tools for
your chosen setup, then continues through worker installation and checks. Give
your agent the target machine and a resource limit. These steps apply to that
machine, including when the agent connects by SSH.

## Choose what to install

| Worker setup | Required on the host |
| --- | --- |
| Linux CPU container | Docker Engine; Compose only for the Compose recipe |
| Linux NVIDIA container | Docker Engine, a compatible NVIDIA driver and NVIDIA Container Toolkit |
| Native Linux CPU | Python 3.10+, a C++17 compiler, curl and tar |
| Native macOS CPU/Metal | Python 3.10+, Xcode command-line tools, curl and tar |
| Native Windows CPU | Python 3.10+, CMake 3.21+, Visual Studio 2022 C++ Build Tools |
| Native NVIDIA CUDA | Native build tools plus a compatible NVIDIA driver and CUDA toolkit with nvcc |

Prebuilt containers include the worker's Python and build dependencies. Native
workers do not need Docker. Node.js and npm are needed only for the optional
`npx skills add` method; agents can read the public skill URL directly.

Before installing, inspect the OS version, architecture, privileges, free disk
and existing workers. Reuse compatible tools. On a shared host, keep the Docker
configuration and workloads: do not remove competing packages, change the
default runtime, replace a working driver or restart the daemon until you know
the effect on those workloads. A pending reboot or interactive installer is an
unfinished setup step.

## Linux: Docker Engine

Check the current Docker context and whether both client and server respond:

```sh
docker context show
docker version
```

Use the intended local or remote daemon throughout. For a permission error, use
the administrator-approved Docker access method, such as sudo on a rootful
Engine. Docker group membership grants root-level privileges, and the socket
must never be world-writable. Keep the contributor's UID and GID for the
dashboard recipe's key and state mounts, even if Docker itself runs under sudo.

If the Engine is absent, follow Docker's current installation instructions for
[Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Debian](https://docs.docker.com/engine/install/debian/), or the
[your distribution](https://docs.docker.com/engine/install/). Check for package
conflicts first; a working distribution-provided Engine can stay. The example
below is for a new Ubuntu or Debian host with systemd and sudo. Run it as one
block, and only after confirming this is the intended machine and that it has
no conflicting packages or existing Docker repository entries:

```sh
set -eu
. /etc/os-release
case "$ID" in
  ubuntu) ECC_DOCKER_DISTRO=ubuntu; ECC_DOCKER_SUITE=${UBUNTU_CODENAME:-$VERSION_CODENAME} ;;
  debian) ECC_DOCKER_DISTRO=debian; ECC_DOCKER_SUITE=$VERSION_CODENAME ;;
  *) echo 'Follow the Docker guide for this distribution' >&2; exit 1 ;;
esac
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl
sudo install -d -m 0755 /etc/apt/keyrings
sudo curl -fsSL "https://download.docker.com/linux/$ECC_DOCKER_DISTRO/gpg" \
  -o /etc/apt/keyrings/docker.asc
sudo chmod 0644 /etc/apt/keyrings/docker.asc
printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
  "$ECC_DOCKER_DISTRO" "$ECC_DOCKER_SUITE" "$(dpkg --print-architecture)" \
  | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io
sudo systemctl enable --now docker
sudo docker version
```

For the Compose recipe, also install `docker-compose-plugin` from that Docker
repository and verify `docker compose version`. A single dashboard container
does not need Compose. For rootless Docker or a host without systemd, follow
Docker's matching instructions instead of the service command above.

## Linux: NVIDIA containers

Inspect the allocated GPU with `nvidia-smi` and check the selected image's CUDA
driver requirements in the [worker guide](https://ecc-challenges.com/docs/running-a-worker#images).
If the driver is absent or incompatible, use NVIDIA's
[driver guide](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/)
for this OS and GPU. Complete any required reboot before proceeding.

If GPU container support is missing, follow the signed production repository
steps in NVIDIA's
[Container Toolkit guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
Install its `nvidia-container-toolkit` package, retain a protected backup of the
existing Docker configuration, then configure the NVIDIA runtime:

```sh
sudo nvidia-ctk runtime configure --runtime=docker
```

Review the configuration change and keep unrelated settings and the current
default runtime. Restart Docker within the host's maintenance scope, then
verify container execution below. A prebuilt CUDA image needs no `nvcc` on the
host. `nvidia-smi` on the host does not show that a container can use the GPU.

If the user asked for a gVisor or Kata runtime, use the repository's
[runner setup skill](https://github.com/isogeny-labs/ecc-challenges/tree/main/.agents/skills/ecc-launch-runners)
and the [sandbox guide](https://github.com/isogeny-labs/ecc-challenges/blob/main/docs/sandbox-runtimes.md).
Those runtimes have extra virtualization and GPU requirements, and ordinary
NVIDIA container support does not show that they work.

## Linux: native build tools

On supported Ubuntu/Debian hosts, install missing tools with:

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3 build-essential ca-certificates curl tar
python3 -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.executable, sys.version)'
c++ --version
```

On other distributions, use their package manager for equivalent tools. If the
OS ships an older Python, install a supported one alongside it, keep the system
Python, and pass the new interpreter's absolute path as `PYTHON` to the worker
installer. The Unix installer compiles directly; CMake and Git are needed only
by recipes that use them. Install the CUDA toolkit only for a requested native
CUDA build, then check `nvcc --version` and driver compatibility.

## macOS: native build tools

Use the native worker on a Mac so it can use Metal. Docker Desktop runs a Linux
VM and does not expose the Mac GPU as Metal. Check the
[Apple command-line tools](https://developer.apple.com/xcode/resources/):

```sh
xcode-select -p
xcrun --find clang++
```

If missing, run `xcode-select --install` and wait for the macOS installer to
finish, then repeat the checks. Install Python 3.10+ from
[python.org](https://www.python.org/downloads/macos/), or use `brew install python`
when Homebrew is already available. Verify the selected interpreter:

```sh
python3 -c 'import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.executable, sys.version)'
clang++ --version
```

Pass `PYTHON=/absolute/path/to/python3` to the worker installer if the shell
still selects an older Python. Run the native worker's Metal check on this
machine before choosing Metal for an ongoing run. If the user wants CPU
containers on macOS, install
[Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/),
finish its first launch, start the Engine and verify `docker version`.

## Windows: native build tools

Use Python 3.10+, CMake 3.21+ and Visual Studio 2022 or its Build Tools with
the "Desktop development with C++" workload. Match the machine's architecture;
arm64 hosts also need the arm64 C++ tools. Install from
[Python](https://docs.python.org/3/using/windows.html),
[CMake](https://cmake.org/download/) and
[Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022),
or use an existing WinGet installation. On an x64 host missing all three:

```powershell
winget install --exact --id Python.Python.3.12 --source winget
winget install --exact --id Kitware.CMake --source winget
winget install --exact --id Microsoft.VisualStudio.2022.BuildTools --source winget --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Wait for each installation and check its result. Microsoft documents the C++
workload identifiers in
[Visual Studio Build Tools workload IDs](https://learn.microsoft.com/en-us/visualstudio/install/workload-component-id-vs-build-tools?view=vs-2022).
Reopen PowerShell to refresh PATH; use a Developer PowerShell if CMake is only
available there. Check `py -3 --version` (at least 3.10) and `cmake --version`
(at least 3.21) before running the native worker installer, which verifies the
C++ build. Install the CUDA toolkit only for CUDA on supported x64 hosts.

If the user asks for a Windows container setup, follow
[Docker Desktop's Windows guide](https://docs.docker.com/desktop/setup/install/windows-install/)
through WSL2/virtualization and first-start checks, then select Linux containers.
The native worker needs no Docker, WSL or Linux VM.

## Verify and continue to the worker

Take the exact image digest from the live catalog or dashboard. Set
`ECC_WORKER_IMAGE` to `cpu_worker_image` for CPU or `worker_image` for NVIDIA,
then run the matching finite check without a key or state mount:

```sh
docker run --rm --network none -e ECC_BACKEND=cpu "$ECC_WORKER_IMAGE" --check
```

For NVIDIA, select the allocated GPU (device 0 here) and require CUDA, so
missing GPU support fails visibly:

```sh
docker run --rm --network none --gpus '"device=0"' \
  -e ECC_BACKEND=cuda "$ECC_WORKER_IMAGE" --check
```

For native installs, follow the [worker guide](https://ecc-challenges.com/docs/running-a-worker),
install with `ECC_START=no`, then run the printed worker command with `--check`
and the chosen `ECC_BACKEND`. These checks need no API key or run registration.
Then continue as [agent setup](https://ecc-challenges.com/docs/agents)
describes: enter the key locally, set up persistent state, start one bounded
worker, and verify its heartbeat and uploads. A working compiler or Docker
client alone does not complete onboarding.
