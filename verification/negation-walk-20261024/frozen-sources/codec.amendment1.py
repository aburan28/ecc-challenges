"""Fixed-width, full affine DP witnesses for every published Certicom curve.

Widths are constant inside a namespace. No binary-field sign convention or
machine integer width is implicit in the wire representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
import struct
from uuid import UUID

from .instances import get_instance

MAX_BATCH_BYTES = 1024 * 1024
# Per-curve DP bits: expected rho steps E = sqrt(pi*n/2) for this walk (no
# automorphism quotient). With up to 2**PLANNED_WALKS_BITS walks running at
# once, d = floor(log2 E) - PLANNED_WALKS_BITS - 7 keeps the steps left in
# unfinished walks under 1% of E, and a curve needs about 2**(PLANNED_WALKS_BITS
# + 7) DPs. MAX_DP_BITS caps the large curves, where the DP count then grows.
PLANNED_WALKS_BITS = 16
MAX_DP_BITS = 34
MAX_TRAIL_STEPS = 1 << 48
PREFIX = struct.Struct("<QQ")

# Walks. The v2 adding walk is the default and its records, namespaces and
# manifests are unchanged. The negation-class walk is a separate namespace
# family behind its own flag (MULTICURVE_NEGATION_ENABLED); see
# docs/negation-walk.md. Its native parameters are pinned by the walk record.
WALK_V2 = "certicom-affine-radding32-v2"
WALK_NEGATION = "certicom-affine-negation-class32-v1"
WALKS = (WALK_V2, WALK_NEGATION)
NEGATION_PARAMETERS = {
    "lookahead_attempts": 4,
    "cycle_window": 32,
    "cycle_check_period": 4,
    "escape_jump_offset": 16,
}


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def expected_steps_log2(order: int, walk: str = WALK_V2) -> float:
    """log2 of the expected steps to a collision: sqrt(pi*n/2) for the v2 walk
    and sqrt(pi*n/4) for the negation-class walk, which searches n/2 classes
    (before the small look-ahead and cycle-handling overhead)."""
    if walk == WALK_NEGATION:
        return (math.log2(order) + math.log2(math.pi / 4)) / 2
    return (math.log2(order) + math.log2(math.pi / 2)) / 2


def dp_bits_for(order: int, walk: str = WALK_V2) -> int:
    return max(1, min(MAX_DP_BITS, math.floor(expected_steps_log2(order, walk)) - PLANNED_WALKS_BITS - 7))


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
    def __init__(self, campaign: str, dp_bits: int | None = None,
                 walk: str = WALK_V2, *, instance=None):
        # `instance` lets tests and measurements build a namespace for a toy
        # curve; the registry instance is used otherwise.
        if walk not in WALKS:
            raise ValueError("unknown walk")
        self.instance = instance if instance is not None else get_instance(campaign)
        self.negation = walk == WALK_NEGATION
        if dp_bits is None:
            dp_bits = dp_bits_for(self.instance.n, walk)
        if type(dp_bits) is not int or not 1 <= dp_bits <= 40:
            raise ValueError("distinguished bits must be between 1 and 40")
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
        if self.negation:
            self.walk = {
                "id": WALK_NEGATION,
                "class_representative": "of {P,-P} the point with the smaller y as an unsigned integer (prime: min(y,p-y); binary: min(y,x^y)); y=-y is its own representative",
                "partition": "sha256(x-field-bytes-BE||y-field-bytes-BE of the class representative)-first-byte-low-5-bits; infinity-zero",
                "distinguished": "finite-sha256(x-field-bytes-BE||y-field-bytes-BE of the class representative)-trailing-dp-bits-zero",
                "coefficients": "R=aG+bQ for the stored representative; (a,b) negated mod n whenever the representative is the negative of the computed point",
                "cycle_handling": {
                    "technique": "2-cycle look-ahead plus periodic cycle check with a deterministic escape from the cycle's smallest member by a jump the cycle cannot have used, itself subject to the look-ahead (Bernstein-Lange-Schwabe 2011; Bos-Kleinjung-Lenstra 2010)",
                    **NEGATION_PARAMETERS,
                },
                "native_job": "ECCJOB04",
                "dp_bits": dp_bits,
                "seed_derivation": "shake256-full-width-rejection-domain-run-uuid-trail-counter-v1",
                "jump_coefficients": [[hex(a), hex(b)] for a, b in self.coefficients],
                "automorphism_quotient": "negation",
                "max_trail_steps": MAX_TRAIL_STEPS,
            }
        else:
            self.walk = {
                "id": WALK_V2,
                "partition": "sha256(x-field-bytes-BE||y-field-bytes-BE)-first-byte-low-5-bits; infinity-zero",
                "distinguished": "finite-sha256(x-field-bytes-BE||y-field-bytes-BE)-trailing-dp-bits-zero",
                "native_job": "ECCJOB03",
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

    def selector(self, point: tuple[int, int]) -> bytes:
        if self.negation:
            # The digest of the class, whichever of P and -P is passed.
            point = self.canonical_point(point)[0]
        return hashlib.sha256(b"".join(v.to_bytes(self.field_bytes, "big") for v in point)).digest()

    def negated_point(self, point):
        """-P for an affine point (None is its own negative)."""
        return self.instance.neg(point)

    def canonical_point(self, point):
        """(class representative, negated) for the negation-class walk.

        The representative is the one of P and -P with the smaller y as an
        unsigned integer; a point with y = -y is its own representative.
        """
        if point is None:
            return None, False
        negative = self.instance.neg(point)
        if negative[1] < point[1]:
            return negative, True
        return tuple(point), False

    def negate_coefficients(self, a: int, b: int) -> tuple[int, int]:
        n = self.instance.n
        return (-a) % n, (-b) % n

    def partition(self, point: tuple[int, int] | None) -> int:
        return 0 if point is None else self.selector(point)[0] & 31

    def distinguished(self, point: tuple[int, int] | None) -> bool:
        return point is not None and int.from_bytes(self.selector(point)[-5:], "big") & ((1 << self.dp_bits) - 1) == 0

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
        if self.negation and self.canonical_point(record.point)[1]:
            raise ValueError("point is not the canonical class representative")
        if not self.distinguished(record.point):
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

    def _start_seed(self, run_id: str, trail_id: int, counter: int) -> bytes:
        run = UUID(str(run_id))
        if type(trail_id) is not int or not 0 <= trail_id < 1 << 64:
            raise ValueError("trail ID out of range")
        return (b"ecc-start-v2:" + bytes.fromhex(self.id) + run.bytes
                + trail_id.to_bytes(8, "little") + bytes([counter]))

    def start(self, run_id: str, trail_id: int) -> Record:
        for counter in range(256):
            seed = self._start_seed(run_id, trail_id, counter)
            a = derive_scalar(seed + b"a", self.instance.n)
            b = derive_scalar(seed + b"b", self.instance.n, nonzero=True)
            point = self.instance.mul2(a, b)
            if point is not None:
                if self.negation:
                    # A trail starts on its class representative, with (a, b)
                    # negated along with the point.
                    point, negated = self.canonical_point(point)
                    if negated:
                        a, b = self.negate_coefficients(a, b)
                # Starts are not DP records: steps=0 and no DP requirement.
                return Record(trail_id, 0, point, a, b)
        raise RuntimeError("unable to derive a finite trail start")

    def check_trail(self, record: Record, run_id: str) -> None:
        """Jumps add only multiples of G, so b is fixed for a whole trail: a DP
        must carry the b of the start this run's trail ID derives."""
        seed = self._start_seed(run_id, record.trail_id, 0)
        start_b = derive_scalar(seed + b"b", self.instance.n, nonzero=True)
        # The negation-class walk negates (a, b) with the point, so a trail's
        # b is the start's b or its negative; no other value is reachable.
        allowed = (start_b, -start_b % self.instance.n) if self.negation else (start_b,)
        if record.b in allowed:
            return
        # Counter 0 yields the point at infinity with probability ~1/n; only
        # then does the start use a later counter.
        later = self.start(run_id, record.trail_id).b
        if record.b not in ((later, -later % self.instance.n) if self.negation else (later,)):
            raise ValueError("DP b does not match its trail start")

    def _solve(self, sign: int, pairs):
        """d from two coefficient pairs under R1 = sign * R2; accepted only if dG = Q.

        sign +1: a1 + b1 d = a2 + b2 d, so d = (a1-a2)/(b2-b1).
        sign -1: a1 + b1 d = -(a2 + b2 d), so d = -(a1+a2)/(b1+b2).
        Returns (state, reason, scalar).
        """
        n = self.instance.n
        (a1, b1), (a2, b2) = pairs
        if sign == 1:
            numerator, denominator = (a1 - a2) % n, (b2 - b1) % n
        else:
            numerator, denominator = (-(a1 + a2)) % n, (b1 + b2) % n
        if not denominator:
            return "degenerate", "coefficient difference has zero target denominator", None
        try:
            value = numerator * pow(denominator, -1, n) % n
        except ValueError:
            return "degenerate", "coefficient difference has a noninvertible denominator", None
        if self.instance.mul(value, self.instance.G) == self.instance.Q:
            return "verified", "recovered scalar independently satisfies dG = Q", value
        return "rejected", "recovered scalar does not satisfy dG = Q", None

    def solve_collision(self, point, pairs):
        """Solve the target from two coefficient pairs of one stored point.

        Returns (state, reason, scalar, sign) with sign +1: both pairs satisfy
        R = aG + bQ for the same point, so d = (a1-a2)/(b2-b1). This is the
        server path for both walks: the negation-class walk stores class
        representatives, so records of one class are equal points.
        """
        return (*self._solve(1, pairs), 1)

    def solve_class_collision(self, first, second):
        """Solve from two records (point, a, b) whose points are R1 = +-R2.

        R1 = R2 gives d = (a1-a2)/(b2-b1); for the negation-class walk R1 = -R2
        gives d = -(a1+a2)/(b1+b2). The sign is read off the points and the
        candidate is accepted only if dG = Q, so a wrong sign cannot be
        returned as a solution. Raises ValueError unless the points are equal
        or, for the negation-class walk, negatives of each other. Returns
        (state, reason, scalar, sign).
        """
        (r1, a1, b1), (r2, a2, b2) = first, second
        if r1 == r2:
            sign = 1
        elif self.negation and r1 == self.negated_point(r2):
            sign = -1
        else:
            raise ValueError("points are not in one negation class" if self.negation else "points differ")
        return (*self._solve(sign, ((a1, b1), (a2, b2))), sign)


@lru_cache(maxsize=96)
def namespace(campaign: str, dp_bits: int | None = None, walk: str = WALK_V2) -> Namespace:
    return Namespace(campaign, dp_bits, walk)


def pinned_namespace(campaign: str, manifest: dict) -> Namespace:
    """Rebuild the namespace a run or batch was pinned to, so runs registered
    under an earlier DP setting keep verifying after the default changes."""
    walk = manifest.get("walk") if isinstance(manifest, dict) else None
    dp_bits = walk.get("dp_bits") if isinstance(walk, dict) else None
    if type(dp_bits) is not int:
        raise ValueError("pinned manifest has no DP bit count")
    walk_id = walk.get("id", WALK_V2)
    if walk_id not in WALKS:
        raise ValueError("pinned manifest names an unknown walk")
    # The v2 call is unchanged so its cache entries and identities are shared.
    return namespace(campaign, dp_bits) if walk_id == WALK_V2 else namespace(campaign, dp_bits, walk_id)
