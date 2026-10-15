#!/usr/bin/env python3
"""Build Linux amd64 images and publish immutable ECR tags from a clean commit."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import subprocess

import boto3


BUILDS = (("ecc-challenges-web", "Dockerfile.web"),
          ("ecc-challenges-audit", "Dockerfile.audit"),
          ("ecc-challenges-worker", "worker/Dockerfile"))


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="AWS CLI profile for the destination account")
    parser.add_argument("--account-id", required=True, help="expected 12-digit destination AWS account ID")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--tag", help="default: git-<12 character commit prefix>")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    root = Path(__file__).resolve().parents[1]
    if git("-C", str(root), "status", "--porcelain"):
        raise RuntimeError("commit the exact source before publishing images")
    tag = args.tag or "git-" + git("-C", str(root), "rev-parse", "HEAD")[:12]
    if not tag or not all(ch.isalnum() or ch in "-_." for ch in tag):
        raise ValueError("image tag contains invalid characters")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    actual_account = session.client("sts").get_caller_identity()["Account"]
    if actual_account != args.account_id:
        raise RuntimeError(f"wrong AWS account: expected {args.account_id}, got {actual_account}")
    registry = f"{args.account_id}.dkr.ecr.{args.region}.amazonaws.com"
    ecr = session.client("ecr")
    auth = ecr.get_authorization_token()["authorizationData"][0]
    password = base64.b64decode(auth["authorizationToken"]).split(b":", 1)[1]
    subprocess.run(["docker", "login", registry, "--username", "AWS", "--password-stdin"],
                   input=password, check=True)
    for repo, dockerfile in BUILDS:
        uri = f"{registry}/{repo}:{tag}"
        subprocess.run(["docker", "buildx", "build", "--platform", "linux/amd64",
                        "--file", dockerfile, "--tag", uri, "--push", "."],
                       cwd=root, check=True)
        digest = ecr.describe_images(repositoryName=repo,
                                     imageIds=[{"imageTag": tag}])["imageDetails"][0]["imageDigest"]
        print(f"{repo}: {registry}/{repo}@{digest}", flush=True)


if __name__ == "__main__":
    main()
