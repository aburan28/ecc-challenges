// The ECC2K-130 iteration function, bitsliced.
//
//     R_{i+1} = sigma^j(R_i) + R_i,     j = 3 + ((HW(x_{R_i}) / 2) mod 8)
//
// HW is the Hamming weight of the x coordinate in normal-basis representation,
// which is invariant under both sigma and negation, so the iteration is well
// defined on the orbits of size 2m and no canonical representative is ever
// needed inside the loop.
//
// Each thread owns BATCH slots; a slot is W walks in the bit lanes of one word.
// One iteration is two passes over the batch:
//
//   pass 1  weight, distinguished-point test, sigma^j(x), and the running
//           product of the addition denominators (Montgomery's trick)
//   pass 2  one inversion for the whole batch, then the affine additions
//
// Pass 2 recomputes sigma^j(x) and sigma^j(y) from the three stored weight bits
// rather than reading stored denominators back. This trades 786 instructions
// for 1048 bytes of traffic per slot; profile the memory hierarchy to assess
// that trade on the target GPU.
#pragma once

#include "fieldbs.h"
#include "fieldpb.h"

// How many sigma^j branches the iteration chooses between, and how wide a
// per-branch step counter is.  ECC_WITNESS=0 compiles the counting out, which
// is the control the paired comparison in CAIRN-WITNESS.md asks for.
#define ECC_JCOUNT 8
// Default to carrying the witness -- except on the table walk, which cannot
// produce one (see below).  Defaulting rather than erroring matters because
// several build recipes select the table walk and say nothing about the
// witness; they want a table-walk binary, not a diagnostic.  An explicit
// ECC_WITNESS=1 alongside the table walk is a different thing and still fails.
#ifndef ECC_WITNESS
#if (defined(ECC_WALK_TABLE) && ECC_WALK_TABLE) || (defined(ECC_PACKED_XONLY_23) && ECC_PACKED_XONLY_23)
#define ECC_WITNESS 0
#else
#define ECC_WITNESS 1
#endif
#endif
#ifndef ECC_COUNT_BITS
#define ECC_COUNT_BITS 32
#endif
// The witness is a factorisation of THIS iteration function.  The table walk
// of ITERATION-FUNCTION.md picks its addend from a table instead of computing
// sigma^j(R), so a trail there is not [prod_j (1 + s^j)^{n_j}]R_0 and eight
// counters say nothing about it.  Refuse the combination rather than emit
// records whose counts are meaningless, or -- worse -- uninitialised, since
// that path's report sites never fill them.
#if ECC_WITNESS && defined(ECC_WALK_TABLE) && ECC_WALK_TABLE
#error "WITNESS=1 needs the sigma^j + 1 iteration: the table walk has no (1 + s^j)^{n_j} factorisation. Build with WITNESS=0 or WALK_TABLE=0."
#endif
// The x-only walk's kernel never fills the eight counters either.
#if ECC_WITNESS && defined(ECC_PACKED_XONLY_23) && ECC_PACKED_XONLY_23
#error "WITNESS=1 needs the sigma^j + 1 iteration: the x-only walk does not carry the eight counters. Build with WITNESS=0 or PACKED_XONLY_23=0."
#endif

// Counters are walk state, so a checkpoint written with them cannot be read by
// a build without them and the version has to say so.  With ECC_WITNESS=0 the
// array is not allocated at all and the format is byte-identical to what
// shipped, which is what makes the control in CAIRN-WITNESS.md a control.
//
// 16 and not 1: the versions in use are 1 (bitsliced), 2 (packed) and 3 (the
// packed table walk), so a bump of one would have put the witnessed packed
// format on 3 and given two different layouts the same number -- which is the
// one thing a format version exists to prevent.  A wide bump also leaves the
// small numbers free for whatever is added next.
#define ECC_CKPT_BUMP (ECC_WITNESS ? 16u : 0u)

struct DpRecord {
    unsigned long long seed;
    unsigned long long iters;
    unsigned long long x[3];
    unsigned long long y[3];
    // The witness: steps this trail took on each branch.  A step is
    // R -> R + sigma^j(R) = [1 + s^j]R and the endomorphism ring commutes, so
    // the whole trail collapses to [mu]R_0 with mu = prod_j (1 + s^j)^{n_j}
    // whatever order the steps came in -- eight numbers, and the point becomes
    // checkable with one double scalar multiplication instead of the 2^25.27
    // steps that made it.  CAIRN-WITNESS.md is the design.
    //
    // 32 bits, and nothing on the hot path checks for overflow because nothing
    // has to: the counts sum to `iters`, which is 64 bits, so a wrapped
    // counter makes the sum disagree and the record says so about itself.
    // ECC_WITNESS=0 leaves them zero, which fails the same test, so there is
    // no separate validity flag to keep in step with anything.
    unsigned counts[ECC_JCOUNT];
};
// Reserved host fetch result. No valid launch may report this many points.
static constexpr unsigned ECC_SEED_EXHAUSTED = ~0u;

// Scalar per-walk counter layout, for the backends where one worker is one
// walk rather than one lane of a bitsliced word.  Thread innermost, so a
// warp's counters are contiguous, exactly as the coordinate arrays are.
ECC_HD size_t eccScalarCountIndex(int slot, int k, int tid, int threads) {
    return ((size_t)(slot * ECC_JCOUNT + k) * (size_t)threads) + (size_t)tid;
}

ECC_HD size_t eccScalarCountWords(int threads, int batch) {
#if ECC_WITNESS
    return (size_t)threads * (size_t)batch * ECC_JCOUNT;
#else
    (void)threads;
    (void)batch;
    return 0;
#endif
}

ECC_HD unsigned long long eccPrf(unsigned long long seed, int idx) {
    unsigned long long z = seed + 0x9E3779B97F4A7C15ull * (unsigned long long)(idx + 1);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

// walk index -> initial seed;  a walk that finishes just increments its seed
ECC_HD unsigned long long eccSeedFor(unsigned runId, unsigned long long walkIndex) {
    return ((unsigned long long)runId << 48) | ((walkIndex & 0xFFFFFFFFull) << 16);
}

// Curve constants live in device memory and are passed in, so the generated
// tables need no __constant__ plumbing.
struct CurveConsts {
    const unsigned long long *px;
    const unsigned long long *py;
    const unsigned long long *qx;
    const unsigned long long *qy;
};

template <class Cfg, class W>
struct Walk {
    typedef typename Cfg::template Field<W> F;
    static const int M = Cfg::M;
    static const int HWBITS = Cfg::HWBITS;
    static const int LANES = WordTraits<W>::LANES;
    static const int STARTTERMS = 128;

    // ---- weight and distinguished-point test ---------------------------
    static ECC_HD void hamming(const W *x, W *hb) { Cfg::hamming(x, hb); }

    // Mask of the lanes whose weight is at most w.  Scanning from the top,
    // `eq` stays set while the weight matches w bit for bit and `gt` latches as
    // soon as the weight is decided larger; the answer is ~gt.
    static ECC_HD W dpMask(const W *hb, int w) {
        W gt = ECC_ZERO;
        W eq = ~ECC_ZERO;
        for (int i = HWBITS - 1; i >= 0; --i) {
            const W h = hb[i];
            if ((w >> i) & 1) {
                eq = eq & h;
            } else {
                gt = gt | (eq & h);
                eq = eq & ~h;
            }
        }
        return ~gt;
    }

    // ---- sigma^j, j = 3 + b0 + 2 b1 + 4 b2, branch free ----------------
    static ECC_BIG void sigmaJ(const W *a, const W *hb, W *out) {
        W r[M], s[M];
        F::template sigma<3>(a, r);
        F::template sigma<1>(r, s);
#pragma unroll
        for (int i = 0; i < M; ++i) r[i] = ECC_SEL(hb[1], s[i], r[i]);
        F::template sigma<2>(r, s);
#pragma unroll
        for (int i = 0; i < M; ++i) r[i] = ECC_SEL(hb[2], s[i], r[i]);
        F::template sigma<4>(r, s);
#pragma unroll
        for (int i = 0; i < M; ++i) out[i] = ECC_SEL(hb[3], s[i], r[i]);
    }

    // ---- one affine addition R = R + S (all lanes) ---------------------
    static ECC_BIG void pointAdd(const W *x1, const W *y1, const W *x2, const W *y2, W *x3, W *y3) {
        W d[M], e[M], di[M], lam[M], t[M];
        F::add(x1, x2, d);
        F::add(y1, y2, e);
        F::inv(d, di);
        F::mul(e, di, lam);
        F::sqr(lam, t);
#pragma unroll
        for (int i = 0; i < M; ++i) t[i] = ECC_XOR3(t[i], lam[i], d[i]);   // x3 = lam^2 + lam + d
        F::add(x1, t, d);                                                  // d = x1 + x3
        F::mul(lam, d, e);
#pragma unroll
        for (int i = 0; i < M; ++i) y3[i] = ECC_XOR3(e[i], t[i], y1[i]);
        F::copy(x3, t);
    }

    // ---- fresh starting point:  R = Q + sum_i c_i sigma^i(P) -----------
    // c is a 128-bit string derived from the walk seed, per lane.
    static ECC_BIG void startPoint(const unsigned long long *seeds, const CurveConsts &K, W *x, W *y) {
        const int NG = LANES / 64 > 0 ? LANES / 64 : 1;
        unsigned long long cw[STARTTERMS][(LANES / 64) > 0 ? (LANES / 64) : 1];
#pragma unroll 1
        for (int i = 0; i < STARTTERMS; ++i)
            for (int g = 0; g < NG; ++g) cw[i][g] = 0;
        for (int lane = 0; lane < LANES; ++lane) {
            const unsigned long long c0 = eccPrf(seeds[lane], 0);
            const unsigned long long c1 = eccPrf(seeds[lane], 1);
            const int g = (LANES >= 64) ? (lane >> 6) : 0;
            const int b = (LANES >= 64) ? (lane & 63) : lane;
            for (int i = 0; i < STARTTERMS; ++i) {
                const unsigned long long bit = (i < 64) ? (c0 >> i) : (c1 >> (i - 64));
                cw[i][g] |= (bit & 1ull) << b;
            }
        }
        W cbit[STARTTERMS];
#pragma unroll 1
        for (int i = 0; i < STARTTERMS; ++i) cbit[i] = eccWordFromLimbs<W>(cw[i]);
        F::broadcast(K.qx, x);
        F::broadcast(K.qy, y);
        W sx[M], sy[M], ax[M], ay[M];
        for (int i = 0; i < STARTTERMS; ++i) {
            F::broadcastSigma(K.px, i % M, sx);
            F::broadcastSigma(K.py, i % M, sy);
            pointAdd(x, y, sx, sy, ax, ay);
#pragma unroll
            for (int t = 0; t < M; ++t) {
                x[t] = ECC_SEL(cbit[i], ax[t], x[t]);
                y[t] = ECC_SEL(cbit[i], ay[t], y[t]);
            }
        }
    }

    // replace only the lanes selected by `mask` with fresh walks
    static ECC_BIG void reseedLanes(W mask, unsigned long long *seeds, const CurveConsts &K, W *x, W *y) {
        W nx[M], ny[M];
        startPoint(seeds, K, nx, ny);
#pragma unroll
        for (int i = 0; i < M; ++i) {
            x[i] = ECC_SEL(mask, nx[i], x[i]);
            y[i] = ECC_SEL(mask, ny[i], y[i]);
        }
    }
};
