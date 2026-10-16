"""Pinned, exact parameters for all 24 Certicom challenge instances."""
from functools import lru_cache
from importlib.resources import files
import json

from .curves import Instance, Point


def _hex(value):
    if not isinstance(value, str):
        raise ValueError("registry integers must be hexadecimal strings")
    result = int(value, 16)
    if hex(result) != value or result < 0:
        raise ValueError("noncanonical registry integer")
    return result


@lru_cache(maxsize=1)
def _load():
    raw = json.loads(files(__package__).joinpath("instances.json").read_text())
    if raw.get("format_version") != 1:
        raise ValueError("unsupported registry version")
    instances = {}
    for row in raw["instances"]:
        identity = row["id"]
        if identity != identity.lower() or identity in instances:
            raise ValueError("duplicate or noncanonical instance ID")
        instance = Instance(identity, row["field_kind"], row["bits"],
                            *(_hex(row[k]) for k in ("modulus", "a", "b", "n")), row["h"],
                            tuple(map(_hex, row["G"])), tuple(map(_hex, row["Q"])))
        if instance._record() != row or not instance.on_curve(instance.G) or not instance.on_curve(instance.Q):
            raise ValueError(f"{identity}: invalid registry encoding or point")
        instances[identity] = instance
    if len(instances) != 24:
        raise ValueError("registry must contain all 24 Certicom instances")
    return instances, {k: v for k, v in raw.items() if k != "instances"}


def get_instance(campaign):
    if not isinstance(campaign, str):
        raise ValueError("campaign must be a string")
    try:
        return _load()[0][campaign.lower()]
    except KeyError:
        raise ValueError(f"unsupported Certicom challenge: {campaign}") from None


def all_instances():
    return tuple(_load()[0].values())


def instance_ids():
    return tuple(_load()[0])


def registry_metadata():
    return json.loads(json.dumps(_load()[1]))
