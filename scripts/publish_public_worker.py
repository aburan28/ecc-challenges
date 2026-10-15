#!/usr/bin/env python3
"""Publish the reviewed Linux amd64 contributor worker to anonymous ECR Public."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

import boto3


ACCOUNT = "590183823895"
REPOSITORY = "ecc-challenges-worker"
URI = "public.ecr.aws/d5a8v6s7/ecc-challenges-worker"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-tag", help="additional version tag after release checks pass, e.g. 0.1.0")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if git(root, "status", "--porcelain"):
        raise RuntimeError("commit the exact worker source before publishing")
    commit_tag = "git-" + git(root, "rev-parse", "HEAD")[:12]
    if args.release_tag and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.release_tag):
        parser.error("release tag must be a semantic version such as 0.1.0")

    session = boto3.Session(region_name="us-east-1")
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise RuntimeError("wrong AWS account")
    ecr = session.client("ecr-public")
    repository = ecr.describe_repositories(repositoryNames=[REPOSITORY])["repositories"][0]
    if repository["repositoryUri"] != URI:
        raise RuntimeError("public repository URI differs from the pinned contributor endpoint")

    password = subprocess.check_output(["aws", "ecr-public", "get-login-password",
                                        "--region", "us-east-1"])
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", "public.ecr.aws"],
                   input=password, check=True)
    tags = [commit_tag]
    if args.release_tag:
        tags.append(args.release_tag)
    command = ["docker", "buildx", "build", "--platform", "linux/amd64",
               "--file", "worker/Dockerfile", "--provenance=mode=max", "--sbom=true"]
    for tag in tags:
        command += ["--tag", f"{URI}:{tag}"]
    subprocess.run([*command, "--push", "."], cwd=root, check=True)
    for tag in tags:
        row = ecr.describe_images(repositoryName=REPOSITORY,
                                  imageIds=[{"imageTag": tag}])["imageDetails"][0]
        print(f"{tag}: {URI}@{row['imageDigest']}", flush=True)


if __name__ == "__main__":
    main()
