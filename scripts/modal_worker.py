#!/usr/bin/env python3
"""Launch persistent, named ECC Challenges GPU workers on Modal.

Install the optional ``modal`` package and run ``modal token new`` first.
The API key is read locally, sent as a Modal Secret, and never printed.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import stat


IMAGE = (
    "public.ecr.aws/o1a3z1g6/ecc-challenges-worker@sha256:"
    "51495ba436eba00427fc13542313f6b737b83134fcccef6d7d20b4b250d06098"
)
APP = "ecc-challenges-public-workers"
BOOT = """
import os
from pathlib import Path
import sys
key = os.environ.pop('ECC_API_KEY')
path = Path('/tmp/ecc_api_key')
path.write_text(key + '\\n')
path.chmod(0o600)
os.environ['ECC_API_KEY_FILE'] = str(path)
os.execv(sys.executable, [sys.executable, '/opt/ecc2k130/client.py'])
"""


def read_key(path: Path) -> str:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError("API key file must be readable only by its owner (chmod 600)")
    key = path.read_text().strip()
    if not re.fullmatch(r"ecc1_[0-9a-f]{24}_[A-Za-z0-9_-]{43}", key):
        raise ValueError("API key file has an invalid format")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--key-file", type=Path, help="0600 API key file; required for start")
    parser.add_argument("--workers", type=int, default=1, help="named workers, 1 to 8")
    parser.add_argument("--hours", type=int, default=1, help="sandbox lifetime, 1 to 24 hours")
    parser.add_argument("--app", default=APP)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be 1 to 8 (the gateway caps active runs per account)")
    if not 1 <= args.hours <= 24:
        parser.error("--hours must be 1 to 24")
    if args.action == "start" and args.key_file is None:
        parser.error("start requires --key-file")

    try:
        import modal
    except ImportError:
        parser.error("install Modal first: python3 -m pip install modal")

    if args.action == "start":
        key = read_key(args.key_file)
        app = modal.App.lookup(args.app, create_if_missing=True)
        image = modal.Image.from_registry(IMAGE).dockerfile_commands("ENTRYPOINT []")
        secret = modal.Secret.from_dict({"ECC_API_KEY": key})
        for index in range(args.workers):
            name = f"worker-{index}"
            try:
                existing = modal.Sandbox.from_name(args.app, name)
            except modal.exception.NotFoundError:
                existing = None
            if existing is not None and existing.poll() is None:
                print(f"{name}: already running ({existing.object_id})")
                continue
            volume = modal.Volume.from_name(
                f"{args.app}-{name}-state", create_if_missing=True
            )
            sandbox = modal.Sandbox.create(
                "python3", "-c", BOOT,
                name=name, app=app, image=image,
                secrets=[secret],
                env={"ECC_API_URL": "https://ecc-challenges.com", "ECC_STATE_DIR": "/data"},
                volumes={"/data": volume},
                gpu="RTX-PRO-6000", timeout=args.hours * 3600,
            )
            print(f"{name}: started {sandbox.object_id}; state volume {args.app}-{name}-state")
        return

    for index in range(args.workers):
        name = f"worker-{index}"
        try:
            sandbox = modal.Sandbox.from_name(args.app, name)
        except modal.exception.NotFoundError:
            print(f"{name}: not found")
            continue
        if args.action == "status":
            code = sandbox.poll()
            print(f"{name}: {sandbox.object_id}; {'running' if code is None else f'exited {code}'}")
        else:
            sandbox.terminate()
            print(f"{name}: stopped {sandbox.object_id}; state volume retained")


if __name__ == "__main__":
    main()
