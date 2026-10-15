// Independent host-side reference implementation.
//
// This deliberately shares no code with the generated bitsliced arithmetic: an
// element is the symmetric vector over Z/n it stands for, and multiplication is
// the plain cyclic convolution that defines the type-II normal basis, rather
// than the Bernstein-Lange polynomial-basis route the fast path takes.  It is
// used to differential-test the fast path, to recompute walks when the server
// resolves a collision, and to verify a recovered discrete logarithm.
#pragma once

#include <stdint.h>
#include <string.h>
#include <string>
#include "bigmod.h"
#include "tablewalk.h"

#ifndef ECC_PACKED_XONLY_23
#define ECC_PACKED_XONLY_23 0
#endif
#if ECC_PACKED_XONLY_23 != 0 && ECC_PACKED_XONLY_23 != 1
#error "ECC_PACKED_XONLY_23 must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_BRIDGE3
#define ECC_PACKED_XONLY_BRIDGE3 0
#endif
#if ECC_PACKED_XONLY_BRIDGE3 != 0 && ECC_PACKED_XONLY_BRIDGE3 != 1
#error "ECC_PACKED_XONLY_BRIDGE3 must be 0 or 1"
#endif
#if ECC_PACKED_XONLY_BRIDGE3 && !ECC_PACKED_XONLY_23
#error "ECC_PACKED_XONLY_BRIDGE3 requires ECC_PACKED_XONLY_23"
#endif
#ifndef ECC_PACKED_XONLY_DOUBLE_ONLY
#define ECC_PACKED_XONLY_DOUBLE_ONLY 0
#endif
#if ECC_PACKED_XONLY_DOUBLE_ONLY != 0 && ECC_PACKED_XONLY_DOUBLE_ONLY != 1
#error "ECC_PACKED_XONLY_DOUBLE_ONLY must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_BRIDGE1_COMMON
#define ECC_PACKED_XONLY_BRIDGE1_COMMON 0
#endif
#if ECC_PACKED_XONLY_BRIDGE1_COMMON != 0 && ECC_PACKED_XONLY_BRIDGE1_COMMON != 1
#error "ECC_PACKED_XONLY_BRIDGE1_COMMON must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_ARITHMETIC_ONLY
#define ECC_PACKED_XONLY_ARITHMETIC_ONLY 0
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY != 0 && ECC_PACKED_XONLY_ARITHMETIC_ONLY != 1
#error "ECC_PACKED_XONLY_ARITHMETIC_ONLY must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT
#define ECC_PACKED_XONLY_POLY_SELECT 0
#endif
#if ECC_PACKED_XONLY_POLY_SELECT != 0 && ECC_PACKED_XONLY_POLY_SELECT != 1
#error "ECC_PACKED_XONLY_POLY_SELECT must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT_MASK
#define ECC_PACKED_XONLY_POLY_SELECT_MASK 0xfffu
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT_TARGET
#define ECC_PACKED_XONLY_POLY_SELECT_TARGET 14u
#endif
#if ECC_PACKED_XONLY_BRIDGE1_COMMON && !ECC_PACKED_XONLY_DOUBLE_ONLY
#error "ECC_PACKED_XONLY_BRIDGE1_COMMON requires the doubling-specialized core"
#endif
#if ECC_PACKED_XONLY_BRIDGE1_COMMON && !ECC_PACKED_XONLY_BRIDGE3 && \
    !ECC_PACKED_XONLY_ARITHMETIC_ONLY
#error "ECC_PACKED_XONLY_BRIDGE1_COMMON requires sparse bridge-3 or arithmetic-only"
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY && \
    (!ECC_PACKED_XONLY_BRIDGE1_COMMON || ECC_PACKED_XONLY_BRIDGE3 || \
     ECC_PACKED_XONLY_POLY_SELECT)
#error "Arithmetic-only is the always-P+sigma(P) diagnostic"
#endif
#if ECC_PACKED_XONLY_POLY_SELECT && \
    (!ECC_PACKED_XONLY_BRIDGE3 || !ECC_PACKED_XONLY_BRIDGE1_COMMON)
#error "Polynomial-bit selection requires the complete two-bridge map"
#endif
#ifndef ECC_PACKED_XONLY_BRIDGE_MOD72
#define ECC_PACKED_XONLY_BRIDGE_MOD72 0
#endif
#if ECC_PACKED_XONLY_BRIDGE_MOD72 != 0 && ECC_PACKED_XONLY_BRIDGE_MOD72 != 1
#error "ECC_PACKED_XONLY_BRIDGE_MOD72 must be 0 or 1"
#endif
#if ECC_PACKED_XONLY_BRIDGE_MOD72 && !ECC_PACKED_XONLY_BRIDGE1_COMMON
#error "ECC_PACKED_XONLY_BRIDGE_MOD72 requires the complete two-bridge map"
#endif
#if ECC_PACKED_XONLY_POLY_SELECT && ECC_PACKED_XONLY_BRIDGE_MOD72
#error "Polynomial-bit selection replaces the modulus-72 Hamming selector"
#endif
#ifndef ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE
#define ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE 0
#endif
#if ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE != 0 && ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE != 1
#error "ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE must be 0 or 1"
#endif
#if ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE && !ECC_PACKED_XONLY_BRIDGE3
#error "ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE requires the sparse bridge queue"
#endif
#if ECC_PACKED_XONLY_BRIDGE_MOD72
#define ECC_PACKED_XONLY_IS_BRIDGE3(hw) (((hw) % 72) == 14)
#else
#define ECC_PACKED_XONLY_IS_BRIDGE3(hw) (((hw) & 31) == 14)
#endif

typedef unsigned long long u64;

// Scalar field in the permuted type-II optimal normal basis: an element is the
// symmetric vector over Z/n it stands for, and multiplication is the cyclic
// convolution that defines the basis.
template <class Cfg>
struct ScalarOnb {
    static const int M = Cfg::M;
    static const int NRING = Cfg::NRING;
    static const int NL = 3;                    // limbs for m-bit coordinates
    static const int SL = (Cfg::NRING + 63) / 64;   // limbs for the n-bit lift

    struct Elem {
        u64 v[NL];
        bool operator==(const Elem &o) const {
            for (int i = 0; i < NL; ++i)
                if (v[i] != o.v[i]) return false;
            return true;
        }
        bool operator!=(const Elem &o) const { return !(*this == o); }
    };

    static Elem zero() { Elem e; memset(e.v, 0, sizeof e.v); return e; }
    static bool isZero(const Elem &a) {
        for (int i = 0; i < NL; ++i)
            if (a.v[i]) return false;
        return true;
    }
    static Elem one() {
        Elem e = zero();
        for (int i = 0; i < M; ++i) e.v[i >> 6] |= 1ull << (i & 63);
        return e;
    }
    static Elem add(const Elem &a, const Elem &b) {
        Elem r;
        for (int i = 0; i < NL; ++i) r.v[i] = a.v[i] ^ b.v[i];
        return r;
    }
    static int bit(const Elem &a, int i) { return (int)((a.v[i >> 6] >> (i & 63)) & 1); }
    static void setBit(Elem &a, int i) { a.v[i >> 6] |= 1ull << (i & 63); }
    static int weight(const Elem &a) {
        int w = 0;
        for (int i = 0; i < NL; ++i) w += __builtin_popcountll(a.v[i]);
        return w;
    }
    static Elem fromLimbs(const unsigned long long *p) {
        Elem e;
        for (int i = 0; i < NL; ++i) e.v[i] = p[i];
        return e;
    }

    // ---- lift to the symmetric n-bit vector and back -------------------
    struct Lift { u64 v[SL]; };

    static Lift lift(const Elem &a) {
        Lift u;
        memset(u.v, 0, sizeof u.v);
        for (int i = 1; i <= M; ++i) {
            if (bit(a, i - 1)) {
                u.v[i >> 6] |= 1ull << (i & 63);
                const int j = NRING - i;
                u.v[j >> 6] |= 1ull << (j & 63);
            }
        }
        return u;
    }
    static Elem unlift(Lift u) {
        if (u.v[0] & 1) {                      // normalise: add the all-ones vector
            for (int i = 0; i < NRING; ++i) u.v[i >> 6] ^= 1ull << (i & 63);
        }
        Elem e = zero();
        for (int i = 1; i <= M; ++i)
            if ((u.v[i >> 6] >> (i & 63)) & 1) setBit(e, i - 1);
        return e;
    }
    static Lift rot(const Lift &u, int k) {
        Lift r;
        memset(r.v, 0, sizeof r.v);
        k %= NRING;
        for (int i = 0; i < NRING; ++i) {
            if ((u.v[i >> 6] >> (i & 63)) & 1) {
                const int j = (i + k) % NRING;
                r.v[j >> 6] |= 1ull << (j & 63);
            }
        }
        return r;
    }

    static Elem mul(const Elem &a, const Elem &b) {
        const Lift ua = lift(a);
        const Lift ub = lift(b);
        Lift acc;
        memset(acc.v, 0, sizeof acc.v);
        for (int i = 0; i < NRING; ++i) {
            if (!((ub.v[i >> 6] >> (i & 63)) & 1)) continue;
            const Lift t = rot(ua, i);
            for (int j = 0; j < SL; ++j) acc.v[j] ^= t.v[j];
        }
        return unlift(acc);
    }

    // squaring is the coordinate permutation i -> fold(2i)
    static Elem sigma(const Elem &a, int k) {
        int e = 1;
        for (int j = 0; j < k % M; ++j) e = (2 * e) % NRING;
        Elem r = zero();
        for (int i = 1; i <= M; ++i) {
            if (!bit(a, i - 1)) continue;
            int t = (int)((long long)i * e % NRING);
            if (t > M) t = NRING - t;
            setBit(r, t - 1);
        }
        return r;
    }
    static Elem sqr(const Elem &a) { return sigma(a, 1); }

    // Itoh-Tsujii; squarings are free so this is 8 multiplications for m=131
    static Elem inv(const Elem &a) {
        Elem acc = a;
        int k = 1;
        const int e = M - 1;
        int hb = 0;
        while ((1 << (hb + 1)) <= e) ++hb;
        for (int b = hb - 1; b >= 0; --b) {
            acc = mul(sigma(acc, k), acc);
            k <<= 1;
            if ((e >> b) & 1) {
                acc = mul(sqr(acc), a);
                k += 1;
            }
        }
        return sqr(acc);
    }
    static Elem pow(const Elem &a, u64 e) {
        Elem r = one(), b = a;
        while (e) {
            if (e & 1) r = mul(r, b);
            b = mul(b, b);
            e >>= 1;
        }
        return r;
    }
    static int trace(const Elem &a) { return weight(a) & 1; }
    // normal-basis coordinates, which this representation already is
    static Elem nbCoords(const Elem &a) { return a; }

};

// Scalar field in a polynomial basis F_2[z]/(F).  The weight is taken in a
// normal basis through the generated row masks, so it stays invariant under
// squaring and the walk still runs on orbits.
template <class Cfg>
struct ScalarPb {
    static const int M = Cfg::M;
    static const int NL = 3;

    struct Elem {
        u64 v[NL];
        bool operator==(const Elem &o) const {
            for (int i = 0; i < NL; ++i)
                if (v[i] != o.v[i]) return false;
            return true;
        }
        bool operator!=(const Elem &o) const { return !(*this == o); }
    };

    static Elem zero() { Elem e; memset(e.v, 0, sizeof e.v); return e; }
    static bool isZero(const Elem &a) {
        for (int i = 0; i < NL; ++i)
            if (a.v[i]) return false;
        return true;
    }
    static Elem one() { Elem e = zero(); e.v[0] = 1; return e; }
    static Elem add(const Elem &a, const Elem &b) {
        Elem r;
        for (int i = 0; i < NL; ++i) r.v[i] = a.v[i] ^ b.v[i];
        return r;
    }
    static int bit(const Elem &a, int i) { return (int)((a.v[i >> 6] >> (i & 63)) & 1); }
    static void setBit(Elem &a, int i) { a.v[i >> 6] |= 1ull << (i & 63); }
    static Elem fromLimbs(const unsigned long long *p) {
        Elem e;
        for (int i = 0; i < NL; ++i) e.v[i] = p[i];
        return e;
    }

    static void shiftUp(Elem &a) {
        a.v[2] = (a.v[2] << 1) | (a.v[1] >> 63);
        a.v[1] = (a.v[1] << 1) | (a.v[0] >> 63);
        a.v[0] <<= 1;
    }
    static void reduceTop(Elem &a) {
        // clear any bit at or above z^M, folding it back through the taps
        for (int j = 3 * 64 - 1; j >= M; --j) {
            if (!bit(a, j)) continue;
            a.v[j >> 6] ^= 1ull << (j & 63);
            const int lo = j - M;
            setBitXor(a, lo);
            for (int t = 0; t < 3; ++t) {
                const int tap = Cfg::PB_TAPS[t];
                if (tap > 0) setBitXor(a, lo + tap);
            }
        }
    }
    static void setBitXor(Elem &a, int i) { a.v[i >> 6] ^= 1ull << (i & 63); }

    static Elem mul(const Elem &a, const Elem &b) {
        Elem r = zero();
        Elem t = a;
        for (int i = 0; i < M; ++i) {
            if (bit(b, i)) r = add(r, t);
            shiftUp(t);
            reduceTop(t);
        }
        return r;
    }
    static Elem sqr(const Elem &a) { return mul(a, a); }
    static Elem sigma(const Elem &a, int k) {
        Elem r = a;
        k %= M;
        for (int i = 0; i < k; ++i) r = sqr(r);
        return r;
    }
    static Elem pow(const Elem &a, u64 e) {
        Elem r = one(), b = a;
        while (e) {
            if (e & 1) r = mul(r, b);
            b = mul(b, b);
            e >>= 1;
        }
        return r;
    }
    static Elem inv(const Elem &a) {
        // a^(2^m - 2)
        Elem acc = a;
        int k = 1;
        const int e = M - 1;
        int hb = 0;
        while ((1 << (hb + 1)) <= e) ++hb;
        for (int b = hb - 1; b >= 0; --b) {
            acc = mul(sigma(acc, k), acc);
            k <<= 1;
            if ((e >> b) & 1) {
                acc = mul(sqr(acc), a);
                k += 1;
            }
        }
        return sqr(acc);
    }
    // weight of the normal-basis coordinates, which squaring only rotates
    static int weight(const Elem &a) {
        int w = 0;
        for (int i = 0; i < M; ++i) {
            u64 acc = 0;
            for (int l = 0; l < NL; ++l) acc ^= a.v[l] & Cfg::NB_ROWS[i][l];
            w += (__builtin_popcountll(acc) & 1);
        }
        return w;
    }
    // the normal-basis coordinate vector itself, coordinate i at bit i-1
    static Elem nbCoords(const Elem &a) {
        Elem r = zero();
        for (int i = 0; i < M; ++i) {
            u64 acc = 0;
            for (int l = 0; l < NL; ++l) acc ^= a.v[l] & Cfg::NB_ROWS[i][l];
            if (__builtin_popcountll(acc) & 1) setBit(r, i);
        }
        return r;
    }
    static int trace(const Elem &a) {
        Elem t = a, acc = a;
        for (int i = 1; i < M; ++i) {
            t = sqr(t);
            acc = add(acc, t);
        }
        return (int)(acc.v[0] & 1) ^ 0;
    }
};


// Curve arithmetic and the walk, over whichever scalar field the curve config
// selects.  This shares no code with the generated bitsliced routines, so it
// serves as the independent oracle for them, recomputes walks when the server
// resolves a collision, and verifies a recovered discrete logarithm.
template <class Cfg, class SF>
struct RefT {
    static const int M = Cfg::M;
    static const int NL = 3;
    typedef typename SF::Elem Elem;

    static Elem zero() { return SF::zero(); }
    static Elem one() { return SF::one(); }
    static bool isZero(const Elem &a) { return SF::isZero(a); }
    static Elem add(const Elem &a, const Elem &b) { return SF::add(a, b); }
    static Elem mul(const Elem &a, const Elem &b) { return SF::mul(a, b); }
    static Elem sqr(const Elem &a) { return SF::sqr(a); }
    static Elem inv(const Elem &a) { return SF::inv(a); }
    static Elem sigma(const Elem &a, int k) { return SF::sigma(a, k); }
    static Elem pow(const Elem &a, u64 e) { return SF::pow(a, e); }
    static int bit(const Elem &a, int i) { return SF::bit(a, i); }
    static void setBit(Elem &a, int i) { SF::setBit(a, i); }
    static int weight(const Elem &a) { return SF::weight(a); }
    static int trace(const Elem &a) { return SF::trace(a); }
    static Elem nbCoords(const Elem &a) { return SF::nbCoords(a); }
    static Elem fromLimbs(const unsigned long long *p) { return SF::fromLimbs(p); }
    // ---- polynomial-basis interoperability (normal-basis curves only) --
    static Elem fromPolyBasis(const unsigned long long *pb, const unsigned long long ztab[][3]) {
        Elem r = zero();
        for (int i = 0; i < M; ++i) {
            if ((pb[i >> 6] >> (i & 63)) & 1) {
                for (int l = 0; l < NL; ++l) r.v[l] ^= ztab[i][l];
            }
        }
        return r;
    }
    static void toPolyBasis(const Elem &a, const unsigned long long gtab[][3], unsigned long long *out) {
        out[0] = out[1] = out[2] = 0;
        for (int i = 0; i < M; ++i) {
            if (bit(a, i)) {
                for (int l = 0; l < NL; ++l) out[l] ^= gtab[i][l];
            }
        }
    }

    // ---- curve ---------------------------------------------------------
    struct Point { Elem x, y; bool inf; };
    static Point infinity() { Point p; p.x = zero(); p.y = zero(); p.inf = true; return p; }
    static Point make(const Elem &x, const Elem &y) { Point p; p.x = x; p.y = y; p.inf = false; return p; }
    static bool eq(const Point &a, const Point &b) {
        if (a.inf || b.inf) return a.inf == b.inf;
        return a.x == b.x && a.y == b.y;
    }
    static bool onCurve(const Point &p) {
        if (p.inf) return true;
        return add(mul(p.y, p.y), mul(p.x, p.y)) == add(mul(mul(p.x, p.x), p.x), one());
    }
    static Point neg(const Point &p) { return p.inf ? p : make(p.x, add(p.x, p.y)); }
    static Point dbl(const Point &p) {
        if (p.inf || isZero(p.x)) return infinity();
        const Elem lam = add(p.x, mul(p.y, inv(p.x)));
        const Elem x3 = add(mul(lam, lam), lam);
        const Elem y3 = add(mul(p.x, p.x), mul(add(lam, one()), x3));
        return make(x3, y3);
    }
    static Point addPt(const Point &a, const Point &b) {
        if (a.inf) return b;
        if (b.inf) return a;
        if (a.x == b.x) return (a.y == b.y) ? dbl(a) : infinity();
        const Elem d = add(a.x, b.x);
        const Elem lam = mul(add(a.y, b.y), inv(d));
        const Elem x3 = add(add(mul(lam, lam), lam), d);
        const Elem y3 = add(add(mul(lam, add(a.x, x3)), x3), a.y);
        return make(x3, y3);
    }
    // Affine addition with no special cases, matching the device formula
    // exactly.  The start-point construction uses this so that a re-walk
    // reproduces the client bit for bit even in the degenerate case where the
    // two summands share an abscissa: the client cannot represent the point at
    // infinity and does not branch, so neither does this.  On the real curves
    // that case has probability about 2^-m and never arises; on a toy field it
    // does, and the two must still agree.
    static Point addPtRaw(const Point &a, const Point &b) {
        const Elem d = add(a.x, b.x);
        const Elem lam = mul(add(a.y, b.y), inv(d));
        const Elem x3 = add(add(mul(lam, lam), lam), d);
        const Elem y3 = add(add(mul(lam, add(a.x, x3)), x3), a.y);
        return make(x3, y3);
    }

    static Point frob(const Point &p, int k) {
        if (p.inf) return p;
        return make(sigma(p.x, k), sigma(p.y, k));
    }
    static Point scalarMul(const Point &p, const U192 &k) {
        Point r = infinity();
        for (int i = u192_bits(k) - 1; i >= 0; --i) {
            r = dbl(r);
            if (u192_bit(k, i)) r = addPt(r, p);
        }
        return r;
    }
    static Elem halfTrace(const Elem &a) {
        Elem acc = a, t = a;
        for (int i = 1; i <= (M - 1) / 2; ++i) {
            t = sigma(t, 2);
            acc = add(acc, t);
        }
        return acc;
    }

    // ---- the iteration function ----------------------------------------
    static int jOf(int hw) { return 3 + ((hw >> 1) & 7); }
    // Packed polynomial low word of a GF(2^131) normal-basis coordinate.
    // Matches packedtransform131.h with FAST_CONVERT=1, which is algebraically
    // the same map as the slower factorization.
    static unsigned polynomialLowWord131(const Elem &x) {
        const uint32_t a0 = (uint32_t)x.v[0];
        const uint32_t a1 = (uint32_t)(x.v[0] >> 32);
        const uint32_t a2 = (uint32_t)x.v[1];
        const uint32_t a3 = (uint32_t)(x.v[1] >> 32);
        const uint32_t a4 = (uint32_t)x.v[2] & 7u;
        const uint32_t sign = 0u - ((a4 >> 2) & 1u);
        uint32_t v0 = (a0 << 1) ^ sign;
        uint32_t v1 = ((a1 << 1) | (a0 >> 31)) ^ sign;
        uint32_t v2 = ((a2 << 1) | (a1 >> 31)) ^ sign;
        uint32_t v3 = ((a3 << 1) | (a2 >> 31)) ^ sign;
        uint32_t v4 = (((a4 << 1) | (a3 >> 31)) ^ sign) & 7u;
        v0 ^= ((v0 >> 2) | (v1 << 30)) & 0xaaaaaaaau;
        v1 ^= ((v1 >> 2) | (v2 << 30)) & 0xaaaaaaaau;
        v2 ^= ((v2 >> 2) | (v3 << 30)) & 0xaaaaaaaau;
        v3 ^= ((v3 >> 2) | (v4 << 30)) & 0xaaaaaaaau;
        v0 ^= ((v0 >> 4) | (v1 << 28)) & 0x66666666u;
        v1 ^= ((v1 >> 4) | (v2 << 28)) & 0x66666666u;
        v2 ^= ((v2 >> 4) | (v3 << 28)) & 0x66666666u;
        v3 ^= ((v3 >> 4) | (v4 << 28)) & 0x66666666u;
        v0 ^= ((v0 >> 8) | (v1 << 24)) & 0x1e1e1e1eu;
        v1 ^= ((v1 >> 8) | (v2 << 24)) & 0x1e1e1e1eu;
        v2 ^= ((v2 >> 8) | (v3 << 24)) & 0x1e1e1e1eu;
        v3 ^= ((v3 >> 8) | (v4 << 24)) & 0x061e1e1eu;
        v0 ^= ((v0 >> 16) | (v1 << 16)) & 0x01fe01feu;
        v1 ^= ((v1 >> 16) | (v2 << 16)) & 0x01fe01feu;
        v2 ^= ((v2 >> 16) | (v3 << 16)) & 0x01fe01feu;
        v3 ^= ((v3 >> 16) | (v4 << 16)) & 0x000601feu;
        v0 ^= v1 & 0x0001fffeu;
        v1 ^= v2 & 0x0001fffeu;
        v2 ^= v3 & 0x0001fffeu;
        v3 ^= v4 & 0x00000006u;
        v0 ^= v2 & 0xfffffffeu;
        v1 ^= v3 & 0x00000001u;
        v2 ^= v4 & 0x00000006u;
        v0 ^= v4 & 0x00000006u;
        (void)v1;
        (void)v2;
        (void)v3;
        return v0;
    }
    static bool polySelectBridge3(const Elem &x) {
        const unsigned bits = (M == 131) ? polynomialLowWord131(x)
                                         : (unsigned)x.v[0];
        return (bits & ECC_PACKED_XONLY_POLY_SELECT_MASK) ==
               ECC_PACKED_XONLY_POLY_SELECT_TARGET;
    }
    static Point step(const Point &p, int hw) {
#if ECC_PACKED_XONLY_23
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY
        (void)hw;
        return addPt(p, frob(p, 1));
#else
#if ECC_PACKED_XONLY_BRIDGE3
        if (
#if ECC_PACKED_XONLY_POLY_SELECT
            polySelectBridge3(p.x)
#else
            ECC_PACKED_XONLY_IS_BRIDGE3(hw)
#endif
        ) return addPt(p, frob(p, 3));
#endif
#if ECC_PACKED_XONLY_BRIDGE1_COMMON
        return addPt(p, frob(p, 1));
#elif ECC_PACKED_XONLY_DOUBLE_ONLY
        return dbl(p);
#else
        const Point twice = dbl(p);
        return ((hw >> 1) & 1) ? addPt(twice, p) : twice;
#endif
#endif
#else
        return addPt(p, frob(p, jOf(hw)));
#endif
    }

    // canonical representative of the orbit under sigma (negation leaves x fixed)
    static Elem canonical(const Elem &x) {
        Elem best = x, cur = x;
        for (int k = 1; k < M; ++k) {
            cur = sqr(cur);
            for (int i = NL - 1; i >= 0; --i) {
                if (cur.v[i] < best.v[i]) { best = cur; break; }
                if (cur.v[i] > best.v[i]) break;
            }
        }
        return best;
    }
    static u64 hashPoint(const Elem &canonX) {
        u64 h = 0xcbf29ce484222325ull;
        for (int i = 0; i < NL; ++i) {
            h ^= canonX.v[i];
            h *= 0x100000001b3ull;
        }
        return h ^ (h >> 29);
    }

    // start point Q + sum c_i sigma^i(P), matching the device derivation
    // The tracked scalar alpha satisfies start = [alpha] P + Q only while every
    // addition is non-degenerate.  On the real curves two summands share an
    // abscissa with probability about 2^-m, so this never fires; on a toy field
    // it does, and `degenerate` reports it.
    static Point startPoint(u64 seed, const Point &basis, const Point &target, U192 *alphaOut,
                            const U192 &ell, const U192 *spow, bool *degenerate = 0) {
        const u64 c0 = eccPrfHost(seed, 0);
        const u64 c1 = eccPrfHost(seed, 1);
        Point r = target;
        U192 alpha = u192_zero();
        for (int i = 0; i < 128; ++i) {
            const u64 b = (i < 64) ? (c0 >> i) : (c1 >> (i - 64));
            if (b & 1) {
                const Point s = frob(basis, i % M);
                if (degenerate && r.x == s.x) *degenerate = true;
                r = addPtRaw(r, s);
                alpha = mod_add(alpha, spow[i % M], ell);
            }
        }
        if (alphaOut) *alphaOut = alpha;
        return r;
    }

    static u64 eccPrfHost(u64 seed, int idx) {
        u64 z = seed + 0x9E3779B97F4A7C15ull * (u64)(idx + 1);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
        return z ^ (z >> 31);
    }
};


template <class Cfg>
using Ref = RefT<Cfg, typename Cfg::Scalar>;


// The table walk of tablewalk.h on the reference arithmetic: the table
// T_h = a_h P + b_h Q with its Frobenius conjugates, one step, and the
// coefficient bookkeeping a re-walk needs (endpoint = a P + b Q).
template <class Cfg>
struct TableWalk {
    typedef Ref<Cfg> R;
    typedef typename R::Elem Elem;
    typedef typename R::Point Point;
    static const int M = Cfg::M;
    static const int H = ECC_TABLE_BRANCHES;

    TableWalkConsts<M> consts;
    Point table[H][M];        // table[h][k] = sigma^k(T_h)
    U192 ta[H], tb[H];        // T_h = ta[h] P + tb[h] Q
    bool ready = false;
    int dpWeight = -1; // caller supplies the campaign reporting threshold

    // The coordinate functions are defined on the permuted type-II normal
    // basis; the polynomial-basis test curves have no such coordinate order.
    static bool applicable() { return Cfg::NRING == 2 * M + 1; }

    // Coefficients are fixed constants of the walk, derived from nothing but
    // the branch index, so every client and the resolver agree on them.
    static U192 coefficient(int h, int which, const U192 &ell) {
        U192 r;
        for (int i = 0; i < 3; ++i) r.v[i] = R::eccPrfHost(0x7ab1e0000000ull + (u64)h * 2 + which, i);
        r.v[2] &= ~(1ull << 63);   // mod_reduce wants a < 2^191
        r = mod_reduce(r, ell);
        if (u192_is_zero(r)) r = u192_from(1);
        return r;
    }

    void setup(const Point &basis, const Point &target, const U192 &ell) {
        consts.build();
        for (int h = 0; h < H; ++h) {
            ta[h] = coefficient(h, 0, ell);
            tb[h] = coefficient(h, 1, ell);
            const Point t = R::addPt(R::scalarMul(basis, ta[h]), R::scalarMul(target, tb[h]));
            for (int k = 0; k < M; ++k) table[h][k] = R::frob(t, k);
        }
        ready = true;
    }

    static int branch(int hw) { return (hw >> 1) & (H - 1); }
    int phase(const Elem &xn, int hw) const { return consts.phase(xn.v, hw); }
    int negationBit(const Elem &xn, const Elem &yn, int k) const { return consts.negationBit(xn.v, yn.v, k); }

    // The tag the point selects before the cycle rule, from its coordinates.
    unsigned rawTag(const Point &p, int hw) const {
        const Elem xn = R::nbCoords(p.x), yn = R::nbCoords(p.y);
        const int k = phase(xn, hw);
        return eccTag(branch(hw), k, negationBit(xn, yn, k));
    }
    struct CycleOps {
        const TableWalk *walk;
        bool distinguished(const Point &p) const { return R::weight(p.x) <= walk->dpWeight; }
        unsigned tag(const Point &p) const { return walk->rawTag(p, R::weight(p.x)); }
        bool next(const Point &p, unsigned t, Point *out) const {
            const Point q = walk->addend(t);
            if (q.inf || p.x == q.x) return false;
            *out = R::addPt(p, q);
            return !out->inf;
        }
        bool oppositeCloses(const Point &start, const Point &after, unsigned t) const {
            Point closed;
            return next(after, t, &closed) && equal(closed, start);
        }
        bool equal(const Point &a, const Point &b) const { return R::eq(a, b); }
        bool less(const Point &a, const Point &b) const {
            const int wa = R::weight(a.x), wb = R::weight(b.x);
            if (wa != wb) return wa < wb;
            const Elem ca = R::canonical(a.x), cb = R::canonical(b.x);
            for (int i = R::NL - 1; i >= 0; --i) {
                if (ca.v[i] != cb.v[i]) return ca.v[i] < cb.v[i];
            }
            return false;
        }
    };
    unsigned resolveTag(const Point &p, unsigned t, u64 hist) const {
        if (!eccTagFruitless(t, hist, M)) return t;
        return eccCycleAnchorTag(p, t, CycleOps{this}, M, H);
    }
    Point addend(unsigned t) const {
        const Point q = table[eccTagH(t)][eccTagK(t)];
        return eccTagEps(t) ? R::neg(q) : q;
    }
    // One step.  Raw addition, as on the device: the degenerate abscissa
    // coincidence has probability 2^-m and is never special-cased there.
    Point step(const Point &p, int hw, u64 *hist, U192 *a, U192 *b, const U192 &ell,
               const U192 *spow) const {
        const unsigned t = resolveTag(p, rawTag(p, hw), *hist);
        *hist = eccHistPush(*hist, t);
        if (a) {
            U192 ca = mod_mul(spow[eccTagK(t)], ta[eccTagH(t)], ell);
            U192 cb = mod_mul(spow[eccTagK(t)], tb[eccTagH(t)], ell);
            if (eccTagEps(t)) { ca = mod_neg(ca, ell); cb = mod_neg(cb, ell); }
            *a = mod_add(*a, ca, ell);
            *b = mod_add(*b, cb, ell);
        }
        return R::addPtRaw(p, addend(t));
    }
};
