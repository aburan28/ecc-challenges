#!/usr/bin/env python3
"""Publish the reviewed Linux amd64 contributor worker to anonymous ECR Public."""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
import re
import subprocess

import boto3


REPOSITORY = "ecc-challenges-worker"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="new account AWS CLI profile")
    parser.add_argument("--account-id", required=True, help="expected 12-digit AWS account ID")
    parser.add_argument("--release-tag", help="additional version tag after release checks pass, e.g. 0.1.0")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    root = Path(__file__).resolve().parents[1]
    if git(root, "status", "--porcelain"):
        raise RuntimeError("commit the exact worker source before publishing")
    commit_tag = "git-" + git(root, "rev-parse", "HEAD")[:12]
    if args.release_tag and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.release_tag):
        parser.error("release tag must be a semantic version such as 0.1.0")

    session = boto3.Session(profile_name=args.profile, region_name="us-east-1")
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual != args.account_id:
        raise RuntimeError(f"wrong AWS account: expected {args.account_id}, got {actual}")
    ecr = session.client("ecr-public")
    try:
        repository = ecr.describe_repositories(repositoryNames=[REPOSITORY])["repositories"][0]
    except ecr.exceptions.RepositoryNotFoundException:
        repository = ecr.create_repository(
            repositoryName=REPOSITORY,
            catalogData={"description": "ECC2K-130 volunteer GPU worker",
                         "architectures": ["x86-64"], "operatingSystems": ["Linux"]},
        )["repository"]
    uri = repository["repositoryUri"]
    if repository["registryId"] != args.account_id or not re.fullmatch(
        r"public\.ecr\.aws/[a-zA-Z0-9_-]+/ecc-challenges-worker", uri
    ):
        raise RuntimeError("public repository does not belong to the expected account")

    tags = [commit_tag]
    if args.release_tag:
        tags.append(args.release_tag)
    for tag in tags:
        try:
            ecr.describe_images(repositoryName=REPOSITORY, imageIds=[{"imageTag": tag}])
        except ecr.exceptions.ImageNotFoundException:
            pass
        else:
            raise RuntimeError(f"public image tag already exists: {tag}")
    authorization = ecr.get_authorization_token()["authorizationData"]
    password = base64.b64decode(authorization["authorizationToken"]).split(b":", 1)[1]
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", "public.ecr.aws"],
                   input=password, check=True)
    command = ["docker", "buildx", "build", "--platform", "linux/amd64",
               "--file", "worker/Dockerfile", "--provenance=mode=max", "--sbom=true"]
    for tag in tags:
        command += ["--tag", f"{uri}:{tag}"]
    subprocess.run([*command, "--push", "."], cwd=root, check=True)
    for tag in tags:
        row = ecr.describe_images(repositoryName=REPOSITORY,
                                  imageIds=[{"imageTag": tag}])["imageDetails"][0]
        print(f"{tag}: {uri}@{row['imageDigest']}", flush=True)


if __name__ == "__main__":
    main()
