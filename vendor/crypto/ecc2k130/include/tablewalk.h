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
// f(R) and f(-R) = -f(R): the raw map descends to the 2m-element classes.
// The cold escape path below additionally compares canonical orbit keys.
//
// Rule v3 uses the v2 tag predicate only as a cheap hint. A hint is never
// sufficient to change a step: probe up to eight RAW steps, require an exact
// return, and choose the least (weight, canonical x) eligible vertex of that
// cycle. Advance h once only at that vertex. Eligibility is recomputed using
// the cycle's own four preceding tags, not the arriving walk's history.
// False hints leave the raw step unchanged. Walks entering a detected cycle
// may take an extra lap to fill their histories, but leave through the same
// orbit edge. This is eventual coalescence, not immediate history independence.
// Longer cycles, and cycles without a tag hint, require the finite trail guard.
// Historical v2 throughput/merge-loss measurements do not certify this rule.
//
// This header carries what host and device share: the tag encoding, the cycle
// rule and the coordinate tables for one field size.  The reference step and
// the table itself are in TableWalk<Cfg> below (host only).
#pragma once
#include <stdint.h>

#ifndef ECC_WALK_TABLE
#define ECC_WALK_TABLE 0
#endif
#ifndef ECC_TABLE_BRANCHES
#define ECC_TABLE_BRANCHES 8
#endif
#if ECC_TABLE_BRANCHES != 8 && ECC_TABLE_BRANCHES != 16
#error "ECC_TABLE_BRANCHES must be 8 or 16"
#endif
#ifndef ECC_CYCLE_FAST2
#define ECC_CYCLE_FAST2 0
#endif
#if ECC_CYCLE_FAST2 != 0 && ECC_CYCLE_FAST2 != 1
#error "ECC_CYCLE_FAST2 must be 0 or 1"
#endif
#ifndef ECC_CYCLE_PROFILE
#define ECC_CYCLE_PROFILE 0
#endif
#if ECC_CYCLE_PROFILE != 0 && ECC_CYCLE_PROFILE != 1
#error "ECC_CYCLE_PROFILE must be 0 or 1"
#endif
#include "bitslice.h"

// Step tag: h in bits 0-3, k in bits 4-11, eps in bit 12.  A lane that has not
// stepped yet holds 0xFFFF in every history slot, which no tag can conjugate to.
#define ECC_TAG_EPS 0x1000u
#define ECC_TAG_NONE 0xFFFFu
#define ECC_HIST_EMPTY 0xFFFFFFFFFFFFFFFFull

ECC_HD unsigned eccTag(int h, int k, int eps) {
    return unsigned(h) | (unsigned(k) << 4) | (unsigned(eps) << 12);
}
ECC_HD int eccTagH(unsigned t) { return int(t & 15u); }
ECC_HD int eccTagK(unsigned t) { return int((t >> 4) & 255u); }
ECC_HD int eccTagEps(unsigned t) { return int((t >> 12) & 1u); }

// Two tags name opposite addends exactly when they differ in the sign bit.
ECC_HD bool eccTagNegates(unsigned a, unsigned b) { return (a ^ b) == ECC_TAG_EPS; }

// The tag d phases on from t (d = 1..3), same branch and sign; k is mod m.
ECC_HD unsigned eccTagAdvanceK(unsigned t, int d, int m) {
    int k = eccTagK(t) + d;
    if (k >= m) k -= m;
    return (t & ~0xFF0u) | (unsigned(k) << 4);
}

// With r = (h, k, eps) the repeated tag, whether {u, v} are its partners in a
// tau-relation: (k+1, eps), (k+2, eps) from sigma^2 + sigma + 2 = 0, or
// (k+1, -eps), (k+3, -eps) from sigma^3 + sigma - 2 = 0.
ECC_HD bool eccTauPartners(unsigned r, unsigned u, unsigned v, int m) {
    const unsigned a1 = eccTagAdvanceK(r, 1, m), a2 = eccTagAdvanceK(r, 2, m);
    if ((u == a1 && v == a2) || (u == a2 && v == a1)) return true;
    const unsigned b1 = a1 ^ ECC_TAG_EPS, b3 = eccTagAdvanceK(r, 3, m) ^ ECC_TAG_EPS;
    return (u == b1 && v == b3) || (u == b3 && v == b1);
}

// Whether four steps sum to O through a tau-relation: one branch, one repeated
// tag, and the other two its partners.  An empty history slot has k = 255 >= m
// and never takes part.
ECC_HD bool eccTauRelation(unsigned t, unsigned t1, unsigned t2, unsigned t3, int m) {
    if (((t ^ t1) | (t ^ t2) | (t ^ t3)) & 15u) return false;
    if (eccTagK(t1) >= m || eccTagK(t2) >= m || eccTagK(t3) >= m) return false;
    if (t == t1) return eccTauPartners(t, t2, t3, m);
    if (t == t2) return eccTauPartners(t, t1, t3, m);
    if (t == t3) return eccTauPartners(t, t1, t2, m);
    if (t1 == t2) return eccTauPartners(t1, t, t3, m);
    if (t1 == t3) return eccTauPartners(t1, t, t2, m);
    if (t2 == t3) return eccTauPartners(t2, t, t1, m);
    return false;
}

// Cheap hint from history (t1 most recent, t2, t3, t4): t negates one of
// these tags, or completes a tau-relation with t1, t2, t3. This is NOT proof
// that the raw walk cycles at the current point. m is the field degree.
ECC_HD bool eccTagFruitless(unsigned t, unsigned long long hist, int m) {
    const unsigned t1 = unsigned(hist & 0xFFFFu);
    const unsigned t2 = unsigned((hist >> 16) & 0xFFFFu);
    const unsigned t3 = unsigned((hist >> 32) & 0xFFFFu);
    const unsigned t4 = unsigned((hist >> 48) & 0xFFFFu);
    if (eccTagNegates(t, t1) || eccTagNegates(t, t2) || eccTagNegates(t, t3) || eccTagNegates(t, t4))
        return true;
    return eccTauRelation(t, t1, t2, t3, m);
}
ECC_HD unsigned long long eccHistPush(unsigned long long hist, unsigned t) {
    return (hist << 16) | (unsigned long long)(t & 0xFFFFu);
}

// Diagnostic-only accounting for the exact v3 cold probe.  Every probe has
// one terminal outcome.  Anchor exits are a subset of the two exact-cycle
// outcomes, and nextCalls counts only Ops::next invocations (the fast2
// denominator check is deliberately not an affine next call).
enum EccCycleProbeOutcome {
    ECC_CYCLE_OUTCOME_NONE = 0,
    ECC_CYCLE_OUTCOME_FAST2,
    ECC_CYCLE_OUTCOME_GENERAL_1,
    ECC_CYCLE_OUTCOME_GENERAL_2,
    ECC_CYCLE_OUTCOME_GENERAL_3,
    ECC_CYCLE_OUTCOME_GENERAL_4,
    ECC_CYCLE_OUTCOME_GENERAL_5,
    ECC_CYCLE_OUTCOME_GENERAL_6,
    ECC_CYCLE_OUTCOME_GENERAL_7,
    ECC_CYCLE_OUTCOME_GENERAL_8,
    ECC_CYCLE_OUTCOME_OPEN_8,
    ECC_CYCLE_OUTCOME_DP_ABORT,
    ECC_CYCLE_OUTCOME_EXCEPTIONAL_ABORT,
};

struct EccCycleProbeProfile {
    unsigned nextCalls;
    unsigned outcome;
    unsigned anchorExit;
};

struct EccCycleProfile {
    unsigned long long hints;
    unsigned long long fast2Hits;
    unsigned long long generalCycles[8];
    unsigned long long openEightStepProbes;
    unsigned long long dpAborts;
    unsigned long long exceptionalDenominatorAborts;
    unsigned long long affineNextCalls;
    unsigned long long anchorExits;
};

ECC_HD unsigned long long *eccCycleOutcomeCounter(EccCycleProfile *totals,
                                                   unsigned outcome) {
    if (outcome == ECC_CYCLE_OUTCOME_FAST2) return &totals->fast2Hits;
    if (outcome >= ECC_CYCLE_OUTCOME_GENERAL_1 &&
        outcome <= ECC_CYCLE_OUTCOME_GENERAL_8)
        return &totals->generalCycles[outcome - ECC_CYCLE_OUTCOME_GENERAL_1];
    if (outcome == ECC_CYCLE_OUTCOME_OPEN_8) return &totals->openEightStepProbes;
    if (outcome == ECC_CYCLE_OUTCOME_DP_ABORT) return &totals->dpAborts;
    if (outcome == ECC_CYCLE_OUTCOME_EXCEPTIONAL_ABORT)
        return &totals->exceptionalDenominatorAborts;
    return nullptr;
}

ECC_HD void eccCycleProfileAccumulate(EccCycleProfile *totals,
                                       const EccCycleProbeProfile &probe) {
    ++totals->hints;
    unsigned long long *outcome = eccCycleOutcomeCounter(totals, probe.outcome);
    if (outcome) ++*outcome;
    totals->affineNextCalls += probe.nextCalls;
    totals->anchorExits += probe.anchorExit;
}

ECC_HD unsigned long long eccCycleProfileTerminalTotal(const EccCycleProfile &totals) {
    unsigned long long result = totals.fast2Hits + totals.openEightStepProbes +
                                totals.dpAborts + totals.exceptionalDenominatorAborts;
    for (int i = 0; i < 8; ++i) result += totals.generalCycles[i];
    return result;
}

// Shared host/device cold-path control flow. Ops supplies a complete raw
// addition, exact point equality, and an orbit-invariant strict ordering.
// A table may contain exceptional points; next() then returns false and this
// bounded probe declines to infer a cycle. It does not invent a point.
#define ECC_CYCLE_PROFILE_BEGIN() ((void)0)
#define ECC_CYCLE_PROFILE_NEXT() ((void)0)
#define ECC_CYCLE_PROFILE_OUTCOME(value) ((void)0)
#define ECC_CYCLE_PROFILE_EXIT() ((void)0)
template <class Point, class Ops>
inline unsigned eccCycleAnchorTag(const Point &start, unsigned raw, const Ops &ops,
                                  int m, int branches) {
#include "cycleanchor_body.h"
}
#ifdef __CUDACC__
template <class Point, class Ops>
__device__ __forceinline__ unsigned eccCycleAnchorTagDevice(const Point &start, unsigned raw, const Ops &ops,
                                  int m, int branches) {
#include "cycleanchor_body.h"
}
#endif
#undef ECC_CYCLE_PROFILE_BEGIN
#undef ECC_CYCLE_PROFILE_NEXT
#undef ECC_CYCLE_PROFILE_OUTCOME
#undef ECC_CYCLE_PROFILE_EXIT

#if ECC_CYCLE_PROFILE
#define ECC_CYCLE_PROFILE_BEGIN() do { profile->nextCalls = 0; \
                                       profile->outcome = ECC_CYCLE_OUTCOME_NONE; \
                                       profile->anchorExit = 0; } while (0)
#define ECC_CYCLE_PROFILE_NEXT() (++profile->nextCalls)
#define ECC_CYCLE_PROFILE_OUTCOME(value) (profile->outcome = (value))
#define ECC_CYCLE_PROFILE_EXIT() (profile->anchorExit = 1)
template <class Point, class Ops>
inline unsigned eccCycleAnchorTagProfile(const Point &start, unsigned raw,
                                         const Ops &ops, int m, int branches,
                                         EccCycleProbeProfile *profile) {
#include "cycleanchor_body.h"
}
#ifdef __CUDACC__
template <class Point, class Ops>
__device__ __forceinline__ unsigned eccCycleAnchorTagDeviceProfile(
        const Point &start, unsigned raw, const Ops &ops, int m, int branches,
        EccCycleProbeProfile *profile) {
#include "cycleanchor_body.h"
}
#endif
#undef ECC_CYCLE_PROFILE_BEGIN
#undef ECC_CYCLE_PROFILE_NEXT
#undef ECC_CYCLE_PROFILE_OUTCOME
#undef ECC_CYCLE_PROFILE_EXIT
#endif

// Coordinate tables for GF(2^M) in the permuted type-II ONB, coordinate i in
// 1..M stored at bit i-1.  Host-side construction; the device gets copies.
template <int M>
struct TableWalkConsts {
    static const int N = 2 * M + 1;
    static const int NL = (M + 63) / 64;
    int L[M + 1];                  // L[i], i = 1..M; L[fold(2^t)] = t
    int inv[M + 1];                // inverse of w mod M, inv[0] = 0
    unsigned long long maskLt[M][NL];        // {i : L(i) < k}
    unsigned long long plane[8][NL];         // {i : bit b of L(i) set}

    static int fold(long long e) {
        e %= N;
        if (e < 0) e += N;
        return e > M ? int(N - e) : int(e);
    }
    static void setBit(unsigned long long *v, int i) { v[(i - 1) >> 6] |= 1ull << ((i - 1) & 63); }
    void build() {
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
                if ((i * j) % M == 1) { inv[i] = j; break; }
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
    bool consistent() const {
        bool seen[M + 1] = {};
        for (int i = 1; i <= M; ++i) {
            if (L[i] < 0 || L[i] >= M || seen[L[i]]) return false;
            seen[L[i]] = true;
        }
        return true;
    }

    // Frobenius phase of a normal-basis coordinate vector of weight hw.
    int phase(const unsigned long long *xn, int hw) const {
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
    void pivot(const unsigned long long *xn, int k, unsigned long long *p) const {
        bool any = false;
        for (int l = 0; l < NL; ++l) { p[l] = xn[l] & maskLt[k][l]; any |= p[l] != 0; }
        if (!any) for (int l = 0; l < NL; ++l) p[l] = xn[l];
        for (int b = 7; b >= 0; --b) {
            unsigned long long t[NL];
            bool anyT = false;
            for (int l = 0; l < NL; ++l) { t[l] = p[l] & plane[b][l]; anyT |= t[l] != 0; }
            if (anyT) for (int l = 0; l < NL; ++l) p[l] = t[l];
        }
    }
    int negationBit(const unsigned long long *xn, const unsigned long long *yn, int k) const {
        unsigned long long p[NL];
        pivot(xn, k, p);
        int c = 0;
        for (int l = 0; l < NL; ++l) c += __builtin_popcountll(yn[l] & p[l]);
        return c & 1;
    }
};
