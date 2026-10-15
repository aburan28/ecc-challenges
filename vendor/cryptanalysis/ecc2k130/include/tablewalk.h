// Imported from github.com/aburan28/crypto ecc2k130/include/tablewalk.h
// (branch ecc2k130-l1-geometry-and-tag-denominator, b8ed2f5), reformatted to
// this repository's .clang-format.  The generated files (packedtransform131,
// packed*reduce131, packedgeneratedproduct131, packedsigma131) come from that
// repository's codegen/ and are not edited here.
// The table walk, ITERATION-FUNCTION.md §4:
//
//     R' = R + (-1)^eps(R) * sigma^k(R) ( T_h(R) ),      T_h = a_h P + b_h Q
//
//     h(R)   = (HW(x_n) / 2) mod H                       normal-basis weight
//     k(R)   = (sum_e L(e) x_e) * HW(x_n)^-1  mod m      Frobenius phase
//     eps(R) = bit p(R) of y_n,  p = argmax_{e in supp x_n} (L(e) - k) mod m
//
// L(e) is the discrete logarithm base 2 of the folded coordinate index e in
// (Z/n)*/±1, so squaring shifts every L by one and k(sigma R) = k(R) + 1;
// eps is sigma-invariant and flips under negation.  Hence f(sigma R) = sigma
// f(R) and f(-R) = -f(R): the walk is a function on the 2m-element classes
// without any canonical representative, exactly as the sigma^j + 1 walk is.
//
// Unlike that walk this one is additive, so it has fruitless cycles: a step
// that cancels the previous one (probability 1/(2 H m) per step) or closes a
// 4-cycle with the three before it.  Both are detected from the last four step
// tags (h, k, eps) and avoided by advancing h.  The decision depends only on
// the last four steps, so two trails that merge inside a cycle re-synchronise
// after one lap: merging, and with it the rho collision structure, survives.
//
// This header carries what host and device share: the tag encoding, the cycle
// rule and the coordinate tables for one field size.  The reference step and
// the table itself are in TableWalk<Cfg> below (host only).
#pragma once
#include <stdint.h>

#ifndef ECC_WALK_TABLE
#    define ECC_WALK_TABLE 0
#endif
#ifndef ECC_TABLE_BRANCHES
#    define ECC_TABLE_BRANCHES 8
#endif
#if ECC_TABLE_BRANCHES != 8 && ECC_TABLE_BRANCHES != 16
#    error "ECC_TABLE_BRANCHES must be 8 or 16"
#endif
#include "bitslice.h"

// Step tag: h in bits 0-3, k in bits 4-11, eps in bit 12.  A lane that has not
// stepped yet holds 0xFFFF in every history slot, which no tag can conjugate to.
#define ECC_TAG_EPS    0x1000u
#define ECC_TAG_NONE   0xFFFFu
#define ECC_HIST_EMPTY 0xFFFFFFFFFFFFFFFFull

ECC_HD unsigned eccTag(int h, int k, int eps)
{
    return unsigned(h) | (unsigned(k) << 4) | (unsigned(eps) << 12);
}
ECC_HD int eccTagH(unsigned t) { return int(t & 15u); }
ECC_HD int eccTagK(unsigned t) { return int((t >> 4) & 255u); }
ECC_HD int eccTagEps(unsigned t) { return int((t >> 12) & 1u); }

// A step with tag t is fruitless after history (t1 most recent, t2, t3) when
// it undoes t1, or when it and t1 undo t2 and t3 respectively (a 4-cycle).
// Two consecutive tags differing only in the sign bit is the whole test.
ECC_HD bool eccTagFruitless(unsigned t, unsigned long long hist)
{
    const unsigned t1 = unsigned(hist & 0xFFFFu);
    const unsigned t2 = unsigned((hist >> 16) & 0xFFFFu);
    const unsigned t3 = unsigned((hist >> 32) & 0xFFFFu);
    return ((t ^ t1) == ECC_TAG_EPS) || (((t ^ t2) == ECC_TAG_EPS) && ((t1 ^ t3) == ECC_TAG_EPS));
}
ECC_HD unsigned long long eccHistPush(unsigned long long hist, unsigned t)
{
    return (hist << 16) | (unsigned long long)(t & 0xFFFFu);
}

// Coordinate tables for GF(2^M) in the permuted type-II ONB, coordinate i in
// 1..M stored at bit i-1.  Host-side construction; the device gets copies
// (and the Metal shader, which takes this header as source, none of it).
#ifndef __METAL_VERSION__
template <int M> struct TableWalkConsts {
    static const int N = 2 * M + 1;
    static const int NL = (M + 63) / 64;
    int L[M + 1];                     // L[i], i = 1..M; L[fold(2^t)] = t
    int inv[M + 1];                   // inverse of w mod M, inv[0] = 0
    unsigned long long maskLt[M][NL]; // {i : L(i) < k}
    unsigned long long plane[8][NL];  // {i : bit b of L(i) set}

    static int fold(long long e)
    {
        e %= N;
        if (e < 0) e += N;
        return e > M ? int(N - e) : int(e);
    }
    static void setBit(unsigned long long *v, int i) { v[(i - 1) >> 6] |= 1ull << ((i - 1) & 63); }
    void build()
    {
        for (int i = 0; i <= M; ++i) L[i] = -1;
        long long e = 1;
        for (int t = 0; t < M; ++t) {
            L[fold(e)] = t;
            e = (2 * e) % N;
        }
        for (int i = 0; i <= M; ++i) {
            inv[i] = 0;
            if (i == 0) continue;
            for (int j = 1; j < M; ++j)
                if ((i * j) % M == 1) {
                    inv[i] = j;
                    break;
                }
        }
        for (int k = 0; k < M; ++k)
            for (int l = 0; l < NL; ++l) maskLt[k][l] = 0;
        for (int b = 0; b < 8; ++b)
            for (int l = 0; l < NL; ++l) plane[b][l] = 0;
        for (int i = 1; i <= M; ++i) {
            for (int k = L[i] + 1; k < M; ++k) setBit(maskLt[k], i);
            for (int b = 0; b < 8; ++b)
                if ((L[i] >> b) & 1) setBit(plane[b], i);
        }
    }
    bool consistent() const
    {
        bool seen[M + 1] = {};
        for (int i = 1; i <= M; ++i) {
            if (L[i] < 0 || L[i] >= M || seen[L[i]]) return false;
            seen[L[i]] = true;
        }
        return true;
    }

    // Frobenius phase of a normal-basis coordinate vector of weight hw.
    int phase(const unsigned long long *xn, int hw) const
    {
        int w = 0;
        for (int b = 0; b < 8; ++b) {
            int c = 0;
            for (int l = 0; l < NL; ++l) c += __builtin_popcountll(xn[l] & plane[b][l]);
            w += c << b;
        }
        return int((long long)(w % M) * inv[hw % M] % M);
    }
    // The support element whose L is last before the phase in cyclic order:
    // set bits with L < k if there are any, otherwise all set bits, and the
    // largest L among them.  Returns a one-hot vector in `p`.
    void pivot(const unsigned long long *xn, int k, unsigned long long *p) const
    {
        bool any = false;
        for (int l = 0; l < NL; ++l) {
            p[l] = xn[l] & maskLt[k][l];
            any |= p[l] != 0;
        }
        if (!any)
            for (int l = 0; l < NL; ++l) p[l] = xn[l];
        for (int b = 7; b >= 0; --b) {
            unsigned long long t[NL];
            bool anyT = false;
            for (int l = 0; l < NL; ++l) {
                t[l] = p[l] & plane[b][l];
                anyT |= t[l] != 0;
            }
            if (anyT)
                for (int l = 0; l < NL; ++l) p[l] = t[l];
        }
    }
    int negationBit(const unsigned long long *xn, const unsigned long long *yn, int k) const
    {
        unsigned long long p[NL];
        pivot(xn, k, p);
        int c = 0;
        for (int l = 0; l < NL; ++l) c += __builtin_popcountll(yn[l] & p[l]);
        return c & 1;
    }
};
#endif // __METAL_VERSION__
