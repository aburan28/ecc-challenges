"""Fixed-width, full affine DP witnesses for every published Certicom curve.

Widths are constant inside a namespace. No binary-field sign convention or
machine integer width is implicit in the wire representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import struct
from uuid import UUID

from .instances import get_instance

MAX_BATCH_BYTES = 1024 * 1024
DEFAULT_DP_BITS = 20
MAX_TRAIL_STEPS = 1 << 48
PREFIX = struct.Struct("<QQ")


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def derive_scalar(domain: bytes, order: int, *, nonzero: bool = False) -> int:
    """Uniform rejection sampling over the entire subgroup width."""
    width = (order.bit_length() + 7) // 8
    mask = (1 << order.bit_length()) - 1
    for counter in range(1 << 16):
        value = int.from_bytes(hashlib.shake_256(
            domain + counter.to_bytes(4, "big")
        ).digest(width), "big") & mask
        if value < order and (value != 0 or not nonzero):
            return value
    raise RuntimeError("scalar rejection sampler exhausted")


@dataclass(frozen=True)
class Record:
    trail_id: int
    steps: int
    point: tuple[int, int] | None
    a: int
    b: int


class Namespace:
    def __init__(self, campaign: str, dp_bits: int = DEFAULT_DP_BITS):
        if not 1 <= dp_bits <= 40:
            raise ValueError("distinguished bits must be between 1 and 40")
        self.instance = get_instance(campaign)
        campaign = self.instance.id
        self.campaign = campaign
        self.dp_bits = dp_bits
        self.field_bytes = self.instance.field_bytes
        self.scalar_bytes = self.instance.scalar_bytes
        self.record_bytes = PREFIX.size + 2 * self.field_bytes + 2 * self.scalar_bytes
        # A nonzero G multiple guarantees each jump is finite. The point map is
        # shared by all trails; the independently seeded b coefficient is kept.
        self.coefficients = tuple(
            (derive_scalar(
                f"ecc-radding32-v1:{self.instance.instance_sha256}:{i}".encode()
                , self.instance.n, nonzero=True), 0)
            for i in range(32)
        )
        self.walk = {
            "id": "certicom-affine-radding32-v1",
            "partition": "finite-x-low-5-bits; infinity-selects-zero",
            "distinguished": "finite-x-low-dp-bits-zero",
            "dp_bits": dp_bits,
            "seed_derivation": "shake256-full-width-rejection-domain-run-uuid-trail-counter-v1",
            "jump_coefficients": [[hex(a), hex(b)] for a, b in self.coefficients],
            "automorphism_quotient": "none",
            "max_trail_steps": MAX_TRAIL_STEPS,
        }
        self.binding = {
            "protocol": 2, "campaign": campaign,
            "instance_sha256": self.instance.instance_sha256,
            "walk": self.walk,
            "codec": {"id": "affine-xy-ab-v1", "coordinate_order": "big",
                      "prefix": "uint64le-trail-id,uint64le-steps",
                      "field_bytes": self.field_bytes, "scalar_bytes": self.scalar_bytes,
                      "record_bytes": self.record_bytes},
        }
        self.id = hashlib.sha256(canonical(self.binding)).hexdigest()

    def public(self) -> dict:
        return {**self.binding, "namespace_id": self.id, "instance": self.instance.public(),
                "max_batch_bytes": MAX_BATCH_BYTES,
                "backends": ["cpu", "cuda"]}

    def point_key(self, record: Record) -> bytes:
        return b"".join(v.to_bytes(self.field_bytes, "big") for v in record.point)

    def witness(self, record: Record) -> bytes:
        return record.a.to_bytes(self.scalar_bytes, "big") + record.b.to_bytes(self.scalar_bytes, "big")

    def encode(self, record: Record) -> bytes:
        self.check_record(record)
        return PREFIX.pack(record.trail_id, record.steps) + self.point_key(record) + self.witness(record)

    def check_record(self, record: Record) -> None:
        if (type(record.trail_id) is not int or type(record.steps) is not int
                or not 0 <= record.trail_id < 1 << 64 or not 1 <= record.steps <= MAX_TRAIL_STEPS):
            raise ValueError("invalid trail identity or step count")
        if not isinstance(record.point, tuple) or len(record.point) != 2:
            raise ValueError("DP witness must be a finite affine coordinate pair")
        limit = self.instance.modulus if self.instance.field_kind == "prime" else 1 << self.instance.bits
        if any(type(value) is not int or not 0 <= value < limit for value in record.point):
            raise ValueError("noncanonical affine coordinate")
        if (type(record.a) is not int or type(record.b) is not int
                or not 0 <= record.a < self.instance.n or not 0 <= record.b < self.instance.n):
            raise ValueError("noncanonical subgroup coefficient")
        if record.point[0] & ((1 << self.dp_bits) - 1):
            raise ValueError("point does not satisfy the namespace DP predicate")

    def decode(self, data: bytes) -> list[Record]:
        if not data or len(data) > MAX_BATCH_BYTES or len(data) % self.record_bytes:
            raise ValueError("batch must contain whole namespace records within the byte limit")
        result = []
        for offset in range(0, len(data), self.record_bytes):
            trail, steps = PREFIX.unpack_from(data, offset)
            cursor = offset + PREFIX.size
            values = []
            for width in (self.field_bytes, self.field_bytes, self.scalar_bytes, self.scalar_bytes):
                values.append(int.from_bytes(data[cursor:cursor + width], "big"))
                cursor += width
            record = Record(trail, steps, (values[0], values[1]), values[2], values[3])
            self.check_record(record)
            result.append(record)
        return result

    def verify(self, record: Record) -> None:
        self.check_record(record)
        if not self.instance.on_curve(record.point):
            raise ValueError("point is not on the assigned curve")
        if self.instance.mul2(record.a, record.b) != record.point:
            raise ValueError("point does not equal aG+bQ")

    @lru_cache(maxsize=1)
    def table(self) -> tuple:
        return tuple((self.instance.mul(a, self.instance.G), a, b) for a, b in self.coefficients)

    def start(self, run_id: str, trail_id: int) -> Record:
        run = UUID(run_id)
        if not 0 <= trail_id < 1 << 64:
            raise ValueError("trail ID out of range")
        prefix = b"ecc-start-v2:" + bytes.fromhex(self.id) + run.bytes + trail_id.to_bytes(8, "little")
        for counter in range(256):
            seed = prefix + bytes([counter])
            a = derive_scalar(seed + b"a", self.instance.n)
            b = derive_scalar(seed + b"b", self.instance.n, nonzero=True)
            point = self.instance.mul2(a, b)
            if point is not None:
                # Starts are not DP records: steps=0 and no DP requirement.
                return Record(trail_id, 0, point, a, b)
        raise RuntimeError("unable to derive a finite trail start")


@lru_cache(maxsize=96)
def namespace(campaign: str, dp_bits: int = DEFAULT_DP_BITS) -> Namespace:
    return Namespace(campaign, dp_bits)
