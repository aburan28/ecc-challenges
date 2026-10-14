#!/usr/bin/env python3
"""Resolve immutable images and prerequisites, then deploy the HTTPS service.

The default is a read-only plan. Pass --apply after DNS validation, OAuth setup,
and the image builds. No credential values are printed or passed on argv.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import boto3


ACCOUNT = "590183823895"
CERT = "arn:aws:acm:us-west-2:590183823895:certificate/d516b1ba-587a-40fc-98d0-22374795bdb3"


def outputs(cfn, stack: str) -> dict[str, str]:
    row = cfn.describe_stacks(StackName=stack)["Stacks"][0]
    if row["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError(f"{stack} is not complete: {row['StackStatus']}")
    return {entry["OutputKey"]: entry["OutputValue"] for entry in row["Outputs"]}


def image_uri(ecr, repo: str, tag: str) -> str:
    image = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])["imageDetails"][0]
    return f"{ACCOUNT}.dkr.ecr.us-west-2.amazonaws.com/{repo}@{image['imageDigest']}"


def public_subnets(ec2, vpc: str) -> tuple[str, str]:
    rows = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]
    by_zone = {}
    for row in sorted(rows, key=lambda item: item["SubnetId"]):
        if row["MapPublicIpOnLaunch"]:
            by_zone.setdefault(row["AvailabilityZone"], row["SubnetId"])
    if len(by_zone) < 2:
        raise RuntimeError("the existing RDS VPC needs two public subnets in distinct zones")
    return tuple(list(by_zone.values())[:2])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="immutable ECR tag shared by web and audit images")
    parser.add_argument("--web-tasks", type=int, default=0)
    parser.add_argument("--audit-tasks", type=int, default=0)
    parser.add_argument("--index-tasks", type=int, default=0)
    parser.add_argument("--reconcile-tasks", type=int, default=0)
    parser.add_argument("--resolve-tasks", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if any(n < 0 for n in (args.web_tasks, args.audit_tasks, args.index_tasks,
                          args.reconcile_tasks, args.resolve_tasks)):
        parser.error("task counts must be nonnegative")
    session = boto3.Session(region_name="us-west-2")
    actual_account = session.client("sts").get_caller_identity()["Account"]
    if actual_account != ACCOUNT:
        raise RuntimeError(f"AWS account mismatch: expected {ACCOUNT}, got {actual_account}")
    cfn, ecr, acm, sm = (session.client(name) for name in
                         ("cloudformation", "ecr", "acm", "secretsmanager"))
    foundation = outputs(cfn, "ecc-challenges-foundation")
    certificate = acm.describe_certificate(CertificateArn=CERT)["Certificate"]
    if certificate["Status"] != "ISSUED":
        raise RuntimeError(f"TLS certificate is {certificate['Status']}; add the ACM CNAMEs in Squarespace")
    github = sm.describe_secret(SecretId="ecc-challenges/github-oauth")
    oauth = json.loads(sm.get_secret_value(SecretId=github["ARN"])["SecretString"])
    if not oauth.get("client_id") or not oauth.get("client_secret"):
        raise RuntimeError("GitHub OAuth App secret is not populated")
    rds = session.client("rds").describe_db_instances(DBInstanceIdentifier="rho-dp")["DBInstances"][0]
    vpc = rds["DBSubnetGroup"]["VpcId"]
    subnets = public_subnets(session.client("ec2"), vpc)
    params = {
        "VpcId": vpc, "SubnetA": subnets[0], "SubnetB": subnets[1],
        "CertificateArn": CERT,
        "WebImageUri": image_uri(ecr, "ecc-challenges-web", args.tag),
        "AuditImageUri": image_uri(ecr, "ecc-challenges-audit", args.tag),
        "GithubSecretArn": github["ARN"],
        "WebPasswordArn": foundation["WebPasswordArn"],
        "WorkerPasswordArn": foundation["WorkerPasswordArn"],
        "KeyPepperArn": foundation["KeyPepperArn"],
        "SessionSecretArn": foundation["SessionSecretArn"],
        "AuditQueueArn": foundation["AuditQueueArn"],
        "AuditQueueUrl": foundation["AuditQueueUrl"],
        "IndexQueueArn": foundation["IndexQueueArn"],
        "IndexQueueUrl": foundation["IndexQueueUrl"],
        "CollisionQueueArn": foundation["CollisionQueueArn"],
        "CollisionQueueUrl": foundation["CollisionQueueUrl"],
        "WebTasks": str(args.web_tasks), "AuditTasks": str(args.audit_tasks),
        "IndexTasks": str(args.index_tasks), "ReconcileTasks": str(args.reconcile_tasks),
        "ResolveTasks": str(args.resolve_tasks),
    }
    print(json.dumps({"stack": "ecc-challenges-service", "tag": args.tag,
                      "images": {key: params[key] for key in ("WebImageUri", "AuditImageUri")},
                      "vpc": vpc, "subnets": subnets,
                      "tasks": {key: params[key] for key in
                                ("WebTasks", "AuditTasks", "IndexTasks", "ReconcileTasks", "ResolveTasks")},
                      "mode": "apply" if args.apply else "plan"}, indent=2))
    if not args.apply:
        return
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["aws", "cloudformation", "deploy", "--region", "us-west-2",
                    "--stack-name", "ecc-challenges-service",
                    "--template-file", str(root / "infra/service.yaml"),
                    "--capabilities", "CAPABILITY_IAM", "--no-fail-on-empty-changeset",
                    "--parameter-overrides", *(f"{key}={value}" for key, value in params.items())], check=True)


if __name__ == "__main__":
    main()
