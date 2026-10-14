// Bitsliced GF(2^m) arithmetic in a polynomial basis, F_2[z]/(F).
//
// Used for fields with no type-II optimal normal basis, where the normal-basis
// route of fieldbs.h does not apply.  ECC2K-95 is the case that matters:
// 2*97+1 = 195 is composite, so GF(2^97) has no such basis, and Harley's 1998
// client solved it exactly this way -- all arithmetic in a polynomial basis,
// with the orbit-invariant weight taken through one linear map into a normal
// basis (the generated `hamming` routine does both steps).
//
// A multiplication is the same Karatsuba tree as the normal-basis path, sharing
// the generated leaf, followed by reduction modulo the trinomial or
// pentanomial, which is a few hundred instructions.  Squaring is no longer
// free, but with a low-weight reduction polynomial it is very cheap: 51
// instructions for GF(2^97).
#pragma once

#include "bitslice.h"
#include "fieldbs.h"

template <class Cfg, class W>
struct FieldPb {
    static const int M = Cfg::M;
    static const int PRODLEN = 2 * Cfg::M - 1;
    static const int LANES = WordTraits<W>::LANES;

    static ECC_HD void setZero(W *r) {
#pragma unroll
        for (int i = 0; i < M; ++i) r[i] = ECC_ZERO;
    }
    static ECC_HD void copy(W *r, const W *a) {
#pragma unroll
        for (int i = 0; i < M; ++i) r[i] = a[i];
    }
    static ECC_HD void add(const W *a, const W *b, W *r) {
#pragma unroll
        for (int i = 0; i < M; ++i) r[i] = a[i] ^ b[i];
    }
    static ECC_HD void addTo(W *r, const W *a) {
#pragma unroll
        for (int i = 0; i < M; ++i) r[i] ^= a[i];
    }
    // one is the constant polynomial 1
    static ECC_HD void setOne(W *r) {
        setZero(r);
        r[0] = ~ECC_ZERO;
    }
    static ECC_HD bool isZeroLane(const W *a, int lane) {
        W acc = ECC_ZERO;
#pragma unroll
        for (int i = 0; i < M; ++i) acc |= a[i];
        return laneBit(acc, lane) == 0;
    }

    static ECC_HD void sqr(const W *a, W *r) { Cfg::sqr(a, r); }

    // sigma^K = K squarings; K is a compile-time constant on the hot path
    template <int K>
    static ECC_HD void sigma(const W *a, W *r) {
        if (K <= 0) {
            copy(r, a);
            return;
        }
        W buf[2][M];
        Cfg::sqr(a, buf[0]);
        int cur = 0;
#pragma unroll
        for (int i = 1; i < K; ++i) {
            Cfg::sqr(buf[cur], buf[cur ^ 1]);
            cur ^= 1;
        }
        copy(r, buf[cur]);
    }

    static ECC_HD void sigmaRun(const W *a, int k, W *r) {
        k %= M;
        if (k == 0) {
            copy(r, a);
            return;
        }
        W buf[2][M];
        Cfg::sqr(a, buf[0]);
        int cur = 0;
        for (int i = 1; i < k; ++i) {
            Cfg::sqr(buf[cur], buf[cur ^ 1]);
            cur ^= 1;
        }
        copy(r, buf[cur]);
    }

    static ECC_BIG void mul(const W *a, const W *b, W *r) {
        W h[PRODLEN];
        Karat<Cfg, W, Cfg::M>::mul(a, b, h);
        Cfg::reduce(h, r);
    }

    // Itoh-Tsujii.  Squarings are cheap rather than free here, so the chain
    // costs about m squarings on top of its 8-ish multiplications; that is
    // still a few percent once amortised over the inversion batch.
    static ECC_BIG void inv(const W *a, W *r) {
        W acc[M], t[M], u[M];
        copy(acc, a);
        int k = 1;
        const int e = M - 1;
        int hb = 0;
        while ((1 << (hb + 1)) <= e) ++hb;
        for (int bit = hb - 1; bit >= 0; --bit) {
            sigmaRun(acc, k, t);
            mul(t, acc, u);
            copy(acc, u);
            k <<= 1;
            if ((e >> bit) & 1) {
                sigmaRun(acc, 1, t);
                mul(t, a, u);
                copy(acc, u);
                k += 1;
            }
        }
        sigmaRun(acc, 1, r);
    }

    static ECC_HD void getLane(const W *a, int lane, unsigned long long *out3) {
        out3[0] = out3[1] = out3[2] = 0;
        for (int i = 0; i < M; ++i) {
            if (laneBit(a[i], lane)) out3[i >> 6] |= 1ull << (i & 63);
        }
    }
    static ECC_HD void setLane(W *a, int lane, const unsigned long long *in3) {
        const W m1 = laneMask<W>(lane);
        for (int i = 0; i < M; ++i) {
            const W bit = ((in3[i >> 6] >> (i & 63)) & 1ull) ? m1 : ECC_ZERO;
            a[i] = (a[i] & ~m1) | bit;
        }
    }
    static ECC_HD void broadcast(const unsigned long long *in3, W *a) {
#pragma unroll
        for (int i = 0; i < M; ++i) {
            a[i] = ((in3[i >> 6] >> (i & 63)) & 1ull) ? ~ECC_ZERO : ECC_ZERO;
        }
    }
    static ECC_HD void broadcastSigma(const unsigned long long *in3, int k, W *a) {
        W t[M];
        broadcast(in3, t);
        sigmaRun(t, k, a);
    }
};
