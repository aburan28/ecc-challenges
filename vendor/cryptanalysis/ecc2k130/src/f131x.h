// f131x.h - f131.h's arithmetic on several lanes at once, in SIMD registers.
//
// A step's five products and its squaring are the same instructions on every
// lane of a batch, and the engine already runs them in independent chains
// (cpuwalk.h): nothing in one lane's product depends on another's.  So the
// limbs of N lanes are held side by side in one vector register each -- N
// lanes' limb 0 in one register, their limb 1 in another, their 3-bit limb 2
// in a third -- and f131.h's routines run unchanged on those registers: a
// 64-bit shift of a limb is a per-element shift, an xor of two limbs is a
// vector xor, and the reduction, the squaring's spread and the conversion
// network are written once, over a vector type, for any N.
//
// What differs by width is the 64 x 64 -> 128 carry-less multiply.  x86-64's
// PCLMULQDQ multiplies one chosen 64-bit half of each 128-bit lane of its two
// operands, so the N products of a limb pair come out of two instructions --
// one for the even elements (imm 0x00), one for the odd (0x11) -- as
// [lo, hi] pairs that an unpacklo/unpackhi sort back into a `lo` and a `hi`
// register.  N is 2 on PCLMULQDQ alone (SSE), 4 with VPCLMULQDQ on AVX2, 8
// with VPCLMULQDQ on AVX-512.  The product is Karatsuba on the two full limbs
// and Karatsuba again between each full limb and the 3-bit limbs: five
// multiplies for N lanes, their sums sorted once per output limb, where
// f131.h's scalar path shifts and masks.  On AVX-512 the multiplies and the
// shuffles that sort them share one port and the shifts have one of their
// own, which is what the instruction choices below are about: funnel shifts
// (VBMI2) for the reduction's shifts across a limb, byte and qword permutes
// (VBMI) for the small products and spreads that would otherwise be masks,
// and as few sorts as the sums allow.
//
// This header selects itself only on x86-64 with a hardware multiplier; the
// scalar path of f131.h stays what AArch64 (PMULL) and the software product
// run.  ECC_F131_LANES=1 forces the scalar engine on any host, and
// ECC_F131_LANES=2 or 4 narrows the vectors on a host that could take 8,
// which is how all three widths are held to f131.h (src/cputest.cpp) and
// measured on one core.
#pragma once

#include "f131.h"

#ifndef ECC_F131_LANES
#    if ECC_HOST_CLMUL && defined(__x86_64__) && defined(__AVX512F__) && defined(__AVX512BW__) &&  \
        defined(__VPCLMULQDQ__)
#        define ECC_F131_LANES 8
#    elif ECC_HOST_CLMUL && defined(__x86_64__) && defined(__AVX2__) && defined(__VPCLMULQDQ__)
#        define ECC_F131_LANES 4
#    elif ECC_HOST_CLMUL && defined(__x86_64__) && defined(__PCLMUL__) && defined(__SSE2__)
#        define ECC_F131_LANES 2
#    else
#        define ECC_F131_LANES 1
#    endif
#endif

#if ECC_F131_LANES != 1 && ECC_F131_LANES != 2 && ECC_F131_LANES != 4 && ECC_F131_LANES != 8
#    error "ECC_F131_LANES must be 1, 2, 4 or 8"
#endif
#if ECC_F131_LANES > 1 && !(ECC_HOST_CLMUL && defined(__x86_64__))
#    error "ECC_F131_LANES > 1 needs x86-64 with a hardware carry-less multiplier"
#endif

// The addend's table points by row loads and a transpose (1) or by gathers
// (0) on the 512-bit path; see addend.
#ifndef ECC_F131_ADDEND_ROWS
#    define ECC_F131_ADDEND_ROWS 1
#endif

#if ECC_F131_LANES > 1

#    include <immintrin.h>

namespace f131x
{

using f131::F131;

// The vector of N 64-bit limbs, as the compiler's own vector type so that the
// shifts, ands and xors of f131.h read the same over it; its signed twin for
// an arithmetic shift, and the intrinsics' type.
template <int N> struct Limbs;
template <> struct Limbs<2> {
    typedef uint64_t V __attribute__((vector_size(16)));
    typedef int64_t S __attribute__((vector_size(16)));
    typedef __m128i M;
};
template <> struct Limbs<4> {
    typedef uint64_t V __attribute__((vector_size(32)));
    typedef int64_t S __attribute__((vector_size(32)));
    typedef __m256i M;
};
template <> struct Limbs<8> {
    typedef uint64_t V __attribute__((vector_size(64)));
    typedef int64_t S __attribute__((vector_size(64)));
    typedef __m512i M;
};

#    define F131X_INLINE inline __attribute__((always_inline))

// Which of the AVX-512 extensions beyond F/BW the build may use.  Each has a
// fallback in plain vector arithmetic, so a width is never refused for want
// of one; they only shorten what the fallback spells out.
#    if defined(__AVX512VBMI2__)
#        define F131X_VBMI2 1 // funnel shifts across a limb boundary
#    else
#        define F131X_VBMI2 0
#    endif
#    if defined(__AVX512VBMI__)
#        define F131X_VBMI 1 // byte permutes: a 64-entry table lookup in a register
#    else
#        define F131X_VBMI 0
#    endif
#    if defined(__AVX512VL__)
#        define F131X_VL 1 // the above on 128- and 256-bit vectors
#    else
#        define F131X_VL 0
#    endif

// The N-lane carry-less multiply as the instruction leaves it.  PCLMULQDQ
// multiplies one chosen 64-bit half of each 128-bit lane, so the N products
// of a limb pair come out of two instructions as [lo, hi] pairs: `e` holds
// the even elements' products, `o` the odd ones'.  lo() and hi() sort them
// back into element order with an unpack.  The unpack is a shuffle, and on a
// 512-bit register the shuffles and the multiplies share one port, so the
// product xors as much as it can in this form and sorts the sums: eight
// unpacks for five limbs instead of two per multiply.
template <int N> struct Prod {
    typename Limbs<N>::V e, o;
    F131X_INLINE Prod operator^(const Prod &p) const { return Prod{e ^ p.e, o ^ p.o}; }
};
template <int N> F131X_INLINE Prod<N> clmul(typename Limbs<N>::V a, typename Limbs<N>::V b);
template <int N> F131X_INLINE typename Limbs<N>::V lo(const Prod<N> &p);
template <int N> F131X_INLINE typename Limbs<N>::V hi(const Prod<N> &p);

template <> F131X_INLINE Prod<2> clmul<2>(Limbs<2>::V a, Limbs<2>::V b)
{
    return Prod<2>{(Limbs<2>::V)_mm_clmulepi64_si128((__m128i)a, (__m128i)b, 0x00),
                   (Limbs<2>::V)_mm_clmulepi64_si128((__m128i)a, (__m128i)b, 0x11)};
}
template <> F131X_INLINE Limbs<2>::V lo<2>(const Prod<2> &p)
{
    return (Limbs<2>::V)_mm_unpacklo_epi64((__m128i)p.e, (__m128i)p.o);
}
template <> F131X_INLINE Limbs<2>::V hi<2>(const Prod<2> &p)
{
    return (Limbs<2>::V)_mm_unpackhi_epi64((__m128i)p.e, (__m128i)p.o);
}
#    if ECC_F131_LANES >= 4
template <> F131X_INLINE Prod<4> clmul<4>(Limbs<4>::V a, Limbs<4>::V b)
{
    return Prod<4>{(Limbs<4>::V)_mm256_clmulepi64_epi128((__m256i)a, (__m256i)b, 0x00),
                   (Limbs<4>::V)_mm256_clmulepi64_epi128((__m256i)a, (__m256i)b, 0x11)};
}
template <> F131X_INLINE Limbs<4>::V lo<4>(const Prod<4> &p)
{
    return (Limbs<4>::V)_mm256_unpacklo_epi64((__m256i)p.e, (__m256i)p.o);
}
template <> F131X_INLINE Limbs<4>::V hi<4>(const Prod<4> &p)
{
    return (Limbs<4>::V)_mm256_unpackhi_epi64((__m256i)p.e, (__m256i)p.o);
}
#    endif
#    if ECC_F131_LANES >= 8
template <> F131X_INLINE Prod<8> clmul<8>(Limbs<8>::V a, Limbs<8>::V b)
{
    return Prod<8>{(Limbs<8>::V)_mm512_clmulepi64_epi128((__m512i)a, (__m512i)b, 0x00),
                   (Limbs<8>::V)_mm512_clmulepi64_epi128((__m512i)a, (__m512i)b, 0x11)};
}
template <> F131X_INLINE Limbs<8>::V lo<8>(const Prod<8> &p)
{
    return (Limbs<8>::V)_mm512_unpacklo_epi64((__m512i)p.e, (__m512i)p.o);
}
template <> F131X_INLINE Limbs<8>::V hi<8>(const Prod<8> &p)
{
    return (Limbs<8>::V)_mm512_unpackhi_epi64((__m512i)p.e, (__m512i)p.o);
}
#    endif

// N field elements, limb by limb.
template <int N> struct F131x {
    typedef typename Limbs<N>::V V;
    V w0, w1, w2;

    // A scalar operand of a vector operator is broadcast, and compiles to the
    // one broadcast instruction; an element-by-element loop compiled to N
    // masked inserts.
    F131X_INLINE static V splat(uint64_t x)
    {
        const V zero = {};
        return zero | x;
    }
    // Lanes i..i+N-1 of a limb-sliced array (unaligned; the engine aligns its
    // arrays, and a batch that is not a multiple of N still loads correctly).
    F131X_INLINE static V loadLimb(const uint64_t *p)
    {
        V v;
        memcpy(&v, p, sizeof(v));
        return v;
    }
    F131X_INLINE static void storeLimb(uint64_t *p, V v) { memcpy(p, &v, sizeof(v)); }
    F131X_INLINE static F131x load(const uint64_t *w0, const uint64_t *w1, const uint64_t *w2)
    {
        return F131x{loadLimb(w0), loadLimb(w1), loadLimb(w2)};
    }
    F131X_INLINE void store(uint64_t *o0, uint64_t *o1, uint64_t *o2) const
    {
        storeLimb(o0, w0);
        storeLimb(o1, w1);
        storeLimb(o2, w2);
    }
    F131X_INLINE F131 lane(int i) const { return F131{{w0[i], w1[i], w2[i]}}; }
    F131X_INLINE void setLane(int i, const F131 &a)
    {
        w0[i] = a.w[0];
        w1[i] = a.w[1];
        w2[i] = a.w[2];
    }
};

template <int N> F131X_INLINE F131x<N> add(const F131x<N> &a, const F131x<N> &b)
{
    return F131x<N>{a.w0 ^ b.w0, a.w1 ^ b.w1, a.w2 ^ b.w2};
}

// A shift across a limb boundary: (hi:lo) >> S, and the upper half of
// (hi:lo) << S.  One funnel-shift instruction on VBMI2; two shifts and an
// or without, which on a 512-bit register are three instructions on the one
// port that shifts.  The reduction is mostly these.
template <int N, int S>
F131X_INLINE typename Limbs<N>::V shr(typename Limbs<N>::V lo, typename Limbs<N>::V hi)
{
    typedef typename Limbs<N>::V V;
    typedef typename Limbs<N>::M M;
#    if F131X_VBMI2
    if constexpr (N == 8) return (V)_mm512_shrdi_epi64((M)lo, (M)hi, S);
#    endif
#    if F131X_VBMI2 && F131X_VL
    if constexpr (N == 4) return (V)_mm256_shrdi_epi64((M)lo, (M)hi, S);
    if constexpr (N == 2) return (V)_mm_shrdi_epi64((M)lo, (M)hi, S);
#    endif
    return (lo >> S) | (hi << (64 - S));
}
template <int N, int S>
F131X_INLINE typename Limbs<N>::V shl(typename Limbs<N>::V hi, typename Limbs<N>::V lo)
{
    typedef typename Limbs<N>::V V;
    typedef typename Limbs<N>::M M;
#    if F131X_VBMI2
    if constexpr (N == 8) return (V)_mm512_shldi_epi64((M)hi, (M)lo, S);
#    endif
#    if F131X_VBMI2 && F131X_VL
    if constexpr (N == 4) return (V)_mm256_shldi_epi64((M)hi, (M)lo, S);
    if constexpr (N == 2) return (V)_mm_shldi_epi64((M)hi, (M)lo, S);
#    endif
    return (hi << S) | (lo >> (64 - S));
}

// t[idx[i] & 7] for each element, t eight 64-bit words: a register permute
// where the width allows one (eight 64-bit elements: the 512-bit permute's
// own table; two 256-bit halves through the two-table permute), N loads
// otherwise.
template <int N>
F131X_INLINE typename Limbs<N>::V lookup8(const uint64_t t[8], typename Limbs<N>::V idx)
{
    typedef typename Limbs<N>::V V;
    typedef typename Limbs<N>::M M;
    if constexpr (N == 8) {
        return (V)_mm512_permutexvar_epi64((M)idx, _mm512_loadu_si512((const void *)t));
    }
#    if F131X_VL
    if constexpr (N == 4) {
        return (V)_mm256_permutex2var_epi64(_mm256_loadu_si256((const __m256i *)t), (M)idx,
                                            _mm256_loadu_si256((const __m256i *)(t + 4)));
    }
#    endif
    V r;
    for (int i = 0; i < N; ++i) r[i] = t[idx[i] & 7];
    return r;
}

// The 3 x 3 carry-less product of the top limbs, a value below 64: a table of
// 64 bytes indexed by (a << 3 | b), through a byte permute where there is one
// over 64 bytes (the other seven bytes of each element index entry 0, which
// is 0 * 0), else the three masked shifts of f131::mulTiny.
struct TinyTable {
    uint8_t t[64];
    constexpr TinyTable() : t{}
    {
        for (int a = 0; a < 8; ++a)
            for (int b = 0; b < 8; ++b) {
                int p = 0;
                for (int k = 0; k < 3; ++k)
                    if ((a >> k) & 1) p ^= b << k;
                t[a * 8 + b] = uint8_t(p);
            }
    }
};
alignas(64) static constexpr TinyTable kTiny{};

template <int N>
F131X_INLINE typename Limbs<N>::V tinyProduct(typename Limbs<N>::V a2, typename Limbs<N>::V b2)
{
    typedef typename Limbs<N>::V V;
    typedef typename Limbs<N>::M M;
#    if F131X_VBMI
    if constexpr (N == 8) {
        return (V)_mm512_permutexvar_epi8((M)((a2 << 3) | b2),
                                          _mm512_load_si512((const void *)kTiny.t));
    }
#    endif
#    if F131X_VBMI && F131X_VL
    if constexpr (N == 4) {
        return (V)_mm256_permutex2var_epi8(_mm256_load_si256((const __m256i *)kTiny.t),
                                           (M)((a2 << 3) | b2),
                                           _mm256_load_si256((const __m256i *)(kTiny.t + 32)));
    }
#    endif
    return (b2 & (V{} - (a2 & F131x<N>::splat(1u)))) ^
           ((b2 << 1) & (V{} - ((a2 >> 1) & F131x<N>::splat(1u)))) ^
           ((b2 << 2) & (V{} - ((a2 >> 2) & F131x<N>::splat(1u))));
}

// The quotient's constant.  f131::reduce forms Q from D = H >> 131 as D plus
// D shifted right by 1, 2, 4, 9, 10, 12, 25, 26, 28, 57, 58, 60, 121, 122
// and 124 -- the sixteen taps of the Barrett constant, factored into two
// shift chains -- and every one of those is a truncating shift, so together
// they are the top of one carry-less product: Q = (D mu) >> 124 with mu the
// sum of z^(124 - t) over the taps.  The first thirteen taps are mu's high
// limb, 0x1d0d000d0000000d (which is also the middle limb of m); the last
// three are its low limb, 0xd, and since D 0xd >> 124 sees only the top nine
// bits of D they are three shifts of one funnel shift.  D is 130 bits and
// mu's high limb 61, so their product fits three limbs; the 2-bit top limb's
// product is a four-entry table.
static const uint64_t kMuTop[8] = {
    0, 0x1d0d000d0000000dull, 0x3a1a001a0000001aull, 0x2717001700000017ull, 0, 0, 0, 0};

// f131::reduce, limb for limb, over N lanes: the quotient from the multiplier
// as above where f131.h shifts, the shifts that remain funnel shifts, and the
// compiler folds the xor chains into three-input logic.
template <int N> F131X_INLINE F131x<N> reduce(const typename Limbs<N>::V h[5])
{
    typedef typename Limbs<N>::V V;
    const V mu = F131x<N>::splat(0x1d0d000d0000000dull);
    const V d0 = shr<N, 3>(h[2], h[3]), d1 = shr<N, 3>(h[3], h[4]),
            d2 = (h[4] >> 3) & F131x<N>::splat(3u);
    const Prod<N> e0 = clmul<N>(d0, mu), e1 = clmul<N>(d1, mu);
    const V p0 = lo<N>(e0), p1 = hi<N>(e0) ^ lo<N>(e1), p2 = hi<N>(e1) ^ lookup8<N>(kMuTop, d2);
    const V v = shr<N, 57>(d1, d2); // D >> 121
    const V q0 = shr<N, 60>(p0, p1) ^ v ^ (v >> 1) ^ (v >> 3), q1 = shr<N, 60>(p1, p2),
            q2 = p2 >> 60;
    // Q m, low 131 bits, with m = (0xd, m1, 0xd) by limb: T = Q 0xd as
    // f131.h has it, and q0 m1 by the multiplier -- its low half is T's
    // four shifted copies and Q << 60 of f131::reduce's limb 1, its high
    // half (with T's top limb) the shifts into limb 2: one product and two
    // unpacks for fourteen shifts.  Limb 2 keeps three bits, so of q1 m1
    // only q1 times the low bits of m1 (which are 5) count there.
    const V t0 = q0 ^ (q0 << 2) ^ (q0 << 3);
    const V t1 = q1 ^ shl<N, 2>(q1, q0) ^ shl<N, 3>(q1, q0);
    const V t2 = q2 ^ shl<N, 2>(q2, q1) ^ shl<N, 3>(q2, q1);
    const Prod<N> p = clmul<N>(q0, F131x<N>::splat(0x1d0d000d0000000dull));
    F131x<N> out;
    out.w0 = h[0] ^ t0;
    out.w1 = h[1] ^ t1 ^ lo<N>(p);
    out.w2 = (h[2] ^ t2 ^ hi<N>(p) ^ t0 ^ q1 ^ (q1 << 2)) & F131x<N>::splat(7u);
    return out;
}

// The unreduced product, five limbs, from five multiplies: Karatsuba on the
// two full limbs, and Karatsuba again for each full limb's cross terms with
// the 3-bit limbs -- (a0 + a2)(b0 + b2) is a0 b0 + a0 b2 + a2 b0 + a2 b2, and
// the first and last are already known -- where f131.h multiplies the four
// cross terms separately.  A multiply here is two instructions on the
// shuffles' port plus the sort; the xors it saves them with are on the ports
// with room.  The 3 x 3 product T lands in the low half of both cross sums
// and is taken back out of limbs 2 and 3; it belongs in limb 4.
template <int N>
F131X_INLINE void product(const F131x<N> &a, const F131x<N> &b, typename Limbs<N>::V h[5])
{
    typedef typename Limbs<N>::V V;
    const Prod<N> L = clmul<N>(a.w0, b.w0), H = clmul<N>(a.w1, b.w1);
    const Prod<N> M = clmul<N>(a.w0 ^ a.w1, b.w0 ^ b.w1) ^ L ^ H;
    const Prod<N> X = clmul<N>(a.w0 ^ a.w2, b.w0 ^ b.w2) ^ L;
    const Prod<N> U = clmul<N>(a.w1 ^ a.w2, b.w1 ^ b.w2) ^ H;
    const Prod<N> HX = H ^ X;
    const V T = tinyProduct<N>(a.w2, b.w2);
    h[0] = lo<N>(L);
    h[1] = hi<N>(L) ^ lo<N>(M);
    h[2] = lo<N>(HX) ^ hi<N>(M) ^ T;
    h[3] = hi<N>(HX) ^ lo<N>(U) ^ T;
    h[4] = hi<N>(U) ^ T;
}

template <int N> F131X_INLINE F131x<N> mul(const F131x<N> &a, const F131x<N> &b)
{
    typename Limbs<N>::V h[5];
    product<N>(a, b, h);
    return reduce<N>(h);
}

// The square: the multiplier spreads each full limb; the 3-bit limb's spread
// (bit i to bit 2i) is an eight-entry table.
static const uint64_t kSpread3[8] = {0, 1, 4, 5, 16, 17, 20, 21};
template <int N> F131X_INLINE F131x<N> sqr(const F131x<N> &a)
{
    typedef typename Limbs<N>::V V;
    V h[5];
    const Prod<N> l = clmul<N>(a.w0, a.w0), u = clmul<N>(a.w1, a.w1);
    h[0] = lo<N>(l);
    h[1] = hi<N>(l);
    h[2] = lo<N>(u);
    h[3] = hi<N>(u);
    h[4] = lookup8<N>(kSpread3, a.w2);
    return reduce<N>(h);
}

// The inverse of N lanes at once, for the one inversion a batch makes.
//
// The engine used to hand that inversion to the packed routines: a chain of
// scalar products across the lanes of the vector to one element, its
// inverse by the normal-basis Itoh-Tsujii chain of include/packed131.h, and
// the chain back.  That is 3(N - 1) dependent scalar products around an
// inversion in 32-bit words, a microsecond at N = 8, which at 2048 lanes a
// batch is 3% of the step on one core while the vector ports idle.  Here the
// N lanes are inverted side by side in the polynomial basis -- no products
// across lanes at all -- by the same addition chain, a^(2^131 - 2) as
// 1, 2, 4, 8, 16, 32, 64, 65, 130, which is eight products and 130
// squarings.  A squaring is a dependent chain of some 25 cycles, so the
// long runs of them are not run: x -> x^(2^k) is linear over GF(2), and for
// k of 8 and up it is read from a table, nibble by nibble (33 nibbles over
// 131 bits), each nibble's sixteen possible images summed in advance.  On a
// 512-bit register sixteen qwords are one two-table permute a limb, so a
// multi-squaring is 99 permutes against four squarings' worth of latency
// per power of two it skips; the tables are 51 KB, built once a process
// and read once a batch.
struct SqrTables {
    static const int kCount = 4;
    static constexpr int kExp[kCount] = {8, 16, 32, 65};
    alignas(64) uint64_t t[kCount][33][3][16];

    SqrTables()
    {
        memset(t, 0, sizeof(t));
        for (int e = 0; e < kCount; ++e)
            for (int j = 0; j < 131; ++j) {
                F131 img = {{0, 0, 0}};
                img.w[j >> 6] = 1ull << (j & 63);
                for (int s = 0; s < kExp[e]; ++s) img = f131::sqr(img);
                const int n = j >> 2, bit = 1 << (j & 3);
                for (int v = 0; v < 16; ++v)
                    if (v & bit)
                        for (int l = 0; l < 3; ++l) t[e][n][l][v] ^= img.w[l];
            }
    }
};
inline const SqrTables &sqrTables()
{
    static const SqrTables tables;
    return tables;
}

// x^(2^k) from its table: the two-table qword permute indexes by the low
// four bits of each element, so a nibble's index is its limb shifted down
// and nothing more.  Without 512-bit registers, lane by lane.
template <int N> F131X_INLINE F131x<N> multiSqr(const F131x<N> &a, const uint64_t (*t)[3][16])
{
    typedef typename Limbs<N>::V V;
    F131x<N> r = {V{}, V{}, V{}};
    if constexpr (N == 8) {
        typedef Limbs<8>::M M;
        const auto look = [&](int n, V idx) __attribute__((always_inline))
        {
            r.w0 ^= (V)_mm512_permutex2var_epi64(_mm512_load_si512((const void *)t[n][0]), (M)idx,
                                                 _mm512_load_si512((const void *)(t[n][0] + 8)));
            r.w1 ^= (V)_mm512_permutex2var_epi64(_mm512_load_si512((const void *)t[n][1]), (M)idx,
                                                 _mm512_load_si512((const void *)(t[n][1] + 8)));
            r.w2 ^= (V)_mm512_permutex2var_epi64(_mm512_load_si512((const void *)t[n][2]), (M)idx,
                                                 _mm512_load_si512((const void *)(t[n][2] + 8)));
        };
        for (int s = 0; s < 64; s += 4) look(s >> 2, a.w0 >> s);
        for (int s = 0; s < 64; s += 4) look(16 + (s >> 2), a.w1 >> s);
        look(32, a.w2);
    } else {
        for (int i = 0; i < N; ++i) {
            const F131 x = a.lane(i);
            F131 y = {{0, 0, 0}};
            for (int n = 0; n < 33; ++n) {
                const unsigned v = unsigned(x.w[n >> 4] >> ((n & 15) * 4)) & 15u;
                for (int l = 0; l < 3; ++l) y.w[l] ^= t[n][l][v];
            }
            r.setLane(i, y);
        }
    }
    return r;
}

template <int N> F131X_INLINE F131x<N> inv(const F131x<N> &a)
{
    const SqrTables &T = sqrTables();
    F131x<N> b = mul<N>(sqr<N>(a), a); // a^(2^2 - 1)
    b = mul<N>(sqr<N>(sqr<N>(b)), b);  // 2^4 - 1
    F131x<N> c = b;
    for (int i = 0; i < 4; ++i) c = sqr<N>(c);
    b = mul<N>(c, b);                      // 2^8 - 1
    b = mul<N>(multiSqr<N>(b, T.t[0]), b); // 2^16 - 1
    b = mul<N>(multiSqr<N>(b, T.t[1]), b); // 2^32 - 1
    b = mul<N>(multiSqr<N>(b, T.t[2]), b); // 2^64 - 1
    b = mul<N>(sqr<N>(b), a);              // 2^65 - 1
    b = mul<N>(multiSqr<N>(b, T.t[3]), b); // 2^130 - 1
    return sqr<N>(b);                      // 2^131 - 2
}

// f131::fromPolynomial over N lanes.
template <int N> F131X_INLINE F131x<N> fromPolynomial(const F131x<N> &a)
{
    typedef typename Limbs<N>::V V;
#    define F131X_C(x) F131x<N>::splat(x)
    V w0 = a.w0, w1 = a.w1;
    const V w2 = a.w2;
    w0 ^= w1 & F131X_C(0xffffffff00000000ull);
    w0 ^= shr<N, 32>(w0, w1) & F131X_C(0xffff0000ffff0000ull);
    w1 ^= shr<N, 32>(w1, w2) & F131X_C(0x00000000ffff0000ull);
    w0 ^= shr<N, 16>(w0, w1) & F131X_C(0xff00ff00ff00ff00ull);
    w1 ^= shr<N, 16>(w1, w2) & F131X_C(0x0000ff00ff00ff00ull);
    w0 ^= shr<N, 8>(w0, w1) & F131X_C(0xf0f0f0f0f0f0f0f0ull);
    w1 ^= shr<N, 8>(w1, w2) & F131X_C(0x00f0f0f0f0f0f0f0ull);
    w0 ^= shr<N, 4>(w0, w1) & F131X_C(0xccccccccccccccccull);
    w1 ^= shr<N, 4>(w1, w2) & F131X_C(0x4cccccccccccccccull);
    w0 ^= shr<N, 2>(w0, w1) & F131X_C(0xaaaaaaaaaaaaaaaaull);
    w1 ^= shr<N, 2>(w1, w2) & F131X_C(0xaaaaaaaaaaaaaaaaull);
    const V sign = V{} - (w0 & F131X_C(1u));
    return F131x<N>{shr<N, 1>(w0, w1) ^ sign, shr<N, 1>(w1, w2) ^ sign,
                    ((w2 >> 1) ^ sign) & F131X_C(7u)};
#    undef F131X_C
}

/* ---- the selection, bitwise over N lanes ---------------------------------- */

// f131.h's selection is table lookups: 17 bytes for the phase sum, 17 for
// the pivot's maximum, a row of the coordinate table for the sign.  A vector
// register has no cheap lookup, but every one of those tables is a function
// of the labels L alone, and include/tablewalk.h's model computes the same
// selection from the bit planes of L (plane j has coordinate e set when bit
// j of L(e) is): the phase sum is sum_j 2^j |x & plane_j|, the mask L < k is
// a bitwise comparison of each coordinate's label with k, the pivot is a
// binary search down the planes for the set coordinate of largest L, and the
// sign is that coordinate of fromPolynomial(y), by linearity the parity the
// row would give.  That is popcounts, ands and compares, the same on every
// lane, so the selection runs here N lanes at a time with no lookup but the
// 132 entries of HW^-1, from registers where there are byte permutes and by
// one gather where there are not.

// The sum of the eight bytes of each element.
template <int N> F131X_INLINE typename Limbs<N>::V byteSum(typename Limbs<N>::V v);
template <> F131X_INLINE Limbs<2>::V byteSum<2>(Limbs<2>::V v)
{
    return (Limbs<2>::V)_mm_sad_epu8((__m128i)v, _mm_setzero_si128());
}
#    if ECC_F131_LANES >= 4
template <> F131X_INLINE Limbs<4>::V byteSum<4>(Limbs<4>::V v)
{
    return (Limbs<4>::V)_mm256_sad_epu8((__m256i)v, _mm256_setzero_si256());
}
#    endif
#    if ECC_F131_LANES >= 8
template <> F131X_INLINE Limbs<8>::V byteSum<8>(Limbs<8>::V v)
{
    return (Limbs<8>::V)_mm512_sad_epu8((__m512i)v, _mm512_setzero_si512());
}
#    endif

// Population count of each 64-bit element: the instruction where there is
// one (AVX-512 VPOPCNTDQ), else the bit-sliced count.
template <int N> F131X_INLINE typename Limbs<N>::V popcnt(typename Limbs<N>::V v)
{
    v = v - ((v >> 1) & F131x<N>::splat(0x5555555555555555ull));
    v = (v & F131x<N>::splat(0x3333333333333333ull)) +
        ((v >> 2) & F131x<N>::splat(0x3333333333333333ull));
    v = (v + (v >> 4)) & F131x<N>::splat(0x0f0f0f0f0f0f0f0full);
    return byteSum<N>(v);
}
#    if defined(__AVX512VPOPCNTDQ__)
#        if ECC_F131_LANES >= 8
template <> F131X_INLINE Limbs<8>::V popcnt<8>(Limbs<8>::V v)
{
    return (Limbs<8>::V)_mm512_popcnt_epi64((__m512i)v);
}
#        endif
#        if defined(__AVX512VL__)
#            if ECC_F131_LANES >= 4
template <> F131X_INLINE Limbs<4>::V popcnt<4>(Limbs<4>::V v)
{
    return (Limbs<4>::V)_mm256_popcnt_epi64((__m256i)v);
}
#            endif
template <> F131X_INLINE Limbs<2>::V popcnt<2>(Limbs<2>::V v)
{
    return (Limbs<2>::V)_mm_popcnt_epi64((__m128i)v);
}
#        endif
#    endif

// The low 32 bits of each element of a times those of b, as a 64-bit product.
template <int N>
F131X_INLINE typename Limbs<N>::V mul32(typename Limbs<N>::V a, typename Limbs<N>::V b);
template <> F131X_INLINE Limbs<2>::V mul32<2>(Limbs<2>::V a, Limbs<2>::V b)
{
    return (Limbs<2>::V)_mm_mul_epu32((__m128i)a, (__m128i)b);
}
#    if ECC_F131_LANES >= 4
template <> F131X_INLINE Limbs<4>::V mul32<4>(Limbs<4>::V a, Limbs<4>::V b)
{
    return (Limbs<4>::V)_mm256_mul_epu32((__m256i)a, (__m256i)b);
}
#    endif
#    if ECC_F131_LANES >= 8
template <> F131X_INLINE Limbs<8>::V mul32<8>(Limbs<8>::V a, Limbs<8>::V b)
{
    return (Limbs<8>::V)_mm512_mul_epu32((__m512i)a, (__m512i)b);
}
#    endif

// t[idx[i]] for each element, t a table of 32-bit words.
template <int N>
F131X_INLINE typename Limbs<N>::V gather32(const uint32_t *t, typename Limbs<N>::V idx);
template <> F131X_INLINE Limbs<2>::V gather32<2>(const uint32_t *t, Limbs<2>::V idx)
{
    const Limbs<2>::V r = {t[idx[0]], t[idx[1]]};
    return r;
}
#    if ECC_F131_LANES >= 4
template <> F131X_INLINE Limbs<4>::V gather32<4>(const uint32_t *t, Limbs<4>::V idx)
{
    return (Limbs<4>::V)_mm256_cvtepu32_epi64(
        _mm256_i64gather_epi32(reinterpret_cast<const int *>(t), (__m256i)idx, 4));
}
#    endif
#    if ECC_F131_LANES >= 8
template <> F131X_INLINE Limbs<8>::V gather32<8>(const uint32_t *t, Limbs<8>::V idx)
{
    return (Limbs<8>::V)_mm512_cvtepu32_epi64(_mm512_i64gather_epi32((__m512i)idx, t, 4));
}
#    endif

// The 64 bits at t + idx[i] for each element (f131::load64 of two words).
template <int N>
F131X_INLINE typename Limbs<N>::V gather64(const uint32_t *t, typename Limbs<N>::V idx);
template <> F131X_INLINE Limbs<2>::V gather64<2>(const uint32_t *t, Limbs<2>::V idx)
{
    const Limbs<2>::V r = {f131::load64(t + idx[0]), f131::load64(t + idx[1])};
    return r;
}
#    if ECC_F131_LANES >= 4
template <> F131X_INLINE Limbs<4>::V gather64<4>(const uint32_t *t, Limbs<4>::V idx)
{
    return (Limbs<4>::V)_mm256_i64gather_epi64(reinterpret_cast<const long long *>(t), (__m256i)idx,
                                               4);
}
#    endif
#    if ECC_F131_LANES >= 8
template <> F131X_INLINE Limbs<8>::V gather64<8>(const uint32_t *t, Limbs<8>::V idx)
{
    return (Limbs<8>::V)_mm512_i64gather_epi64((__m512i)idx, t, 4);
}
#    endif

// p mod 131 for p below 2^16: the quotient by a reciprocal multiply (64036 =
// ceil(2^23 / 131); the error 108 p stays below 2^23 for p < 77672).
template <int N> F131X_INLINE typename Limbs<N>::V mod131(typename Limbs<N>::V p)
{
    typedef typename Limbs<N>::V V;
    const V q = mul32<N>(p, F131x<N>::splat(64036u)) >> 23;
    return p - ((q << 7) + (q << 1) + q);
}

// The low byte of each element, to out[0..N).
template <int N> F131X_INLINE void storeBytes(typename Limbs<N>::V v, unsigned char *out)
{
    for (int i = 0; i < N; ++i) out[i] = (unsigned char)v[i];
}

// The selection's constants in the form the vector stages read: the eight bit
// planes of L, limb by limb, from the shared constant buffer's L^-1 table;
// for the 3-bit limb, whose eight values are fewer than its planes, a table
// of its phase sum (low half) and weight (high half); and the HW^-1 table in
// place.
struct SelectConsts {
    uint64_t plane[8][3];
    // The planes again, each broadcast to a vector's width: loaded as vectors
    // they are plain loads, where a broadcast of the scalar goes through a
    // general register and port 5 (gcc 13 does that for all 24 of them, with
    // the complement taken in the scalar, and that is a quarter of the
    // selection's port-5 work).
    alignas(64) uint64_t planeV[8][3][8];
    uint64_t top[8];
    const uint32_t *inv;
    alignas(64) uint8_t inv8[192]; // the same 132 entries as bytes, for the byte permutes

    // The nibble tables of the 512-bit path (selectTables), where a byte
    // permute looks 128 entries up at once: entry (p << 4 | v) is about
    // nibble p of a limb when its value is v, for the two full limbs and
    // the low and high nibble of each byte -- the phase sum of its set
    // coordinates mod 131, and 1 + the largest L among them (0 for v = 0).
    // maxTop is the latter for the 3-bit limb, by its value.  maskLt[l][k]
    // is limb l of {e : L(e) < k}; rowL[l][b] is limb l of the row of the
    // polynomial -> normal basis map that gives coordinate L^-1(b - 1), so
    // that the sign is one dot product with the pivot's row instead of the
    // whole conversion, indexed by 1 + the pivot's L as the search leaves
    // it.  The two full limbs of each are read by gathers, a limb an array
    // so that the index is the value itself; the 3-bit limb is a byte, and
    // 132 bytes are a register permute over the first 128 and a 64-byte
    // permute over the rest, blended by bit 7 of the index -- a gather is
    // eight loads, and the selection's loads outnumber its arithmetic.
    alignas(64) uint8_t phaseNib[2][2][128];
    alignas(64) uint8_t maxNib[2][2][128];
    alignas(64) uint64_t maxTop[8];
    alignas(64) uint64_t maskLt[2][136];
    alignas(64) uint64_t rowL[2][136];
    alignas(64) uint8_t maskLt2[192]; // limb 2 of maskLt[k]: [0, 128) then [128, 132) at 128
    alignas(64) uint8_t rowL2[192];   // limb 2 of rowL[b], the same way

    void build(const uint32_t *tw)
    {
        using namespace eccPacked131;
        const uint8_t *linv = reinterpret_cast<const uint8_t *>(tw) + 4 * TW_LINV_OFF;
        int L[131];
        for (int l = 0; l < 131; ++l) L[linv[l]] = l;
        memset(plane, 0, sizeof(plane));
        memset(top, 0, sizeof(top));
        for (int l = 0; l < 131; ++l) {
            const int e = linv[l];
            for (int j = 0; j < 8; ++j)
                if ((l >> j) & 1) plane[j][e >> 6] |= 1ull << (e & 63);
            if (e >= 128)
                for (int v = 0; v < 8; ++v)
                    if ((v >> (e - 128)) & 1) top[v] += uint64_t(l) + (1ull << 32);
        }
        for (int j = 0; j < 8; ++j)
            for (int l = 0; l < 3; ++l)
                for (int i = 0; i < 8; ++i) planeV[j][l][i] = plane[j][l];
        inv = tw + TW_INV_OFF;
        memset(inv8, 0, sizeof(inv8));
        for (int w = 0; w < 132; ++w) inv8[w] = uint8_t(inv[w]);

        for (int limb = 0; limb < 2; ++limb)
            for (int half = 0; half < 2; ++half)
                for (int p = 0; p < 8; ++p)
                    for (int v = 0; v < 16; ++v) {
                        unsigned s = 0;
                        int best = -1;
                        for (int t = 0; t < 4; ++t)
                            if ((v >> t) & 1) {
                                const int l = L[64 * limb + 8 * p + 4 * half + t];
                                s += unsigned(l);
                                if (l > best) best = l;
                            }
                        phaseNib[limb][half][p << 4 | v] = uint8_t(s % 131u);
                        maxNib[limb][half][p << 4 | v] = uint8_t(best + 1);
                    }
        for (int v = 0; v < 8; ++v) {
            int best = -1;
            for (int t = 0; t < 3; ++t)
                if ((v >> t) & 1 && L[128 + t] > best) best = L[128 + t];
            maxTop[v] = uint64_t(best + 1);
        }
        memset(maskLt, 0, sizeof(maskLt));
        memset(maskLt2, 0, sizeof(maskLt2));
        for (int e = 0; e < 131; ++e)
            for (int k = L[e] + 1; k < 131; ++k) {
                if (e < 128)
                    maskLt[e >> 6][k] |= 1ull << (e & 63);
                else
                    maskLt2[k] |= uint8_t(1u << (e - 128));
            }
        // Row e of the conversion: bit e of the image of each unit vector.
        uint64_t rowE[131][3];
        memset(rowE, 0, sizeof(rowE));
        for (int j = 0; j < 131; ++j) {
            F131 unit = {{0, 0, 0}};
            unit.w[j >> 6] = 1ull << (j & 63);
            const F131 image = f131::fromPolynomial(unit);
            for (int e = 0; e < 131; ++e)
                if ((image.w[e >> 6] >> (e & 63)) & 1) rowE[e][j >> 6] |= 1ull << (j & 63);
        }
        memset(rowL, 0, sizeof(rowL));
        memset(rowL2, 0, sizeof(rowL2));
        for (int l = 0; l < 131; ++l) {
            for (int w = 0; w < 2; ++w) rowL[w][l + 1] = rowE[linv[l]][w];
            rowL2[l + 1] = uint8_t(rowE[linv[l]][2]);
        }
    }
};

// HW^-1 mod 131 of each element, HW in [0, 131].  A gather is the one memory
// operation in the selection and the slowest instruction in it (a dozen uops,
// on k's dependency chain), so where there are byte permutes the table is
// read from registers instead: the two-table permute covers entries 0 to 127
// by the low seven bits of the index, and a permute over a third table the
// four above, blended in where bit 7 is set.  The other seven bytes of each
// element are zero and read entry 0, which the mask drops.
template <int N>
F131X_INLINE typename Limbs<N>::V inverseWeight(const SelectConsts &c, typename Limbs<N>::V hw)
{
    typedef typename Limbs<N>::V V;
    typedef typename Limbs<N>::M M;
#    if F131X_VBMI
    if constexpr (N == 8) {
        const M lo = _mm512_permutex2var_epi8(_mm512_load_si512((const void *)c.inv8), (M)hw,
                                              _mm512_load_si512((const void *)(c.inv8 + 64)));
        const M hi =
            _mm512_permutexvar_epi8((M)hw, _mm512_load_si512((const void *)(c.inv8 + 128)));
        const M r = _mm512_mask_blend_epi8(_mm512_movepi8_mask((M)hw), lo, hi);
        return (V)r & F131x<N>::splat(0xffu);
    }
#    endif
    return gather32<N>(c.inv, hw);
}

// HW(x), the phase k and the sign eps of N lanes -- f131::weight, selectPhase
// and coordinate(y, selectPivot(x, k)) -- from x in the normal basis and y in
// the polynomial basis; G vectors at once, statement by statement.
//
// One vector's selection is some 420 instructions on a dependency chain of
// a hundred cycles or so: the phase sum, k, eight planes of the comparison
// and eight of the search, each waiting on the one before.  A core whose
// reorder window is about that many instructions overlaps little of the
// next vector's chain with this one's, so vector by vector the selection
// runs at the chain's pace and not the ports'.  Interleaving G vectors in
// the instruction stream puts G chains inside the window: measured on one
// Sapphire Rapids core, two vectors took 8% off the time a lane and four
// 15%; eight spilled registers and gave some of it back.  The same on an
// AVX2 build with its sixteen registers, four best.  The table form below
// holds fewer values live and waits on gathers instead, and takes eight:
// 3% under four, and sixteen spills (8% over eight).  With the chains'
// forward pass inside the loop (cpuwalk.h) four and eight measure the
// same and sixteen 4% slower; ECC_F131_SELECT_GROUP overrides.
#    ifdef ECC_F131_SELECT_GROUP
static const int kSelectGroup = ECC_F131_SELECT_GROUP;
#    elif F131X_VBMI && ECC_F131_LANES >= 8
static const int kSelectGroup = 8;
#    else
static const int kSelectGroup = 4;
#    endif

#    if F131X_VBMI && ECC_F131_LANES >= 8
// t[idx[i]] for each element, t a table of 64-bit words.
F131X_INLINE Limbs<8>::V gatherQ(const uint64_t *t, Limbs<8>::V idx)
{
    return (Limbs<8>::V)_mm512_i64gather_epi64((__m512i)idx, reinterpret_cast<const long long *>(t),
                                               8);
}

// The 128-entry byte table t[0..127] at the low seven bits of each byte of
// idx.
F131X_INLINE Limbs<8>::V permute128(const uint8_t *t, Limbs<8>::V idx)
{
    return (Limbs<8>::V)_mm512_permutex2var_epi8(_mm512_load_si512((const void *)t), (__m512i)idx,
                                                 _mm512_load_si512((const void *)(t + 64)));
}

// A byte table of up to 192 entries at each element of idx, idx below 192
// with t[0] = 0: the 128-entry permute by the low seven bits, a 64-entry
// one over t + 128 by the low six, the latter where bit 7 of the byte is
// set.  The element's seven high bytes are zero and read t[0].
F131X_INLINE Limbs<8>::V permute192(const uint8_t *t, Limbs<8>::V idx)
{
    const __m512i lo = (__m512i)permute128(t, idx);
    const __m512i hi =
        _mm512_permutexvar_epi8((__m512i)idx, _mm512_load_si512((const void *)(t + 128)));
    return (Limbs<8>::V)_mm512_mask_blend_epi8(_mm512_movepi8_mask((__m512i)idx), lo, hi);
}

// The selection of eight lanes by table, the form packedtablewalk.cuh gives
// the device, where the bit-plane form below costs some 250 instructions a
// vector on the two ports that take 512-bit work:
//
//   - the phase sum from the nibble tables: each byte of a limb, low and
//     high nibble apart, indexes a 128-entry permute by (position, value)
//     and reads its coordinates' L mod 131; a byte sum per lookup (16
//     masked popcounts before, 4 permutes and 4 sums now);
//   - k in one multiply and one reduction, s * HW^-1 being below 2^19 and
//     the 32-bit reciprocal exact to 2^25 (two reductions before);
//   - the mask {L < k} gathered from its 131 rows, a limb a gather (the
//     comparator ran on 8 planes, 5 instructions a plane a limb);
//   - the pivot by the same nibble permute over 1 + max L, a byte max down
//     to one byte, and the 3-bit limb's eight values (eight rounds of mask
//     and blend before);
//   - the sign as the parity of y against the pivot's row of the conversion
//     map, a gather a limb and one popcount, where converting all of y to
//     read one bit was 40 instructions.
//
// A gather here is eight loads from a table in L1 and about three cycles
// on the vector ports, which is cheaper than any of the stages it stands
// in for.  Measured on one Sapphire Rapids core, the selection stage (with
// the tag and the addend, which did not change) went from 6.4 to 4.4 ns a
// lane, the step from 17.2 to 15.2.  The 3-bit limbs of the mask and the
// row are bytes, and read by register permutes (permute192) rather than
// gathers: with eleven gathers a vector between this and the addend, the
// stage issues more loads than vector instructions.
template <int G>
F131X_INLINE void selectTables(const F131x<8> *xn, const F131x<8> *yp, const SelectConsts &c,
                               Limbs<8>::V *hwOut, Limbs<8>::V *kOut, Limbs<8>::V *epsOut)
{
    typedef Limbs<8>::V V;
    typedef Limbs<8>::M M;
#        define F131X_C(x) F131x<8>::splat(x)
    // Byte p of a limb indexes entries p << 4 | v: the position in bits 4
    // to 6, the nibble below.
    const V pos = F131X_C(0x7060504030201000ull), nib = F131X_C(0x0f0f0f0f0f0f0f0full);
    V top[G], hw[G], s[G], k[G];
    V i00[G], i01[G], i10[G], i11[G]; // limb, half
    for (int g = 0; g < G; ++g) {
        i00[g] = (xn[g].w0 & nib) | pos;
        i01[g] = ((xn[g].w0 >> 4) & nib) | pos;
        i10[g] = (xn[g].w1 & nib) | pos;
        i11[g] = ((xn[g].w1 >> 4) & nib) | pos;
    }
    for (int g = 0; g < G; ++g) top[g] = lookup8<8>(c.top, xn[g].w2);
    for (int g = 0; g < G; ++g) hw[g] = popcnt<8>(xn[g].w0) + popcnt<8>(xn[g].w1) + (top[g] >> 32);
    for (int g = 0; g < G; ++g)
        s[g] = (top[g] & F131X_C(0xffffffffu)) + byteSum<8>(permute128(c.phaseNib[0][0], i00[g])) +
               byteSum<8>(permute128(c.phaseNib[0][1], i01[g])) +
               byteSum<8>(permute128(c.phaseNib[1][0], i10[g])) +
               byteSum<8>(permute128(c.phaseNib[1][1], i11[g]));
    // k = s * HW^-1 mod 131: s is below 32 * 130 + 3 * 130 and the inverse
    // below 131, so the product is below 2^20; the quotient by
    // ceil(2^32 / 131) is exact below 2^25.
    for (int g = 0; g < G; ++g) {
        const V p = mul32<8>(s[g], inverseWeight<8>(c, hw[g]));
        const V q = mul32<8>(p, F131X_C(32786010u)) >> 32;
        k[g] = p - mul32<8>(q, F131X_C(131u));
    }
    // The candidates: the set coordinates with L < k, else all of them.
    V p0[G], p1[G], p2[G];
    for (int g = 0; g < G; ++g) {
        const V m0 = gatherQ(c.maskLt[0], k[g]), m1 = gatherQ(c.maskLt[1], k[g]),
                m2 = permute192(c.maskLt2, k[g]);
        p0[g] = xn[g].w0 & m0, p1[g] = xn[g].w1 & m1, p2[g] = xn[g].w2 & m2;
        const M any3 = (M)(p0[g] | p1[g] | p2[g]);
        const __mmask8 any = _mm512_test_epi64_mask(any3, any3);
        p0[g] = (V)_mm512_mask_blend_epi64(any, (M)xn[g].w0, (M)p0[g]);
        p1[g] = (V)_mm512_mask_blend_epi64(any, (M)xn[g].w1, (M)p1[g]);
        p2[g] = (V)_mm512_mask_blend_epi64(any, (M)xn[g].w2, (M)p2[g]);
    }
    // 1 + the largest L among them, the byte max of the nibble lookups.
    V best[G];
    for (int g = 0; g < G; ++g) {
        const M a = _mm512_max_epu8((M)permute128(c.maxNib[0][0], (p0[g] & nib) | pos),
                                    (M)permute128(c.maxNib[0][1], ((p0[g] >> 4) & nib) | pos));
        const M b = _mm512_max_epu8((M)permute128(c.maxNib[1][0], (p1[g] & nib) | pos),
                                    (M)permute128(c.maxNib[1][1], ((p1[g] >> 4) & nib) | pos));
        M m = _mm512_max_epu8(a, b);
        m = _mm512_max_epu8(m, _mm512_srli_epi64(m, 32));
        m = _mm512_max_epu8(m, _mm512_srli_epi64(m, 16));
        m = _mm512_max_epu8(m, _mm512_srli_epi64(m, 8));
        best[g] = (V)_mm512_max_epu64((M)((V)m & F131X_C(0xffu)), (M)lookup8<8>(c.maxTop, p2[g]));
    }
    // eps: coordinate L^-1(best - 1) of y in the normal basis, the parity of
    // y against that row of the conversion (rowL is indexed by best itself).
    for (int g = 0; g < G; ++g) {
        const V r0 = gatherQ(c.rowL[0], best[g]), r1 = gatherQ(c.rowL[1], best[g]),
                r2 = permute192(c.rowL2, best[g]);
        epsOut[g] = popcnt<8>((yp[g].w0 & r0) ^ (yp[g].w1 & r1) ^ (yp[g].w2 & r2)) & F131X_C(1u);
        hwOut[g] = hw[g];
        kOut[g] = k[g];
    }
#        undef F131X_C
}
#    endif

template <int N, int G>
F131X_INLINE void select(const F131x<N> *xn, const F131x<N> *yp, const SelectConsts &c,
                         typename Limbs<N>::V *hwOut, typename Limbs<N>::V *kOut,
                         typename Limbs<N>::V *epsOut)
{
    typedef typename Limbs<N>::V V;
#    if F131X_VBMI && ECC_F131_LANES >= 8
    if constexpr (N == 8) {
        selectTables<G>(xn, yp, c, hwOut, kOut, epsOut);
        return;
    }
#    endif
#    define F131X_C(x)    F131x<N>::splat(x)
#    define F131X_P(j, l) (*reinterpret_cast<const V *>(c.planeV[j][l]))
    // The 3-bit limb's share of the weight and of the phase sum, from its table.
    V top[G], hw[G], s[G], k[G];
    for (int g = 0; g < G; ++g) top[g] = lookup8<N>(c.top, xn[g].w2);
    for (int g = 0; g < G; ++g) hw[g] = popcnt<N>(xn[g].w0) + popcnt<N>(xn[g].w1) + (top[g] >> 32);
    // The phase sum, sum_e L(e) x_e = sum_j 2^j |x & plane_j|, below 131^2.
    for (int g = 0; g < G; ++g) s[g] = top[g] & F131X_C(0xffffffffu);
    for (int j = 0; j < 8; ++j)
        for (int g = 0; g < G; ++g)
            s[g] += (popcnt<N>(xn[g].w0 & F131X_P(j, 0)) + popcnt<N>(xn[g].w1 & F131X_P(j, 1)))
                    << j;
    for (int g = 0; g < G; ++g)
        k[g] = mod131<N>(mul32<N>(mod131<N>(s[g]), inverseWeight<N>(c, hw[g])));
    // The pivot's mask, the coordinates with L(e) < k: a bitwise comparison
    // of every coordinate's label with k, least significant bit first.  At
    // each bit, L < k so far if bit j of k is set and of L clear, or the two
    // agree and L < k already: the majority of (k_j, not L_j, so far), one
    // three-input logic instruction per limb.
    typedef typename Limbs<N>::S S;
    V lt0[G], lt1[G], lt2[G];
    for (int g = 0; g < G; ++g) lt0[g] = lt1[g] = lt2[g] = V{};
    for (int j = 0; j < 8; ++j) {
        const V n0 = ~F131X_P(j, 0), n1 = ~F131X_P(j, 1), n2 = ~F131X_P(j, 2);
        for (int g = 0; g < G; ++g) {
            const V kj = (V)((S)(k[g] << (63 - j)) >> 63);
            lt0[g] = (kj & n0) | (kj & lt0[g]) | (n0 & lt0[g]);
            lt1[g] = (kj & n1) | (kj & lt1[g]) | (n1 & lt1[g]);
            lt2[g] = (kj & n2) | (kj & lt2[g]) | (n2 & lt2[g]);
        }
    }
    V p0[G], p1[G], p2[G];
    for (int g = 0; g < G; ++g) {
        p0[g] = xn[g].w0 & lt0[g], p1[g] = xn[g].w1 & lt1[g], p2[g] = xn[g].w2 & lt2[g];
        const V any = (V)((p0[g] | p1[g] | p2[g]) != V{});
        p0[g] = (p0[g] & any) | (xn[g].w0 & ~any);
        p1[g] = (p1[g] & any) | (xn[g].w1 & ~any);
        p2[g] = (p2[g] & any) | (xn[g].w2 & ~any);
    }
    // The set coordinate of largest L: down the planes, keep the candidates
    // with bit j of L set whenever there are any.  L is a bijection, so one
    // coordinate is left.
    for (int j = 7; j >= 0; --j)
        for (int g = 0; g < G; ++g) {
            const V t0 = p0[g] & F131X_P(j, 0), t1 = p1[g] & F131X_P(j, 1),
                    t2 = p2[g] & F131X_P(j, 2);
            const V nz = (V)((t0 | t1 | t2) != V{});
            p0[g] = (t0 & nz) | (p0[g] & ~nz);
            p1[g] = (t1 & nz) | (p1[g] & ~nz);
            p2[g] = (t2 & nz) | (p2[g] & ~nz);
        }
    // eps: that coordinate of y in the normal basis.
    for (int g = 0; g < G; ++g) {
        const F131x<N> yn = fromPolynomial<N>(yp[g]);
        epsOut[g] = (V)(((yn.w0 & p0[g]) | (yn.w1 & p1[g]) | (yn.w2 & p2[g])) != V{}) & F131X_C(1u);
        hwOut[g] = hw[g];
        kOut[g] = k[g];
    }
#    undef F131X_P
#    undef F131X_C
}

// One vector's selection.
template <int N>
F131X_INLINE void select(const F131x<N> &xn, const F131x<N> &yp, const SelectConsts &c,
                         typename Limbs<N>::V *hwOut, typename Limbs<N>::V *kOut,
                         typename Limbs<N>::V *epsOut)
{
    select<N, 1>(&xn, &yp, c, hwOut, kOut, epsOut);
}

// Whether any element of v is nonzero.
template <int N> F131X_INLINE bool anySet(typename Limbs<N>::V v)
{
    typedef typename Limbs<N>::M M;
    if constexpr (N == 8) return _mm512_test_epi64_mask((M)v, (M)v) != 0;
    if constexpr (N == 4) return !_mm256_testz_si256((M)v, (M)v);
    if constexpr (N == 2) return !_mm_testz_si128((M)v, (M)v);
}

// The low 32 bits of each element, to out[0..N).
template <int N> F131X_INLINE void storeWords(typename Limbs<N>::V v, uint32_t *out)
{
    for (int i = 0; i < N; ++i) out[i] = (uint32_t)v[i];
}

// f131::tagOf over N lanes: the tag from the weight, the phase and the sign,
// the cycle rule against each lane's history, the histories advanced.  The
// rule is a compare against three slots of the history, so a lane whose tag
// is fruitless picks the next branch and every lane is tested again; a lane
// that passed once passes again, the test being a function of the tag and
// the old history alone.  Lane by lane this was a fifth of the step's
// scalar work for a dozen instructions a lane.
template <int N>
F131X_INLINE typename Limbs<N>::V tags(typename Limbs<N>::V hw, typename Limbs<N>::V k,
                                       typename Limbs<N>::V eps, unsigned long long *hist)
{
    typedef typename Limbs<N>::V V;
#    define F131X_C(x) F131x<N>::splat(x)
    V old;
    memcpy(&old, hist, sizeof(old));
    const V rest = (k << 4) | (eps << 12);
    V h = (hw >> 1) & F131X_C(eccPacked131::TW_H - 1);
    V tag = h | rest;
    const V t1 = old & F131X_C(0xffffu), t2 = (old >> 16) & F131X_C(0xffffu),
            t3 = (old >> 32) & F131X_C(0xffffu);
    const V undo13 = (V)((t1 ^ t3) == F131X_C(ECC_TAG_EPS));
    for (;;) {
        const V m = (V)((tag ^ t1) == F131X_C(ECC_TAG_EPS)) |
                    ((V)((tag ^ t2) == F131X_C(ECC_TAG_EPS)) & undo13);
        if (__builtin_expect(!anySet<N>(m), 1)) break;
        h = (h + (m & F131X_C(1u))) & F131X_C(eccPacked131::TW_H - 1);
        tag = h | rest;
    }
    const V pushed = (old << 16) | tag;
    memcpy(hist, &pushed, sizeof(pushed));
    return tag;
#    undef F131X_C
}

// f131::addend over N lanes: d = x + x_T and e = y + y_T (+ x_T when the
// table point is negated), the table points from the shared constant buffer
// -- the two limb pairs of x_T and y_T as 64-bit words, and the word that
// holds the top limbs of four entries.  No two lanes read the same entry,
// and the table is 35 KB, in L1 beside the batch's lines.  The four full
// limbs were a gather each, eight loads apiece; on the 512-bit path an
// entry's 32 bytes are one 256-bit load, so the eight lanes' entries are
// eight loads into four registers and a 4 x 8 transpose of qwords (four
// unpacks, four two-source permutes), with the top word still gathered.
// Measured on one Sapphire Rapids core, the tag and addend 8% faster: the
// stage's gathers had it issuing more loads than vector instructions.
template <int N>
F131X_INLINE void addend(typename Limbs<N>::V tag, const F131x<N> &xp, const F131x<N> &yp,
                         const uint32_t *tw, F131x<N> *d, F131x<N> *e)
{
    typedef typename Limbs<N>::V V;
#    define F131X_C(x) F131x<N>::splat(x)
    using namespace eccPacked131;
    const V h = tag & F131X_C(15u), k = (tag >> 4) & F131X_C(255u);
    const V kbase = mul32<N>(k, F131X_C(unsigned(TW_KWORDS)));
    const V t = kbase + (h << 3); // h * TW_ENTRY, eight words an entry
    // A word offset within the entry goes on the table pointer, where it is
    // the gather's displacement, rather than on the index, where it is an
    // add of a constant a gather (four instructions a vector; the gathers
    // set the pace here and the time did not move).
    const V top = gather32<N>(tw + TW_H * TW_ENTRY, kbase + (h >> 2)) >> ((h & F131X_C(3u)) << 3) &
                  F131X_C(63u);
    const V neg = V{} - ((tag >> 12) & F131X_C(1u));
    V tx0, tx1, ty0, ty1;
#    if ECC_F131_ADDEND_ROWS
    if constexpr (N == 8) {
        // Lane i's entry is the 32 bytes at word t[i]: x0 x1 y0 y1.  Two
        // entries a register, z[i] holding lanes 2i and 2i + 1; the lane
        // indices go through memory, since eight extracts would be eight
        // more shuffles.
        typedef Limbs<8>::M M;
        alignas(64) uint64_t ti[8];
        memcpy(ti, &t, sizeof(ti));
        M z[4];
        for (int i = 0; i < 4; ++i)
            z[i] = _mm512_inserti64x4(
                _mm512_castsi256_si512(_mm256_loadu_si256((const __m256i *)(tw + ti[2 * i]))),
                _mm256_loadu_si256((const __m256i *)(tw + ti[2 * i + 1])), 1);
        // The unpacks pair lanes two apart: a01 is x0 of lanes 0 and 2, y0
        // of 0 and 2, x0 of 1 and 3, y0 of 1 and 3, and b01 the same of x1
        // and y1; the permutes then pick each limb's eight lanes in order
        // from a pair of those.
        const M a01 = _mm512_unpacklo_epi64(z[0], z[1]), b01 = _mm512_unpackhi_epi64(z[0], z[1]),
                a23 = _mm512_unpacklo_epi64(z[2], z[3]), b23 = _mm512_unpackhi_epi64(z[2], z[3]);
        const M ix = _mm512_set_epi64(13, 9, 12, 8, 5, 1, 4, 0),
                iy = _mm512_set_epi64(15, 11, 14, 10, 7, 3, 6, 2);
        tx0 = (V)_mm512_permutex2var_epi64(a01, ix, a23);
        ty0 = (V)_mm512_permutex2var_epi64(a01, iy, a23);
        tx1 = (V)_mm512_permutex2var_epi64(b01, ix, b23);
        ty1 = (V)_mm512_permutex2var_epi64(b01, iy, b23);
    } else
#    endif
    {
        tx0 = gather64<N>(tw, t), tx1 = gather64<N>(tw + 2, t);
        ty0 = gather64<N>(tw + 4, t), ty1 = gather64<N>(tw + 6, t);
    }
    const V tx2 = top & F131X_C(7u);
    d->w0 = xp.w0 ^ tx0;
    d->w1 = xp.w1 ^ tx1;
    d->w2 = xp.w2 ^ tx2;
    e->w0 = yp.w0 ^ ty0 ^ (tx0 & neg);
    e->w1 = yp.w1 ^ ty1 ^ (tx1 & neg);
    e->w2 = yp.w2 ^ (top >> 3) ^ (tx2 & neg);
#    undef F131X_C
}

#    undef F131X_INLINE

} // namespace f131x

#endif // ECC_F131_LANES > 1
