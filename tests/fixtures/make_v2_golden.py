"""Regenerate tests/fixtures/v2_golden.json (run only against an unmodified v2).

The fixture pins every v2 namespace id, public manifest digest, job encoding
and native-engine output, so a change to the walk code that alters any v2
byte is caught by tests/test_negation_walk.py::test_v2_outputs_unchanged.
It was generated once, from the tree before the negation-class walk existed.

    PYTHONPATH=src:worker python tests/fixtures/make_v2_golden.py ENGINE > tests/fixtures/v2_golden.json
"""
import hashlib
import json
import sys
from pathlib import Path

from ecc_challenges.multicurve.codec import Namespace, canonical, dp_bits_for
from ecc_challenges.multicurve.instances import all_instances, instance_ids
from generic.runtime import NativeState, pack_job, pack_state, run_round

RUN_ID = "18c3053b-96df-4879-a49d-18a52b8fdf8b"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(engine: Path) -> dict:
    result = {"namespaces": {}, "engine": {}}
    for instance in all_instances():
        ns = Namespace(instance.id)
        result["namespaces"][instance.id] = {
            "namespace_id": ns.id, "dp_bits": ns.dp_bits,
            "dp_bits_for": dp_bits_for(instance.n),
            "public_sha256": sha(canonical(ns.public())),
            "record_bytes": ns.record_bytes,
        }
    for campaign in instance_ids():
        ns = Namespace(campaign, dp_bits=3)
        starts = tuple(NativeState(ns.start(RUN_ID, t)) for t in range(3))
        job = pack_job(ns, 3, 12)
        out = run_round(engine, "cpu", ns, starts, 12, timeout=300)
        result["engine"][campaign] = {
            "namespace_id": ns.id,
            "job_sha256": sha(job),
            "start_sha256": sha(b"".join(pack_state(s.record, s.active) for s in starts)),
            "state_sha256": sha(b"".join(pack_state(s.record, s.active) for s in out.states)),
            "dp_sha256": sha(b"".join(pack_state(NativeState(r).record, 0) for r in out.distinguished)),
            "steps": [s.steps for s in out.states],
        }
    return result


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1])), indent=1, sort_keys=True))
