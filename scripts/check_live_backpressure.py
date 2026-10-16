#!/usr/bin/env python3
"""Bounded v2 admission/recovery check using 33 duplicate valid DP records.

This exercises the account backlog limit, not fresh-point drain capacity.
The default writes a plan; --apply briefly stops the quiet audit service.
A separate watchdog restores the exact prior service/scaling settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

import boto3
from botocore.config import Config

from ecc_challenges.multicurve.codec import namespace

ACCOUNT = "104186811093"
REGION = "us-west-2"
CLUSTER = "ecc-challenges"
SERVICE = "ecc-challenges-audit"
RESOURCE = f"service/{CLUSTER}/{SERVICE}"
ORIGIN = "https://ecc-challenges.com"


class CheckError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError("unexpected_redirect")


def write_new(path, value):
    with Path(path).open("x") as output:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write("\n")


def clients(profile):
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3})
    if session.client("sts", config=config).get_caller_identity()["Account"] != ACCOUNT:
        raise CheckError("wrong_aws_account")
    return {name: session.client(name, config=config) for name in
            ("ecs", "application-autoscaling", "sqs", "s3", "cloudformation")}


def restore(api, plan):
    # Restore workers before allowing automatic scaling again.
    api["ecs"].update_service(cluster=CLUSTER, service=SERVICE,
                              desiredCount=plan["desired_count"])
    api["application-autoscaling"].register_scalable_target(
        ServiceNamespace="ecs", ResourceId=RESOURCE,
        ScalableDimension="ecs:service:DesiredCount",
        SuspendedState=plan["scaling_suspended_state"],
    )


def mark_restored(path, by):
    try:
        write_new(path, {"restored": True, "by": by, "at_unix": time.time()})
    except FileExistsError:
        if json.loads(Path(path).read_text()).get("restored") is not True:
            raise CheckError("invalid_restoration_marker")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="ecc-challenges-new")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-plan", type=Path)
    args = parser.parse_args()
    api = clients(args.profile)
    if args.restore_plan:
        plan = json.loads(args.restore_plan.read_text())
        write_new(args.restore_plan.with_suffix(".watchdog-ready.json"), {"ready": True})
        time.sleep(max(0, args.restore_plan.stat().st_mtime + 240 - time.time()))
        marker = args.restore_plan.with_suffix(".restored.json")
        if not marker.exists():
            restore(api, plan)
            mark_restored(marker, "watchdog")
        return

    args.out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    binding = json.loads((root / "verification/all-curves-20261016/release/live-cpu-archive-binding.json").read_text())
    assert binding["ok"] and binding["campaign"] == "eccp-79" and len(binding["rows"]) == 1
    source = binding["rows"][0]
    raw = api["s3"].get_object(Bucket="ecc-challenges-data-104186811093",
                                Key=source["object_key"], VersionId=source["version_id"])["Body"].read(57)
    ns = namespace("eccp-79")
    assert ns.id == binding["namespace_id"] and len(raw) == ns.record_bytes == 56
    assert hashlib.sha256(raw).hexdigest() == source["sha256"]
    records = ns.decode(raw)
    assert len(records) == 1
    ns.verify(records[0])
    service = api["ecs"].describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
    if service["pendingCount"] or service["desiredCount"] != service["runningCount"] or len(service["deployments"]) != 1:
        raise CheckError("audit_service_not_stable")
    target = api["application-autoscaling"].describe_scalable_targets(
        ServiceNamespace="ecs", ResourceIds=[RESOURCE])["ScalableTargets"][0]
    stack = api["cloudformation"].describe_stacks(StackName="ecc-challenges-foundation")["Stacks"][0]
    outputs = {row["OutputKey"]: row["OutputValue"] for row in stack["Outputs"]}
    audit_queue = outputs["AuditQueueUrl"]

    def queue():
        result = api["sqs"].get_queue_attributes(QueueUrl=audit_queue, AttributeNames=[
            "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed"])["Attributes"]
        return {name: int(value) for name, value in result.items()}

    baseline = queue()
    if any(baseline.values()):
        raise CheckError("audit_queue_not_quiet")
    task_definition = api["ecs"].describe_task_definition(taskDefinition=service["taskDefinition"])["taskDefinition"]
    plan = {"format": "v2-bounded-backpressure-plan-v1", "account": ACCOUNT,
            "cluster": CLUSTER, "service": SERVICE, "desired_count": service["desiredCount"],
            "task_definition": service["taskDefinition"],
            "container_images": [row["image"] for row in task_definition["containerDefinitions"]],
            "scaling_suspended_state": target["SuspendedState"], "baseline_queue": baseline,
            "source": source, "namespace_id": ns.id, "record_sha256": hashlib.sha256(raw).hexdigest(),
            "records_per_batch": 1, "initial_batches": 32, "after_drain_batches": 1,
            "payload_bytes_total": 33 * len(raw), "operator_pause_target_seconds": 180,
            "restore_watchdog_delay_seconds": 240,
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "scope": "duplicate valid witness admission, account backlog 429, restart/drain, idempotent commit; no fresh-point throughput claim"}
    plan_path = args.out / "plan.json"
    write_new(plan_path, plan)
    if not args.apply:
        print(json.dumps(plan, sort_keys=True))
        return
    if not args.key_file or args.key_file.stat().st_mode & 0o077:
        raise CheckError("private_key_file_required")
    credential = args.key_file.read_text().strip()
    if not re.fullmatch(r"ecc1_[0-9a-f]{24}_[A-Za-z0-9_-]{43}", credential):
        raise CheckError("invalid_contributor_key")
    opener = build_opener(NoRedirect)
    pause_deadline = None

    def network_timeout():
        if pause_deadline is None:
            return 20
        remaining = pause_deadline - time.monotonic()
        if remaining <= 0:
            raise CheckError("bounded_pause_deadline")
        return min(20, remaining)

    def request(method, path, body=None):
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode() if body is not None else (b"" if method == "POST" else None)
        req = Request(ORIGIN + path, method=method, data=data, headers={
            "Authorization": "Bearer " + credential, "Content-Type": "application/json",
            "User-Agent": "ecc-challenges-bounded-release-check/1"})
        try:
            with opener.open(req, timeout=network_timeout()) as response:
                return response.status, {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read(131072))
        except HTTPError as error:
            return error.code, {k.lower(): v for k, v in error.headers.items()}, json.loads(error.read(8192))

    receipt = {"format": "v2-bounded-backpressure-result-v1", "ok": False,
               "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(), "batches": []}
    paused = False
    watchdog = None
    started = time.monotonic()
    run_id = None
    try:
        status, _, catalog = request("GET", "/v1/campaigns")
        if status != 200 or catalog.get("multicurve_enabled") is not False:
            raise CheckError("requires_private_multicurve_canary_window")
        status, _, assigned = request("POST", "/v2/register", {"campaign": ns.campaign, "registration_id": str(uuid4())})
        if status != 201 or assigned["namespace_id"] != ns.id:
            raise CheckError("registration_failed")
        run_id = assigned["run_id"]
        receipt["run_id"] = run_id
        # This detached subprocess does not receive the contributor key.
        watchdog_log = (args.out / "restore-watchdog.log").open("x")
        watchdog = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--profile", args.profile,
            "--restore-plan", str(plan_path.resolve()), "--out", str(args.out.resolve())],
            stdout=watchdog_log, stderr=subprocess.STDOUT, start_new_session=True)
        watchdog_log.close()
        until = time.monotonic() + 15
        while time.monotonic() < until:
            if watchdog.poll() is not None:
                raise CheckError("restore_watchdog_failed_before_pause")
            if plan_path.with_suffix(".watchdog-ready.json").exists():
                break
            time.sleep(0.25)
        else:
            raise CheckError("restore_watchdog_not_ready")
        paused = True
        pause_deadline = time.monotonic() + 180
        api["application-autoscaling"].register_scalable_target(
            ServiceNamespace="ecs", ResourceId=RESOURCE, ScalableDimension="ecs:service:DesiredCount",
            SuspendedState={"DynamicScalingInSuspended": True, "DynamicScalingOutSuspended": True,
                            "ScheduledScalingSuspended": True})
        api["ecs"].update_service(cluster=CLUSTER, service=SERVICE, desiredCount=0)
        until = time.monotonic() + 90
        while time.monotonic() < until:
            row = api["ecs"].describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
            if row["runningCount"] == row["pendingCount"] == row["desiredCount"] == 0:
                break
            time.sleep(3)
        else:
            raise CheckError("audit_stop_deadline")
        receipt["consumers_stopped"] = True

        def intent(offset):
            return request("POST", f"/v2/runs/{run_id}/batches", {
                "offset": offset, "byte_length": len(raw), "records": 1,
                "sha256": hashlib.sha256(raw).hexdigest()})

        def upload(offset):
            status, _, value = intent(offset)
            if status != 201 or not value.get("upload"):
                raise CheckError("intent_failed_http_" + str(status))
            upload = value["upload"]
            parsed = urlsplit(upload["url"])
            if (parsed.scheme != "https" or not parsed.hostname
                    or not parsed.hostname.endswith(".s3.us-west-2.amazonaws.com")):
                raise CheckError("unexpected_upload_host")
            # The gateway bearer is never sent to S3.
            with opener.open(Request(upload["url"], method="PUT", data=raw,
                                     headers=upload["headers"]), timeout=network_timeout()) as response:
                if response.status != 200:
                    raise CheckError("s3_put_failed")
            status, _, committed = request("POST", f"/v2/runs/{run_id}/batches/{value['batch_id']}/commit")
            if (status != 200 or committed["cursor"] != offset + len(raw)
                    or committed["batch_end_cursor"] != offset + len(raw)
                    or committed["run_id"] != run_id or committed["namespace_id"] != ns.id
                    or committed["batch_id"] != value["batch_id"] or not committed["admission_key"]):
                raise CheckError("commit_failed")
            receipt["batches"].append({"batch_id": value["batch_id"], "offset": offset,
                                       "cursor": committed["cursor"], "admission_key": committed["admission_key"]})
            return committed

        for i in range(32):
            if time.monotonic() >= pause_deadline:
                raise CheckError("bounded_pause_deadline")
            upload(i * len(raw))
            time.sleep(1)
        status, headers, rejected = intent(32 * len(raw))
        receipt["pressure_response"] = {"status": status, "retry_after": headers.get("retry-after"), "body": rejected}
        if status != 429 or headers.get("retry-after") != "60" or rejected.get("error") != "account verification backlog limit reached":
            raise CheckError("expected_account_backlog_refusal_missing")
        status, _, pending = request("GET", f"/v2/runs/{run_id}")
        receipt["paused_run_status"] = pending
        receipt["paused_queue"] = queue()
        if (status != 200 or pending["batches"] != {"admitted": {"batches": 32, "records": 32}}
                or pending["pending_batch_id"] or pending["cursor"] != 32 * len(raw)
                or pending["total_records"] != 32 or pending["run_id"] != run_id
                or pending["namespace_id"] != ns.id):
            raise CheckError("paused_admission_count_mismatch")
    except Exception as error:
        receipt["error"] = str(error) if isinstance(error, CheckError) else type(error).__name__
    finally:
        pause_deadline = None
        if paused:
            try:
                restore(api, plan)
                marker = plan_path.with_suffix(".restored.json")
                mark_restored(marker, "controller")
                receipt["audit_settings_restored"] = True
            except Exception as error:
                receipt["restore_error_type"] = type(error).__name__
        if watchdog is not None and receipt.get("audit_settings_restored"):
            try:
                watchdog.terminate()
                watchdog.wait(timeout=10)
            except Exception as error:
                receipt["watchdog_cleanup_error_type"] = type(error).__name__

    try:
        if "error" not in receipt and receipt.get("audit_settings_restored"):
            def wait_indexed(expected):
                until = time.monotonic() + 360
                while time.monotonic() < until:
                    status, _, state = request("GET", f"/v2/runs/{run_id}")
                    if status != 200 or state["batches"].get("failed"):
                        raise CheckError("drain_status_failed")
                    if (state["run_id"] != run_id or state["namespace_id"] != ns.id
                            or state["cursor"] != expected * len(raw) or state["total_records"] != expected
                            or state["pending_batch_id"]):
                        raise CheckError("drain_binding_mismatch")
                    if state["batches"] == {"indexed": {"batches": expected, "records": expected}}:
                        return state
                    time.sleep(3)
                raise CheckError("drain_deadline")
            receipt["after_restart_status"] = wait_indexed(32)
            upload(32 * len(raw))
            receipt["after_pressure_retry_status"] = wait_indexed(33)
            status, _, replay = request("POST", f"/v2/runs/{run_id}/batches/{receipt['batches'][0]['batch_id']}/commit")
            receipt["commit_replay"] = replay
            first = receipt["batches"][0]
            if (status != 200 or not replay["duplicate"] or replay["cursor"] != 33 * len(raw)
                    or replay["batch_id"] != first["batch_id"] or replay["admission_key"] != first["admission_key"]
                    or replay["run_id"] != run_id or replay["namespace_id"] != ns.id):
                raise CheckError("commit_replay_changed_cursor")
            receipt["queue_after_drain"] = queue()
            receipt["ok"] = True
    except Exception as error:
        receipt["error"] = str(error) if isinstance(error, CheckError) else type(error).__name__
        receipt["ok"] = False
    finally:
        try:
            if run_id:
                status, _, closed = request("POST", f"/v2/runs/{run_id}/close")
                receipt["closure"] = closed
                if status != 200 or not closed.get("closed_at"):
                    receipt["ok"] = False
        except Exception as error:
            receipt["closure_error_type"] = type(error).__name__
            receipt["ok"] = False
        finally:
            try:
                actual_target = api["application-autoscaling"].describe_scalable_targets(
                    ServiceNamespace="ecs", ResourceIds=[RESOURCE])["ScalableTargets"][0]
                actual_service = api["ecs"].describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
                receipt["final_cloud_state"] = {"suspended_state": actual_target["SuspendedState"],
                    "desired_count": actual_service["desiredCount"], "running_count": actual_service["runningCount"],
                    "pending_count": actual_service["pendingCount"], "task_definition": actual_service["taskDefinition"]}
                if actual_target["SuspendedState"] != plan["scaling_suspended_state"]:
                    receipt["ok"] = False
            except Exception as error:
                receipt["cloud_state_capture_error_type"] = type(error).__name__
                receipt["ok"] = False
            receipt["elapsed_seconds"] = time.monotonic() - started
            write_new(args.out / "result.json", receipt)
    print(json.dumps({"ok": receipt["ok"], "run_id": run_id, "error": receipt.get("error"),
                      "audit_settings_restored": receipt.get("audit_settings_restored")}, sort_keys=True))
    if not receipt["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
