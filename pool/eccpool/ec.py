"""Short-Weierstrass elliptic curves over prime fields, affine coordinates.

Points are ``(x, y)`` tuples of ints; the point at infinity is ``None``.
This is reference arithmetic: it is what the server uses to verify shares
and what any optimised worker (C, CUDA, ...) must agree with bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass

Point = tuple[int, int] | None


@dataclass(frozen=True)
class Curve:
    """y^2 = x^3 + a x + b over F_p, with a base point G of prime order n."""

    p: int
    a: int
    b: int
    gx: int
    gy: int
    n: int

    @property
    def G(self) -> tuple[int, int]:
        return (self.gx, self.gy)

    def validate(self) -> None:
        """Raise ValueError unless this is a usable prime-order curve."""
        if self.p < 5 or not is_probable_prime(self.p):
            raise ValueError("p must be prime")
        if not is_probable_prime(self.n):
            raise ValueError("n must be prime")
        if (4 * self.a ** 3 + 27 * self.b ** 2) % self.p == 0:
            raise ValueError("singular curve")
        if not 0 <= self.a < self.p or not 0 <= self.b < self.p:
            raise ValueError("a and b must be reduced mod p")
        if not self.on_curve(self.G) or self.mul_raw(self.n, self.G) is not None:
            raise ValueError("G is not a point of order n")

    def on_curve(self, P: Point) -> bool:
        if P is None:
            return True
        x, y = P
        if not (0 <= x < self.p and 0 <= y < self.p):
            return False
        return (y * y - (x * x * x + self.a * x + self.b)) % self.p == 0

    def neg(self, P: Point) -> Point:
        if P is None:
            return None
        return (P[0], (-P[1]) % self.p)

    def add(self, P: Point, Q: Point) -> Point:
        if P is None:
            return Q
        if Q is None:
            return P
        p = self.p
        x1, y1 = P
        x2, y2 = Q
        if x1 == x2:
            if (y1 + y2) % p == 0:
                return None
            lam = (3 * x1 * x1 + self.a) * pow(2 * y1, -1, p) % p
        else:
            lam = (y2 - y1) * pow(x2 - x1, -1, p) % p
        x3 = (lam * lam - x1 - x2) % p
        return (x3, (lam * (x1 - x3) - y1) % p)

    def mul(self, k: int, P: Point) -> Point:
        k %= self.n
        R: Point = None
        A = P
        while k:
            if k & 1:
                R = self.add(R, A)
            A = self.add(A, A)
            k >>= 1
        return R

    def mul2(self, a: int, b: int, Q: Point) -> Point:
        """a*G + b*Q (Shamir's trick)."""
        a %= self.n
        b %= self.n
        GQ = self.add(self.G, Q)
        R: Point = None
        for i in range(max(a.bit_length(), b.bit_length()) - 1, -1, -1):
            R = self.add(R, R)
            bits = ((a >> i) & 1, (b >> i) & 1)
            if bits == (1, 0):
                R = self.add(R, self.G)
            elif bits == (0, 1):
                R = self.add(R, Q)
            elif bits == (1, 1):
                R = self.add(R, GQ)
        return R

    def in_subgroup(self, P: Point) -> bool:
        return P is not None and self.on_curve(P) and self.mul_raw(self.n, P) is None

    def mul_raw(self, k: int, P: Point) -> Point:
        """Scalar multiplication without reducing k mod n (for order checks)."""
        R: Point = None
        A = P
        while k:
            if k & 1:
                R = self.add(R, A)
            A = self.add(A, A)
            k >>= 1
        return R

    def sqrt(self, v: int) -> int | None:
        """A square root of v mod p, or None (Tonelli-Shanks)."""
        p = self.p
        v %= p
        if v == 0:
            return 0
        if pow(v, (p - 1) // 2, p) != 1:
            return None
        if p % 4 == 3:
            return pow(v, (p + 1) // 4, p)
        q, s = p - 1, 0
        while q % 2 == 0:
            q //= 2
            s += 1
        z = 2
        while pow(z, (p - 1) // 2, p) != p - 1:
            z += 1
        m, c, t, r = s, pow(z, q, p), pow(v, q, p), pow(v, (q + 1) // 2, p)
        while t != 1:
            i, t2 = 0, t
            while t2 != 1:
                t2 = t2 * t2 % p
                i += 1
            bpow = pow(c, 1 << (m - i - 1), p)
            m, c = i, bpow * bpow % p
            t, r = t * c % p, r * bpow % p
        return r

    def lift_x(self, x: int) -> Point:
        y = self.sqrt(x * x * x + self.a * x + self.b)
        if y is None:
            return None
        return (x, min(y, self.p - y))

    def to_dict(self) -> dict:
        return {k: hex(getattr(self, k)) for k in ("p", "a", "b", "gx", "gy", "n")}

    @classmethod
    def from_dict(cls, d: dict) -> "Curve":
        return cls(**{k: int(d[k], 0) if isinstance(d[k], str) else int(d[k])
                      for k in ("p", "a", "b", "gx", "gy", "n")})


def is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for q in small:
        if n % q == 0:
            return n == q
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in small:
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True
