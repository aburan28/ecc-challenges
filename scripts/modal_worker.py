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
import json
from urllib.request import urlopen, Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlencode


APP = "ecc-challenges-public-workers"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("unexpected gateway redirect")


def authenticated_config(key: str, campaign: str) -> dict:
    request = Request("https://ecc-challenges.com/v2/config?" + urlencode({"campaign": campaign}),
                      headers={"Authorization": "Bearer " + key})
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            value = json.load(response)
    except HTTPError as exc:
        raise ValueError(f"gateway does not enable protocol 2 for this key (HTTP {exc.code})") from None
    if (value.get("protocol") != 2 or value.get("campaign") != campaign
            or not re.fullmatch(r"[0-9a-f]{64}", value.get("namespace_id", ""))):
        raise ValueError("gateway returned an invalid namespace")
    return value
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
    parser.add_argument("--campaign", default="ecc2k-130")
    parser.add_argument("--protocol", type=int, choices=(1,2), default=2)
    parser.add_argument("--backend", choices=("auto","cpu","cuda"), default="auto")
    parser.add_argument("--gpu", default="L4", help="Modal GPU type, or none for CPU")
    parser.add_argument("--image", help="override with an immutable public ECR worker digest")
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
        with urlopen("https://ecc-challenges.com/v1/campaigns",timeout=30) as response:
            catalog=json.load(response)
        row=next((r for r in catalog["campaigns"] if r["id"]==args.campaign),None)
        if not row or (args.protocol == 1 and row["contribution"]=="not_integrated"):
            parser.error("selected campaign is not enabled by the gateway")
        if args.protocol == 2:
            try:
                authenticated_config(key, row["id"])
            except ValueError as exc:
                parser.error(str(exc))
        image_uri=args.image or catalog["worker_image"]
        if not re.fullmatch(r"(?:public\.ecr\.aws/[A-Za-z0-9_-]+|ghcr\.io/aburan28)/ecc-challenges-worker@sha256:[0-9a-f]{64}",image_uri):
            parser.error("worker image must be pinned by digest in an approved public registry")
        app = modal.App.lookup(args.app, create_if_missing=True)
        image = modal.Image.from_registry(image_uri).dockerfile_commands("ENTRYPOINT []")
        secret = modal.Secret.from_dict({"ECC_API_KEY": key})
        for index in range(args.workers):
            name = f"{args.campaign}-v{args.protocol}-worker-{index}"
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
                env={"ECC_API_URL": "https://ecc-challenges.com", "ECC_STATE_DIR": "/data",
                     "ECC_CAMPAIGN":args.campaign,"ECC_PROTOCOL":str(args.protocol),"ECC_BACKEND":args.backend},
                volumes={"/data": volume},
                gpu=None if args.gpu=="none" else args.gpu, timeout=args.hours * 3600,
            )
            print(f"{name}: started {sandbox.object_id}; state volume {args.app}-{name}-state")
        return

    for index in range(args.workers):
        name = f"{args.campaign}-v{args.protocol}-worker-{index}"
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
