# Native launches

Use the repository's installers and supervisors; do not write another native
worker. Start from the selected source commit and preserve existing installs
and checkpoints on resume. These recipes run the generic protocol-2 client.

## Linux with systemd

Read `deploy/native/install.sh`, `deploy/systemd/ecc-worker@.service`, and
`deploy/systemd/example.env`. Needs Python 3.10+ and a C++17 compiler. For explicit
CUDA, the installer's account must see `nvcc`; use `CUDA=yes` so a missing CUDA
toolkit fails rather than silently producing a CPU-only install. The container
recipe is usually easier for NVIDIA hosts without a toolkit.

For a new install, from the repo root:

```sh
sudo deploy/native/install.sh
```

The installer builds and self-tests the engine, prints `--probe`, installs
`/opt/ecc-challenges/bin/ecc-worker`, and installs the service template. Before
starting any new runner, check the installed worker on the intended backend:

```sh
ECC_BACKEND=cpu /opt/ecc-challenges/bin/ecc-worker --check
```

For CUDA use `ECC_BACKEND=cuda ECC_GPU=DEVICE` instead, substituting the allocated
host device. A successful `--probe` or CPU-only self-test is not a CUDA check.

Provision the existing key as `/etc/ecc-challenges/api-key`, mode 0600, without
printing it. The service uses `LoadCredential` to hand a private copy to the
`ecc-worker` user; do not relax key permissions to make it readable by that user.
Create a new `/etc/ecc-challenges/INSTANCE.env` from `example.env`, with the
selected `ECC_CAMPAIGN`, explicit `ECC_BACKEND`, `ECC_PROTOCOL=2`, and either
`ECC_GPU` or the allocated `OMP_NUM_THREADS`. Inspect existing instance files
before editing them. Do not reuse an instance with unrelated saved state.

Set `ECC_INSTANCE` to the selected instance name (e.g. `cpu` or `gpu0`). The
supplied unit binds that instance to `/var/lib/ecc-challenges/INSTANCE` and
allows 150 seconds for a graceful stop:

```sh
sudo systemctl enable --now "ecc-worker@$ECC_INSTANCE"
systemctl is-active "ecc-worker@$ECC_INSTANCE"
sudo journalctl -u "ecc-worker@$ECC_INSTANCE" -n 80 --no-pager
```

Verify durable state and the private dashboard, then hand off these commands:

```sh
sudo journalctl -fu "ecc-worker@$ECC_INSTANCE"
sudo systemctl stop "ecc-worker@$ECC_INSTANCE"
sudo systemctl start "ecc-worker@$ECC_INSTANCE"
```

`stop` leaves an enabled service able to start on a later boot; use `disable
--now` as well when the requested stop must survive reboot. Neither deletes
state or closes the server run.

## Linux without root/systemd

```sh
deploy/native/install.sh --prefix "$HOME/.local/ecc-challenges" --no-systemd
ECC_BACKEND=cpu "$HOME/.local/ecc-challenges/bin/ecc-worker" --check
```

Run the installed wrapper with explicit `ECC_CAMPAIGN`, `ECC_PROTOCOL=2`,
`ECC_BACKEND`, `ECC_API_KEY_FILE`, absolute `ECC_STATE_DIR`, and the agreed CPU
thread/device setting. For a persistent launch, use an available user supervisor
with a 150-second stop timeout. A foreground terminal or `nohup` by itself does
not establish restart-after-reboot behavior; describe that limit if it is the
user's chosen launch mode. Keep separate state directories for each process.

## macOS with Metal

Read the macOS section of `docs/running-a-worker.md` and
`deploy/launchd/com.ecc-challenges.worker.plist`. Needs Python 3.10+ and Xcode
command-line tools. Run the native installer as the target login user:

```sh
deploy/native/install.sh --prefix "$HOME/.local/ecc-challenges"
ECC_BACKEND=metal "$HOME/.local/ecc-challenges/bin/ecc-worker" --check
```

The installer writes a plist at
`~/.local/ecc-challenges/com.ecc-challenges.worker.plist`. Inspect the installed
engine's `--probe` for Metal devices. Set the generated plist's environment to
the chosen campaign, explicit backend (`metal` for a GPU request), device when
needed, `ECC_PROTOCOL=2`, protected key-file path, and dedicated durable state.
Retain `ExitTimeOut=150`. Use unique labels, plist names, log paths, and state
paths when more than one runner was requested; never overwrite an existing
loaded agent to add another runner. Create the target user's log directory.

Copy the configured plist to `~/Library/LaunchAgents/`, validate with
`plutil -lint`, and load it with `launchctl bootstrap gui/UID PLIST_PATH`, using
the target login user's UID. Check `launchctl print gui/UID/LABEL` and bounded
logs. For stop use `launchctl bootout gui/UID PLIST_PATH`; bootstrap the same
plist to resume. A launch agent starts at login; do not promise pre-login boot
startup. Do not select Docker to access Metal.

## Windows

Read `deploy/native/install.ps1` and the Windows section of the worker guide.
Use Python 3.10+, CMake 3.21+, and Visual Studio 2022 C++ Build Tools. Follow the
host's execution policy; do not change machine-wide policy. From the repo root:

```powershell
& .\deploy\native\install.ps1
& "$env:LOCALAPPDATA\ecc-challenges\bin\generic-engine.exe" --probe
$env:ECC_BACKEND = 'cpu'
& "$env:LOCALAPPDATA\ecc-challenges\bin\ecc-worker.cmd" --check
```

For requested CUDA, require an x64 host/toolkit, use the installer's `-Cuda On`,
and run the check with `ECC_BACKEND=cuda` and the allocated `ECC_GPU`. The
installer's default prefix is `%LOCALAPPDATA%\ecc-challenges`. Protect the
existing key with an ACL for the runner account. Set explicit campaign,
protocol, backend, key path, state directory, and resource limits before
starting `bin\ecc-worker.cmd`.

Use Ctrl-C for a foreground stop and let the worker save. For an unattended
runner, configure the user's selected service/task supervisor to pass the same
environment and paths, capture bounded logs, and permit graceful shutdown.
Do not claim persistence just because the foreground command started, and
do not kill the whole console or delete its state as a restart method.
