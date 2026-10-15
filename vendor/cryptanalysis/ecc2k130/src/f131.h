// f131.h - the walk's hot path in 64-bit limbs, for a host CPU.
//
// include/packed131.h lays an element out as five 32-bit words because that is
// what the device wants; a 64-bit core pays for it twice, in instructions
// (about 100 for a product, 95 for its reduction, 290 for the selection) and
// in the narrow stores and wide loads the word shuffling leaves behind.  This
// header is the same arithmetic on three 64-bit limbs (64, 64, 3 bits), for
// the five routines a step spends its time in:
//
//   mul, sqr        the polynomial-basis product and squaring: Karatsuba over
//                   the host's 64 x 64 carry-less multiply, then the direct
//                   reduction modulo 0xd1d0d000d0000000d000000000000000d
//   fromPolynomial  the reduced polynomial -> normal basis network
//   select          twSelect: phase, pivot, sign, cycle rule, history
//   addend          twAddend: d = x + x_T, e = y + y_T (+ x_T when negated)
//
// Nothing here is a new formulation.  The reduction is packeddirectreduce131.h
// with its shifts written across 64-bit limbs:
//
//   D = H >> 131,  R = D + D>>1 + D>>3,
//   Q = D + R>>1 + R>>9 + R>>25 + R>>57 + R>>121        (the quotient)
//   T = Q + Q<<2 + Q<<3                                  (Q * 0xd)
//   H mod m = H + T * (1 + z^64 + z^96 + z^112 + z^120 + z^128) + Q * z^124
//
// the conversion is packed131.h's fromPolynomialReduced131 stage for stage, and
// the selection reads the byte tables of packedtablewalk.cuh's constant buffer
// (ECC_TABLE_PIVOT_BYTES layout).  Inversion and the start points stay on the
// packed routines: they are a hundredth of the work.  src/cputest.cpp holds
// every routine here to its packed counterpart, which hostcheck.cpp holds to
// the golden model, and then the engine built on them to the model directly.
#pragma once

#include <stdint.h>
#include <string.h>

#include "../include/packed131.h"
#include "../include/packedtablewalk.cuh"

#if !ECC_TABLE_PIVOT_BYTES
#    error "f131.h reads the byte-table layout of the constant buffer (ECC_TABLE_PIVOT_BYTES=1)"
#endif

namespace f131
{

using eccPacked131::P131;

struct F131 {
    uint64_t w[3];
};

#define F131_INLINE static inline __attribute__((always_inline))

F131_INLINE F131 fromPacked(const P131 &a)
{
    return F131{
        {a.v[0] | (uint64_t(a.v[1]) << 32), a.v[2] | (uint64_t(a.v[3]) << 32), a.v[4] & 7u}};
}
F131_INLINE P131 toPacked(const F131 &a)
{
    return P131{{uint32_t(a.w[0]), uint32_t(a.w[0] >> 32), uint32_t(a.w[1]), uint32_t(a.w[1] >> 32),
                 uint32_t(a.w[2]) & 7u}};
}
F131_INLINE F131 add(const F131 &a, const F131 &b)
{
    return F131{{a.w[0] ^ b.w[0], a.w[1] ^ b.w[1], a.w[2] ^ b.w[2]}};
}
F131_INLINE int weight(const F131 &a)
{
    return __builtin_popcountll(a.w[0]) + __builtin_popcountll(a.w[1]) +
           __builtin_popcountll(a.w[2] & 7u);
}

// 64 x 64 -> 128 carry-less: the host's instruction, or packed131.h's
// masked-multiply product where the build has none.
F131_INLINE void clmul(uint64_t a, uint64_t b, uint64_t *lo, uint64_t *hi)
{
#if ECC_HOST_CLMUL
    eccHostClmul64(a, b, lo, hi);
#else
    const uint32_t aw[2] = {uint32_t(a), uint32_t(a >> 32)},
                   bw[2] = {uint32_t(b), uint32_t(b >> 32)};
    uint32_t r[4];
    eccPacked131::clmul64(r, aw, bw);
    *lo = r[0] | (uint64_t(r[1]) << 32);
    *hi = r[2] | (uint64_t(r[3]) << 32);
#endif
}

// x * y for a 3-bit x: three masked shifts, (lo, hi) with hi below 4.
F131_INLINE void mulTiny(uint64_t x, uint64_t y, uint64_t *lo, uint64_t *hi)
{
    const uint64_t m0 = 0 - (x & 1), m1 = 0 - ((x >> 1) & 1), m2 = 0 - ((x >> 2) & 1);
    *lo = (y & m0) ^ ((y << 1) & m1) ^ ((y << 2) & m2);
    *hi = ((y >> 63) & m1) ^ ((y >> 62) & m2);
}

// H mod m for a product H of degree at most 260, five limbs.
F131_INLINE F131 reduce(const uint64_t h[5])
{
    const uint64_t d0 = (h[2] >> 3) | (h[3] << 61), d1 = (h[3] >> 3) | (h[4] << 61),
                   d2 = (h[4] >> 3) & 3u;
    const uint64_t r0 = d0 ^ (d0 >> 1) ^ (d1 << 63) ^ (d0 >> 3) ^ (d1 << 61);
    const uint64_t r1 = d1 ^ (d1 >> 1) ^ (d2 << 63) ^ (d1 >> 3) ^ (d2 << 61);
    const uint64_t r2 = d2 ^ (d2 >> 1);
    const uint64_t q0 = d0 ^ (r0 >> 1) ^ (r1 << 63) ^ (r0 >> 9) ^ (r1 << 55) ^ (r0 >> 25) ^
                        (r1 << 39) ^ (r0 >> 57) ^ (r1 << 7) ^ (r1 >> 57) ^ (r2 << 7);
    const uint64_t q1 = d1 ^ (r1 >> 1) ^ (r2 << 63) ^ (r1 >> 9) ^ (r2 << 55) ^ (r1 >> 25) ^
                        (r2 << 39) ^ (r1 >> 57) ^ (r2 << 7);
    const uint64_t q2 = d2 ^ (r2 >> 1);
    const uint64_t t0 = q0 ^ (q0 << 2) ^ (q0 << 3);
    const uint64_t t1 = q1 ^ (q1 << 2) ^ (q0 >> 62) ^ (q1 << 3) ^ (q0 >> 61);
    const uint64_t t2 = q2 ^ (q2 << 2) ^ (q1 >> 62) ^ (q2 << 3) ^ (q1 >> 61);
    F131 out;
    out.w[0] = h[0] ^ t0;
    out.w[1] = h[1] ^ t1 ^ t0 ^ (t0 << 32) ^ (t0 << 48) ^ (t0 << 56) ^ (q0 << 60);
    out.w[2] = (h[2] ^ t2 ^ t1 ^ (t0 >> 32) ^ (t0 >> 16) ^ (t0 >> 8) ^ t0 ^ (q0 >> 4)) & 7u;
    return out;
}

// The unreduced product, five limbs: Karatsuba on the two full limbs, then the
// 3-bit top limbs' cross terms.
#if ECC_HOST_CLMUL && defined(__aarch64__)
// On PMULL the cross terms are four more multiplies, and the three 128-bit
// partial sums stay in vector registers until the five limbs come out.
F131_INLINE void product(const F131 &a, const F131 &b, uint64_t h[5])
{
    // Operands as [w0, w1] vectors: pmull takes the low lanes, pmull2 the high
    // ones, so nothing crosses between the integer and vector registers on the
    // way in.
    const uint64x2_t zero = vdupq_n_u64(0);
    const poly64x2_t av = vreinterpretq_p64_u64(vld1q_u64(a.w)),
                     bv = vreinterpretq_p64_u64(vld1q_u64(b.w));
    const poly64x2_t a2 = vreinterpretq_p64_u64(vdupq_n_u64(a.w[2])),
                     b2 = vreinterpretq_p64_u64(vdupq_n_u64(b.w[2]));
    const poly64x2_t as = vreinterpretq_p64_u64(veorq_u64(
                         vreinterpretq_u64_p64(av), vextq_u64(vreinterpretq_u64_p64(av), zero, 1))),
                     bs = vreinterpretq_p64_u64(veorq_u64(
                         vreinterpretq_u64_p64(bv), vextq_u64(vreinterpretq_u64_p64(bv), zero, 1)));
#    define F131_PMULL(p, q)                                                                       \
        vreinterpretq_u64_p128(vmull_p64(vgetq_lane_p64(p, 0), vgetq_lane_p64(q, 0)))
#    define F131_PMULL2(p, q) vreinterpretq_u64_p128(vmull_high_p64(p, q))
    const uint64x2_t l = F131_PMULL(av, bv), u = F131_PMULL2(av, bv);
    const uint64x2_t m = veorq_u64(F131_PMULL(as, bs), veorq_u64(l, u));
    const uint64x2_t x = veorq_u64(F131_PMULL(a2, bv), F131_PMULL(b2, av));   // at limb 2
    const uint64x2_t y = veorq_u64(F131_PMULL2(a2, bv), F131_PMULL2(b2, av)); // at limb 3
    const uint64x2_t t = F131_PMULL(a2, b2);                                  // at limb 4
#    undef F131_PMULL
#    undef F131_PMULL2
    const uint64x2_t lo = veorq_u64(l, vextq_u64(zero, m, 1));            // l ^ [0, m0]
    const uint64x2_t hi = veorq_u64(veorq_u64(u, x), vextq_u64(m, y, 1)); // u ^ x ^ [m1, y0]
    h[0] = vgetq_lane_u64(lo, 0);
    h[1] = vgetq_lane_u64(lo, 1);
    h[2] = vgetq_lane_u64(hi, 0);
    h[3] = vgetq_lane_u64(hi, 1);
    h[4] = vgetq_lane_u64(y, 1) ^ vgetq_lane_u64(t, 0);
}
#else
F131_INLINE void product(const F131 &a, const F131 &b, uint64_t h[5])
{
    uint64_t l0, l1, h0, h1, m0, m1;
    clmul(a.w[0], b.w[0], &l0, &l1);
    clmul(a.w[1], b.w[1], &h0, &h1);
    clmul(a.w[0] ^ a.w[1], b.w[0] ^ b.w[1], &m0, &m1);
    m0 ^= l0 ^ h0;
    m1 ^= l1 ^ h1;
    uint64_t x0, x1, y0, y1, u0, u1, v0, v1;
    mulTiny(a.w[2], b.w[0], &x0, &x1);
    mulTiny(b.w[2], a.w[0], &y0, &y1);
    mulTiny(a.w[2], b.w[1], &u0, &u1);
    mulTiny(b.w[2], a.w[1], &v0, &v1);
    uint64_t top, topHi;
    mulTiny(a.w[2], b.w[2], &top, &topHi);
    h[0] = l0;
    h[1] = l1 ^ m0;
    h[2] = h0 ^ m1 ^ x0 ^ y0;
    h[3] = h1 ^ x1 ^ y1 ^ u0 ^ v0;
    h[4] = u1 ^ v1 ^ top;
}
#endif

F131_INLINE F131 mul(const F131 &a, const F131 &b)
{
    uint64_t h[5];
    product(a, b, h);
    return reduce(h);
}

// A carry-less square spreads the bits: one multiply per limb with a
// multiplier, the shift-and-mask spread without.
F131_INLINE void spread(uint64_t x, uint64_t *lo, uint64_t *hi)
{
#if ECC_HOST_CLMUL
    eccHostClmul64(x, x, lo, hi);
#else
    *lo = eccPacked131::spread32alu(uint32_t(x));
    *hi = eccPacked131::spread32alu(uint32_t(x >> 32));
#endif
}
F131_INLINE F131 sqr(const F131 &a)
{
    uint64_t h[5];
    spread(a.w[0], &h[0], &h[1]);
    spread(a.w[1], &h[2], &h[3]);
    h[4] = (a.w[2] & 1u) | ((a.w[2] & 2u) << 1) | ((a.w[2] & 4u) << 2);
    return reduce(h);
}

// Reduced polynomial basis -> permuted normal basis: fromPolynomialReduced131,
// each stage V ^= (V >> s) & mask, on limbs.
F131_INLINE F131 fromPolynomial(const F131 &a)
{
    uint64_t w0 = a.w[0], w1 = a.w[1];
    const uint64_t w2 = a.w[2];
    w0 ^= w1 & 0xffffffff00000000ull;                        // shift 64
    w0 ^= ((w0 >> 32) | (w1 << 32)) & 0xffff0000ffff0000ull; // shift 32
    w1 ^= ((w1 >> 32) | (w2 << 32)) & 0x00000000ffff0000ull;
    w0 ^= ((w0 >> 16) | (w1 << 48)) & 0xff00ff00ff00ff00ull; // shift 16
    w1 ^= ((w1 >> 16) | (w2 << 48)) & 0x0000ff00ff00ff00ull;
    w0 ^= ((w0 >> 8) | (w1 << 56)) & 0xf0f0f0f0f0f0f0f0ull; // shift 8
    w1 ^= ((w1 >> 8) | (w2 << 56)) & 0x00f0f0f0f0f0f0f0ull;
    w0 ^= ((w0 >> 4) | (w1 << 60)) & 0xccccccccccccccccull; // shift 4
    w1 ^= ((w1 >> 4) | (w2 << 60)) & 0x4cccccccccccccccull;
    w0 ^= ((w0 >> 2) | (w1 << 62)) & 0xaaaaaaaaaaaaaaaaull; // shift 2
    w1 ^= ((w1 >> 2) | (w2 << 62)) & 0xaaaaaaaaaaaaaaaaull;
    const uint64_t sign = 0 - (w0 & 1u);
    return F131{{((w0 >> 1) | (w1 << 63)) ^ sign, ((w1 >> 1) | (w2 << 63)) ^ sign,
                 ((w2 >> 1) ^ sign) & 7u}};
}

/* ---- the table walk's selection, from the shared constant buffer ---------- */

F131_INLINE uint64_t load64(const uint32_t *p)
{
    uint64_t v;
    memcpy(&v, p, 8);
    return v;
}
F131_INLINE unsigned max2(unsigned a, unsigned b) { return a > b ? a : b; }

// Sum (mode 0) or maximum (mode 1) of table[i][byte i of (s0, s1, s2)] over the
// 17 bytes, as a tree: the packed form's running sum and running maximum are
// 17-long dependency chains.
F131_INLINE unsigned phaseSum(const uint8_t *t, uint64_t s0, uint64_t s1, uint64_t s2)
{
    unsigned acc[4] = {0, 0, 0, 0};
    for (int i = 0; i < 8; ++i) {
        acc[i & 1] += t[i * 256 + ((s0 >> (8 * i)) & 0xFF)];
        acc[2 + (i & 1)] += t[(8 + i) * 256 + ((s1 >> (8 * i)) & 0xFF)];
    }
    return (acc[0] + acc[1]) + (acc[2] + acc[3]) + t[16 * 256 + (s2 & 0xFF)];
}
F131_INLINE unsigned pivotMax(const uint8_t *t, uint64_t s0, uint64_t s1, uint64_t s2)
{
    unsigned m[8];
    for (int i = 0; i < 8; ++i)
        m[i] = max2(t[i * 256 + ((s0 >> (8 * i)) & 0xFF)],
                    t[(8 + i) * 256 + ((s1 >> (8 * i)) & 0xFF)]);
    const unsigned a = max2(max2(m[0], m[1]), max2(m[2], m[3])),
                   b = max2(max2(m[4], m[5]), max2(m[6], m[7]));
    return max2(max2(a, b), t[16 * 256 + (s2 & 7u)]);
}

// twSelect in its three dependent stages.  One point's selection is a chain --
// the phase picks the mask, the mask the pivot, the pivot the sign -- some 90
// cycles long for 240 instructions, so a core that runs it point by point
// waits on the chain; run stage by stage over a batch (cpuwalk.h) each loop's
// chain is a third of that and the core stays full.
//
// Stage 1, the Frobenius phase k = (sum_e L(e) x_e) * HW(x)^-1 mod 131.
F131_INLINE int selectPhase(const F131 &x, int hw, const uint32_t *tw)
{
    using namespace eccPacked131;
    const uint8_t *bytes = reinterpret_cast<const uint8_t *>(tw);
    const unsigned s = phaseSum(bytes + 4 * TW_PHASE_OFF, x.w[0], x.w[1], x.w[2]);
    return int(((s % 131u) * tw[TW_INV_OFF + hw]) % 131u);
}
// Stage 2, the pivot: the support element with the largest L below k, or with
// the largest L when none is below.
F131_INLINE int selectPivot(const F131 &x, int k, const uint32_t *tw)
{
    using namespace eccPacked131;
    const uint8_t *bytes = reinterpret_cast<const uint8_t *>(tw);
    const uint32_t *m = tw + TW_MASK_OFF + k * 5;
    uint64_t s0 = x.w[0] & load64(m), s1 = x.w[1] & load64(m + 2), s2 = x.w[2] & m[4];
    const bool any = (s0 | s1 | s2) != 0;
    s0 = any ? s0 : x.w[0];
    s1 = any ? s1 : x.w[1];
    s2 = any ? s2 : x.w[2];
    return bytes[4 * TW_LINV_OFF + pivotMax(bytes + 4 * TW_MAX_OFF, s0, s1, s2) - 1];
}
// Stage 3: eps is coordinate p of y in the normal basis, a parity of the
// polynomial-basis y against row p ...
F131_INLINE int coordinate(const F131 &yp, int p, const uint32_t *tw)
{
    using namespace eccPacked131;
    const uint32_t *row = tw + TW_ROW_OFF + p * 4;
    const uint64_t rowTop = (tw[TW_ROWTOP_OFF + (p >> 3)] >> ((p & 7) * 4)) & 7u;
    return __builtin_parityll((yp.w[0] & load64(row)) ^ (yp.w[1] & load64(row + 2)) ^
                              (yp.w[2] & rowTop));
}
// ... then the tag, the cycle rule, the history.
F131_INLINE unsigned tagOf(int hw, int k, int eps, unsigned long long *hist)
{
    using namespace eccPacked131;
    int h = (hw >> 1) & (TW_H - 1);
    unsigned tag = eccTag(h, k, eps);
    const unsigned long long old = *hist;
    while (__builtin_expect(eccTagFruitless(tag, old), 0)) {
        h = (h + 1) & (TW_H - 1);
        tag = eccTag(h, k, eps);
    }
    *hist = eccHistPush(old, tag);
    return tag;
}
F131_INLINE unsigned selectTag(const F131 &yp, int hw, int k, int p, unsigned long long *hist,
                               const uint32_t *tw)
{
    return tagOf(hw, k, coordinate(yp, p, tw), hist);
}
// twSelect: x in the normal basis, yp in the polynomial basis; returns the tag
// after the cycle rule and advances the history.
F131_INLINE unsigned select(const F131 &x, const F131 &yp, int hw, unsigned long long *hist,
                            const uint32_t *tw)
{
    const int k = selectPhase(x, hw, tw);
    return selectTag(yp, hw, k, selectPivot(x, k, tw), hist, tw);
}

// twAddend: d = x + x_T and e = y + y_T (+ x_T when the table point is negated).
F131_INLINE void addend(unsigned tag, const F131 &xp, const F131 &yp, const uint32_t *tw, F131 *d,
                        F131 *e)
{
    using namespace eccPacked131;
    const int h = eccTagH(tag);
    const uint32_t *kbase = tw + eccTagK(tag) * TW_KWORDS, *t = kbase + h * TW_ENTRY;
    const uint64_t top = (kbase[TW_H * TW_ENTRY + (h >> 2)] >> ((h & 3) * 8)) & 63u;
    const uint64_t neg = 0 - uint64_t(eccTagEps(tag));
    const uint64_t tx0 = load64(t), tx1 = load64(t + 2), tx2 = top & 7u;
    d->w[0] = xp.w[0] ^ tx0;
    d->w[1] = xp.w[1] ^ tx1;
    d->w[2] = xp.w[2] ^ tx2;
    e->w[0] = yp.w[0] ^ load64(t + 4) ^ (tx0 & neg);
    e->w[1] = yp.w[1] ^ load64(t + 6) ^ (tx1 & neg);
    e->w[2] = yp.w[2] ^ (top >> 3) ^ (tx2 & neg);
}

} // namespace f131
