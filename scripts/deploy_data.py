#!/usr/bin/env python3
"""Deploy the foundation and isolated data stack into an explicit AWS account."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

import boto3


def outputs(cfn, name: str) -> dict[str, str]:
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError(f"{name} is not complete: {stack['StackStatus']}")
    return {row["OutputKey"]: row["OutputValue"] for row in stack["Outputs"]}


def deploy(profile: str, region: str, name: str, template: Path,
           params: dict[str, str] | None = None) -> None:
    command = ["aws", "--profile", profile, "cloudformation", "deploy", "--region", region,
               "--stack-name", name, "--template-file", str(template),
               "--capabilities", "CAPABILITY_IAM", "--no-fail-on-empty-changeset"]
    if params:
        command.extend(["--parameter-overrides", *(f"{key}={value}" for key, value in params.items())])
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--database-class", default="db.r7g.xlarge")
    parser.add_argument("--database-storage-gib", type=int, default=400)
    parser.add_argument("--admin-image-uri", default="", help="commit-bound digest from publish_admin.py")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    if not 200 <= args.database_storage_gib <= 4000:
        parser.error("--database-storage-gib must be between 200 and 4000")
    if args.admin_image_uri:
        prefix = (f"{args.account_id}.dkr.ecr.{args.region}.amazonaws.com/"
                  "ecc-challenges-admin@sha256:")
        if not args.admin_image_uri.startswith(prefix) or not re.fullmatch(
            r"[0-9a-f]{64}", args.admin_image_uri[len(prefix):]
        ):
            parser.error("--admin-image-uri must be a digest in the destination account and region")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual != args.account_id:
        raise RuntimeError(f"wrong AWS account: expected {args.account_id}, got {actual}")
    root = Path(__file__).resolve().parents[1]
    plan = {"account_id": args.account_id, "region": args.region,
            "foundation_stack": "ecc-challenges-foundation", "data_stack": "ecc-challenges-data",
            "database_class": args.database_class, "database_storage_gib": args.database_storage_gib,
            "admin_image_uri": args.admin_image_uri or None,
            "mode": "apply" if args.apply else "plan"}
    print(json.dumps(plan, indent=2), flush=True)
    if not args.apply:
        return
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True)
    if dirty:
        raise RuntimeError("commit the exact templates before deploying")
    deploy(args.profile, args.region, "ecc-challenges-foundation", root / "infra/foundation.yaml")
    foundation = outputs(session.client("cloudformation"), "ecc-challenges-foundation")
    params = {"WebPasswordArn": foundation["WebPasswordArn"],
              "WorkerPasswordArn": foundation["WorkerPasswordArn"],
              "DatabaseClass": args.database_class,
              "DatabaseStorageGiB": str(args.database_storage_gib)}
    if args.admin_image_uri:
        params["AdminImageUri"] = args.admin_image_uri
    deploy(args.profile, args.region, "ecc-challenges-data", root / "infra/data.yaml", params)
    data = outputs(session.client("cloudformation"), "ecc-challenges-data")
    print(json.dumps({key: data[key] for key in
                      ("VpcId", "DatabaseHost", "DataBucketName", "GithubOAuthSecretArn",
                       "AdminRepositoryUri")}, indent=2), flush=True)


if __name__ == "__main__":
    main()
