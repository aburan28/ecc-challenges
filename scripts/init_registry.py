#!/usr/bin/env python3
"""Create the isolated public seed registry marker with an account guard."""

from __future__ import annotations

import argparse
import hashlib
import json

import boto3
from botocore.exceptions import ClientError


KEY = "seed-registry/curve-131/config.json"
CONFIG = {"version": 1, "state": "ready", "automatic_owner_expiry": False,
          "imported_records": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual != args.account_id:
        raise RuntimeError(f"wrong AWS account: expected {args.account_id}, got {actual}")
    stack = session.client("cloudformation").describe_stacks(StackName="ecc-challenges-data")["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError(f"data stack is not complete: {stack['StackStatus']}")
    outputs = {row["OutputKey"]: row["OutputValue"] for row in stack["Outputs"]}
    bucket = outputs["DataBucketName"]
    if bucket != f"ecc-challenges-data-{args.account_id}":
        raise RuntimeError("data bucket does not belong to the expected account")
    s3 = session.client("s3")
    s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=args.account_id)
    try:
        existing = s3.get_object(Bucket=bucket, Key=KEY, ExpectedBucketOwner=args.account_id)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
            raise
    else:
        current = json.loads(existing["Body"].read())
        if current.get("version") != 1 or current.get("state") != "ready" or \
           current.get("automatic_owner_expiry") is not False:
            raise RuntimeError("existing seed registry is incompatible")
        print(f"registry ready in {bucket}; no write needed")
        return
    body = (json.dumps(CONFIG, sort_keys=True, separators=(",", ":")) + "\n").encode()
    print(json.dumps({"bucket": bucket, "key": KEY, "sha256": hashlib.sha256(body).hexdigest(),
                      "mode": "apply" if args.apply else "plan"}, indent=2))
    if args.apply:
        s3.put_object(Bucket=bucket, Key=KEY, Body=body, ContentType="application/json",
                      ServerSideEncryption="AES256", IfNoneMatch="*",
                      ExpectedBucketOwner=args.account_id)


if __name__ == "__main__":
    main()
