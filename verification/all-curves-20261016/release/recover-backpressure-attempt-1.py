"""Finish the exact reservation left by the first bounded harness attempt."""
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

receipt = {"format": "release-reservation-recovery-v1", "ok": False,
           "run_id": "1135f9c9-156b-4554-9de5-f498247401dd",
           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
destination = Path(__file__).with_name("backpressure-attempt-1-recovery.json")
if destination.exists():
    raise FileExistsError(destination)
try:
    binding = json.loads(Path(__file__).with_name("live-cpu-archive-binding.json").read_text())
    source = binding["rows"][0]
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
        "offset": 0, "byte_length": len(raw), "records": 1, "sha256": source["sha256"]})
    if intent["upload"]:
        api.upload(intent["upload"], raw)
    receipt["commit"] = api.request("POST", f"/v2/runs/{run}/batches/{intent['batch_id']}/commit")
    until = time.monotonic() + 180
    while time.monotonic() < until:
        status = api.request("GET", f"/v2/runs/{run}")
        receipt["status"] = status
        if status["batches"] == {"indexed": {"batches": 1, "records": 1}}:
            break
        time.sleep(3)
    else:
        raise RuntimeError("recovery_index_deadline")
    receipt["closure"] = api.request("POST", f"/v2/runs/{run}/close")
    assert receipt["closure"].get("closed_at")
    receipt["ok"] = True
except Exception as error:
    receipt["error_type"] = type(error).__name__
finally:
    with destination.open("x") as output:
        json.dump(receipt, output, sort_keys=True, indent=2)
        output.write("\n")
print(json.dumps({"ok": receipt["ok"], "error_type": receipt.get("error_type")}))
raise SystemExit(0 if receipt["ok"] else 1)
