#!/usr/bin/env python3
"""Run and verify the isolated database bootstrap task in ECS Fargate."""

from __future__ import annotations

import argparse
import json
import re

import boto3


def is_pinned_admin_image(image: str, account_id: str, region: str) -> bool:
    prefix = (f"{account_id}.dkr.ecr.{region}.amazonaws.com/"
              "ecc-challenges-admin@sha256:")
    return image.startswith(prefix) and bool(
        re.fullmatch(r"[0-9a-f]{64}", image[len(prefix):])
    )


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
    task_definition = outputs["AdminTaskArn"]
    if not task_definition.startswith(f"arn:aws:ecs:{args.region}:{args.account_id}:task-definition/"):
        raise RuntimeError("admin task definition does not belong to the destination account")
    ecs = session.client("ecs")
    definition = ecs.describe_task_definition(taskDefinition=task_definition)["taskDefinition"]
    image = definition["containerDefinitions"][0]["image"]
    if not is_pinned_admin_image(image, args.account_id, args.region):
        raise RuntimeError("admin task image is not a pinned destination-account image")
    cluster = outputs["AdminClusterName"]
    network = {"awsvpcConfiguration": {
        "subnets": [outputs["PublicSubnetA"], outputs["PublicSubnetB"]],
        "securityGroups": [outputs["AdminSecurityGroupId"]],
        "assignPublicIp": "ENABLED",
    }}
    print(json.dumps({"cluster": cluster, "task_definition": task_definition,
                      "network": network, "mode": "apply" if args.apply else "plan"}, indent=2))
    if not args.apply:
        return
    result = ecs.run_task(cluster=cluster, taskDefinition=task_definition,
                          launchType="FARGATE", networkConfiguration=network,
                          startedBy="ecc-challenges-db-bootstrap")
    if result["failures"] or len(result["tasks"]) != 1:
        raise RuntimeError(f"admin task did not start: {result['failures']}")
    task_arn = result["tasks"][0]["taskArn"]
    print(f"admin task started: {task_arn}", flush=True)
    ecs.get_waiter("tasks_stopped").wait(cluster=cluster, tasks=[task_arn],
                                           WaiterConfig={"Delay": 15, "MaxAttempts": 80})
    task = ecs.describe_tasks(cluster=cluster, tasks=[task_arn])["tasks"][0]
    containers = task.get("containers", [])
    if task.get("stopCode") != "EssentialContainerExited" or len(containers) != 1 or \
       containers[0].get("exitCode") != 0:
        raise RuntimeError(f"admin task failed: stop={task.get('stoppedReason')} "
                           f"container={containers[0].get('reason') if containers else 'missing'} "
                           f"exit={containers[0].get('exitCode') if containers else 'missing'}")
    print(f"admin task succeeded: {task_arn}")


if __name__ == "__main__":
    main()
