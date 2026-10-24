// Specialised arithmetic and batched walks for the 131-bit Certicom curves.
//
// ec_core.h is the reference: one bit-serial definition of every field and
// one inversion per step. This header computes the same walks faster for
//   - ECC2-131: GF(2^131) = GF(2)[t]/(t^131 + t^13 + t^2 + t + 1) in three
//     64-bit words, carry-less multiplication (PMULL, PCLMULQDQ or a masked
//     integer-multiply product on GPUs), dedicated squaring and Itoh-Tsujii
//     inversion;
//   - prime fields below 2^132 (ECCp-131 and the smaller prime curves):
//     Montgomery arithmetic in 3x64-bit limbs on CPUs and 5x32-bit limbs on
//     GPUs, Fermat inversion;
// and shares one inversion between the walks of a batch (Montgomery's trick).
//
// Three walks are implemented on that arithmetic:
//   v2  certicom-affine-radding32-v2: the published walk, bit for bit (the
//       SHA-256 point digest selects one of 32 jumps and decides DPs);
//   v3  x-only partition and DP test from bits of x, r-adding table;
//   v4  v3 on classes {P, -P} (negation map) with fruitless-cycle escape.
// codec.py defines v3 and v4 normatively; the reference walk in
// tests/test_fast131.py replays both in Python.
//
// The header compiles as host C++17, CUDA and Metal like ec_core.h; Metal
// receives it concatenated after ec_core.h, so it includes nothing there.
#ifndef ECC_FAST131_H
#define ECC_FAST131_H

#if !defined(__METAL_VERSION__)
#include "ec_core.h"
#endif

#if !defined(__METAL_VERSION__) && !defined(__CUDACC__) && !defined(F131_SOFT_CLMUL)
#if defined(__aarch64__) && (defined(__APPLE__) || defined(__linux__))
#include <arm_neon.h>
#define F131_PMULL 1
#if defined(__linux__)
#include <sys/auxv.h>
#include <asm/hwcap.h>
#endif
#elif defined(__x86_64__) || (defined(_M_X64) && defined(_MSC_VER))
#include <immintrin.h>
#define F131_PCLMUL 1
#endif
#endif
#if !defined(__METAL_VERSION__) && !defined(__CUDACC__) && defined(_MSC_VER) && \
    !defined(__clang__) && !defined(__SIZEOF_INT128__)
#include <intrin.h>
#endif

#if defined(__METAL_VERSION__)
#define F131_DEV device
#else
#define F131_DEV
#endif

namespace ecc {
namespace f131 {
typedef std::uint32_t u32;
typedef std::uint64_t u64;

// Optional instructions stay inside these functions. Distributed binaries
// must also run on hosts without PCLMULQDQ or the Arm crypto extension.
#if defined(F131_PMULL) || defined(F131_PCLMUL)
inline bool hardware_clmul_available() {
#if defined(F131_PMULL) && defined(__APPLE__)
    return true;  // Every supported Apple-silicon target has PMULL.
#elif defined(F131_PMULL)
    static const bool available = (getauxval(AT_HWCAP) & HWCAP_PMULL) != 0;
#elif defined(_MSC_VER)
    static const bool available = []() {
        int info[4];
        __cpuid(info, 1);
        return (info[2] & (1 << 1)) != 0;
    }();
#else
    static const bool available = __builtin_cpu_supports("pclmul");
#endif
#if !(defined(F131_PMULL) && defined(__APPLE__))
    return available;
#endif
}
#if defined(F131_PMULL) && defined(__linux__)
__attribute__((target("arch=armv8-a+crypto")))
#elif defined(F131_PCLMUL) && (defined(__GNUC__) || defined(__clang__))
__attribute__((target("pclmul")))
#endif
inline void hardware_clmul(u64 a, u64 b, u64& lo, u64& hi) {
#if defined(F131_PMULL)
    const uint64x2_t p = vreinterpretq_u64_p128(vmull_p64((poly64_t)a, (poly64_t)b));
    lo = vgetq_lane_u64(p, 0);
    hi = vgetq_lane_u64(p, 1);
#else
    const __m128i p = _mm_clmulepi64_si128(_mm_cvtsi64_si128((long long)a),
                                         _mm_cvtsi64_si128((long long)b), 0);
    lo = (u64)_mm_cvtsi128_si64(p);
    hi = (u64)_mm_cvtsi128_si64(_mm_unpackhi_epi64(p, p));
#endif
}
#endif

template <typename W, int N> struct Num { W v[N]; };
typedef Num<u64, 3> N3;
typedef Num<u32, 5> N5;

// ---------------------------------------------------------------- limbs
HD inline u32 addc(u32 a, u32 b, u32 cin, ECC_THREAD u32& cout) {
    const u64 s = u64(a) + b + cin;
    cout = u32(s >> 32);
    return u32(s);
}
HD inline u64 addc(u64 a, u64 b, u64 cin, ECC_THREAD u64& cout) {
    const u64 s = a + b;
    const u64 r = s + cin;
    cout = u64(s < a) | u64(r < s);
    return r;
}
HD inline u32 subb(u32 a, u32 b, u32 bin, ECC_THREAD u32& bout) {
    const u64 d = u64(a) - u64(b) - u64(bin);
    bout = u32(d >> 63);
    return u32(d);
}
HD inline u64 subb(u64 a, u64 b, u64 bin, ECC_THREAD u64& bout) {
    const u64 d = a - b;
    bout = u64(a < b) | u64(d < bin);
    return d - bin;
}
HD inline void mulw(u32 a, u32 b, ECC_THREAD u32& lo, ECC_THREAD u32& hi) {
#if defined(F131_SOFT_MUL32)
    // A 32x32 product using only 32-bit multiplies. In particular this keeps
    // the Metal path independent of how a device lowers unsigned 64-bit mul.
    const u32 a0 = a & 0xffffU, a1 = a >> 16;
    const u32 b0 = b & 0xffffU, b1 = b >> 16;
    const u32 p00 = a0 * b0, p01 = a0 * b1;
    const u32 p10 = a1 * b0, p11 = a1 * b1;
    const u32 mid = (p00 >> 16) + (p01 & 0xffffU) + (p10 & 0xffffU);
    lo = (p00 & 0xffffU) | (mid << 16);
    hi = p11 + (p01 >> 16) + (p10 >> 16) + (mid >> 16);
#else
    const u64 p = u64(a) * u64(b);
    lo = u32(p);
    hi = u32(p >> 32);
#endif
}
HD inline void mulw(u64 a, u64 b, ECC_THREAD u64& lo, ECC_THREAD u64& hi) {
#if defined(__CUDA_ARCH__)
    lo = a * b;
    hi = u64(__umul64hi((unsigned long long)a, (unsigned long long)b));
#elif defined(__SIZEOF_INT128__) && !defined(__METAL_VERSION__)
    const unsigned __int128 p = (unsigned __int128)a * b;
    lo = u64(p);
    hi = u64(p >> 64);
#elif defined(_MSC_VER) && defined(_M_X64)
    lo = _umul128(a, b, &hi);
#else
    // Metal has no 64-bit mulhi builtin. The split product is also the
    // portable host fallback when a native 128-bit integer is unavailable.
    const u64 a0 = a & 0xffffffffU, a1 = a >> 32, b0 = b & 0xffffffffU, b1 = b >> 32;
    const u64 p00 = a0 * b0, p01 = a0 * b1, p10 = a1 * b0, p11 = a1 * b1;
    const u64 mid = (p00 >> 32) + (p01 & 0xffffffffU) + (p10 & 0xffffffffU);
    lo = (p00 & 0xffffffffU) | (mid << 32);
    hi = p11 + (p01 >> 32) + (p10 >> 32) + (mid >> 32);
#endif
}

template <typename W, int N> HD inline Num<W, N> num_zero() {
    Num<W, N> r;
    for (int i = 0; i < N; ++i) r.v[i] = 0;
    return r;
}
template <typename W, int N> HD inline bool num_is_zero(Num<W, N> a) {
    W r = 0;
    for (int i = 0; i < N; ++i) r |= a.v[i];
    return r == 0;
}
template <typename W, int N> HD inline bool num_eq(Num<W, N> a, Num<W, N> b) {
    W r = 0;
    for (int i = 0; i < N; ++i) r |= a.v[i] ^ b.v[i];
    return r == 0;
}
// a < b as unsigned integers, from the most significant limb down. Plain
// compares keep this free of subb's aliased borrow, which no caller needs here.
template <typename W, int N> HD inline bool num_less(Num<W, N> a, Num<W, N> b) {
    W less = 0, equal = 1;
    for (int i = N - 1; i >= 0; --i) {
        less |= equal & W(a.v[i] < b.v[i]);
        equal &= W(a.v[i] == b.v[i]);
    }
    return less != 0;
}
template <typename W, int N> HD inline Num<W, N> num_sub(Num<W, N> a, Num<W, N> b,
                                                        ECC_THREAD W& borrow) {
    Num<W, N> r;
    borrow = 0;
    for (int i = 0; i < N; ++i) r.v[i] = subb(a.v[i], b.v[i], borrow, borrow);
    return r;
}
template <typename W, int N> HD inline Num<W, N> num_add(Num<W, N> a, Num<W, N> b,
                                                        ECC_THREAD W& carry) {
    Num<W, N> r;
    carry = 0;
    for (int i = 0; i < N; ++i) r.v[i] = addc(a.v[i], b.v[i], carry, carry);
    return r;
}
template <typename W, int N> HD inline Num<W, N> num_select(W mask, Num<W, N> a,
                                                           Num<W, N> b) {
    Num<W, N> r;  // mask all-ones selects a
    for (int i = 0; i < N; ++i) r.v[i] = (a.v[i] & mask) | (b.v[i] & ~mask);
    return r;
}
// Reduced a, b < m with m below half the container: (a + b) mod m.
template <typename W, int N> HD inline Num<W, N> mod_add(Num<W, N> a, Num<W, N> b,
                                                        Num<W, N> m) {
    W carry, borrow;
    const Num<W, N> s = num_add(a, b, carry);
    const Num<W, N> t = num_sub(s, m, borrow);
    return num_select(W(0) - borrow, s, t);
}
template <typename W, int N> HD inline Num<W, N> mod_sub(Num<W, N> a, Num<W, N> b,
                                                        Num<W, N> m) {
    W borrow, carry;
    const Num<W, N> d = num_sub(a, b, borrow);
    const Num<W, N> e = num_add(d, m, carry);
    return num_select(W(0) - borrow, e, d);
}
template <typename W, int N> HD inline Num<W, N> mod_neg(Num<W, N> a, Num<W, N> m) {
    return mod_sub(num_zero<W, N>(), a, m);
}
// Canonical value of a, as three little-endian 64-bit words.
HD inline void words(N3 a, ECC_THREAD u64 out[3]) {
    out[0] = a.v[0]; out[1] = a.v[1]; out[2] = a.v[2];
}
HD inline void words(N5 a, ECC_THREAD u64 out[3]) {
    out[0] = u64(a.v[0]) | (u64(a.v[1]) << 32);
    out[1] = u64(a.v[2]) | (u64(a.v[3]) << 32);
    out[2] = u64(a.v[4]);
}

// --------------------------------------------------------- prime fields
// Montgomery multiplication (CIOS), R = 2^(w N). Needs p odd, p < 2^(wN-1).
template <typename W, int N>
HD inline Num<W, N> mont_mul(Num<W, N> a, Num<W, N> b, Num<W, N> p, W pinv) {
    W t[N + 2];
    for (int i = 0; i < N + 2; ++i) t[i] = 0;
    for (int i = 0; i < N; ++i) {
        W carry = 0, lo, hi, c1, c2;
        for (int j = 0; j < N; ++j) {
            mulw(a.v[j], b.v[i], lo, hi);
            W s = addc(t[j], lo, 0, c1);
            s = addc(s, carry, 0, c2);
            t[j] = s;
            carry = hi + c1 + c2;
        }
        t[N] = addc(t[N], carry, 0, c1);
        t[N + 1] = c1;
        const W m = t[0] * pinv;
        mulw(m, p.v[0], lo, hi);
        addc(t[0], lo, 0, c1);
        carry = hi + c1;
        for (int j = 1; j < N; ++j) {
            mulw(m, p.v[j], lo, hi);
            W s = addc(t[j], lo, 0, c1);
            s = addc(s, carry, 0, c2);
            t[j - 1] = s;
            carry = hi + c1 + c2;
        }
        t[N - 1] = addc(t[N], carry, 0, c1);
        t[N] = t[N + 1] + c1;
    }
    Num<W, N> r;
    for (int i = 0; i < N; ++i) r.v[i] = t[i];
    W borrow;
    const Num<W, N> s = num_sub(r, p, borrow);
    // t < 2p < R, so t[N] is zero and one subtraction suffices.
    return num_select(W(0) - borrow, r, s);
}

// a * R^-1 mod p (Montgomery reduction of a single operand): half a product.
template <typename W, int N>
HD inline Num<W, N> mont_redc(Num<W, N> a, Num<W, N> p, W pinv) {
    W t[N + 1];
    for (int i = 0; i < N; ++i) t[i] = a.v[i];
    t[N] = 0;
    for (int i = 0; i < N; ++i) {
        const W m = t[0] * pinv;
        W lo, hi, c1;
        mulw(m, p.v[0], lo, hi);
        addc(t[0], lo, 0, c1);
        W carry = hi + c1;
        for (int j = 1; j < N; ++j) {
            W c2;
            mulw(m, p.v[j], lo, hi);
            W s = addc(t[j], lo, 0, c1);
            s = addc(s, carry, 0, c2);
            t[j - 1] = s;
            carry = hi + c1 + c2;
        }
        t[N - 1] = addc(t[N], carry, 0, c1);
        t[N] = c1;
    }
    Num<W, N> r;
    for (int i = 0; i < N; ++i) r.v[i] = t[i];
    W borrow;
    const Num<W, N> s = num_sub(r, p, borrow);
    return num_select(W(0) - borrow, r, s);
}

template <typename W, int N> struct PrimeCtx {
    Num<W, N> p, n, one, r2, halfp, pm2;  // one = R mod p, halfp = (p-1)/2
    Num<W, N> a, b;                     // curve coefficients, Montgomery form
    W pinv;                             // -p^-1 mod 2^w
    u32 bits;                           // field bits
};

template <typename W, int N> struct PrimeField {
    typedef Num<W, N> E;  // field element, Montgomery form
    typedef Num<W, N> S;  // scalar modulo n
    typedef PrimeCtx<W, N> C;
    static HD E add(E x, E y, C c) { return mod_add(x, y, c.p); }
    static HD E sub(E x, E y, C c) { return mod_sub(x, y, c.p); }
    static HD E mul(E x, E y, C c) { return mont_mul(x, y, c.p, c.pinv); }
    static HD E sqr(E x, C c) { return mont_mul(x, x, c.p, c.pinv); }
    static HD E one(C c) { return c.one; }
    static HD E zero() { return num_zero<W, N>(); }
    static HD bool is_zero(E x) { return num_is_zero(x); }
    static HD bool eq(E x, E y) { return num_eq(x, y); }
    // x^(p-2) with 4-bit fixed windows over the exponent.
    static HD E inv(E x, C c) {
        E table[16];
        table[0] = c.one;
        table[1] = x;
        for (int i = 2; i < 16; ++i) table[i] = mul(table[i - 1], x, c);
        E r = c.one;
        const int bits_per = int(sizeof(W) * 8);
        bool started = false;
        for (int w = (N * bits_per) / 4 - 1; w >= 0; --w) {
            const int bit = w * 4;
            const u32 nibble = u32(c.pm2.v[bit / bits_per] >> (bit % bits_per)) & 15U;
            if (started) {
                r = sqr(r, c); r = sqr(r, c); r = sqr(r, c); r = sqr(r, c);
            }
            if (nibble) {
                if (started) r = mul(r, table[nibble], c);
                else r = table[nibble];
                started = true;
            }
        }
        return r;
    }
    static HD void canon(E x, C c, ECC_THREAD u64 out[3]) {
        words(mont_redc(x, c.p, c.pinv), out);
    }
    // -y in the field, for the negation map: -(x, y) = (x, -y).
    static HD E neg_y(E x, E y, C c) { (void)x; return mod_neg(y, c.p); }
    // True when y is the larger of {y, -y} as canonical integers.
    static HD bool y_is_upper(E x, E y, C c) {
        (void)x;
        return num_less(c.halfp, mont_redc(y, c.p, c.pinv));
    }
    static HD S scalar_add(S a, S b, C c) { return mod_add(a, b, c.n); }
    static HD S scalar_neg(S a, C c) { return mod_neg(a, c.n); }
    // Affine P + R for x1 != x2, with inv = 1 / (x1 - x2).
    static HD void add_points(E x1, E y1, E x2, E y2, E inv, C c,
                              ECC_THREAD E& x3, ECC_THREAD E& y3) {
        const E l = mul(sub(y1, y2, c), inv, c);
        x3 = sub(sub(sqr(l, c), x1, c), x2, c);
        y3 = sub(mul(l, sub(x1, x3, c), c), y1, c);
    }
    static HD E denominator(E x1, E x2, C c) { return sub(x1, x2, c); }
    // 2P; returns false for the identity (y = 0).
    static HD bool double_point(E x, E y, C c, ECC_THREAD E& x3, ECC_THREAD E& y3) {
        if (is_zero(y)) return false;
        const E x2 = sqr(x, c);
        const E num = add(add(add(x2, x2, c), x2, c), c.a, c);
        const E l = mul(num, inv(add(y, y, c), c), c);
        x3 = sub(sqr(l, c), add(x, x, c), c);
        y3 = sub(mul(l, sub(x, x3, c), c), y, c);
        return true;
    }
    // P == -R given equal x.
    static HD bool opposite(E y1, E y2, C c) { return !eq(y1, y2) || is_zero(y1); }
};

// --------------------------------------------------------- binary field
// Carry-less 32 x 32 -> 64 from integer multiplies of bit classes mod 4:
// within one product a class position collects at most 8 partial products,
// so its count never carries into the next position of the same class.
HD inline u64 clmul32_soft(u32 x, u32 y) {
    const u64 x0 = x & 0x11111111U, x1 = x & 0x22222222U;
    const u64 x2 = x & 0x44444444U, x3 = x & 0x88888888U;
    const u64 y0 = y & 0x11111111U, y1 = y & 0x22222222U;
    const u64 y2 = y & 0x44444444U, y3 = y & 0x88888888U;
    const u64 z0 = (x0 * y0) ^ (x1 * y3) ^ (x2 * y2) ^ (x3 * y1);
    const u64 z1 = (x0 * y1) ^ (x1 * y0) ^ (x2 * y3) ^ (x3 * y2);
    const u64 z2 = (x0 * y2) ^ (x1 * y1) ^ (x2 * y0) ^ (x3 * y3);
    const u64 z3 = (x0 * y3) ^ (x1 * y2) ^ (x2 * y1) ^ (x3 * y0);
    return (z0 & 0x1111111111111111ULL) | (z1 & 0x2222222222222222ULL) |
           (z2 & 0x4444444444444444ULL) | (z3 & 0x8888888888888888ULL);
}
HD inline void clmul64_soft(u64 a, u64 b, ECC_THREAD u64& lo, ECC_THREAD u64& hi) {
    const u32 a0 = u32(a), a1 = u32(a >> 32), b0 = u32(b), b1 = u32(b >> 32);
    const u64 l = clmul32_soft(a0, b0), h = clmul32_soft(a1, b1);
    const u64 m = clmul32_soft(a0 ^ a1, b0 ^ b1) ^ l ^ h;
    lo = l ^ (m << 32);
    hi = h ^ (m >> 32);
}
HD inline u64 spread32(u32 x) {
    u64 v = x;
    v = (v | (v << 16)) & 0x0000ffff0000ffffULL;
    v = (v | (v << 8)) & 0x00ff00ff00ff00ffULL;
    v = (v | (v << 4)) & 0x0f0f0f0f0f0f0f0fULL;
    v = (v | (v << 2)) & 0x3333333333333333ULL;
    v = (v | (v << 1)) & 0x5555555555555555ULL;
    return v;
}
template <bool SOFT>
HD inline void clmul64(u64 a, u64 b, ECC_THREAD u64& lo, ECC_THREAD u64& hi) {
#if defined(F131_PMULL) || defined(F131_PCLMUL)
    if (!SOFT && hardware_clmul_available()) {
        hardware_clmul(a, b, lo, hi);
        return;
    }
#endif
    clmul64_soft(a, b, lo, hi);
}
template <bool SOFT> HD inline void square64(u64 a, ECC_THREAD u64& lo, ECC_THREAD u64& hi) {
#if defined(F131_PMULL) || defined(F131_PCLMUL)
    if (!SOFT) { clmul64<false>(a, a, lo, hi); return; }
#endif
    lo = spread32(u32(a));
    hi = spread32(u32(a >> 32));
}

// Reduce c0..c4 (degree <= 260) modulo t^131 + t^13 + t^2 + t + 1.
HD inline N3 f2_reduce(u64 c0, u64 c1, u64 c2, u64 c3, u64 c4) {
    const u64 h0 = (c2 >> 3) | (c3 << 61);
    const u64 h1 = (c3 >> 3) | (c4 << 61);
    const u64 h2 = c4 >> 3;
    u64 t0 = h0 ^ (h0 << 1) ^ (h0 << 2) ^ (h0 << 13);
    const u64 t1 = h1 ^ (h1 << 1) ^ (h1 << 2) ^ (h1 << 13) ^
                   (h0 >> 63) ^ (h0 >> 62) ^ (h0 >> 51);
    u64 t2 = h2 ^ (h2 << 1) ^ (h2 << 2) ^ (h2 << 13) ^
             (h1 >> 63) ^ (h1 >> 62) ^ (h1 >> 51);
    const u64 g = t2 >> 3;
    t2 &= 7;
    t0 ^= g ^ (g << 1) ^ (g << 2) ^ (g << 13);
    N3 r;
    r.v[0] = c0 ^ t0;
    r.v[1] = c1 ^ t1;
    r.v[2] = (c2 & 7) ^ t2;
    return r;
}
template <bool SOFT> HD inline N3 f2_mul(N3 a, N3 b) {
    u64 l0, h0, l1, h1, lm, hm;
    clmul64<SOFT>(a.v[0], b.v[0], l0, h0);
    clmul64<SOFT>(a.v[1], b.v[1], l1, h1);
    clmul64<SOFT>(a.v[0] ^ a.v[1], b.v[0] ^ b.v[1], lm, hm);
    lm ^= l0 ^ l1;
    hm ^= h0 ^ h1;
    u64 c2 = l1 ^ hm, c3 = h1, c4 = 0;
    const u64 a2 = a.v[2], b2 = b.v[2];
    // The 3-bit top words: a2 * (b0 + b1 X) + b2 * (a0 + a1 X) + a2 b2 X^2.
    const u64 m0a = 0 - (a2 & 1), m0b = 0 - (b2 & 1);
    const u64 m1a = 0 - ((a2 >> 1) & 1), m1b = 0 - ((b2 >> 1) & 1);
    const u64 m2a = 0 - ((a2 >> 2) & 1), m2b = 0 - ((b2 >> 2) & 1);
    const u64 s0 = (m0a & b.v[0]) ^ (m0b & a.v[0]), s1 = (m0a & b.v[1]) ^ (m0b & a.v[1]);
    const u64 t0 = (m1a & b.v[0]) ^ (m1b & a.v[0]), t1 = (m1a & b.v[1]) ^ (m1b & a.v[1]);
    const u64 u0 = (m2a & b.v[0]) ^ (m2b & a.v[0]), u1 = (m2a & b.v[1]) ^ (m2b & a.v[1]);
    c2 ^= s0 ^ (t0 << 1) ^ (u0 << 2);
    c3 ^= s1 ^ (t1 << 1) ^ (t0 >> 63) ^ (u1 << 2) ^ (u0 >> 62);
    c4 ^= (t1 >> 63) ^ (u1 >> 62);
    c4 ^= (m0b & a2) ^ (m1b & (a2 << 1)) ^ (m2b & (a2 << 2));
    return f2_reduce(l0, h0 ^ lm, c2, c3, c4);
}
template <bool SOFT> HD inline N3 f2_sqr(N3 a) {
    u64 c0, c1, c2, c3;
    square64<SOFT>(a.v[0], c0, c1);
    square64<SOFT>(a.v[1], c2, c3);
    return f2_reduce(c0, c1, c2, c3, spread32(u32(a.v[2])));
}
template <bool SOFT> HD inline N3 f2_sqr_n(N3 a, int n) {
    for (int i = 0; i < n; ++i) a = f2_sqr<SOFT>(a);
    return a;
}
// Itoh-Tsujii: b_k = a^(2^k - 1), b_(i+j) = b_i^(2^j) b_j along
// 1, 2, 4, ..., 128, 130; a^-1 = a^(2^131 - 2) = b_130^2. Zero maps to zero.
template <bool SOFT> HD inline N3 f2_inv(N3 a) {
    const N3 b2 = f2_mul<SOFT>(f2_sqr<SOFT>(a), a);
    const N3 b4 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b2, 2), b2);
    const N3 b8 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b4, 4), b4);
    const N3 b16 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b8, 8), b8);
    const N3 b32 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b16, 16), b16);
    const N3 b64 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b32, 32), b32);
    const N3 b128 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b64, 64), b64);
    const N3 b130 = f2_mul<SOFT>(f2_sqr_n<SOFT>(b128, 2), b2);
    return f2_sqr<SOFT>(b130);
}

struct BinaryCtx {
    N3 a, b, n;
};
template <bool SOFT> struct BinaryField {
    typedef N3 E;
    typedef N3 S;
    typedef BinaryCtx C;
    static HD E xor3(E x, E y) {
        E r;
        r.v[0] = x.v[0] ^ y.v[0]; r.v[1] = x.v[1] ^ y.v[1]; r.v[2] = x.v[2] ^ y.v[2];
        return r;
    }
    static HD E add(E x, E y, C) { return xor3(x, y); }
    static HD E sub(E x, E y, C) { return xor3(x, y); }
    static HD E mul(E x, E y, C) { return f2_mul<SOFT>(x, y); }
    static HD E sqr(E x, C) { return f2_sqr<SOFT>(x); }
    static HD E inv(E x, C) { return f2_inv<SOFT>(x); }
    static HD E one(C) { E r = num_zero<u64, 3>(); r.v[0] = 1; return r; }
    static HD E zero() { return num_zero<u64, 3>(); }
    static HD bool is_zero(E x) { return num_is_zero(x); }
    static HD bool eq(E x, E y) { return num_eq(x, y); }
    static HD void canon(E x, C, ECC_THREAD u64 out[3]) { words(x, out); }
    // -(x, y) = (x, x + y).
    static HD E neg_y(E x, E y, C) { return xor3(x, y); }
    static HD bool y_is_upper(E x, E y, C) { return num_less(xor3(x, y), y); }
    static HD S scalar_add(S a, S b, C c) { return mod_add(a, b, c.n); }
    static HD S scalar_neg(S a, C c) { return mod_neg(a, c.n); }
    static HD void add_points(E x1, E y1, E x2, E y2, E inv, C c,
                              ECC_THREAD E& x3, ECC_THREAD E& y3) {
        const E l = mul(xor3(y1, y2), inv, c);
        x3 = xor3(xor3(xor3(sqr(l, c), l), xor3(x1, x2)), c.a);
        y3 = xor3(xor3(mul(l, xor3(x1, x3), c), x3), y1);
    }
    static HD E denominator(E x1, E x2, C) { return xor3(x1, x2); }
    static HD bool double_point(E x, E y, C c, ECC_THREAD E& x3, ECC_THREAD E& y3) {
        if (is_zero(x)) return false;
        const E l = xor3(x, mul(y, inv(x, c), c));
        x3 = xor3(xor3(sqr(l, c), l), c.a);
        y3 = xor3(sqr(x, c), mul(xor3(l, one(c)), x3, c));
        return true;
    }
    static HD bool opposite(E y1, E y2, C) { return !num_eq(y1, y2); }
};

// ---------------------------------------------------------------- walks
static ECC_CONST constexpr u32 WALK_V2 = 2, WALK_V3 = 3, WALK_V4 = 4;
static ECC_CONST constexpr u32 MODE_NORMAL = 0, MODE_FINDMIN = 1, MODE_SEEK = 2;
static ECC_CONST constexpr u64 PARTITION_MULTIPLIER = 0x9e3779b97f4a7c15ULL;

struct Params {
    u32 walk;          // WALK_V2, WALK_V3 or WALK_V4
    u32 dp_bits;
    u32 table_log2;    // v3/v4: log2 of the r-adding table size
    u32 escape;        // v4: index of the escape jump (= table size)
    u32 anchor_log2;   // v4: cycle anchors every 2^anchor_log2 steps
    u32 field_bytes;   // v2 digest width
    u32 has_db;        // some jump carries a b coefficient (production tables do not)
    u32 dp_shift;      // v3/v4: DP bits are x bits dp_shift .. dp_shift + dp_bits - 1
};

template <class FD> struct Entry {
    typename FD::E x, y;
    typename FD::S da, db;
};

// One walk. The cycle fields are round-local: they reset whenever the
// engine loads a lane, which only delays a detection (see codec.py).
template <class FD> struct Lane {
    typename FD::E x, y;
    typename FD::S a, b;
    u64 steps;
    u64 fcur, fprev, anchor, target;  // v4 fingerprints: low 64 bits of x
    u32 active, infinity, negated, err;
    u32 index;                        // next jump for v2 and v3
    u32 mode, count, length;
    u32 fcur_ok, fprev_ok, anchor_ok, anchor_lo;
};

// v2: SHA-256 over the big-endian canonical coordinates.
HD inline void put_be(ECC_THREAD const u64* w, u32 width, ECC_THREAD std::uint8_t* out) {
    for (u32 i = 0; i < width; ++i) {
        const u32 byte = width - 1 - i;  // counted from the least significant
        out[i] = std::uint8_t(w[byte / 8] >> ((byte % 8) * 8));
    }
}
HD inline u32 x_partition(u64 x0, u32 table_log2) {
    return u32((x0 * PARTITION_MULTIPLIER) >> (64 - table_log2));
}
// Bits dp_shift .. dp_shift + dp_bits - 1 of x (a 128-bit window, 1 <= shift,
// shift + bits <= 128) are all zero.
HD inline bool x_distinguished(u64 w0, u64 w1, u32 shift, u32 dp_bits) {
    const u64 window = shift >= 64 ? (w1 >> (shift - 64))
                                   : ((w0 >> shift) | (w1 << (64 - shift)));
    return (window & ((u64(1) << dp_bits) - 1)) == 0;
}

// Sets the next jump of a finite lane and stops it on a DP. For v2 this is
// exactly walk_lane's digest test; v3 and v4 read bits of x.
template <class FD>
HD inline void classify(ECC_THREAD Lane<FD>& L, typename FD::C c, Params prm) {
    u64 xw[3];
    FD::canon(L.x, c, xw);
    if (prm.walk == WALK_V2) {
        u64 yw[3];
        FD::canon(L.y, c, yw);
        std::uint8_t encoded[34];
        std::uint8_t digest[32];
        put_be(xw, prm.field_bytes, encoded);
        put_be(yw, prm.field_bytes, encoded + prm.field_bytes);
        if (!sha256_short(encoded, 2 * prm.field_bytes, digest)) { L.err = 3; return; }
        L.index = digest[0] & 31U;
        if (digest_distinguished(digest, prm.dp_bits)) L.active = 0;
        return;
    }
    L.index = x_partition(xw[0], prm.table_log2);
    L.fprev = L.fcur;
    L.fprev_ok = L.fcur_ok;
    L.fcur = xw[0];
    L.fcur_ok = 1;
    if (x_distinguished(xw[0], xw[1], prm.dp_shift, prm.dp_bits)) L.active = 0;
}

// v4 bookkeeping after a finite step to the point whose fingerprint is now
// fcur (fprev: the point it came from).
//   NORMAL : fcur equal to the fingerprint two points back is a 2-cycle;
//            fcur equal to the anchor is a cycle of the anchor's age.
//   FINDMIN: walk once around the cycle keeping its least fingerprint.
//   SEEK   : walk to that point, then take the escape jump instead of the
//            table jump (choose_jump). The escape depends only on the cycle,
//            so walks that share a cycle leave it to the same point.
template <class FD>
HD inline void track_cycles(ECC_THREAD Lane<FD>& L, Params prm, u64 two_back,
                            u32 two_back_ok) {
    const u64 fp = L.fcur;
    if (L.mode == MODE_NORMAL) {
        if (two_back_ok && fp == two_back) {
            L.mode = MODE_SEEK;
            L.target = L.fprev < fp ? L.fprev : fp;
            L.count = 2;
        } else if (L.anchor_ok && fp == L.anchor) {
            L.mode = MODE_FINDMIN;
            L.length = u32(L.steps) - L.anchor_lo;
            L.count = L.length;
            L.target = fp;
        }
    } else if (L.mode == MODE_FINDMIN) {
        if (fp < L.target) L.target = fp;
        if (--L.count == 0) {
            L.mode = MODE_SEEK;
            L.count = L.length;
        }
    }
    if ((L.steps & ((u64(1) << prm.anchor_log2) - 1)) == 0) {
        L.anchor = fp;
        L.anchor_lo = u32(L.steps);
        L.anchor_ok = 1;
    }
}

template <class FD> HD inline u32 choose_jump(ECC_THREAD Lane<FD>& L, Params prm) {
    if (prm.walk == WALK_V4 && L.mode == MODE_SEEK) {
        if (L.fcur == L.target) {
            L.mode = MODE_NORMAL;
            return prm.escape;
        }
        if (--L.count == 0) L.mode = MODE_NORMAL;
    }
    return L.index;
}

template <class FD> HD inline void canonicalize(ECC_THREAD Lane<FD>& L, typename FD::C c,
                                                Params prm) {
    if (FD::y_is_upper(L.x, L.y, c)) {
        L.y = FD::neg_y(L.x, L.y, c);
        L.a = FD::scalar_neg(L.a, c);
        if (prm.has_db) L.b = FD::scalar_neg(L.b, c);
        L.negated ^= 1U;
    }
}

// Prepares a lane loaded from a state: the jump its point selects and fresh
// cycle state. A v2 lane's first digest is taken here, as walk_lane does.
template <class FD> HD inline void begin_lane(ECC_THREAD Lane<FD>& L, typename FD::C c,
                                              Params prm) {
    L.err = 0; L.negated = 0; L.index = 0;
    L.mode = MODE_NORMAL; L.count = 0; L.length = 0;
    L.fcur = L.fprev = L.anchor = L.target = 0;
    L.fcur_ok = L.fprev_ok = L.anchor_ok = 0; L.anchor_lo = 0;
    if (!L.active || L.infinity) return;
    const u32 active = L.active;
    classify(L, c, prm);
    L.active = active;  // the starting point is never itself reported
}

// Advances lanes[0..count) by at most `steps` transitions, sharing one field
// inversion per step between all of them (Montgomery's trick). count <= K.
template <class FD, int K>
HD inline void walk_batch(typename FD::C c, const F131_DEV Entry<FD>* table, Params prm,
                          ECC_THREAD Lane<FD>* lanes, u32 count, u32 steps) {
    typedef typename FD::E E;
    const u32 NONE = 0xffffffffU;
    for (u32 s = 0; s < steps; ++s) {
        E prefix[K];
        u32 jump[K];
        E acc = FD::one(c);
        bool any = false;
        for (u32 k = 0; k < count; ++k) {
            ECC_THREAD Lane<FD>& L = lanes[k];
            prefix[k] = acc;
            jump[k] = NONE;
            if (!L.active || L.err) continue;
            if (L.steps == UINT64_MAX) { L.err = 2; continue; }
            any = true;
            if (L.infinity) { jump[k] = 0; continue; }
            jump[k] = choose_jump(L, prm);
            const E d = FD::denominator(L.x, table[jump[k]].x, c);
            if (!FD::is_zero(d)) acc = FD::mul(acc, d, c);
        }
        if (!any) break;
        E inv = FD::inv(acc, c);
        for (int kk = int(count) - 1; kk >= 0; --kk) {
            ECC_THREAD Lane<FD>& L = lanes[kk];
            const u32 j = jump[kk];
            if (j == NONE) continue;
            const E tx = table[j].x, ty = table[j].y;
            const bool from_infinity = L.infinity != 0;
            if (from_infinity) {
                L.x = tx; L.y = ty; L.infinity = 0;
            } else {
                const E d = FD::denominator(L.x, tx, c);
                if (FD::is_zero(d)) {
                    E x3, y3;
                    if (FD::opposite(L.y, ty, c) || !FD::double_point(L.x, L.y, c, x3, y3)) {
                        L.infinity = 1;
                    } else {
                        L.x = x3; L.y = y3;
                    }
                } else {
                    const E ik = FD::mul(inv, prefix[kk], c);
                    inv = FD::mul(inv, d, c);
                    E x3, y3;
                    FD::add_points(L.x, L.y, tx, ty, ik, c, x3, y3);
                    L.x = x3; L.y = y3;
                }
            }
            L.a = FD::scalar_add(L.a, table[j].da, c);
            if (prm.has_db) L.b = FD::scalar_add(L.b, table[j].db, c);
            ++L.steps;
            if (L.infinity) {
                // The identity's next step adds jump 0, as in walk_lane.
                L.x = FD::zero(); L.y = FD::zero();
                L.index = 0;
                L.mode = MODE_NORMAL; L.fcur_ok = 0; L.fprev_ok = 0; L.anchor_ok = 0;
                continue;
            }
            if (prm.walk == WALK_V4) canonicalize(L, c, prm);
            const u64 two_back = L.fprev;
            const u32 two_back_ok = L.fprev_ok;
            classify(L, c, prm);
            if (prm.walk == WALK_V4 && L.active) track_cycles(L, prm, two_back, two_back_ok);
        }
    }
}


// Device entry point shared by the CUDA and Metal kernels: thread `first / K`
// loads K lanes, runs `steps` transitions sharing one inversion per step, and
// stores them. Lanes keep their cycle state in device memory between chunks,
// so splitting a round into command buffers never changes an output.
template <class FD, int K>
HD inline void gpu_chunk(typename FD::C c, const F131_DEV Entry<FD>* table, Params prm,
                         F131_DEV Lane<FD>* lanes, u32 total, u32 first, u32 steps) {
    if (first >= total) return;
    const u32 count = total - first < u32(K) ? total - first : u32(K);
    Lane<FD> local[K];
    for (u32 k = 0; k < count; ++k) local[k] = lanes[first + k];
    walk_batch<FD, K>(c, table, prm, local, count, steps);
    for (u32 k = 0; k < count; ++k) lanes[first + k] = local[k];
}

}  // namespace f131
}  // namespace ecc

#endif  // ECC_FAST131_H
