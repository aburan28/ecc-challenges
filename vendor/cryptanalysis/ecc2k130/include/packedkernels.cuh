// Imported from github.com/aburan28/crypto ecc2k130/include/packedkernels.cuh
// (branch ecc2k130-l1-geometry-and-tag-denominator, b8ed2f5), reformatted to
// this repository's .clang-format.  The generated files (packedtransform131,
// packed*reduce131, packedgeneratedproduct131, packedsigma131) come from that
// repository's codegen/ and are not edited here.
// One scalar walk per slot, retaining the existing iteration and DP record.
#pragma once
#include "kernel.h"
#include "packed131.h"
#ifndef ECC_PACKED_COMPACT_STATE
#    define ECC_PACKED_COMPACT_STATE 0
#endif
#if ECC_PACKED_COMPACT_STATE != 0 && ECC_PACKED_COMPACT_STATE != 1
#    error "ECC_PACKED_COMPACT_STATE must be 0 or 1"
#endif
#if ECC_PACKED_COMPACT_STATE
#    include "packedcompactstate.cuh"
#endif
#if ECC_WALK_TABLE
#    include "packedtablewalk.cuh"
#endif

namespace eccPacked131
{
#ifndef ECC_PACKED_CACHE_DENOM
#    define ECC_PACKED_CACHE_DENOM 0
#endif
#if ECC_PACKED_CACHE_DENOM != 0 && ECC_PACKED_CACHE_DENOM != 1
#    error "ECC_PACKED_CACHE_DENOM must be 0 or 1"
#endif
#ifndef ECC_PACKED_POLY_CHAIN
#    define ECC_PACKED_POLY_CHAIN 0
#endif
#ifndef ECC_PACKED_PAIR_PRODUCTS
#    define ECC_PACKED_PAIR_PRODUCTS 0
#endif
#if ECC_PACKED_PAIR_PRODUCTS && !ECC_PACKED_POLY_CHAIN
#    error "ECC_PACKED_PAIR_PRODUCTS requires polynomial chains"
#endif
#if ECC_PACKED_POLY_CHAIN != 0 && ECC_PACKED_POLY_CHAIN != 1
#    error "ECC_PACKED_POLY_CHAIN must be 0 or 1"
#endif
#if ECC_PACKED_POLY_CHAIN && !ECC_PACKED_CACHE_DENOM
#    error "ECC_PACKED_POLY_CHAIN requires the denominator cache"
#endif
#ifndef ECC_PACKED_POLY_STATE
#    define ECC_PACKED_POLY_STATE 0
#endif
#if ECC_PACKED_POLY_STATE != 0 && ECC_PACKED_POLY_STATE != 1
#    error "ECC_PACKED_POLY_STATE must be 0 or 1"
#endif
#if ECC_PACKED_POLY_STATE && (!ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN)
#    error "ECC_PACKED_POLY_STATE requires the denominator cache and polynomial chains"
#endif
#if ECC_PACKED_WEIGHTED_PREFIX && (!ECC_PACKED_POLY_STATE || !ECC_PACKED_POLY_CHAIN ||             \
                                   !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_PAIR_PRODUCTS)
#    error                                                                                         \
        "ECC_PACKED_WEIGHTED_PREFIX requires polynomial state, polynomial chains, denominator cache and paired products"
#endif
#if ECC_PACKED_WEIGHTED_PREFIX == 2 && !(ECC_PACKED_PERM_SIGMA & 1) && !ECC_WALK_TABLE
#    error "ECC_PACKED_WEIGHTED_PREFIX=2 requires the walk permutation network"
#endif
#if ECC_WALK_TABLE && !ECC_PACKED_WEIGHTED_PREFIX
#    error "ECC_WALK_TABLE is implemented on the weighted-prefix path only"
#endif
#ifndef ECC_UNROLL_SLOTS
#    define ECC_UNROLL_SLOTS 1
#endif
#if ECC_UNROLL_SLOTS < 1 || ECC_UNROLL_SLOTS > ECC_BATCH
#    error "ECC_UNROLL_SLOTS must be between 1 and ECC_BATCH"
#endif
#ifndef ECC_PACKED_SLOT_PREFETCH
#    define ECC_PACKED_SLOT_PREFETCH 0
#endif
#if ECC_PACKED_SLOT_PREFETCH != 0 && ECC_PACKED_SLOT_PREFETCH != 1
#    error "ECC_PACKED_SLOT_PREFETCH must be 0 or 1"
#endif
#if ECC_PACKED_SLOT_PREFETCH && !ECC_PACKED_COMPACT_STATE
#    error "ECC_PACKED_SLOT_PREFETCH requires compact state"
#endif
#ifndef ECC_PACKED_SLOT_PIPELINE
#    define ECC_PACKED_SLOT_PIPELINE 0
#endif
#if ECC_PACKED_SLOT_PIPELINE != 0 && ECC_PACKED_SLOT_PIPELINE != 1
#    error "ECC_PACKED_SLOT_PIPELINE must be 0 or 1"
#endif
#if ECC_PACKED_SLOT_PIPELINE && !ECC_WALK_TABLE
#    error "ECC_PACKED_SLOT_PIPELINE requires ECC_WALK_TABLE"
#endif
#if ECC_PACKED_SLOT_PIPELINE && !ECC_PACKED_COMPACT_STATE
#    error "ECC_PACKED_SLOT_PIPELINE requires compact state"
#endif
#if ECC_PACKED_SLOT_PIPELINE && ECC_PACKED_SLOT_PREFETCH
#    error                                                                                         \
        "ECC_PACKED_SLOT_PIPELINE and ECC_PACKED_SLOT_PREFETCH both issue the next slot load; pick one"
#endif
#if ECC_TABLE_TAG_DENOM && (ECC_PACKED_SLOT_PIPELINE || ECC_PACKED_SLOT_PREFETCH)
#    error "ECC_TABLE_TAG_DENOM has no denominator field for the slot pipeline/prefetch to load"
#endif
// ECC_TABLE_FUSED=1: one pass per step instead of two.  The reverse pass of
// step s produces (nx, ny) in registers, and the forward pass of step s+1
// needs exactly that point: its weight, selection, denominator and the
// prefix-chain product.  Doing that work here, before nx/ny are stored, means
// x and y are loaded once per update instead of twice.  Montgomery's trick does
// not care in which order the batch is multiplied up, only that the inverse
// chain runs back through the same order, so the fused pass accumulates the
// next step's chain in the order it visits the slots and the following pass
// visits them in reverse: slot order alternates 0..B-1, B-1..0 per step.  A
// launch begins with one plain forward pass and its last fused pass performs
// no selection, so the state left in memory (points advanced, hist holding the
// tags of the steps taken) is what the two-pass kernel leaves; the two
// kernels compute the same walk and the same reports.
#ifndef ECC_TABLE_FUSED
#    define ECC_TABLE_FUSED 0
#endif
#if ECC_TABLE_FUSED != 0 && ECC_TABLE_FUSED != 1
#    error "ECC_TABLE_FUSED must be 0 or 1"
#endif
#if ECC_TABLE_FUSED && (!ECC_WALK_TABLE || !ECC_TABLE_TAG_DENOM || !ECC_PACKED_POLY_STATE ||       \
                        ECC_PACKED_WEIGHTED_PREFIX != 2)
#    error                                                                                         \
        "ECC_TABLE_FUSED requires the table walk with ECC_TABLE_TAG_DENOM, polynomial state and weighted prefix 2"
#endif
// ECC_TABLE_FUSED_PIPE=1: the fused pass loads the next slot's x, y, W and
// hist one iteration ahead of their use (17 more live registers).
#ifndef ECC_TABLE_FUSED_PIPE
#    define ECC_TABLE_FUSED_PIPE 0
#endif
#if ECC_TABLE_FUSED_PIPE != 0 && ECC_TABLE_FUSED_PIPE != 1
#    error "ECC_TABLE_FUSED_PIPE must be 0 or 1"
#endif
#if ECC_TABLE_FUSED_PIPE && !ECC_TABLE_FUSED
#    error "ECC_TABLE_FUSED_PIPE requires ECC_TABLE_FUSED"
#endif
#ifndef ECC_PACKED_STATE_TILE
#    define ECC_PACKED_STATE_TILE 0
#endif
#if ECC_PACKED_STATE_TILE != 0 && ECC_PACKED_STATE_TILE != 256
#    error "ECC_PACKED_STATE_TILE must be 0 or 256"
#endif
#if ECC_PACKED_STATE_TILE &&                                                                       \
    (!ECC_PACKED_POLY_STATE || !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN)
#    error                                                                                         \
        "ECC_PACKED_STATE_TILE requires polynomial state, denominator cache and polynomial chains"
#endif
#if ECC_PACKED_STATE_TILE && ECC_THREADS != 256 && !ECC_PACKED_COMPACT_STATE
#    error "ECC_PACKED_STATE_TILE requires ECC_THREADS=256 unless compact state is on"
#endif
#if ECC_PACKED_COMPACT_STATE && (ECC_PACKED_STATE_TILE != 256 || !ECC_PACKED_POLY_STATE ||         \
                                 !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN)
#    error                                                                                         \
        "ECC_PACKED_COMPACT_STATE requires TILE256, polynomial state, denominator cache and polynomial chains"
#endif
#if ECC_PACKED_STATE_TILE
ECC_HD size_t physicalStateThreads(size_t threads) { return ((threads + 255) / 256) * 256; }
ECC_HD size_t stateWordIndex(int slot, int word, int tid)
{
    return ((size_t(tid) / 256 * ECC_BATCH * 5 + size_t(slot) * 5 + word) * 256) +
           size_t(tid) % 256;
}
#endif
static __constant__ P131 orbitX[128], orbitY[128], targetX, targetY;

// ECC_PHASE_PROFILE=1: lane 0 of every warp accumulates clock64() cycles per
// phase of the two-pass walk (forward pass, inversion, reverse pass) and the
// number of steps, so the per-warp critical path can be read without a
// profiler.  Costs a few instructions per step; off by default.
// ECC_PACKED_CHAIN_FIRST=1: in the reverse pass issue the chain product inv*d
// before lambda = inv*W, so the reduction that gates the next slot is not the
// last of the pair's twelve CLMADs to complete.
#ifndef ECC_PACKED_CHAIN_FIRST
#    define ECC_PACKED_CHAIN_FIRST 0
#endif
#ifndef ECC_PHASE_PROFILE
#    define ECC_PHASE_PROFILE 0
#endif
#if ECC_PHASE_PROFILE
__device__ unsigned long long phaseCycles[4];
#    define ECC_PHASE_MARK(v) const long long v = clock64()
#    define ECC_PHASE_ADD(i, a, b)                                                                 \
        if ((threadIdx.x & 31) == 0) atomicAdd(&phaseCycles[i], (unsigned long long)((b) - (a)))
#else
#    define ECC_PHASE_MARK(v)
#    define ECC_PHASE_ADD(i, a, b)
#endif

__device__ __forceinline__ P131 load(const unsigned *p, int slot, int tid, int threads)
{
#if ECC_PACKED_COMPACT_STATE
    return compactLoad131(p, slot, tid);
#else
    P131 a;
#    pragma unroll
#    if ECC_PACKED_STATE_TILE
    for (int i = 0; i < 5; ++i) a.v[i] = p[stateWordIndex(slot, i, tid)];
#    else
    for (int i = 0; i < 5; ++i) a.v[i] = p[(size_t(slot) * 5 + i) * threads + tid];
#    endif
    return a;
#endif
}
__device__ __forceinline__ void prefetch(const unsigned *p, int slot, int tid, int threads)
{
#if ECC_PACKED_SLOT_PREFETCH
    compactPrefetch131(p, slot, tid);
#else
    (void)p;
    (void)slot;
    (void)tid;
    (void)threads;
#endif
}
__device__ __forceinline__ void store(unsigned *p, int slot, int tid, int threads, P131 a)
{
#if ECC_PACKED_COMPACT_STATE
    compactStore131(p, slot, tid, a);
#else
#    pragma unroll
#    if ECC_PACKED_STATE_TILE
    for (int i = 0; i < 5; ++i) p[stateWordIndex(slot, i, tid)] = a.v[i];
#    else
    for (int i = 0; i < 5; ++i) p[(size_t(slot) * 5 + i) * threads + tid] = a.v[i];
#    endif
#endif
}
__device__ __forceinline__ void toLimbs(P131 a, unsigned long long *out)
{
    out[0] = a.v[0] | (static_cast<unsigned long long>(a.v[1]) << 32);
    out[1] = a.v[2] | (static_cast<unsigned long long>(a.v[3]) << 32);
    out[2] = a.v[4];
}
__device__ __forceinline__ int weight(P131 a)
{
    return __popc(a.v[0]) + __popc(a.v[1]) + __popc(a.v[2]) + __popc(a.v[3]) + __popc(a.v[4]);
}

static __global__ void ECC_BOUNDS init(WalkParams<unsigned> p, bool reseed)
{
    const size_t id = size_t(blockIdx.x) * blockDim.x + threadIdx.x;
    if (id >= size_t(p.threads) * ECC_BATCH || (reseed && !p.dead[id])) return;
    const unsigned long long seed = reseed ? p.seed[id] + 1 : eccSeedFor(p.runId, id);
    const unsigned long long c0 = eccPrf(seed, 0), c1 = eccPrf(seed, 1);
    P131 x = targetX, y = targetY;
#pragma unroll 1
    for (int i = 0; i < 128; ++i) {
        if (((i < 64 ? c0 >> (i & 63) : c1 >> (i & 63)) & 1) == 0) continue;
        P131 d = add131(x, orbitX[i]), e = add131(y, orbitY[i]);
        P131 lambda = mul131(e, inv131(d));
        P131 nx = add131(add131(sqr131(lambda), lambda), d);
        y = add131(add131(mul131(lambda, add131(x, nx)), nx), y);
        x = nx;
    }
    const int slot = int(id / p.threads), tid = int(id % p.threads);
#if ECC_PACKED_POLY_STATE
    // Seed construction uses the established normal-basis point arithmetic.
    // Only the persistent coordinate representation changes.
    x = toPolynomial131(x);
    y = toPolynomial131(y);
#endif
    store(p.x, slot, tid, p.threads, x);
    store(p.y, slot, tid, p.threads, y);
    p.seed[id] = seed;
    p.startIter[id] = p.iterBase;
    p.dead[id] = 0;
#if ECC_WALK_TABLE
    p.hist[id] = ECC_HIST_EMPTY;
#endif
}

// ECC_TABLE_PIPE_SELECT=1: software-pipeline the forward pass.  The phase
// profile (ECC_PHASE_PROFILE) puts the forward pass at ~4,540 cycles per slot
// per warp against 12 x 304 = 3,650 of carry-less unit share: the ~900 cycles
// of selection work (fromPolynomial, weight, the LUT lookups, the cycle rule)
// are not overlapped with the products' wait because they sit on the far side
// of a call and of branches.  Here slot k's twelve CLMADs are issued first,
// slot k+1's selection runs while they are in flight, and the two reductions
// come last.  Same products, same order of the chain, same walk.
#ifndef ECC_TABLE_PIPE_SELECT
#    define ECC_TABLE_PIPE_SELECT 0
#endif
#if ECC_TABLE_PIPE_SELECT != 0 && ECC_TABLE_PIPE_SELECT != 1
#    error "ECC_TABLE_PIPE_SELECT must be 0 or 1"
#endif
#if ECC_TABLE_PIPE_SELECT && (!ECC_WALK_TABLE || !ECC_TABLE_TAG_DENOM || !ECC_PACKED_POLY_STATE || \
                              ECC_PACKED_WEIGHTED_PREFIX != 2 || ECC_TABLE_FUSED ||                \
                              ECC_PACKED_SLOT_PIPELINE || ECC_PACKED_SLOT_PREFETCH)
#    error                                                                                         \
        "ECC_TABLE_PIPE_SELECT requires the two-pass table walk with ECC_TABLE_TAG_DENOM, polynomial state and weighted prefix 2"
#endif

#if ECC_WALK_TABLE
// The forward-pass selection of one slot, without the chain product: load the
// point, weight and distinguished-point test, table-walk selection with its
// history update, and the addend (d, e) in the polynomial basis.
__device__ __forceinline__ void tableSelectSlot(const WalkParams<unsigned> &p, int slot, int tid,
                                                unsigned long long now, bool guard,
                                                const uint32_t *twSel, const uint32_t *twTab,
                                                P131 *dp, P131 *ep)
{
    const P131 xp = load(p.x, slot, tid, p.threads);
    const P131 yp = load(p.y, slot, tid, p.threads);
    const size_t id = size_t(slot) * p.threads + tid;
    const P131 x = fromPolynomial131(xp);
    const int hw = weight(x);
    if (!p.dead[id]) {
        if (hw <= p.dpWeight) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            const unsigned dest = atomicAdd(p.dpCount, 1u);
            if (dest < p.dpCap) {
                DpRecord rec;
                rec.seed = p.seed[id];
                rec.iters = now - p.startIter[id];
                toLimbs(x, rec.x);
                toLimbs(fromPolynomial131(yp), rec.y);
                p.dp[dest] = rec;
            }
            p.dead[id] = 1;
        } else if (guard && now - p.startIter[id] >= p.maxIters) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            p.dead[id] = 1;
            atomicAdd(p.dpCount + 1, 1u);
        }
    }
    const unsigned tag = twSelect(x, yp, hw, p.hist + id, twSel);
    twAddend(tag, xp, yp, twTab, dp, ep);
}
#endif

#if ECC_TABLE_FUSED
// The forward-pass work for one point of one slot: the normal-basis weight and
// distinguished-point test, the table-walk selection with its history update,
// the addend, and this slot's contribution to the running prefix chain.
// `first` marks the first slot of the accumulation (its W is e alone).
__device__ __forceinline__ void fusedSelect(const WalkParams<unsigned> &p, const P131 &xp,
                                            const P131 &yp, size_t id, int slot, int tid,
                                            unsigned long long now, bool guard, bool first,
                                            unsigned long long hist, const uint32_t *twSel,
                                            const uint32_t *twTab, P131 *prod)
{
    const P131 x = fromPolynomial131(xp);
    const int hw = weight(x);
    if (!p.dead[id]) {
        if (hw <= p.dpWeight) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            const unsigned dest = atomicAdd(p.dpCount, 1u);
            if (dest < p.dpCap) {
                DpRecord rec;
                rec.seed = p.seed[id];
                rec.iters = now - p.startIter[id];
                toLimbs(x, rec.x);
                toLimbs(fromPolynomial131(yp), rec.y);
                p.dp[dest] = rec;
            }
            p.dead[id] = 1;
        } else if (guard && now - p.startIter[id] >= p.maxIters) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            p.dead[id] = 1;
            atomicAdd(p.dpCount + 1, 1u);
        }
    }
    const unsigned tag = twSelectHist(x, yp, hw, &hist, twSel);
    p.hist[id] = hist;
    P131 dp, ep;
    twAddend(tag, xp, yp, twTab, &dp, &ep);
    if (!first) {
        PolynomialPair pair = mulPolynomialPair131(*prod, ep, dp);
        store(p.pchain, slot, tid, p.threads, pair.first);
        *prod = pair.second;
    } else {
        *prod = dp;
        store(p.pchain, slot, tid, p.threads, ep);
    }
}

static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators)
{
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    (void)denominators;
#    if ECC_TABLE_GLOBAL
    const uint32_t *twSel = p.twConsts;
    const uint32_t *twTab = p.twConsts;
#    elif ECC_TABLE_ADDEND_GLOBAL
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts + TW_MASK_OFF, TW_SEL_WORDS);
    const uint32_t *twTab = p.twConsts;
#    else
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts);
    const uint32_t *twTab = twSel;
#    endif
    if (tid >= p.threads) return;
    P131 prod;
    {
        // The plain forward pass of the launch's first step, slot order 0..B-1.
        const unsigned long long now = p.iterBase;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
#    pragma unroll 1
        for (int slot = 0; slot < ECC_BATCH; ++slot) {
            const size_t id = size_t(slot) * p.threads + tid;
            fusedSelect(p, load(p.x, slot, tid, p.threads), load(p.y, slot, tid, p.threads), id,
                        slot, tid, now, guard, slot == 0, p.hist[id], twSel, twTab, &prod);
        }
    }
#    pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        // This pass visits the slots in the reverse of the order the chain was
        // accumulated: the prologue and every odd fused pass run 0..B-1, so
        // even fused passes run B-1..0.
        const bool forward = (step & 1) != 0;
        const bool last = step + 1 == p.steps;
        const unsigned long long now = p.iterBase + step + 1;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
        P131 inv = toPolynomial131(inv131(fromPolynomial131(prod)));
        P131 next;
#    if ECC_TABLE_FUSED_PIPE
        // The slot's operands are loaded one iteration ahead, before this
        // iteration's stores: every state array is a plain pointer, so the
        // compiler must otherwise assume the stores alias the next loads and
        // cannot start them early.
        const int slot0 = forward ? 0 : ECC_BATCH - 1;
        P131 nxX = load(p.x, slot0, tid, p.threads), nxY = load(p.y, slot0, tid, p.threads);
        P131 nxW = load(p.pchain, slot0, tid, p.threads);
        unsigned long long nxHist = p.hist[size_t(slot0) * p.threads + tid];
#    endif
#    if ECC_UNROLL_SLOTS > 1
#        pragma unroll 2
#    else
#        pragma unroll 1
#    endif
        for (int i = 0; i < ECC_BATCH; ++i) {
            const int slot = forward ? i : ECC_BATCH - 1 - i;
            const size_t id = size_t(slot) * p.threads + tid;
#    if ECC_TABLE_FUSED_PIPE
            const P131 x = nxX, y = nxY, w = nxW;
            const unsigned long long hist = nxHist;
            if (i + 1 < ECC_BATCH) {
                const int s1 = forward ? slot + 1 : slot - 1;
                nxX = load(p.x, s1, tid, p.threads);
                nxY = load(p.y, s1, tid, p.threads);
                nxW = load(p.pchain, s1, tid, p.threads);
                nxHist = p.hist[size_t(s1) * p.threads + tid];
            }
#    else
            const P131 x = load(p.x, slot, tid, p.threads), y = load(p.y, slot, tid, p.threads);
            const unsigned long long hist = p.hist[id];
            const P131 w = load(p.pchain, slot, tid, p.threads);
#    endif
            P131 dp;
            twDenominator(unsigned(hist & 0xFFFFu), x, twTab, &dp);
            P131 lambdaPoly;
            if (i + 1 < ECC_BATCH) {
                PolynomialPair pair = mulPolynomialPair131(inv, w, dp);
                lambdaPoly = pair.first;
                inv = pair.second;
            } else {
                lambdaPoly = mulPolynomial131(inv, w);
            }
            const P131 nx = add131(add131(squarePolynomial131(lambdaPoly), lambdaPoly), dp);
            const P131 product = mulPolynomial131(lambdaPoly, add131(x, nx));
            const P131 ny = add131(add131(product, nx), y);
            store(p.x, slot, tid, p.threads, nx);
            store(p.y, slot, tid, p.threads, ny);
            if (!last)
                fusedSelect(p, nx, ny, id, slot, tid, now, guard, i == 0, hist, twSel, twTab,
                            &next);
        }
        prod = next;
    }
}
#else
static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators)
{
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
#    if ECC_WALK_TABLE
    // All block threads participate, including inactive partial-tile workers.
#        if ECC_TABLE_GLOBAL
    const uint32_t *twSel = p.twConsts;
    const uint32_t *twTab = p.twConsts;
#        elif ECC_TABLE_ADDEND_GLOBAL
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts + TW_MASK_OFF, TW_SEL_WORDS);
    const uint32_t *twTab = p.twConsts;
#        else
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts);
    const uint32_t *twTab = twSel;
#        endif
#    elif ECC_PACKED_SHARED_SIGMA
    initSigmaWalkShared131();
#    endif
    if (tid >= p.threads) return;
#    if ECC_PACKED_POLY_CHAIN && !ECC_PACKED_POLY_STATE
    unsigned *polyDenominators = denominators + size_t(p.threads) * ECC_BATCH * 5;
#    endif
#    if !ECC_PACKED_CACHE_DENOM
    unsigned char js[ECC_BATCH];
#    endif
    P131 prod, inv;
#    if ECC_PACKED_SLOT_PIPELINE
    P131 pipeX = load(p.x, 0, tid, p.threads);
    P131 pipeY = load(p.y, 0, tid, p.threads);
    P131 pipeD, pipeC;
#    endif
#    pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        const unsigned long long now = p.iterBase + step;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
        ECC_PHASE_MARK(ph0);
#    if ECC_TABLE_PIPE_SELECT
        {
            P131 dpN, epN;
            tableSelectSlot(p, 0, tid, now, guard, twSel, twTab, &dpN, &epN);
            prod = dpN;
            store(p.pchain, 0, tid, p.threads, epN);
            tableSelectSlot(p, 1, tid, now, guard, twSel, twTab, &dpN, &epN);
            // Twelve CLMADs per slot: the chain product prod*d first, because
            // its reduction gates the next slot's products, then W = prod*e.
            // W's reduction and store are deferred by one slot, into the time
            // the next slot's products spend in the unit, and the next slot's
            // selection also runs while the products are in flight.  (A
            // two-buffer form without the copy below measured slower, 116
            // registers against 110.)
            uint32_t hbPrev[9];
#        pragma unroll 1
            for (int slot = 1; slot < ECC_BATCH; ++slot) {
                const P131 dp = dpN, ep = epN;
                uint32_t hb[9], hc[9];
                product131(prod, dp, hc);
                product131(prod, ep, hb);
                if (slot > 1)
                    store(p.pchain, slot - 1, tid, p.threads, reducePolynomial131(hbPrev));
                if (slot + 1 < ECC_BATCH)
                    tableSelectSlot(p, slot + 1, tid, now, guard, twSel, twTab, &dpN, &epN);
                prod = reducePolynomial131(hc);
#        pragma unroll
                for (int i = 0; i < 9; ++i) hbPrev[i] = hb[i];
            }
            store(p.pchain, ECC_BATCH - 1, tid, p.threads, reducePolynomial131(hbPrev));
        }
#    else
#        if ECC_UNROLL_SLOTS >= 8
#            pragma unroll 8
#        elif ECC_UNROLL_SLOTS >= 4
#            pragma unroll 4
#        elif ECC_UNROLL_SLOTS > 1
#            pragma unroll 2
#        else
#            pragma unroll 1
#        endif
        for (int slot = 0; slot < ECC_BATCH; ++slot) {
#        if ECC_PACKED_SLOT_PREFETCH
            if (slot + 1 < ECC_BATCH) {
                prefetch(p.x, slot + 1, tid, p.threads);
                prefetch(p.y, slot + 1, tid, p.threads);
            }
#        endif
#        if ECC_PACKED_SLOT_PIPELINE
            P131 x = pipeX;
            const P131 xp = x;
            const P131 yp = pipeY;
            if (slot + 1 < ECC_BATCH) {
                pipeX = load(p.x, slot + 1, tid, p.threads);
                pipeY = load(p.y, slot + 1, tid, p.threads);
            }
#        else
            P131 x = load(p.x, slot, tid, p.threads);
#            if ECC_WALK_TABLE
            const P131 xp = x;
#            endif
#        endif
#        if ECC_PACKED_POLY_STATE
            x = fromPolynomial131(x);
#        endif
            const int hw = weight(x);
            const size_t id = size_t(slot) * p.threads + tid;
            if (!p.dead[id]) {
                if (hw <= p.dpWeight) {
                    if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
                    const unsigned dest = atomicAdd(p.dpCount, 1u);
                    if (dest < p.dpCap) {
                        DpRecord rec;
                        rec.seed = p.seed[id];
                        rec.iters = now - p.startIter[id];
                        toLimbs(x, rec.x);
#        if ECC_PACKED_POLY_STATE
                        toLimbs(fromPolynomial131(load(p.y, slot, tid, p.threads)), rec.y);
#        else
                        toLimbs(load(p.y, slot, tid, p.threads), rec.y);
#        endif
                        p.dp[dest] = rec;
                    }
                    p.dead[id] = 1;
                } else if (guard && now - p.startIter[id] >= p.maxIters) {
                    if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
                    // Overdue walks need a restart, not a false DP report.
                    p.dead[id] = 1;
                    atomicAdd(p.dpCount + 1, 1u);
                }
            }
#        if ECC_WALK_TABLE
            // Table walk: the branch, phase and sign come from the normal-basis
            // x and one coordinate of y; the addend is read from the table in
            // the polynomial basis, so nothing is converted and no Frobenius
            // network runs.  The second pass only needs dp and the chain.
#            if !ECC_PACKED_SLOT_PIPELINE
            const P131 yp = load(p.y, slot, tid, p.threads);
#            endif
            const unsigned tag = twSelect(x, yp, hw, p.hist + id, twSel);
            P131 dp, ep;
            twAddend(tag, xp, yp, twTab, &dp, &ep);
            if (slot) {
                PolynomialPair pair = mulPolynomialPair131(prod, ep, dp);
                store(p.pchain, slot, tid, p.threads, pair.first);
                prod = pair.second;
            } else {
                prod = dp;
                store(p.pchain, slot, tid, p.threads, ep);
            }
#            if !ECC_TABLE_TAG_DENOM
            store(denominators, slot, tid, p.threads, dp);
#            endif
#        else
            const int j = 3 + ((hw >> 1) & 7);
#            if !ECC_PACKED_CACHE_DENOM
            js[slot] = j;
#            endif
#            if ECC_PACKED_WEIGHTED_PREFIX
            const P131 normalY = fromPolynomial131(load(p.y, slot, tid, p.threads));
#                if ECC_PACKED_WEIGHTED_PREFIX == 2
#                    if ECC_PACKED_SHARED_SIGMA
            const SigmaWalkPair131 sigmas = sigmaWalkNetworkPairShared131(x, normalY, j - 3);
#                    else
            const SigmaWalkPair131 sigmas = sigmaWalkNetworkPair131(x, normalY, j - 3);
#                    endif
            P131 d = add131(x, sigmas.first);
            P131 ep = toPolynomial131(add131(normalY, sigmas.second));
#                else
            P131 d = add131(x, sigma131(x, j));
            P131 ep = toPolynomial131(add131(normalY, sigma131(normalY, j)));
#                endif
            P131 dp = toPolynomial131(d);
            // W_i = E_i * product_{k<i}(D_k). Every prefix slot is used.
            if (slot) {
                PolynomialPair pair = mulPolynomialPair131(prod, ep, dp);
                store(p.pchain, slot, tid, p.threads, pair.first);
                prod = pair.second;
            } else {
                prod = dp;
                store(p.pchain, slot, tid, p.threads, ep);
            }
#            else
            P131 d = add131(x, sigma131(x, j));
#                if ECC_PACKED_POLY_CHAIN
            P131 dp = toPolynomial131(d);
            prod = slot == 0 ? dp : mulPolynomial131(prod, dp);
#                    if !ECC_PACKED_POLY_STATE
            store(polyDenominators, slot, tid, p.threads, dp);
#                    endif
#                else
            prod = slot == 0 ? d : mul131(prod, d);
#                endif
            if (slot + 1 < ECC_BATCH) store(p.pchain, slot, tid, p.threads, prod);
#            endif
#            if ECC_PACKED_CACHE_DENOM
            // The high 29 bits are unused by the field. Keep the jump index
            // beside the denominator, eliminating the separate local array.
#                if ECC_PACKED_POLY_STATE
            dp.v[4] |= unsigned(j - 3) << 3;
            store(denominators, slot, tid, p.threads, dp);
#                else
            d.v[4] |= unsigned(j - 3) << 3;
            store(denominators, slot, tid, p.threads, d);
#                endif
#            endif
#        endif // ECC_WALK_TABLE
        }
#    endif     // ECC_TABLE_PIPE_SELECT
        ECC_PHASE_MARK(ph1);
#    if ECC_PACKED_POLY_CHAIN
        inv = toPolynomial131(inv131(fromPolynomial131(prod)));
#    else
        inv = inv131(prod);
#    endif
#    if ECC_PHASE_PROFILE
        // The inversion's result is consumed by the first reverse-pass product;
        // make the clock read after its last instruction by using a lane's word.
        if (inv.v[0] == 0xFFFFFFFFu && inv.v[1] == 0x12345678u) phaseCycles[3] = 1ull;
#    endif
        ECC_PHASE_MARK(ph2);
#    if ECC_PACKED_SLOT_PIPELINE
        pipeX = load(p.x, ECC_BATCH - 1, tid, p.threads);
        pipeY = load(p.y, ECC_BATCH - 1, tid, p.threads);
        pipeD = load(denominators, ECC_BATCH - 1, tid, p.threads);
        pipeC = load(p.pchain, ECC_BATCH - 1, tid, p.threads);
#    endif
#    if ECC_UNROLL_SLOTS >= 8
#        pragma unroll 8
#    elif ECC_UNROLL_SLOTS >= 4
#        pragma unroll 4
#    elif ECC_UNROLL_SLOTS > 1
#        pragma unroll 2
#    else
#        pragma unroll 1
#    endif
        for (int slot = ECC_BATCH - 1; slot >= 0; --slot) {
#    if ECC_PACKED_SLOT_PREFETCH
            if (slot > 0) {
                prefetch(p.x, slot - 1, tid, p.threads);
                prefetch(p.y, slot - 1, tid, p.threads);
                prefetch(p.pchain, slot - 1, tid, p.threads);
                prefetch(denominators, slot - 1, tid, p.threads);
            }
#    endif
#    if ECC_PACKED_SLOT_PIPELINE
            P131 x = pipeX, y = pipeY, dp = pipeD, pch = pipeC;
            if (slot > 0) {
                pipeX = load(p.x, slot - 1, tid, p.threads);
                pipeY = load(p.y, slot - 1, tid, p.threads);
                pipeD = load(denominators, slot - 1, tid, p.threads);
                pipeC = load(p.pchain, slot - 1, tid, p.threads);
            }
            dp.v[4] &= 7;
            P131 lambdaPoly;
            if (slot) {
                PolynomialPair pair = mulPolynomialPair131(inv, pch, dp);
                lambdaPoly = pair.first;
                inv = pair.second;
            } else {
                lambdaPoly = mulPolynomial131(inv, pch);
            }
#    else
            P131 x = load(p.x, slot, tid, p.threads), y = load(p.y, slot, tid, p.threads);
#        if ECC_PACKED_WEIGHTED_PREFIX
#            if ECC_TABLE_TAG_DENOM
            // The forward pass pushed this step's tag into hist; x is still the
            // pre-step polynomial coordinate, so d = x + x_T comes back from the
            // table for the cost of a 5-word shared read instead of a 17-byte
            // global store and load.
            P131 dp;
            twDenominator(unsigned(p.hist[size_t(slot) * p.threads + tid] & 0xFFFFu), x, twTab,
                          &dp);
#            else
            P131 dp = load(denominators, slot, tid, p.threads);
            dp.v[4] &= 7;
#            endif
            P131 lambdaPoly;
            if (slot) {
#            if ECC_PACKED_CHAIN_FIRST
                // The chain product inv*d gates the next slot; issue it first.
                PolynomialPair pair =
                    mulPolynomialPair131(inv, dp, load(p.pchain, slot, tid, p.threads));
                lambdaPoly = pair.second;
                inv = pair.first;
#            else
                PolynomialPair pair =
                    mulPolynomialPair131(inv, load(p.pchain, slot, tid, p.threads), dp);
                lambdaPoly = pair.first;
                inv = pair.second;
#            endif
            } else {
                lambdaPoly = mulPolynomial131(inv, load(p.pchain, 0, tid, p.threads));
            }
#        else
#            if ECC_PACKED_POLY_STATE
            P131 dp = load(denominators, slot, tid, p.threads), ii;
            const int j = 3 + ((dp.v[4] >> 3) & 7);
            dp.v[4] &= 7;
            const P131 normalY = fromPolynomial131(y);
            P131 ep = toPolynomial131(add131(normalY, sigma131(normalY, j)));
#            elif ECC_PACKED_CACHE_DENOM
            P131 d = load(denominators, slot, tid, p.threads), ii;
            const int j = 3 + ((d.v[4] >> 3) & 7);
            d.v[4] &= 7;
            P131 e = add131(y, sigma131(y, j));
#            else
            const int j = js[slot];
            P131 d = add131(x, sigma131(x, j)), e = add131(y, sigma131(y, j)), ii;
#            endif
            if (slot) {
#            if ECC_PACKED_POLY_CHAIN
#                if ECC_PACKED_PAIR_PRODUCTS
                PolynomialPair pair =
                    mulPolynomialPair131(inv, load(p.pchain, slot - 1, tid, p.threads),
#                    if ECC_PACKED_POLY_STATE
                                         dp);
#                    else
                                         load(polyDenominators, slot, tid, p.threads));
#                    endif
                ii = pair.first;
                inv = pair.second;
#                else
                ii = mulPolynomial131(inv, load(p.pchain, slot - 1, tid, p.threads));
#                    if ECC_PACKED_POLY_STATE
                inv = mulPolynomial131(inv, dp);
#                    else
                inv = mulPolynomial131(inv, load(polyDenominators, slot, tid, p.threads));
#                    endif
#                endif
#            else
                ii = mul131(inv, load(p.pchain, slot - 1, tid, p.threads));
                inv = mul131(inv, d);
#            endif
            } else
                ii = inv;
#            if ECC_PACKED_POLY_STATE
            P131 lambdaPoly = mulPolynomial131(ep, ii);
#            endif
#        endif
#    endif
#    if ECC_PACKED_POLY_STATE
            P131 nx = add131(add131(squarePolynomial131(lambdaPoly), lambdaPoly), dp);
            P131 product = mulPolynomial131(lambdaPoly, add131(x, nx));
            P131 ny = add131(add131(product, nx), y);
#    else
#        if ECC_PACKED_POLY_CHAIN
            P131 lambdaPoly = mulPolynomial131(toPolynomial131(e), ii);
            P131 lambda = fromPolynomial131(lambdaPoly);
#        else
            P131 lambda = mul131(e, ii);
#        endif
            P131 nx = add131(add131(sqr131(lambda), lambda), d);
#        if ECC_PACKED_POLY_CHAIN
            P131 product = mulPolynomial131(lambdaPoly, toPolynomial131(add131(x, nx)));
            P131 ny = add131(add131(fromPolynomial131(product), nx), y);
#        else
            P131 ny = add131(add131(mul131(lambda, add131(x, nx)), nx), y);
#        endif
#    endif
            store(p.x, slot, tid, p.threads, nx);
            store(p.y, slot, tid, p.threads, ny);
#    if ECC_PACKED_SLOT_PIPELINE
            // Reverse pass ends at slot 0 with pipeX/pipeY still holding that
            // slot's pre-add coordinates. Refresh them so the next step's first
            // pass does not rebuild the addend from a stale point.
            if (!slot) {
                pipeX = nx;
                pipeY = ny;
            }
#    endif
        }
        ECC_PHASE_MARK(ph3);
        ECC_PHASE_ADD(0, ph0, ph1);
        ECC_PHASE_ADD(1, ph1, ph2);
        ECC_PHASE_ADD(2, ph2, ph3);
    }
}
#endif // ECC_TABLE_FUSED

#if ECC_PHASE_PROFILE
// Host: read and reset the phase counters.  `warpSteps` is the number of
// (warp, step) pairs the counters cover, so the result is cycles per warp-step.
inline void phaseProfileReport(double warpSteps)
{
    unsigned long long h[4] = {0, 0, 0, 0};
    cudaMemcpyFromSymbol(h, phaseCycles, sizeof(h));
    const double tot = double(h[0] + h[1] + h[2]);
    fprintf(stderr,
            "phase profile: per warp-step %.0f cycles = forward %.0f (%.1f%%) + inversion %.0f "
            "(%.1f%%) + reverse %.0f (%.1f%%); per update %.1f warp-cycles\n",
            tot / warpSteps, h[0] / warpSteps, 100.0 * h[0] / tot, h[1] / warpSteps,
            100.0 * h[1] / tot, h[2] / warpSteps, 100.0 * h[2] / tot, tot / warpSteps / ECC_BATCH);
    unsigned long long z[4] = {0, 0, 0, 0};
    cudaMemcpyToSymbol(phaseCycles, z, sizeof(z));
}
#endif
} // namespace eccPacked131
