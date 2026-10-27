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

# Walk versions. V2 is the published SHA-256 walk and stays valid for every
# curve and for every run already registered under it. V3 and V4 read the
# partition and the DP test from bits of x (no hash) and are defined only for
# the two 131-bit Certicom curves; V4 also walks the classes {P, -P}.
WALK_V2, WALK_V3, WALK_V4, WALK_V5 = 2, 3, 4, 5
WALK_NEGATION = "certicom-affine-negation-class32-v1"
WALK_IDS = {
    WALK_V2: "certicom-affine-radding32-v2",
    WALK_V3: "certicom-affine-radding32-xonly-v3",
    WALK_V4: "certicom-affine-radding32-negmap-v4",
    WALK_V5: WALK_NEGATION,
}
WALK_V2_ID = WALK_IDS[WALK_V2]
WALKS = tuple(WALK_IDS.values())
NEGATION_PARAMETERS = {
    "lookahead_attempts": 4,
    "cycle_window": 32,
    "cycle_check_period": 4,
    "escape_multiplier_max": 65535,
}
FAST_WALK_CAMPAIGNS = ("ecc2-131", "eccp-131")
DEFAULT_WALKS = {campaign: WALK_V4 for campaign in FAST_WALK_CAMPAIGNS}
PARTITION_MULTIPLIER = 0x9E3779B97F4A7C15
TABLE_LOG2 = 5
DP_SHIFT = 64  # walks 3/4: the DP test reads x bits DP_SHIFT .. DP_SHIFT + dp_bits - 1
ANCHOR_LOG2 = 10
M64 = (1 << 64) - 1


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def expected_steps_log2(order: int, walk: int = WALK_V2) -> float:
    # Walks 4 and 5 quotient by negation. Walk 5 also accepts its public ID.
    factor = math.pi / 4 if walk in (WALK_V4, WALK_V5, WALK_NEGATION) else math.pi / 2
    return (math.log2(order) + math.log2(factor)) / 2


def dp_bits_for(order: int, walk: int = WALK_V2) -> int:
    return max(1, min(MAX_DP_BITS,
                      math.floor(expected_steps_log2(order, walk)) - PLANNED_WALKS_BITS - 7))


def partition_x(x: int) -> int:
    """V3/V4 jump index: top bits of a multiplicative hash of x's low word."""
    return ((x & M64) * PARTITION_MULTIPLIER & M64) >> (64 - TABLE_LOG2)


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
    def __init__(self, campaign: str, dp_bits: int | None = None, walk_version: int | str | None = None,
                 *, walk: str | None = None, instance=None):
        self.instance = instance if instance is not None else get_instance(campaign)
        if walk is not None:
            if walk_version is not None:
                raise ValueError("specify only one walk selector")
            walk_version = walk
        if isinstance(walk_version, str):
            walk_version = {identity: version for version, identity in WALK_IDS.items()}.get(walk_version)
            if walk_version is None:
                raise ValueError("unknown walk version")
        if walk_version is None:
            walk_version = DEFAULT_WALKS.get(self.instance.id, WALK_V2)
        if walk_version not in WALK_IDS:
            raise ValueError("unknown walk version")
        if walk_version not in (WALK_V2, WALK_V5) and self.instance.id not in FAST_WALK_CAMPAIGNS:
            raise ValueError("walks 3 and 4 are defined only for the 131-bit Certicom curves")
        self.walk_version = walk_version
        self.negation = walk_version in (WALK_V4, WALK_V5)
        self.dp_shift = DP_SHIFT  # tests of toy groups narrower than 64 bits lower it
        if dp_bits is None:
            dp_bits = dp_bits_for(self.instance.n, walk_version)
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
        if walk_version == WALK_V2:
            self.walk = {
                "id": WALK_IDS[WALK_V2],
                "partition": "sha256(x-field-bytes-BE||y-field-bytes-BE)-first-byte-low-5-bits; infinity-zero",
                "distinguished": "finite-sha256(x-field-bytes-BE||y-field-bytes-BE)-trailing-dp-bits-zero",
                "native_job": "ECCJOB03",
                "dp_bits": dp_bits,
                "seed_derivation": "shake256-full-width-rejection-domain-run-uuid-trail-counter-v1",
                "jump_coefficients": [[hex(a), hex(b)] for a, b in self.coefficients],
                "automorphism_quotient": "none",
                "max_trail_steps": MAX_TRAIL_STEPS,
            }
        elif walk_version in (WALK_V3, WALK_V4):
            # One extra coefficient is the V4 escape jump, derived like the others.
            self.escape = (derive_scalar(
                f"ecc-radding32-v1:{self.instance.instance_sha256}:32".encode(),
                self.instance.n, nonzero=True), 0)
            self.walk = {
                "id": WALK_IDS[walk_version],
                "partition": "jump=((x mod 2^64)*0x9e3779b97f4a7c15 mod 2^64)>>59; infinity-zero",
                "distinguished": "finite-bits-64..64+dp_bits-1-of-x-zero",
                "native_job": "ECCJOB04",
                "dp_bits": dp_bits,
                "seed_derivation": "shake256-full-width-rejection-domain-run-uuid-trail-counter-v1",
                "jump_coefficients": [[hex(a), hex(b)] for a, b in self.coefficients],
                "automorphism_quotient": "none" if walk_version == WALK_V3 else "negation",
                "max_trail_steps": MAX_TRAIL_STEPS,
            }
            if walk_version == WALK_V4:
                self.walk.update({
                    "canonical_point": "y-not-greater-than-its-negation (prime: y<=(p-1)/2; binary: y<=x^y)",
                    "negation_coefficients": "a and b are negated with the point, so aG+bQ equals the stored point exactly",
                    "escape_jump": [hex(self.escape[0]), hex(self.escape[1])],
                    "cycle_escape": f"2-cycles and cycles revisiting a 2^{ANCHOR_LOG2}-step anchor leave through the escape jump at the cycle's least x low word; cycle state resets each engine round",
                })
        else:
            self.walk = {
                "id": WALK_NEGATION,
                "class_representative": "of {P,-P} the point with the smaller y as an unsigned integer (prime: min(y,p-y); binary: min(y,x^y)); y=-y is its own representative",
                "partition": "sha256(x-field-bytes-BE||y-field-bytes-BE of the class representative)-first-byte-low-5-bits; infinity-zero",
                "distinguished": "finite-sha256(x-field-bytes-BE||y-field-bytes-BE of the class representative)-trailing-dp-bits-zero",
                "coefficients": "R=aG+bQ for the stored representative; (a,b) negated mod n whenever the representative is the negative of the computed point",
                "cycle_handling": {
                    "technique": "2-cycle look-ahead plus periodic cycle check; a detected cycle is left from its smallest-fingerprint member by adding c*G with c = 1 + (fingerprint>>5) mod escape_multiplier_max, (a,b)->(a+c,b), a deterministic function of the cycle",
                    **NEGATION_PARAMETERS,
                },
                "native_job": "ECCJOB04", "dp_bits": dp_bits,
                "seed_derivation": "shake256-full-width-rejection-domain-run-uuid-trail-counter-v1",
                "jump_coefficients": [[hex(a), hex(b)] for a, b in self.coefficients],
                "automorphism_quotient": "negation", "max_trail_steps": MAX_TRAIL_STEPS,
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

    def canonical_sign(self, point: tuple[int, int]) -> bool:
        """True if the point is the representative of {P, -P} that V4 stores."""
        x, y = point
        if self.instance.field_kind == "prime":
            return y <= (self.instance.modulus - 1) // 2
        return (x ^ y) >= y

    def selector(self, point: tuple[int, int]) -> bytes:
        if self.walk_version == WALK_V5:
            point = self.canonical_point(point)[0]
        return hashlib.sha256(b"".join(v.to_bytes(self.field_bytes, "big") for v in point)).digest()

    def canonical_point(self, point):
        """Return the smaller-y member of {P, -P} and whether it was negated."""
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
        if point is None:
            return 0
        if self.walk_version in (WALK_V3, WALK_V4):
            return partition_x(point[0])
        return self.selector(point)[0] & 31

    def distinguished(self, point: tuple[int, int] | None) -> bool:
        if point is None:
            return False
        if self.walk_version in (WALK_V3, WALK_V4):
            return (point[0] >> self.dp_shift) & ((1 << self.dp_bits) - 1) == 0
        return int.from_bytes(self.selector(point)[-5:], "big") & ((1 << self.dp_bits) - 1) == 0

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
        if not self.distinguished(record.point):
            raise ValueError("point does not satisfy the namespace DP predicate")
        if self.walk_version == WALK_V4 and not self.canonical_sign(record.point):
            raise ValueError("point is not the canonical representative of its +/- class")
        if self.walk_version == WALK_V5 and self.canonical_point(record.point)[1]:
            raise ValueError("point is not the canonical class representative")

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

    def escape_jump(self) -> tuple:
        """The V4 escape jump (point, da, db)."""
        a, b = self.escape
        return (self.instance.mul(a, self.instance.G), a, b)

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
                if self.walk_version == WALK_V5:
                    point, negated = self.canonical_point(point)
                    if negated:
                        a, b = self.negate_coefficients(a, b)
                # Starts are not DP records: steps=0 and no DP requirement.
                return Record(trail_id, 0, point, a, b)
        raise RuntimeError("unable to derive a finite trail start")

    def check_trail(self, record: Record, run_id: str) -> None:
        """Jumps add only multiples of G, so b is fixed for a whole trail: a DP
        must carry the b of the start this run's trail ID derives."""
        # The V4 negation map flips the sign of b with the point, so a trail's
        # b is the start's b or its negative.
        n = self.instance.n
        accepted = (lambda b0: {b0, (n - b0) % n}) if self.negation else (lambda b0: {b0})
        seed = self._start_seed(run_id, record.trail_id, 0)
        if record.b in accepted(derive_scalar(seed + b"b", n, nonzero=True)):
            return
        # Counter 0 yields the point at infinity with probability ~1/n; only
        # then does the start use a later counter.
        if record.b not in accepted(self.start(run_id, record.trail_id).b):
            raise ValueError("DP b does not match its trail start")

    def _solve(self, sign: int, pairs):
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
        """Solve two coefficient witnesses for the same stored point."""
        return (*self._solve(1, pairs), 1)

    def solve_class_collision(self, first, second):
        """Solve records with equal points, or opposite points for walk 5."""
        (r1, a1, b1), (r2, a2, b2) = first, second
        if r1 == r2:
            sign = 1
        elif self.walk_version == WALK_V5 and r1 == self.instance.neg(r2):
            sign = -1
        else:
            raise ValueError("points are not in one negation class" if self.negation else "points differ")
        return (*self._solve(sign, ((a1, b1), (a2, b2))), sign)


@lru_cache(maxsize=96)
def _namespace(campaign: str, dp_bits: int, walk_version: int) -> Namespace:
    return Namespace(campaign, dp_bits, walk_version)


def namespace(campaign: str, dp_bits: int | None = None, walk_version: int | str | None = None) -> Namespace:
    """One shared object per (campaign, DP bits, walk), however the caller spells the defaults."""
    instance = get_instance(campaign)
    if walk_version is None:
        walk_version = DEFAULT_WALKS.get(instance.id, WALK_V2)
    if isinstance(walk_version, str):
        walk_version = {identity: version for version, identity in WALK_IDS.items()}.get(walk_version)
        if walk_version is None:
            raise ValueError("unknown walk version")
    if dp_bits is None and walk_version in WALK_IDS:
        dp_bits = dp_bits_for(instance.n, walk_version)
    return _namespace(campaign, dp_bits, walk_version)


def pinned_namespace(campaign: str, manifest: dict) -> Namespace:
    """Rebuild the namespace a run or batch was pinned to, so runs registered
    under an earlier DP setting keep verifying after the default changes."""
    walk = manifest.get("walk") if isinstance(manifest, dict) else None
    dp_bits = walk.get("dp_bits") if isinstance(walk, dict) else None
    if type(dp_bits) is not int:
        raise ValueError("pinned manifest has no DP bit count")
    # Manifests written before walk versions existed carry the V2 id.
    versions = {identity: version for version, identity in WALK_IDS.items()}
    version = versions.get(walk.get("id"))
    if version is None:
        raise ValueError("pinned manifest names an unknown walk")
    return namespace(campaign, dp_bits, version)
