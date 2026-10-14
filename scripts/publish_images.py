#!/usr/bin/env python3
"""Build Linux amd64 images and publish immutable ECR tags from a clean commit."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import subprocess

import boto3


ACCOUNT = "590183823895"
REGISTRY = f"{ACCOUNT}.dkr.ecr.us-west-2.amazonaws.com"
BUILDS = (("ecc-challenges-web", "Dockerfile.web"),
          ("ecc-challenges-audit", "Dockerfile.audit"),
          ("ecc-challenges-worker", "worker/Dockerfile"))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="default: git-<12 character commit prefix>")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if git("-C", str(root), "status", "--porcelain"):
        raise RuntimeError("commit the exact source before publishing images")
    tag = args.tag or "git-" + git("-C", str(root), "rev-parse", "HEAD")[:12]
    if not tag or not all(ch.isalnum() or ch in "-_." for ch in tag):
        raise ValueError("image tag contains invalid characters")
    session = boto3.Session(region_name="us-west-2")
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise RuntimeError("wrong AWS account")
    ecr = session.client("ecr")
    auth = ecr.get_authorization_token()["authorizationData"][0]
    password = base64.b64decode(auth["authorizationToken"]).split(b":", 1)[1]
    subprocess.run(["docker", "login", REGISTRY, "--username", "AWS", "--password-stdin"],
                   input=password, check=True)
    for repo, dockerfile in BUILDS:
        uri = f"{REGISTRY}/{repo}:{tag}"
        subprocess.run(["docker", "buildx", "build", "--platform", "linux/amd64",
                        "--file", dockerfile, "--tag", uri, "--push", "."],
                       cwd=root, check=True)
        digest = ecr.describe_images(repositoryName=repo,
                                     imageIds=[{"imageTag": tag}])["imageDetails"][0]["imageDigest"]
        print(f"{repo}: {REGISTRY}/{repo}@{digest}", flush=True)


if __name__ == "__main__":
    main()
