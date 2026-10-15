// hostcheck.h - the packed GF(2^131) arithmetic and the walk held to this
// repository's golden model (fpga/model/ecc2k130.c).
//
// Two independent implementations of the same field meet here.  The golden
// model is the reference the FPGA core is checked against: shift-and-xor
// convolution in the permuted type-II optimal normal basis, written for
// clarity.  The packed arithmetic is what the GPU runs: five 32-bit words,
// polynomial-basis products via the Bernstein-Lange conversions, generated
// permutation networks for the Frobenius.  Both store coordinate i of beta_i
// at bit i-1, so a value moves between them by re-slicing its words, and the
// checks below are equality, not equivalence up to a basis change.
//
// The re-walk is the other job: given a device report (seed, iterations, x,
// y), rebuild the start point from the seed and step it forward on the golden
// model.  For the sigma walk the step is the model's own ec2k_step; for the
// table walk the selection is computed from the model's coordinates by the
// tablewalk.h reference functions rather than by the device's lookup tables.
// A report that re-walks was computed correctly; a corpus in which every
// sampled report re-walks was produced by a correct kernel.
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <vector>

extern "C" {
#include "../../fpga/model/ecc2k130.h"
}

#include "../include/kernel.h"
#include "../include/packed131.h"
#if ECC_WALK_TABLE
#    include "../include/packedtablewalk.cuh"
#endif

namespace ec2k_gpu
{

using eccPacked131::P131;

// ---- the campaign ----------------------------------------------------------

// The distinguished-point cutoff of the live ecc2k-130 campaign.  A walk
// stops at its first point of weight <= the cutoff, so points collected at
// another weight end most trails somewhere else and mostly cannot collide
// with the campaign's table.
static const int CAMPAIGN_DP_WEIGHT = 32;
// The campaign restarts a lane that has walked this many steps without a
// report (about 12 times the expected 2^28.41 at weight 32).  A restarted
// lane's steps are lost, so the limit sits far out in the tail: 2^30, three
// mean trails, cut 4.9% of honest trails and 15.6% of all steps
// (github.com/aburan28/crypto ecc2k130/WALK-CONSTANT.md section 6); 2^32 cuts
// 0.0006%.  The limit decides which trails are abandoned, never which points
// a walk reaches, so it does not change the points or the records.
static const unsigned long long CAMPAIGN_MAX_ITERS = 1ull << 32;

// Certicom's published ECC2K-130 base point P and target Q, in the model's
// representation (coefficient of beta_i at bit i-1).  The limbs are those of
// github.com/aburan28/crypto ecc2k130/generated/eccF131.h, the constants its
// campaign walks from; hosttest checks that both are on the curve and of order
// n, and that walks from them reproduce records that campaign's own client
// wrote (tests/campaign-kat.hex).
inline void challengePoints(ec2k_pt *P, ec2k_pt *Q)
{
    *P = ec2k_pt{{{0x83e0790d2530939full, 0xcdd9d226e6977edbull, 0x5ull}},
                 {{0x415d9bb8bbf0d331ull, 0x4fd41d580ce1af22ull, 0x7ull}},
                 0};
    *Q = ec2k_pt{{{0x1df1f53c9de44e94ull, 0x441132bb7aa42480ull, 0x1ull}},
                 {{0x18ca143c048e8bf0ull, 0x4055f79a09e58061ull, 0x0ull}},
                 0};
}

// n = 680564733841876926932320129493409985129, the prime order of P.
static const uint64_t SUBGROUP_ORDER[3] = {0x4d4fdd5703a3f269ull, 0x0ull, 0x2ull};

// ---- representation bridge -------------------------------------------------

inline P131 toPacked(const ec2k_fe &a)
{
    P131 r;
    r.v[0] = (uint32_t)a.w[0];
    r.v[1] = (uint32_t)(a.w[0] >> 32);
    r.v[2] = (uint32_t)a.w[1];
    r.v[3] = (uint32_t)(a.w[1] >> 32);
    r.v[4] = (uint32_t)a.w[2] & 7u;
    return r;
}

inline ec2k_fe fromPacked(const P131 &a)
{
    ec2k_fe r;
    r.w[0] = (uint64_t)a.v[0] | ((uint64_t)a.v[1] << 32);
    r.w[1] = (uint64_t)a.v[2] | ((uint64_t)a.v[3] << 32);
    r.w[2] = (uint64_t)(a.v[4] & 7u);
    return r;
}

inline void toLimbs(const ec2k_fe &a, unsigned long long out[3])
{
    out[0] = a.w[0];
    out[1] = a.w[1];
    out[2] = a.w[2];
}

inline ec2k_fe feOf(const unsigned long long v[3])
{
    ec2k_fe r;
    r.w[0] = v[0];
    r.w[1] = v[1];
    r.w[2] = v[2];
    return r;
}

inline bool sameFe(const ec2k_fe &a, const ec2k_fe &b) { return ec2k_fe_equal(&a, &b) != 0; }

// The model stores its words as uint64_t, tablewalk.h reads unsigned long long;
// the same 64 bits, but not the same type on LP64.
inline const unsigned long long *limbs(const ec2k_fe &a)
{
    return reinterpret_cast<const unsigned long long *>(a.w);
}

inline void randomFe(ec2k_fe *a, uint64_t *state)
{
    auto next = [&]() {
        uint64_t z = (*state += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
        z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        return z ^ (z >> 31);
    };
    a->w[0] = next();
    a->w[1] = next();
    a->w[2] = next() & 7u;
}

// The campaign's 32-byte record of a report: the seed, then the smallest x
// over the point's Frobenius orbit.  This is fpga/model's record and, byte
// for byte, the DpFileRecord the campaign's corpus and store hold (u64 seed,
// three u64 limbs of the orbit-minimum x, the top limb's unused bytes zero).
inline void campaignRecord(uint8_t out[EC2K_RECORD_BYTES], const DpRecord &r)
{
    ec2k_record rec;
    rec.seed = r.seed;
    const ec2k_fe x = feOf(r.x);
    ec2k_orbit_min(&rec.x, &x);
    ec2k_record_encode(out, &rec);
}

// ---- the walk's data -------------------------------------------------------

// Everything the device and the re-walk need from P and Q: the start-point
// terms sigma^i(P), and for the table walk its table.  In the table walk's
// shape packedtablewalk.cuh's twFillConsts reads (table[h][k].x.v as three
// limbs, consts as TableWalkConsts<131>): T_h = [a_h]P + [b_h]Q with 128-bit
// coefficients derived from the branch index by the same PRF as the seeds, so
// every client that agrees on P and Q agrees on the table; table[h][k] =
// sigma^k(T_h).
struct HostWalk {
    ec2k_pt P, Q;
    ec2k_pt orbitP[128]; // sigma^i(P), the start-point terms
#if ECC_WALK_TABLE
    struct Limbs {
        unsigned long long v[3];
    };
    struct Entry {
        Limbs x, y;
    };
    static const int H = ECC_TABLE_BRANCHES;
    TableWalkConsts<131> consts;
    Entry table[H][131];
    ec2k_pt point[H][131];
    uint64_t a[H][2], b[H][2];
#endif

    void build(const ec2k_pt &basis, const ec2k_pt &target)
    {
        P = basis;
        Q = target;
        for (int i = 0; i < 128; ++i) ec2k_pt_frob(&orbitP[i], &P, (unsigned)i);
#if ECC_WALK_TABLE
        consts.build();
        for (int h = 0; h < H; ++h) {
            for (int w = 0; w < 2; ++w) {
                a[h][w] = eccPrf(0x7ab1e0000000ull + 2ull * (uint64_t)h, w);
                b[h][w] = eccPrf(0x7ab1e0000000ull + 2ull * (uint64_t)h + 1, w);
            }
            ec2k_pt ap, bq, t;
            ec2k_pt_mul(&ap, &P, a[h], 2);
            ec2k_pt_mul(&bq, &Q, b[h], 2);
            ec2k_pt_add(&t, &ap, &bq);
            for (int k = 0; k < 131; ++k) {
                ec2k_pt_frob(&point[h][k], &t, (unsigned)k);
                toLimbs(point[h][k].x, table[h][k].x.v);
                toLimbs(point[h][k].y, table[h][k].y.v);
            }
        }
#endif
    }

#if ECC_WALK_TABLE
    // The device's flat constant buffer, built by the imported host routine.
    std::vector<uint32_t> deviceConsts() const
    {
        std::vector<uint32_t> out(eccPacked131::TW_WORDS);
        eccPacked131::twFillConsts(*this, out.data());
        return out;
    }
#endif
};

#if ECC_WALK_TABLE
// The CPU and Metal clients (client.h, cpuwalk.h, metalwalk.h) walk only the
// table walk, and know this struct by that walk's name.
using HostTable = HostWalk;
#endif

// ---- the walk on the golden model ------------------------------------------

// The start point of a lane: Q + sum of sigma^i(P) over the 128 bits of the
// seed's PRF output, as the init kernel computes it.  Returns false on the
// degenerate abscissa coincidence, which the kernel does not special-case
// either (probability 2^-131 per term).
inline bool startPoint(const HostWalk &t, unsigned long long seed, ec2k_pt *r)
{
    const unsigned long long c0 = eccPrf(seed, 0), c1 = eccPrf(seed, 1);
    *r = t.Q;
    for (int i = 0; i < 128; ++i) {
        const unsigned long long bit = i < 64 ? (c0 >> i) : (c1 >> (i - 64));
        if (!(bit & 1)) continue;
        if (ec2k_fe_equal(&r->x, &t.orbitP[i].x)) return false;
        ec2k_pt s;
        ec2k_pt_add(&s, r, &t.orbitP[i]);
        *r = s;
    }
    return !r->inf;
}

#if ECC_WALK_TABLE
// The tag a point selects, from the reference coordinate functions of
// tablewalk.h (bit-plane popcounts on the golden model's limbs), after the
// cycle rule against `hist`.  Independent of the device's byte tables.
inline unsigned referenceTag(const HostWalk &t, const ec2k_pt &p, unsigned long long hist)
{
    const int hw = (int)ec2k_fe_weight(&p.x);
    const int k = t.consts.phase(limbs(p.x), hw);
    const int eps = t.consts.negationBit(limbs(p.x), limbs(p.y), k);
    unsigned tag = eccTag((hw >> 1) & (HostWalk::H - 1), k, eps);
    for (int i = 0; i < HostWalk::H && eccTagFruitless(tag, hist); ++i)
        tag = eccTag((eccTagH(tag) + 1) & (HostWalk::H - 1), eccTagK(tag), eccTagEps(tag));
    return tag;
}

// A lane's history before its first step.
static const unsigned long long HIST_START = ECC_HIST_EMPTY;

// One step, R' = R + (-1)^eps sigma^k(T_h), on the golden model.
inline bool referenceStep(const HostWalk &t, ec2k_pt *r, unsigned long long *hist)
{
    const unsigned tag = referenceTag(t, *r, *hist);
    *hist = eccHistPush(*hist, tag);
    ec2k_pt q = t.point[eccTagH(tag)][eccTagK(tag)];
    if (eccTagEps(tag)) {
        ec2k_pt n;
        ec2k_pt_neg(&n, &q);
        q = n;
    }
    if (ec2k_fe_equal(&r->x, &q.x)) return false; // degenerate, never special-cased on the device
    ec2k_pt s;
    ec2k_pt_add(&s, r, &q);
    *r = s;
    return !r->inf;
}
#else
// The sigma walk keeps no history.
static const unsigned long long HIST_START = 0;

// One step, R' = R + sigma^j(R), on the golden model: its own ec2k_step.
inline bool referenceStep(const HostWalk &, ec2k_pt *r, unsigned long long *)
{
    ec2k_pt s;
    if (!ec2k_step(&s, r)) return false; // sigma^j(R) = +-R, never special-cased on the device
    *r = s;
    return !r->inf;
}
#endif

// Re-walk a report from its seed and compare.  `stepsOut` receives the number
// of steps taken (the record's iteration count, or fewer on a degeneracy).
inline bool rewalk(const HostWalk &t, const DpRecord &rec, unsigned long long *stepsOut)
{
    ec2k_pt r;
    if (!startPoint(t, rec.seed, &r)) return false;
    unsigned long long hist = HIST_START;
    for (unsigned long long i = 0; i < rec.iters; ++i)
        if (!referenceStep(t, &r, &hist)) {
            if (stepsOut) *stepsOut = i;
            return false;
        }
    if (stepsOut) *stepsOut = rec.iters;
    return sameFe(r.x, feOf(rec.x)) && sameFe(r.y, feOf(rec.y));
}

// Walk a seed to its first point of weight <= dpWeight and write the report,
// giving up after maxIters steps.  This is what the device computes for a
// lane, one step at a time on the model.
inline bool referenceReport(const HostWalk &t, unsigned long long seed, int dpWeight,
                            unsigned long long maxIters, DpRecord *out)
{
    ec2k_pt r;
    if (!startPoint(t, seed, &r)) return false;
    unsigned long long hist = HIST_START;
    for (unsigned long long i = 0; i <= maxIters; ++i) {
        if ((int)ec2k_fe_weight(&r.x) <= dpWeight) {
            out->seed = seed;
            out->iters = i;
            toLimbs(r.x, out->x);
            toLimbs(r.y, out->y);
            return true;
        }
        if (!referenceStep(t, &r, &hist)) return false;
    }
    return false;
}

// ---- arithmetic cross-check ------------------------------------------------

inline int weightOf(const P131 &a)
{
    int n = 0;
    for (int i = 0; i < 5; ++i) n += __builtin_popcount(a.v[i]);
    return n;
}

struct CheckResult {
    int checks = 0, failures = 0;
    void note(bool ok, const char *what)
    {
        checks++;
        if (!ok) {
            failures++;
            fprintf(stderr, "ecc2k130 hostcheck: failed: %s\n", what);
        }
    }
};

// Every packed routine the walk uses against the golden model, on random
// inputs: the normal-basis multiply (which converts to the polynomial basis
// and back), squaring, inversion, the Frobenius powers the walk and the
// inversion chain take through the generated networks, the polynomial-basis
// product and squaring through the conversions, the walk's selection
// primitives, and the walk itself for a few dozen steps.  Defined in
// hostcheck.cpp, which g++ compiles: the selection primitives are __device__
// functions under nvcc.
CheckResult crossCheck(const HostWalk &t, int rounds, uint64_t seed);

#if !ECC_WALK_TABLE
// The campaign's own checks, on top of crossCheck: Certicom's P and Q are on
// the curve and of order n, and every record in the known-answer file (the
// campaign client's own reports, tests/campaign-kat.hex) comes back byte for
// byte when its seed is walked from them on the model.
CheckResult &campaignCheck(CheckResult &cr, const char *katPath);
#endif

} // namespace ec2k_gpu
