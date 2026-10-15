#!/usr/bin/env python3
"""Publish a commit-bound, immutable admin image to the isolated AWS account."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import subprocess

import boto3


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="us-west-2")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    root = Path(__file__).resolve().parents[1]
    if git(root, "status", "--porcelain"):
        raise RuntimeError("commit the exact source before publishing images")
    tag = "git-" + git(root, "rev-parse", "HEAD")[:12]
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual != args.account_id:
        raise RuntimeError(f"wrong AWS account: expected {args.account_id}, got {actual}")
    ecr = session.client("ecr")
    repo = "ecc-challenges-admin"
    ecr.describe_repositories(repositoryNames=[repo])
    registry = f"{args.account_id}.dkr.ecr.{args.region}.amazonaws.com"
    authorization = ecr.get_authorization_token()["authorizationData"][0]
    password = base64.b64decode(authorization["authorizationToken"]).split(b":", 1)[1]
    subprocess.run(["docker", "login", registry, "--username", "AWS", "--password-stdin"],
                   input=password, check=True)
    image = f"{registry}/{repo}:{tag}"
    subprocess.run(["docker", "buildx", "build", "--platform", "linux/amd64",
                    "--file", "Dockerfile.admin", "--tag", image, "--push", "."],
                   cwd=root, check=True)
    digest = ecr.describe_images(repositoryName=repo,
                                 imageIds=[{"imageTag": tag}])["imageDetails"][0]["imageDigest"]
    print(f"{registry}/{repo}@{digest}", flush=True)


if __name__ == "__main__":
    main()
