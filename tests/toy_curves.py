"""Loader for the small planted-solve curves in tests/fixtures/toy_instances.json."""
import json
from pathlib import Path

from ecc_challenges.multicurve.curves import Instance

FIXTURE = Path(__file__).parent / "fixtures" / "toy_instances.json"


def load_toy_instances():
    result = {}
    for row in json.loads(FIXTURE.read_text()):
        instance = Instance(
            row["id"], row["field_kind"], row["bits"], int(row["modulus"], 16),
            int(row["a"], 16), int(row["b"], 16), int(row["n"], 16), row["h"],
            tuple(int(v, 16) for v in row["G"]), tuple(int(v, 16) for v in row["Q"]))
        instance.validate()
        result[instance.id] = (instance, int(row["planted_d"], 16))
    return result
