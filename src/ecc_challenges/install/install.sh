#!/bin/sh
# Download, build and start the ECC Challenges worker on Linux or macOS.
#
#   curl -fsSL https://ecc-challenges.com/install.sh | sh
#   curl -fsSL https://ecc-challenges.com/install.sh | ECC_CAMPAIGN=ecc2-131 sh
#
# Installs to ~/.local/ecc-challenges without root, asks for your API key once
# (saved to ~/.config/ecc-challenges/api-key, mode 0600) and offers to start a
# worker. Run it again to update. Settings: ECC_CAMPAIGN (default eccp-131),
# ECC_PREFIX, ECC_STATE_DIR, ECC_REF (git ref, default main), ECC_START=no.
# Needs curl, tar, python3 3.10+ and a C++17 compiler; this script names the
# packages to install if one is missing. Source: deploy/native/install.sh at
# https://github.com/aburan28/ecc-challenges
set -eu

repo=aburan28/ecc-challenges
api=${ECC_API_URL:-https://ecc-challenges.com}
ref=${ECC_REF:-main}
campaign=${ECC_CAMPAIGN:-eccp-131}
prefix=${ECC_PREFIX:-$HOME/.local/ecc-challenges}
state=${ECC_STATE_DIR:-$HOME/ecc-data/$campaign}
keyfile=$HOME/.config/ecc-challenges/api-key
os=$(uname -s)

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
# stdin is this script when piped from curl, so questions go to the terminal.
tty=no
if [ -r /dev/tty ] && [ -w /dev/tty ] && (: </dev/tty) 2>/dev/null; then tty=yes; fi

missing=
command -v curl >/dev/null 2>&1 || missing="$missing curl"
command -v tar >/dev/null 2>&1 || missing="$missing tar"
python=${PYTHON:-$(command -v python3 || true)}
if [ -z "$python" ] || ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
  missing="$missing python3>=3.10"
fi
command -v g++ >/dev/null 2>&1 || command -v clang++ >/dev/null 2>&1 || missing="$missing c++-compiler"
if [ -n "$missing" ]; then
  echo "missing:$missing" >&2
  case "$os" in
    Darwin) echo "install with:  xcode-select --install   and Python 3.10+ from python.org or 'brew install python'" >&2 ;;
    *) if command -v apt-get >/dev/null 2>&1; then echo "install with:  sudo apt-get install -y curl tar python3 g++" >&2
       elif command -v dnf >/dev/null 2>&1; then echo "install with:  sudo dnf install -y curl tar python3 gcc-c++" >&2
       elif command -v pacman >/dev/null 2>&1; then echo "install with:  sudo pacman -S --needed curl tar python gcc" >&2
       elif command -v apk >/dev/null 2>&1; then echo "install with:  sudo apk add curl tar python3 g++" >&2
       fi ;;
  esac
  if [ -n "$python" ] && "$python" -V >/dev/null 2>&1; then
    echo "$python is $("$python" -V 2>&1); install python3.12 (or 3.10+) and set PYTHON=/path/to/it" >&2
  fi
  echo "then run this command again" >&2
  exit 1
fi
export PYTHON="$python"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
say "downloading $repo ($ref)"
curl -fsSL "https://codeload.github.com/$repo/tar.gz/$ref" | tar -xz -C "$work" --strip-components=1 \
  || die "could not download https://github.com/$repo at $ref"

say "building the worker into $prefix (a few minutes)"
sh "$work/deploy/native/install.sh" --prefix "$prefix" --no-systemd >"$work/build.log" 2>&1 || {
  tail -n 30 "$work/build.log" >&2
  die "build failed; the full log is above. Please open an issue at https://github.com/$repo/issues"
}
grep -E '"(backends|metal_devices|cuda_devices)"' "$work/build.log" 2>/dev/null || true

if [ ! -s "$keyfile" ]; then
  [ "$tty" = yes ] || die "no API key at $keyfile; create one at $api/dashboard, save it there and run this again"
  say "paste the API key from $api/dashboard (input is hidden), then press Enter"
  stty -echo </dev/tty 2>/dev/null || true
  IFS= read -r key </dev/tty || key=
  stty echo </dev/tty 2>/dev/null || true
  echo
  key=$(printf '%s' "$key" | tr -d '[:space:]')
  case "$key" in ecc1_*) ;; *) die "that doesn't look like an API key (they start with ecc1_)" ;; esac
  mkdir -p "$(dirname "$keyfile")"
  (umask 077; printf '%s\n' "$key" >"$keyfile")
  say "saved the key to $keyfile"
fi

mkdir -p "$state"
run="ECC_CAMPAIGN=$campaign ECC_STATE_DIR=\"$state\" ECC_API_KEY_FILE=\"$keyfile\" \"$prefix/bin/ecc-worker\""
cat <<NEXT

Installed. Start a worker any time with:
  $run
Stop it with Ctrl-C: it finishes its round, uploads and saves. Keep
$state; the run resumes from it.
NEXT

if ! curl -fsS "$api/v1/campaigns" 2>/dev/null | tr -d ' \n' | grep -q '"multicurve_enabled":true'; then
  cat <<NOTE

Note: $api is not yet accepting native workers (all-curves release is off;
see $api/v1/campaigns). Until then only the NVIDIA Docker worker on the
dashboard is accepted. Start the command above once it is on.
NOTE
  exit 0
fi

[ "$tty" = yes ] && [ "${ECC_START:-ask}" != no ] || exit 0
printf 'Start the worker now? [Y/n] ' >/dev/tty
IFS= read -r answer </dev/tty || answer=n
case "$answer" in [nN]*) exit 0 ;; esac
trap - EXIT INT TERM
rm -rf "$work"
ECC_CAMPAIGN=$campaign ECC_STATE_DIR=$state ECC_API_KEY_FILE=$keyfile exec "$prefix/bin/ecc-worker" </dev/tty
