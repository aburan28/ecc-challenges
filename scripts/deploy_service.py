#!/usr/bin/env python3
"""Resolve immutable images and prerequisites, then deploy the HTTPS service.

The default is a read-only plan. Pass --apply after DNS validation, OAuth setup,
and the image builds. No credential values are printed or passed on argv.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import hashlib
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError


def outputs(cfn, stack: str) -> dict[str, str]:
    row = cfn.describe_stacks(StackName=stack)["Stacks"][0]
    if row["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError(f"{stack} is not complete: {row['StackStatus']}")
    return {entry["OutputKey"]: entry["OutputValue"] for entry in row["Outputs"]}


def image_uri(ecr, account: str, region: str, repo: str, tag: str) -> str:
    image = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])["imageDetails"][0]
    return f"{account}.dkr.ecr.{region}.amazonaws.com/{repo}@{image['imageDigest']}"


def public_subnets(ec2, vpc: str) -> tuple[str, str]:
    rows = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]
    by_zone = {}
    for row in sorted(rows, key=lambda item: item["SubnetId"]):
        if row["MapPublicIpOnLaunch"]:
            by_zone.setdefault(row["AvailabilityZone"], row["SubnetId"])
    if len(by_zone) < 2:
        raise RuntimeError("the dedicated VPC needs two public subnets in distinct zones")
    return tuple(list(by_zone.values())[:2])


def current_alert_email(cfn) -> str:
    try:
        stack = cfn.describe_stacks(StackName="ecc-challenges-service")["Stacks"][0]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ValidationError" and "does not exist" in str(exc):
            return ""
        raise
    return next((row["ParameterValue"] for row in stack.get("Parameters", [])
                 if row["ParameterKey"] == "AlertEmail"), "")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="optional AWS profile; otherwise use the configured identity")
    parser.add_argument("--account-id", required=True, help="expected 12-digit destination AWS account ID")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--certificate-arn", required=True)
    parser.add_argument("--public-worker-image", required=True,
                        help="new-account ECR Public image pinned by digest")
    parser.add_argument("--public-cpu-worker-image", default="",
                        help="public multi-architecture CPU image pinned by digest")
    parser.add_argument("--vpc-id", required=True, help="destination account VPC for the ALB and ECS tasks")
    parser.add_argument("--db-host", required=True, help="PostgreSQL hostname reachable from the destination VPC")
    parser.add_argument("--db-name", default="rho_dp")
    parser.add_argument("--bucket", required=True, help="S3 bucket accessible to the destination ECS roles")
    parser.add_argument("--tag", required=True, help="immutable ECR tag shared by web and audit images")
    parser.add_argument("--web-tasks", type=int, default=0)
    parser.add_argument("--audit-tasks", type=int, default=0)
    parser.add_argument("--index-tasks", type=int, default=0)
    parser.add_argument("--reconcile-tasks", type=int, default=0)
    parser.add_argument("--resolve-tasks", type=int, default=0)
    parser.add_argument("--alert-email",
                        help="optional email address for CloudWatch alarm notifications; recipient must confirm SNS subscription")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--enable-multicurve", action="store_true",
                        help="enable the validated v2 worker, API and point index")
    parser.add_argument("--multicurve-canary-key", default="", help="key ID allowed to test before public v2 enablement")
    parser.add_argument("--point-index-mode", choices=("dynamodb", "compact"), default="dynamodb",
                        help="v2 point index: DynamoDB items, or compact S3 parts merged by reconcile")
    parser.add_argument("--point-index-merge-seconds", type=int, default=3600)
    parser.add_argument("--max-pending-batches", type=int, default=512,
                        help="bounded pending batches per protocol; tune from measured drain capacity")
    args = parser.parse_args()
    if len(args.account_id) != 12 or not args.account_id.isdigit():
        parser.error("--account-id must be 12 digits")
    cert_prefix = f"arn:aws:acm:{args.region}:{args.account_id}:certificate/"
    if not args.certificate_arn.startswith(cert_prefix):
        parser.error("--certificate-arn must belong to the destination account and region")
    if not re.fullmatch(r"(?:public\.ecr\.aws/[A-Za-z0-9_-]+|ghcr\.io/aburan28)/ecc-challenges-worker@sha256:[0-9a-f]{64}",
                        args.public_worker_image):
        parser.error("--public-worker-image must be a pinned public worker image")
    if args.public_cpu_worker_image and not re.fullmatch(
        r"(?:public\.ecr\.aws/[A-Za-z0-9_-]+|ghcr\.io/aburan28)/ecc-challenges-worker-cpu@sha256:[0-9a-f]{64}",
        args.public_cpu_worker_image,
    ):
        parser.error("--public-cpu-worker-image must be a pinned public CPU worker image")
    if any(n < 0 for n in (args.web_tasks, args.audit_tasks, args.index_tasks,
                          args.reconcile_tasks, args.resolve_tasks)):
        parser.error("task counts must be nonnegative")
    if not 16 <= args.max_pending_batches <= 100000:
        parser.error("--max-pending-batches must be between 16 and 100000")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    actual_account = session.client("sts").get_caller_identity()["Account"]
    if actual_account != args.account_id:
        raise RuntimeError(f"AWS account mismatch: expected {args.account_id}, got {actual_account}")
    public_ecr = boto3.Session(profile_name=args.profile, region_name="us-east-1").client("ecr-public")
    public_repo = public_ecr.describe_repositories(
        registryId=args.account_id, repositoryNames=["ecc-challenges-worker"]
    )["repositories"][0]
    mirrored = args.public_worker_image.startswith("ghcr.io/aburan28/ecc-challenges-worker@")
    if public_repo["registryId"] != args.account_id or (not mirrored and not args.public_worker_image.startswith(
        public_repo["repositoryUri"] + "@"
    )):
        raise RuntimeError("public worker image is not in the destination account")
    worker_digest = args.public_worker_image.rsplit("@", 1)[1]
    public_ecr.describe_images(registryId=args.account_id,
                               repositoryName="ecc-challenges-worker",
                               imageIds=[{"imageDigest": worker_digest}])
    if mirrored:
        # An allowed mirror must remain an anonymous, byte-identical copy of an
        # image in this account. No GitHub or AWS credential goes to the mirror.
        with urlopen("https://ghcr.io/token?service=ghcr.io&scope=repository:aburan28/ecc-challenges-worker:pull",timeout=30) as response:
            token=json.load(response)["token"]
        request=Request("https://ghcr.io/v2/aburan28/ecc-challenges-worker/manifests/"+worker_digest,
                        headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.oci.image.index.v1+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json"})
        with urlopen(request,timeout=30) as response:
            manifest=response.read(4*1024*1024+1)
        if len(manifest)>4*1024*1024 or "sha256:"+hashlib.sha256(manifest).hexdigest()!=worker_digest:
            raise RuntimeError("public mirror manifest differs from the account's source image")
    cfn, ecr, acm, sm = (session.client(name) for name in
                         ("cloudformation", "ecr", "acm", "secretsmanager"))
    foundation = outputs(cfn, "ecc-challenges-foundation")
    data = outputs(cfn, "ecc-challenges-data")
    for supplied, expected, name in (
        (args.vpc_id, data["VpcId"], "VPC"),
        (args.bucket, data["DataBucketName"], "S3 bucket"),
        (args.db_host, data["DatabaseHost"], "database host"),
    ):
        if supplied != expected:
            raise RuntimeError(f"{name} differs from the isolated data stack")
    data_stack = cfn.describe_stacks(StackName="ecc-challenges-data")["Stacks"][0]
    data_params = {entry["ParameterKey"]: entry["ParameterValue"]
                   for entry in data_stack.get("Parameters", [])}
    if (data_params.get("WebPasswordArn") != foundation["WebPasswordArn"] or
            data_params.get("WorkerPasswordArn") != foundation["WorkerPasswordArn"]):
        raise RuntimeError("data stack database password secrets differ from the foundation stack")
    certificate = acm.describe_certificate(CertificateArn=args.certificate_arn)["Certificate"]
    if certificate["Status"] != "ISSUED":
        raise RuntimeError(f"TLS certificate is {certificate['Status']}; add the ACM CNAMEs in Squarespace")
    names = set(certificate.get("SubjectAlternativeNames", []))
    if not {"ecc-challenges.com", "www.ecc-challenges.com"}.issubset(names):
        raise RuntimeError("TLS certificate must cover the apex and www hostnames")
    github = sm.describe_secret(SecretId="ecc-challenges/github-oauth")
    oauth = json.loads(sm.get_secret_value(SecretId=github["ARN"])["SecretString"])
    if not oauth.get("client_id") or not oauth.get("client_secret"):
        raise RuntimeError("GitHub App client ID and client secret are not populated")
    subnets = public_subnets(session.client("ec2"), args.vpc_id)
    params = {
        "VpcId": args.vpc_id, "SubnetA": subnets[0], "SubnetB": subnets[1],
        "CertificateArn": args.certificate_arn,
        "Bucket": args.bucket, "DbHost": args.db_host, "DbName": args.db_name,
        "WebImageUri": image_uri(ecr, args.account_id, args.region, "ecc-challenges-web", args.tag),
        "AuditImageUri": image_uri(ecr, args.account_id, args.region, "ecc-challenges-audit", args.tag),
        "PublicWorkerImage": args.public_worker_image,
        "PublicCpuWorkerImage": args.public_cpu_worker_image,
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
        "AlertEmail": args.alert_email if args.alert_email is not None else current_alert_email(cfn),
        "MulticurveEnabled": "true" if args.enable_multicurve else "false",
        "MulticurveCanaryKey": args.multicurve_canary_key,
        "MaxPendingBatches": str(args.max_pending_batches),
        "PointIndexMode": args.point_index_mode,
        "PointIndexMergeSeconds": str(args.point_index_merge_seconds),
    }
    print(json.dumps({"stack": "ecc-challenges-service", "tag": args.tag,
                      "images": {key: params[key] for key in ("WebImageUri", "AuditImageUri")},
                      "public_worker_image": args.public_worker_image,
                      "vpc": args.vpc_id, "subnets": subnets,
                      "bucket": args.bucket, "db_host": args.db_host, "db_name": args.db_name,
                      "tasks": {key: params[key] for key in
                                ("WebTasks", "AuditTasks", "IndexTasks", "ReconcileTasks", "ResolveTasks")},
                      "alert_subscription_requested": bool(params["AlertEmail"]),
                      "point_index": {"mode": args.point_index_mode,
                                      "merge_seconds": args.point_index_merge_seconds},
                      "mode": "apply" if args.apply else "plan"}, indent=2))
    if not args.apply:
        return
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["aws", "--profile", args.profile, "cloudformation", "deploy", "--region", args.region,
                    "--stack-name", "ecc-challenges-service",
                    "--template-file", str(root / "infra/service.yaml"),
                    "--capabilities", "CAPABILITY_IAM", "--no-fail-on-empty-changeset",
                    "--parameter-overrides", *(f"{key}={value}" for key, value in params.items())], check=True)


if __name__ == "__main__":
    main()
