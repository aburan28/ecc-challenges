"""Independent arbitrary-width affine arithmetic for public Certicom instances."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import hashlib
import json
from math import isqrt

Point = tuple[int, int] | None


def polynomial_remainder(value: int, modulus: int) -> int:
    if value < 0 or modulus <= 0:
        raise ValueError("invalid polynomial")
    while value.bit_length() >= modulus.bit_length():
        value ^= modulus << (value.bit_length() - modulus.bit_length())
    return value


def polynomial_gcd(a: int, b: int) -> int:
    while b:
        a, b = b, polynomial_remainder(a, b)
    return a


def binary_multiply(a: int, b: int, modulus: int) -> int:
    bits = modulus.bit_length() - 1
    if bits < 1 or a < 0 or b < 0 or a >> bits or b >> bits:
        raise ValueError("noncanonical field element")
    out = 0
    while b:
        if b & 1:
            out ^= a
        b >>= 1
        a <<= 1
        if a >> bits:
            a ^= modulus
    return out


def binary_inverse(a: int, modulus: int) -> int:
    if not a:
        raise ZeroDivisionError("zero has no inverse")
    if a < 0 or a.bit_length() >= modulus.bit_length():
        raise ValueError("noncanonical field element")
    u, v, g1, g2 = a, modulus, 1, 0
    while u != 1:
        if not u:
            raise ValueError("element is not invertible")
        shift = u.bit_length() - v.bit_length()
        if shift < 0:
            u, v, g1, g2 = v, u, g2, g1
            shift = -shift
        u ^= v << shift
        g1 ^= g2 << shift
    return polynomial_remainder(g1, modulus)


def polynomial_is_irreducible(modulus: int) -> bool:
    degree = modulus.bit_length() - 1
    if degree == 1:
        return True
    if degree < 1 or not modulus & 1:
        return False
    factors, left, divisor = set(), degree, 2
    while divisor * divisor <= left:
        if left % divisor == 0:
            factors.add(divisor)
            while left % divisor == 0:
                left //= divisor
        divisor += 1
    if left > 1:
        factors.add(left)
    checks = {degree // factor for factor in factors}
    value = 2
    for power in range(1, degree + 1):
        value = binary_multiply(value, value, modulus)
        if power in checks and polynomial_gcd(value ^ 2, modulus) != 1:
            return False
    return value == 2


def is_probable_prime(value: int) -> bool:
    """Fixed Miller-Rabin checks; this is not a primality certificate."""
    bases = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    if value < 2:
        return False
    for base in bases:
        if value % base == 0:
            return value == base
    odd, twos = value - 1, 0
    while not odd & 1:
        odd >>= 1
        twos += 1
    for base in bases:
        power = pow(base, odd, value)
        if power in (1, value - 1):
            continue
        for _ in range(twos - 1):
            power = power * power % value
            if power == value - 1:
                break
        else:
            return False
    return True


@dataclass(frozen=True)
class Instance:
    id: str
    field_kind: str
    bits: int
    modulus: int
    a: int
    b: int
    n: int
    h: int
    G: tuple[int, int]
    Q: tuple[int, int]

    def __post_init__(self):
        if any(type(point) is not tuple or len(point) != 2 for point in (self.G, self.Q)):
            raise ValueError("G and Q must be immutable coordinate pairs")
        if self.field_kind not in ("prime", "binary") or self.bits < 2 or self.n < 2 or self.h < 1:
            raise ValueError("invalid field or subgroup geometry")
        if self.modulus.bit_length() != self.bits + (self.field_kind == "binary"):
            raise ValueError("field modulus width mismatch")
        limit = self.modulus if self.field_kind == "prime" else 1 << self.bits
        if any(type(v) is not int or not 0 <= v < limit for v in (self.a, self.b, *self.G, *self.Q)):
            raise ValueError("noncanonical field parameter")

    @property
    def field_bytes(self):
        return (self.bits + 7) // 8

    @property
    def scalar_bytes(self):
        return (self.n.bit_length() + 7) // 8

    @property
    def equation(self):
        return "y^2=x^3+a*x+b" if self.field_kind == "prime" else "y^2+x*y=x^3+a*x^2+b"

    def _record(self):
        return {"id": self.id, "field_kind": self.field_kind, "bits": self.bits,
                "modulus": hex(self.modulus), "a": hex(self.a), "b": hex(self.b),
                "n": hex(self.n), "h": self.h,
                "G": [hex(v) for v in self.G], "Q": [hex(v) for v in self.Q],
                "coordinate_encoding": "affine-xy-be", "equation": self.equation}

    @cached_property
    def instance_sha256(self):
        return hashlib.sha256(json.dumps(self._record(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def public(self):
        return {**self._record(), "instance_sha256": self.instance_sha256}

    def on_curve(self, point: Point):
        if point is None:
            return True
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            return False
        x, y = point
        limit = self.modulus if self.field_kind == "prime" else 1 << self.bits
        if any(type(v) is not int or not 0 <= v < limit for v in (x, y)):
            return False
        if self.field_kind == "prime":
            return (y*y - x*x*x - self.a*x - self.b) % self.modulus == 0
        xx = self._fmul(x, x)
        return self._fmul(y, y) ^ self._fmul(x, y) ^ self._fmul(xx, x) ^ self._fmul(self.a, xx) ^ self.b == 0

    def _require_point(self, point):
        if not self.on_curve(point):
            raise ValueError("point is not canonical on this curve")

    def _fmul(self, a, b):
        return binary_multiply(a, b, self.modulus)

    def neg(self, point):
        self._require_point(point)
        if point is None:
            return None
        x, y = point
        return (x, (-y) % self.modulus) if self.field_kind == "prime" else (x, x ^ y)

    def add(self, left, right):
        self._require_point(left)
        self._require_point(right)
        return self._add(tuple(left) if left is not None else None,
                         tuple(right) if right is not None else None)

    def _add(self, left, right):
        if left is None:
            return right
        if right is None:
            return left
        x, y = left
        u, v = right
        if self.field_kind == "prime":
            p = self.modulus
            if x == u:
                if (y + v) % p == 0:
                    return None
                slope = (3*x*x + self.a) * pow(2*y, -1, p) % p
            else:
                slope = (v-y) * pow(u-x, -1, p) % p
            z = (slope*slope-x-u) % p
            return z, (slope*(x-z)-y) % p
        if x == u:
            if y != v or x == 0:
                return None
            slope = x ^ self._fmul(y, binary_inverse(x, self.modulus))
            z = self._fmul(slope, slope) ^ slope ^ self.a
            return z, self._fmul(x, x) ^ self._fmul(slope ^ 1, z)
        slope = self._fmul(y ^ v, binary_inverse(x ^ u, self.modulus))
        z = self._fmul(slope, slope) ^ slope ^ x ^ u ^ self.a
        return z, self._fmul(slope, x ^ z) ^ z ^ y

    def mul(self, scalar, point, reduce=False):
        if type(scalar) is not int:
            raise TypeError("scalar must be integer")
        self._require_point(point)
        point = tuple(point) if point is not None else None
        if reduce:
            scalar %= self.n
        if scalar < 0:
            scalar, point = -scalar, self.neg(point)
        result = None
        while scalar:
            if scalar & 1:
                result = self._add(result, point)
            scalar >>= 1
            if scalar:
                point = self._add(point, point)
        return result

    def mul2(self, a, b):
        if type(a) is not int or type(b) is not int:
            raise TypeError("coefficients must be integers")
        a, b = a % self.n, b % self.n
        table = (None, self.G, self.Q, self._add(self.G, self.Q))
        result = None
        for shift in range(max(a.bit_length(), b.bit_length())-1, -1, -1):
            result = self._add(result, result)
            result = self._add(result, table[((a >> shift) & 1) | (((b >> shift) & 1) << 1)])
        return result

    def validate(self, *, check_orders=True):
        checks = {"G_on_curve": self.on_curve(self.G), "Q_on_curve": self.on_curve(self.Q),
                  "n_probable_prime": is_probable_prime(self.n)}
        if self.field_kind == "prime":
            checks.update(p_probable_prime=is_probable_prime(self.modulus),
                          nonsingular=(4*self.a**3 + 27*self.b**2) % self.modulus != 0)
            q = self.modulus
        else:
            checks.update(polynomial_irreducible=polynomial_is_irreducible(self.modulus), nonsingular=self.b != 0)
            q = 1 << self.bits
        checks["hn_in_hasse_interval"] = abs(self.h*self.n-(q+1)) <= isqrt(4*q)
        checks["n_exceeds_hasse_interval_width"] = self.n > 2*isqrt(4*q)
        if check_orders:
            checks.update(nG_identity=self.mul(self.n, self.G) is None, nQ_identity=self.mul(self.n, self.Q) is None)
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise ValueError(f"{self.id}: invalid instance parameters: {', '.join(failed)}")
        return checks
