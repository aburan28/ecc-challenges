"""Complete the held intent and observe recovery of interrupted canary batches.

No lease/state mutation or queue purge. Exact commit replays provide normal
delivery retries, and the pipeline's existing leases remain authoritative.
"""
import hashlib
import json
from pathlib import Path
import sys
import time
import boto3

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "worker"))
from generic.client import API
from ecc_challenges.multicurve.codec import namespace

destination = Path(__file__).with_name("backpressure-attempt-2-recovery.json")
if destination.exists():
    raise FileExistsError(destination)
receipt = {"format": "release-interrupted-batch-recovery-v1", "ok": False,
           "run_id": "5d4d1a88-01d7-40db-9a21-01e533a9f546",
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "lease_state_modified": False, "status_observations": []}
started = time.monotonic()
try:
    source = json.loads(Path(__file__).with_name("live-cpu-archive-binding.json").read_text())["rows"][0]
    session = boto3.Session(profile_name="ecc-challenges-new", region_name="us-west-2")
    assert session.client("sts").get_caller_identity()["Account"] == "104186811093"
    raw = session.client("s3").get_object(Bucket="ecc-challenges-data-104186811093",
        Key=source["object_key"], VersionId=source["version_id"])["Body"].read(57)
    ns = namespace("eccp-79")
    assert len(raw) == 56 and hashlib.sha256(raw).hexdigest() == source["sha256"]
    ns.verify(ns.decode(raw)[0])
    api = API("https://ecc-challenges.com", (root / ".tmp/contributor-canary.key").read_text().strip())
    run = receipt["run_id"]
    intent = api.request("POST", f"/v2/runs/{run}/batches", {
        "offset": 1792, "byte_length": len(raw), "records": 1, "sha256": source["sha256"]})
    if intent["upload"]:
        api.upload(intent["upload"], raw)
    receipt["completed_reservation"] = api.request("POST", f"/v2/runs/{run}/batches/{intent['batch_id']}/commit")
    receipt["closure"] = api.request("POST", f"/v2/runs/{run}/close")
    assert receipt["closure"].get("closed_at")
    prior = json.loads((Path(__file__).parent / "live-backpressure-attempt-2/result.json").read_text())
    batch_ids = [row["batch_id"] for row in prior["batches"]] + [intent["batch_id"]]
    until = time.monotonic() + 1500
    while time.monotonic() < until:
        status = api.request("GET", f"/v2/runs/{run}")
        observation = {"elapsed_seconds": time.monotonic() - started,
                       "cursor": status["cursor"], "batches": status["batches"],
                       "pending_batch_id": status["pending_batch_id"], "closed_at": status["closed_at"]}
        receipt["status_observations"].append(observation)
        if status["batches"] == {"indexed": {"batches": 33, "records": 33}}:
            assert status["cursor"] == 1848 and not status["pending_batch_id"]
            receipt["final_status"] = status
            receipt["ok"] = True
            break
        if len(receipt["status_observations"]) == 1 or receipt["status_observations"][-2]["batches"] != status["batches"]:
            print(json.dumps(observation), flush=True)
        for identity in batch_ids:
            replay = api.request("POST", f"/v2/runs/{run}/batches/{identity}/commit")
            assert replay["duplicate"] and replay["cursor"] == 1848
        time.sleep(30)
    if not receipt["ok"]:
        receipt["error"] = "recovery_deadline"
except Exception as error:
    receipt["error_type"] = type(error).__name__
finally:
    receipt["elapsed_seconds"] = time.monotonic() - started
    with destination.open("x") as output:
        json.dump(receipt, output, sort_keys=True, indent=2)
        output.write("\n")
print(json.dumps({"ok": receipt["ok"], "error_type": receipt.get("error_type"),
                  "elapsed_seconds": receipt["elapsed_seconds"]}))
raise SystemExit(0 if receipt["ok"] else 1)
