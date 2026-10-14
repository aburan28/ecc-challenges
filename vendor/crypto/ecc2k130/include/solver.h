// Distinguished-point store and collision resolution.
//
// Clients report only (seed, endpoint); no linear combination of P and Q is
// tracked inside the walk, exactly as in the ECC2K-130 design.  When two seeds
// reach the same orbit the server recomputes both walks, this time counting how
// often each sigma^j + 1 was applied, which gives
//
//     endpoint = [mu] (alpha0 P + Q),   mu = prod_j (1 + s^j)^{n_j}
//
// so endpoint = a P + b Q with a = mu alpha0 and b = mu.  Matching the two
// endpoints up to Frobenius and negation, endA = eps sigma^c endB, yields
//
//     k = (a_A - eps s^c a_B) / (eps s^c b_B - b_A)   mod l
//
// Under ECC_WALK_TABLE (tablewalk.h) the walk adds table points instead, and
// the re-walk accumulates a = alpha0 + sum eps_t s^k_t a_h_t and
// b = 1 + sum eps_t s^k_t b_h_t directly; the resolution formula is the same.
#pragma once

#include <map>
#include <string>
#include <unordered_map>
#include <vector>
#include "ref.h"
#include "walk.h"

template <class Cfg>
struct Solver {
    typedef Ref<Cfg> R;
    typedef typename R::Elem Elem;
    typedef typename R::Point Point;
    static const int M = Cfg::M;

    U192 ell;
    U192 s;
    U192 spow[256];
    Point basis;
    Point target;
    int dpWeight;
    unsigned long long maxIters;

    void setup(const unsigned long long *px, const unsigned long long *py,
               const unsigned long long *qx, const unsigned long long *qy,
               const char *ellDec, const char *sDec, int w, unsigned long long maxIt) {
        ell = u192_from_dec(ellDec);
        s = u192_from_dec(sDec);
        basis = R::make(R::fromLimbs(px), R::fromLimbs(py));
        target = R::make(R::fromLimbs(qx), R::fromLimbs(qy));
        dpWeight = w;
        maxIters = maxIt;
        spow[0] = u192_from(1);
        for (int i = 1; i < 256; ++i) spow[i] = mod_mul(spow[i - 1], s, ell);
        setupWalk();
    }

    bool checkSetup(std::string *why) const {
        if (!R::onCurve(basis)) { *why = "P is not on the curve"; return false; }
        if (!R::onCurve(target)) { *why = "Q is not on the curve"; return false; }
        if (!R::scalarMul(basis, ell).inf) { *why = "P does not have order l"; return false; }
        if (!R::scalarMul(target, ell).inf) { *why = "Q does not have order l"; return false; }
        if (!R::eq(R::frob(basis, 1), R::scalarMul(basis, s))) { *why = "sigma(P) != [s]P"; return false; }
        if (!u192_is_zero(mod_add(mod_add(mod_mul(s, s, ell), s, ell), u192_from(2), ell))) {
            *why = "s does not satisfy s^2 + s + 2 = 0"; return false;
        }
        return true;
    }

#if ECC_WALK_TABLE
    TableWalk<Cfg> walk;
    void setupWalk() { walk.setup(basis, target, ell); walk.dpWeight = dpWeight; }
#else
    void setupWalk() {}
#endif

    // endPoint = a P + b Q.  For the sigma^j + 1 walk a = mu alpha0 and b = mu
    // with mu the product of the (1 + s^j); counts records how often each j
    // was used.  For the table walk a and b accumulate the table coefficients
    // directly, and counts is unused.
    struct WalkResult {
        bool ok;
        Point endPoint;
        // The trail's start, R_0 = [alpha0]P + Q.  Kept because the witness
        // check is [mu]R_0 == endPoint, which is ONE scalar multiplication on
        // a point the walk already built, against the two that rebuilding
        // [mu*alpha0]P + [mu]Q costs.  Same statement -- R_0 is that sum --
        // for 196 point operations instead of 392.
        Point startPt;
        unsigned long long iters;
        unsigned long long counts[8];
        U192 alpha0;
        U192 a, b;
        u64 seed;
    };

    // Recompute a walk from its seed, tracking the endpoint's coefficients.
    WalkResult rewalk(u64 seed) const {
        WalkResult out;
        out.ok = false;
        out.seed = seed;
        out.iters = 0;
        for (int i = 0; i < 8; ++i) out.counts[i] = 0;
        Point p = R::startPoint(seed, basis, target, &out.alpha0, ell, spow);
        out.startPt = p;
        out.a = out.alpha0;
        out.b = u192_from(1);
        u64 hist = ECC_HIST_EMPTY;
        (void)hist;
        for (unsigned long long it = 0;; ++it) {
            const int hw = R::weight(p.x);
            if (hw <= dpWeight) {
                out.ok = true;
                out.endPoint = p;
                out.iters = it;
#if !ECC_WALK_TABLE
                const U192 mu = multiplier(out.counts);
                out.a = mod_mul(mu, out.alpha0, ell);
                out.b = mu;
#endif
                return out;
            }
            if (it >= maxIters) return out;
#if ECC_WALK_TABLE
            p = walk.step(p, hw, &hist, &out.a, &out.b, ell, spow);
#else
#if ECC_PACKED_XONLY_23
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY
            out.counts[0]++;
#elif ECC_PACKED_XONLY_BRIDGE3
            if (
#if ECC_PACKED_XONLY_POLY_SELECT
                R::polySelectBridge3(p.x)
#else
                ECC_PACKED_XONLY_IS_BRIDGE3(hw)
#endif
            ) out.counts[2]++;
            else
#if ECC_PACKED_XONLY_DOUBLE_ONLY
            out.counts[0]++;
#else
            out.counts[(hw >> 1) & 1]++;
#endif
#elif ECC_PACKED_XONLY_DOUBLE_ONLY
            out.counts[0]++;
#else
            out.counts[(hw >> 1) & 1]++;
#endif
#else
            out.counts[R::jOf(hw) - 3]++;
#endif
            p = R::step(p, hw);
#endif
        }
    }

    // Rebuild a walk's result from a witness the walk itself carried, with no
    // replay.  This is the whole point of carrying the counts: mu from the
    // counts sends the start point to the endpoint in about 227 group
    // operations, against the 2^25.27 steps a replay of one ECC2K-130 trail
    // costs.  Note what is and is not checked here.  The endpoint is COMPUTED
    // from mu, so comparing it against mu again would be a tautology; the
    // binding checks are that the counts sum to the claimed trail length, that
    // the endpoint is genuinely distinguished, and -- in the caller -- that it
    // lands on the orbit the corpus record names.  Those three are exactly
    // what a payer verifying the claim can check, and they are enough: a
    // forged count vector would have to hit a named low-weight orbit, which is
    // the search the trail was.
    WalkResult fromCounts(u64 seed, const unsigned long long *counts,
                          unsigned long long iters) const {
        WalkResult out;
        out.ok = false;
        out.seed = seed;
        out.iters = iters;
        unsigned long long total = 0;
        for (int i = 0; i < 8; ++i) { out.counts[i] = counts[i]; total += counts[i]; }
#if ECC_WALK_TABLE
        (void)total;
        return out;   // the table walk has no (1 + s^j)^{n_j} factorisation
#else
        if (total != iters) return out;
        out.startPt = R::startPoint(seed, basis, target, &out.alpha0, ell, spow);
        const U192 mu = multiplier(out.counts);
        out.a = mod_mul(mu, out.alpha0, ell);
        out.b = mu;
        // [mu]R_0, not [mu*alpha0]P + [mu]Q: the same point by R_0's own
        // definition, reached with one scalar multiplication instead of two.
        out.endPoint = R::scalarMul(out.startPt, mu);
        if (R::weight(out.endPoint.x) > dpWeight) return out;
        out.ok = true;
        return out;
#endif
    }

    // mu = prod_j (1 + s^j)^{n_j}
    U192 multiplier(const unsigned long long *counts) const {
        U192 mu = u192_from(1);
#if ECC_PACKED_XONLY_23
#if ECC_PACKED_XONLY_BRIDGE1_COMMON
        if (counts[0]) {
            U192 e = u192_zero();
            e.v[0] = counts[0];
            const U192 bridge1 = mod_add(u192_from(1), spow[1], ell);
            mu = mod_mul(mu, mod_pow(bridge1, e, ell), ell);
        }
#else
        for (int j = 0; j < 2; ++j) {
            if (!counts[j]) continue;
            U192 e = u192_zero();
            e.v[0] = counts[j];
            mu = mod_mul(mu, mod_pow(u192_from(j + 2), e, ell), ell);
        }
#endif
#if ECC_PACKED_XONLY_BRIDGE3
        if (counts[2]) {
            U192 e = u192_zero();
            e.v[0] = counts[2];
            const U192 bridge = mod_add(u192_from(1), spow[3], ell);
            mu = mod_mul(mu, mod_pow(bridge, e, ell), ell);
        }
#endif
#else
        for (int j = 0; j < 8; ++j) {
            if (!counts[j]) continue;
            const U192 f = mod_add(u192_from(1), spow[j + 3], ell);
            U192 e = u192_zero();
            e.v[0] = counts[j];
            mu = mod_mul(mu, mod_pow(f, e, ell), ell);
        }
#endif
        return mu;
    }

    bool solve(const WalkResult &A, const WalkResult &B, U192 *kOut, std::string *why) const {
        if (!A.ok || !B.ok) { *why = "a walk did not reach a distinguished point"; return false; }
        const U192 aA = A.a, bA = A.b, aB = B.a, bB = B.b;
        for (int c = 0; c < M; ++c) {
            const Point rot = R::frob(B.endPoint, c);
            int eps = 0;
            if (R::eq(rot, A.endPoint)) eps = 1;
            else if (R::eq(R::neg(rot), A.endPoint)) eps = -1;
            else continue;
            U192 sc = spow[c % M];
            if (eps < 0) sc = mod_neg(sc, ell);
            const U192 num = mod_sub(aA, mod_mul(sc, aB, ell), ell);
            const U192 den = mod_sub(mod_mul(sc, bB, ell), bA, ell);
            if (u192_is_zero(den)) { *why = "degenerate collision (identical walk)"; return false; }
            const U192 k = mod_mul(num, mod_inv(den, ell), ell);
            if (!R::eq(R::scalarMul(basis, k), target)) { *why = "candidate k failed verification"; return false; }
            *kOut = k;
            return true;
        }
        *why = "endpoints are not in the same orbit";
        return false;
    }

    // ---- store ---------------------------------------------------------
    struct Key {
        u64 v[3];
        bool operator==(const Key &o) const { return v[0] == o.v[0] && v[1] == o.v[1] && v[2] == o.v[2]; }
    };
    struct KeyHash {
        size_t operator()(const Key &k) const {
            u64 h = k.v[0] * 0x9E3779B97F4A7C15ull;
            h ^= (k.v[1] + 0x632BE59BD9B4E019ull) * 0xBF58476D1CE4E5B9ull;
            h ^= (k.v[2] + 0x94D049BB133111EBull) * 0x2545F4914F6CDD1Dull;
            return (size_t)(h ^ (h >> 29));
        }
    };
    struct Entry { u64 seed; unsigned long long iters; };

    std::unordered_map<Key, Entry, KeyHash> store;
    size_t duplicates = 0;
    size_t inserted = 0;

    static Key keyOf(const Elem &canonX) {
        Key k;
        k.v[0] = canonX.v[0];
        k.v[1] = canonX.v[1];
        k.v[2] = canonX.v[2];
        return k;
    }

    // Insert an already-canonical key, as reloaded from a corpus file.
    bool insertKey(const Key &key, u64 seed, unsigned long long iters, Entry *other) {
        auto it = store.find(key);
        if (it != store.end()) {
            if (it->second.seed == seed) { ++duplicates; return false; }
            *other = it->second;
            return true;
        }
        Entry e;
        e.seed = seed;
        e.iters = iters;
        store.emplace(key, e);
        ++inserted;
        return false;
    }

    // Returns true and fills `other` when this point collides with a stored one.
    bool insert(const DpRecord &rec, Entry *other) {
        const Elem x = R::fromLimbs(rec.x);
        const Key key = keyOf(R::canonical(x));
        auto it = store.find(key);
        if (it != store.end()) {
            if (it->second.seed == rec.seed) { ++duplicates; return false; }
            *other = it->second;
            return true;
        }
        Entry e;
        e.seed = rec.seed;
        e.iters = rec.iters;
        store.emplace(key, e);
        ++inserted;
        return false;
    }
};
