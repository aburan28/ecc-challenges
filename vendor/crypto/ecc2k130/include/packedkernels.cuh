// One scalar walk per slot, retaining the existing iteration and DP record.
#pragma once
#include "kernel.h"
#include "packed131.h"
#ifndef ECC_PACKED_BLOCK_INVERSE
#define ECC_PACKED_BLOCK_INVERSE 0
#endif
#if ECC_PACKED_BLOCK_INVERSE != 0 && ECC_PACKED_BLOCK_INVERSE != 1
#error "ECC_PACKED_BLOCK_INVERSE must be 0 or 1"
#endif
#if ECC_PACKED_LOGICAL_PAIR_INVERSE && !ECC_PACKED_BLOCK_INVERSE
#error "LOGICAL_PAIR_INVERSE requires BLOCK_INVERSE"
#endif
#if ECC_PACKED_BLOCK_INVERSE
#include "packedblockinverse131.cuh"
#endif
#include "packedsigmascratch.h"
#ifndef ECC_PACKED_COMPACT_STATE
#define ECC_PACKED_COMPACT_STATE 0
#endif
#if ECC_PACKED_COMPACT_STATE != 0 && ECC_PACKED_COMPACT_STATE != 1
#error "ECC_PACKED_COMPACT_STATE must be 0 or 1"
#endif
#if ECC_PACKED_COMPACT_STATE
#include "packedcompactstate.cuh"
#endif
#if ECC_WALK_TABLE
#include "packedtablewalk.cuh"
#endif

namespace eccPacked131 {
#ifndef ECC_PACKED_CACHE_DENOM
#define ECC_PACKED_CACHE_DENOM 0
#endif
#if ECC_PACKED_CACHE_DENOM != 0 && ECC_PACKED_CACHE_DENOM != 1
#error "ECC_PACKED_CACHE_DENOM must be 0 or 1"
#endif
#ifndef ECC_PACKED_POLY_CHAIN
#define ECC_PACKED_POLY_CHAIN 0
#endif
#ifndef ECC_PACKED_PAIR_PRODUCTS
#define ECC_PACKED_PAIR_PRODUCTS 0
#endif
#if ECC_PACKED_PAIR_PRODUCTS && !ECC_PACKED_POLY_CHAIN
#error "ECC_PACKED_PAIR_PRODUCTS requires polynomial chains"
#endif
#if ECC_PACKED_POLY_CHAIN != 0 && ECC_PACKED_POLY_CHAIN != 1
#error "ECC_PACKED_POLY_CHAIN must be 0 or 1"
#endif
#if ECC_PACKED_BLOCK_INVERSE && !ECC_PACKED_POLY_CHAIN
#error "ECC_PACKED_BLOCK_INVERSE requires polynomial chains"
#endif
#if ECC_PACKED_POLY_CHAIN && !ECC_PACKED_CACHE_DENOM
#error "ECC_PACKED_POLY_CHAIN requires the denominator cache"
#endif
#ifndef ECC_PACKED_POLY_STATE
#define ECC_PACKED_POLY_STATE 0
#endif
#if ECC_PACKED_POLY_STATE != 0 && ECC_PACKED_POLY_STATE != 1
#error "ECC_PACKED_POLY_STATE must be 0 or 1"
#endif
#if ECC_PACKED_POLY_STATE && (!ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN)
#error "ECC_PACKED_POLY_STATE requires the denominator cache and polynomial chains"
#endif
#if ECC_PACKED_WEIGHTED_PREFIX && (!ECC_PACKED_POLY_STATE || !ECC_PACKED_POLY_CHAIN || !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_PAIR_PRODUCTS)
#error "ECC_PACKED_WEIGHTED_PREFIX requires polynomial state, polynomial chains, denominator cache and paired products"
#endif
#if ECC_PACKED_WEIGHTED_PREFIX == 2 && !(ECC_PACKED_PERM_SIGMA & 1) && !ECC_WALK_TABLE
#error "ECC_PACKED_WEIGHTED_PREFIX=2 requires the walk permutation network"
#endif
#if ECC_WALK_TABLE && !ECC_PACKED_WEIGHTED_PREFIX
#error "ECC_WALK_TABLE is implemented on the weighted-prefix path only"
#endif
#ifndef ECC_UNROLL_SLOTS
#define ECC_UNROLL_SLOTS 1
#endif
#if ECC_UNROLL_SLOTS < 1 || ECC_UNROLL_SLOTS > ECC_BATCH
#error "ECC_UNROLL_SLOTS must be between 1 and ECC_BATCH"
#endif
#ifndef ECC_PACKED_SLOT_PREFETCH
#define ECC_PACKED_SLOT_PREFETCH 0
#endif
#if ECC_PACKED_SLOT_PREFETCH != 0 && ECC_PACKED_SLOT_PREFETCH != 1
#error "ECC_PACKED_SLOT_PREFETCH must be 0 or 1"
#endif
#if ECC_PACKED_SLOT_PREFETCH && !ECC_PACKED_COMPACT_STATE
#error "ECC_PACKED_SLOT_PREFETCH requires compact state"
#endif
#ifndef ECC_PACKED_SLOT_PIPELINE
#define ECC_PACKED_SLOT_PIPELINE 0
#endif
#if ECC_PACKED_SLOT_PIPELINE != 0 && ECC_PACKED_SLOT_PIPELINE != 1
#error "ECC_PACKED_SLOT_PIPELINE must be 0 or 1"
#endif
#if ECC_PACKED_SLOT_PIPELINE && !ECC_WALK_TABLE
#error "ECC_PACKED_SLOT_PIPELINE requires ECC_WALK_TABLE"
#endif
#if ECC_PACKED_SLOT_PIPELINE && !ECC_PACKED_COMPACT_STATE
#error "ECC_PACKED_SLOT_PIPELINE requires compact state"
#endif
#if ECC_PACKED_SLOT_PIPELINE && ECC_PACKED_SLOT_PREFETCH
#error "ECC_PACKED_SLOT_PIPELINE and ECC_PACKED_SLOT_PREFETCH both issue the next slot load; pick one"
#endif
#if ECC_TABLE_TAG_DENOM && (ECC_PACKED_SLOT_PIPELINE || ECC_PACKED_SLOT_PREFETCH)
#error "ECC_TABLE_TAG_DENOM has no denominator field for the slot pipeline/prefetch to load"
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
#define ECC_TABLE_FUSED 0
#endif
#if ECC_TABLE_FUSED != 0 && ECC_TABLE_FUSED != 1
#error "ECC_TABLE_FUSED must be 0 or 1"
#endif
#if ECC_TABLE_FUSED && (!ECC_WALK_TABLE || !ECC_TABLE_TAG_DENOM || !ECC_PACKED_POLY_STATE || ECC_PACKED_WEIGHTED_PREFIX != 2)
#error "ECC_TABLE_FUSED requires the table walk with ECC_TABLE_TAG_DENOM, polynomial state and weighted prefix 2"
#endif
// ECC_TABLE_FUSED_PIPE=1: the fused pass loads the next slot's x, y, W and
// hist one iteration ahead of their use (17 more live registers).
#ifndef ECC_TABLE_FUSED_PIPE
#define ECC_TABLE_FUSED_PIPE 0
#endif
#if ECC_TABLE_FUSED_PIPE != 0 && ECC_TABLE_FUSED_PIPE != 1
#error "ECC_TABLE_FUSED_PIPE must be 0 or 1"
#endif
#if ECC_TABLE_FUSED_PIPE && !ECC_TABLE_FUSED
#error "ECC_TABLE_FUSED_PIPE requires ECC_TABLE_FUSED"
#endif
// ECC_SIGMA_FUSED=1 applies the same reverse/next-forward fusion to the
// sigma^j+1 walk.  The launch begins with one ordinary forward pass.  Each
// reverse pass then computes the next points and, except on the final step,
// immediately builds the next step's denominator, numerator and prefix chain
// from those register values.  Slot order alternates so Montgomery inversion
// still consumes each chain in reverse.  The walk and completed-launch state
// are bit-identical; one x/y load pass per update is removed.
#ifndef ECC_SIGMA_FUSED
#define ECC_SIGMA_FUSED 0
#endif
#if ECC_SIGMA_FUSED != 0 && ECC_SIGMA_FUSED != 1
#error "ECC_SIGMA_FUSED must be 0 or 1"
#endif
#ifndef ECC_SIGMA_FUSED_LATE_Y
#define ECC_SIGMA_FUSED_LATE_Y 0
#endif
#if ECC_SIGMA_FUSED_LATE_Y != 0 && ECC_SIGMA_FUSED_LATE_Y != 1
#error "ECC_SIGMA_FUSED_LATE_Y must be 0 or 1"
#endif
#if ECC_SIGMA_FUSED_LATE_Y && !ECC_SIGMA_FUSED
#error "ECC_SIGMA_FUSED_LATE_Y requires ECC_SIGMA_FUSED"
#endif
#ifndef ECC_SIGMA_FUSED_SHARED_SLOTS
#define ECC_SIGMA_FUSED_SHARED_SLOTS 0
#endif
#if ECC_SIGMA_FUSED_SHARED_SLOTS != 0 && ECC_SIGMA_FUSED_SHARED_SLOTS != 2 && \
    ECC_SIGMA_FUSED_SHARED_SLOTS != 3 && ECC_SIGMA_FUSED_SHARED_SLOTS != 4
#error "ECC_SIGMA_FUSED_SHARED_SLOTS must be 0, 2, 3 or 4"
#endif
#if ECC_SIGMA_SQUARE_TABLE && (!ECC_SIGMA_FUSED || ECC_WALK_TABLE || !ECC_PACKED_POLY_STATE)
#error "ECC_SIGMA_SQUARE_TABLE requires the polynomial-state sigma-fused walk"
#endif
#if ECC_SIGMA_SQUARE_TABLE && (ECC_PACKED_SQUARE_TABLE || ECC_PACKED_ALU_SQUARE)
#error "ECC_SIGMA_SQUARE_TABLE is a standalone first comparison; keep PACKED_SQUARE_TABLE and PACKED_ALU_SQUARE off"
#endif
#if ECC_SIGMA_SQUARE_TABLE
static_assert(size_t(SQ_TAB_WORDS) * sizeof(uint32_t) == 8320,
              "sigma lambda square table layout changed");
#endif
#if ECC_SIGMA_FUSED && (ECC_WALK_TABLE || !ECC_PACKED_POLY_STATE || \
                        !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN || \
                        ECC_PACKED_WEIGHTED_PREFIX != 2 || ECC_TABLE_FUSED || \
                        ECC_TABLE_TAG_DENOM || ECC_PACKED_SLOT_PIPELINE || \
                        ECC_PACKED_SLOT_PREFETCH || ECC_PHASE_PROFILE || \
                        ECC_PACKED_CHAIN_FIRST)
#error "ECC_SIGMA_FUSED requires the one-chain polynomial sigma walk in weighted-prefix mode 2"
#endif
#if ECC_SIGMA_FUSED_SHARED_SLOTS && \
    (!ECC_SIGMA_FUSED || ECC_BATCH != 16 || ECC_THREADS != 256 || ECC_MINBLOCKS != 2 || \
     !ECC_PACKED_COMPACT_STATE || !ECC_PACKED_SHARED_SIGMA || ECC_WITNESS || \
     ECC_PACKED_INLINE_POLY != 3 || ECC_SIGMA_FUSED_LATE_Y || ECC_PACKED_SQUARE_TABLE)
#error "sigma fused shared scratch requires the exact counter-free B16/T256/min2 fused preset"
#endif
#ifndef ECC_PACKED_STATE_TILE
#define ECC_PACKED_STATE_TILE 0
#endif
#if ECC_PACKED_STATE_TILE != 0 && ECC_PACKED_STATE_TILE != 256
#error "ECC_PACKED_STATE_TILE must be 0 or 256"
#endif
#if ECC_PACKED_STATE_TILE && (!ECC_PACKED_POLY_STATE || !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN)
#error "ECC_PACKED_STATE_TILE requires polynomial state, denominator cache and polynomial chains"
#endif
#if ECC_PACKED_STATE_TILE && ECC_THREADS != 256 && !ECC_PACKED_COMPACT_STATE
#error "ECC_PACKED_STATE_TILE requires ECC_THREADS=256 unless compact state is on"
#endif
#if ECC_PACKED_COMPACT_STATE && (ECC_PACKED_STATE_TILE != 256 || !ECC_PACKED_POLY_STATE || !ECC_PACKED_CACHE_DENOM || !ECC_PACKED_POLY_CHAIN)
#error "ECC_PACKED_COMPACT_STATE requires TILE256, polynomial state, denominator cache and polynomial chains"
#endif
#if ECC_PACKED_FUSED_SIGMA && (!ECC_PACKED_POLY_STATE || ECC_PACKED_WEIGHTED_PREFIX != 2)
#error "ECC_PACKED_FUSED_SIGMA requires polynomial state and weighted-prefix mode 2"
#endif
#ifndef ECC_PACKED_BATCH_SPLIT
#define ECC_PACKED_BATCH_SPLIT 1
#endif
#if ECC_PACKED_BATCH_SPLIT != 1 && ECC_PACKED_BATCH_SPLIT != 2
#error "ECC_PACKED_BATCH_SPLIT must be 1 or 2"
#endif
#if ECC_PACKED_BATCH_SPLIT == 2 && ((ECC_BATCH < 16 || ECC_BATCH > 32 || (ECC_BATCH & 1)) || ECC_THREADS != 256 || !ECC_PACKED_BLOCK_INVERSE || ECC_PACKED_WEIGHTED_PREFIX != 2)
#error "Split batches require an even batch16..32, threads256, block inversion and weighted-prefix mode2"
#endif
#ifndef ECC_PACKED_LAST_SLOT_CACHE
#define ECC_PACKED_LAST_SLOT_CACHE 0
#endif
#if ECC_PACKED_LAST_SLOT_CACHE < 0 || ECC_PACKED_LAST_SLOT_CACHE > 2
#error "ECC_PACKED_LAST_SLOT_CACHE must be 0, 1 or 2"
#endif
#if ECC_PACKED_LAST_SLOT_CACHE && ((ECC_BATCH < 16 || ECC_BATCH > 32 || (ECC_BATCH & 1)) || ECC_PACKED_BATCH_SPLIT != 2 || !ECC_PACKED_BLOCK_INVERSE || ECC_PACKED_WEIGHTED_PREFIX != 2)
#error "Last-slot cache requires an even batch16..32, split2, block inversion and weighted-prefix mode2"
#endif
#if ECC_PACKED_LAST_SLOT_CACHE
static constexpr int lastLocalSlot131 = ECC_BATCH / ECC_PACKED_BATCH_SPLIT - 1;
#endif
#ifndef ECC_PACKED_SHARED_X_SLOTS
#define ECC_PACKED_SHARED_X_SLOTS 0
#endif
#if ECC_PACKED_SHARED_X_SLOTS != 0 && ECC_PACKED_SHARED_X_SLOTS != 2 && ECC_PACKED_SHARED_X_SLOTS != 4
#error "SHARED_X_SLOTS must be 0, 2 or 4"
#endif
#if ECC_PACKED_SHARED_X_SLOTS && ((ECC_BATCH < 16 || ECC_BATCH > 32 || (ECC_BATCH & 1)) || ECC_THREADS != 256 || ECC_PACKED_BATCH_SPLIT != 2 || !ECC_PACKED_BLOCK_INVERSE || !ECC_PACKED_COMPACT_STATE || !ECC_PACKED_POLY_STATE || ECC_PACKED_WEIGHTED_PREFIX != 2)
#error "SHARED_X_SLOTS requires an even B16..32 split2 compact polynomial block-inverse layout"
#endif
#if ECC_PACKED_SHARED_X_SLOTS
static constexpr int firstSharedXLocalSlot131=ECC_BATCH/ECC_PACKED_BATCH_SPLIT-ECC_PACKED_SHARED_X_SLOTS;
__device__ __forceinline__ P131 loadSharedX131(const uint4*low,const unsigned char*tail,int index) {
    const uint4 v=low[index];return P131{{v.x,v.y,v.z,v.w,unsigned(tail[index])}};
}
__device__ __forceinline__ void storeSharedX131(uint4*low,unsigned char*tail,int index,P131 value) {
    low[index]=uint4{value.v[0],value.v[1],value.v[2],value.v[3]};
    tail[index]=static_cast<unsigned char>(value.v[4]);
}
#endif
static constexpr int walkWorkersPerBlock131 = ECC_THREADS / ECC_PACKED_BATCH_SPLIT;
#if ECC_PACKED_STATE_TILE
ECC_HD size_t physicalStateThreads(size_t threads) {
    return ((threads + 255) / 256) * 256;
}
ECC_HD size_t stateWordIndex(int slot, int word, int tid) {
    return ((size_t(tid) / 256 * ECC_BATCH * 5 + size_t(slot) * 5 + word) * 256)
           + size_t(tid) % 256;
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
#define ECC_PACKED_CHAIN_FIRST 0
#endif
#ifndef ECC_PHASE_PROFILE
#define ECC_PHASE_PROFILE 0
#endif
#if ECC_PHASE_PROFILE
__device__ unsigned long long phaseCycles[4];
#define ECC_PHASE_MARK(v) const long long v = clock64()
#define ECC_PHASE_ADD(i, a, b) \
    if ((threadIdx.x & 31) == 0) atomicAdd(&phaseCycles[i], (unsigned long long)((b) - (a)))
#else
#define ECC_PHASE_MARK(v)
#define ECC_PHASE_ADD(i, a, b)
#endif

__device__ __forceinline__ P131 load(const unsigned *p, int slot, int tid, int threads) {
#if ECC_PACKED_COMPACT_STATE
    return compactLoad131(p, slot, tid);
#else
    P131 a;
#pragma unroll
#if ECC_PACKED_STATE_TILE
    for (int i = 0; i < 5; ++i) a.v[i] = p[stateWordIndex(slot, i, tid)];
#else
    for (int i = 0; i < 5; ++i) a.v[i] = p[(size_t(slot) * 5 + i) * threads + tid];
#endif
    return a;
#endif
}
__device__ __forceinline__ void prefetch(const unsigned *p, int slot, int tid, int threads) {
#if ECC_PACKED_SLOT_PREFETCH
    compactPrefetch131(p, slot, tid);
#else
    (void)p; (void)slot; (void)tid; (void)threads;
#endif
}
__device__ __forceinline__ void store(unsigned *p, int slot, int tid, int threads, P131 a) {
#if ECC_PACKED_COMPACT_STATE
    compactStore131(p, slot, tid, a);
#else
#pragma unroll
#if ECC_PACKED_STATE_TILE
    for (int i = 0; i < 5; ++i) p[stateWordIndex(slot, i, tid)] = a.v[i];
#else
    for (int i = 0; i < 5; ++i) p[(size_t(slot) * 5 + i) * threads + tid] = a.v[i];
#endif
#endif
}
__device__ __forceinline__ void toLimbs(P131 a, unsigned long long *out) {
    out[0] = a.v[0] | (static_cast<unsigned long long>(a.v[1]) << 32);
    out[1] = a.v[2] | (static_cast<unsigned long long>(a.v[3]) << 32);
    out[2] = a.v[4];
}
__device__ __forceinline__ int weight(P131 a) {
    return __popc(a.v[0]) + __popc(a.v[1]) + __popc(a.v[2]) + __popc(a.v[3]) + __popc(a.v[4]);
}

static __global__ void ECC_BOUNDS init(WalkParams<unsigned> p, bool reseed) {
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
#if ECC_WITNESS
    // A trail's witness starts at zero or it reports steps an earlier trail
    // took. This kernel serves both the first start and every restart, so it
    // is the one place that has to do it.
    for (int k = 0; k < ECC_JCOUNT; ++k) p.counts[eccScalarCountIndex(slot, k, tid, p.threads)] = 0;
#endif
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
#define ECC_TABLE_PIPE_SELECT 0
#endif
#if ECC_TABLE_PIPE_SELECT != 0 && ECC_TABLE_PIPE_SELECT != 1
#error "ECC_TABLE_PIPE_SELECT must be 0 or 1"
#endif
#if ECC_TABLE_PIPE_SELECT && (!ECC_WALK_TABLE || !ECC_TABLE_TAG_DENOM || !ECC_PACKED_POLY_STATE || ECC_PACKED_WEIGHTED_PREFIX != 2 || ECC_TABLE_FUSED || ECC_PACKED_SLOT_PIPELINE || ECC_PACKED_SLOT_PREFETCH)
#error "ECC_TABLE_PIPE_SELECT requires the two-pass table walk with ECC_TABLE_TAG_DENOM, polynomial state and weighted prefix 2"
#endif
// The v3 cycle-anchor probe is cold but needs a large point array. Keep it out
// of the prefix product's live range by selecting every slot first, then reload
// the unchanged polynomial coordinates and build the prefix from hist's tag.
#ifndef ECC_TABLE_SPLIT_FORWARD
#define ECC_TABLE_SPLIT_FORWARD 0
#endif
#if ECC_TABLE_SPLIT_FORWARD != 0 && ECC_TABLE_SPLIT_FORWARD != 1
#error "ECC_TABLE_SPLIT_FORWARD must be 0 or 1"
#endif
#if ECC_TABLE_SPLIT_FORWARD && !ECC_TABLE_PIPE_SELECT
#error "ECC_TABLE_SPLIT_FORWARD requires ECC_TABLE_PIPE_SELECT"
#endif
#ifndef ECC_TABLE_BATCH_HINTS
#define ECC_TABLE_BATCH_HINTS 0
#endif
#if ECC_TABLE_BATCH_HINTS != 0 && ECC_TABLE_BATCH_HINTS != 1
#error "ECC_TABLE_BATCH_HINTS must be 0 or 1"
#endif
#if ECC_TABLE_BATCH_HINTS && (!ECC_TABLE_SPLIT_FORWARD || ECC_BATCH > 64)
#error "ECC_TABLE_BATCH_HINTS requires split-forward and at most 64 slots"
#endif
#ifndef ECC_TABLE_BLOCK_HINTS
#define ECC_TABLE_BLOCK_HINTS 0
#endif
#ifndef ECC_TABLE_GLOBAL_HINTS
#define ECC_TABLE_GLOBAL_HINTS 0
#endif
#ifndef ECC_TABLE_GLOBAL_HINT_THREADS
#define ECC_TABLE_GLOBAL_HINT_THREADS 128
#endif
#if ECC_TABLE_GLOBAL_HINTS != 0 && ECC_TABLE_GLOBAL_HINTS != 1
#error "ECC_TABLE_GLOBAL_HINTS must be 0 or 1"
#endif
#if ECC_TABLE_GLOBAL_HINT_THREADS != 128 && ECC_TABLE_GLOBAL_HINT_THREADS != 256 && ECC_TABLE_GLOBAL_HINT_THREADS != 512
#error "ECC_TABLE_GLOBAL_HINT_THREADS must be 128, 256 or 512"
#endif
#if ECC_TABLE_GLOBAL_HINTS && (!ECC_WALK_TABLE || !ECC_TABLE_BATCH_HINTS || \
    !ECC_TABLE_SPLIT_FORWARD || ECC_TABLE_BLOCK_HINTS || ECC_TABLE_FUSED || \
    ECC_TABLE_GLOBAL || ECC_TABLE_ADDEND_GLOBAL || ECC_CYCLE_PROFILE || ECC_PHASE_PROFILE)
#error "GPU-wide hints require split/batch v3 with full shared tables, no block queue, fusion or profiling"
#endif
#ifndef ECC_TABLE_HINT_QUEUE
#define ECC_TABLE_HINT_QUEUE 512
#endif
#if ECC_TABLE_BLOCK_HINTS != 0 && ECC_TABLE_BLOCK_HINTS != 1
#error "ECC_TABLE_BLOCK_HINTS must be 0 or 1"
#endif
#if ECC_TABLE_BLOCK_HINTS && (!ECC_TABLE_BATCH_HINTS || ECC_TABLE_HINT_QUEUE < 1 || ECC_TABLE_HINT_QUEUE > 65536 || ECC_BATCH * ECC_THREADS > 65536)
#error "ECC_TABLE_BLOCK_HINTS requires batch hints, a queue in [1,65536], and a 16-bit (slot,thread) index"
#endif
// ECC_PACKED_CHAINS=2: every thread runs two independent Montgomery chains
// of ECC_BATCH/2 slots each (slots [0, B/2) and [B/2, B)) instead of one
// chain of ECC_BATCH.  The chains are interleaved slot by slot in both
// passes, and their two inversions run link by link through inv131x2, so
// within one warp every phase carries two independent dependency chains.
// The point of it (TWO-CHAINS.md): the phase profile of ONE-BLOCK-GEOMETRY.md
// puts the inversion at 69% and the forward pass at 84% of the carry-less
// unit's share, and nothing in a one-chain warp can overlap either; only the
// other warps can, and at 512 threads per SM they are in the same phase.  Two
// chains at half the threads keep the state footprint, the products per
// update (B/2 slots per inversion, so the batch must be twice the one-chain
// batch for the same inversion share) and the walk itself unchanged, and
// trade warps for instruction-level parallelism.  Same walk, same tags,
// same distinguished points as the one-chain kernel.
#ifndef ECC_PACKED_CHAINS
#define ECC_PACKED_CHAINS 1
#endif
#if ECC_PACKED_CHAINS != 1 && ECC_PACKED_CHAINS != 2
#error "ECC_PACKED_CHAINS must be 1 or 2"
#endif
#if ECC_PACKED_CHAINS == 2 && (!ECC_WALK_TABLE || !ECC_TABLE_TAG_DENOM || !ECC_PACKED_POLY_STATE || ECC_PACKED_WEIGHTED_PREFIX != 2 || ECC_TABLE_FUSED || ECC_TABLE_PIPE_SELECT || ECC_PACKED_SLOT_PIPELINE || ECC_PACKED_SLOT_PREFETCH || !ECC_PACKED_UNROLL_INV)
#error "ECC_PACKED_CHAINS=2 requires the two-pass table walk with ECC_TABLE_TAG_DENOM, polynomial state, weighted prefix 2 and the unrolled inversion; it has its own forward-pass pipelining"
#endif
#if ECC_PACKED_CHAINS == 2 && (ECC_BATCH % 2 != 0 || ECC_BATCH < 4)
#error "ECC_PACKED_CHAINS=2 needs an even ECC_BATCH of at least 4"
#endif
#if ECC_SIGMA_FUSED && ECC_PACKED_CHAINS != 1
#error "ECC_SIGMA_FUSED requires one Montgomery chain"
#endif
#if ECC_PACKED_SIGMA_TABLE && (!ECC_PACKED_POLY_STATE || ECC_PACKED_WEIGHTED_PREFIX != 2 || ECC_WALK_TABLE)
#error "ECC_PACKED_SIGMA_TABLE requires the polynomial-state ordinary or fused sigma walk"
#endif
#if ECC_PACKED_SIGMA_TABLE
// Dynamic shared memory for the Frobenius nibble table (SIGMA_TABLE_BYTES
// plus 256 bytes of alignment slack); the engine sizes every walk launch and
// publishes the global copy through this symbol.
extern __shared__ __align__(256) uint32_t eccSigmaSmem[];
static __device__ const uint32_t *eccSigmaTableDevice;
static const size_t SIGMA_SMEM_BYTES = size_t(SIGMA_TABLE_BYTES) + 256;
#else
static const size_t SIGMA_SMEM_BYTES = 0;
#endif
#if ECC_TABLE_GLOBAL_HINTS && ECC_PACKED_CHAINS != 1
#error "GPU-wide hints require one Montgomery chain"
#endif

#if ECC_WALK_TABLE
#if ECC_CYCLE_PROFILE
#define ECC_CYCLE_PROFILE_ARG(params) , (params).cycleProfile
#else
#define ECC_CYCLE_PROFILE_ARG(params)
#endif
// The forward-pass selection of one slot, without the chain product: load the
// point, weight and distinguished-point test, table-walk selection with its
// history update, and the addend (d, e) in the polynomial basis.
__device__ __forceinline__ void tableSelectSlot(const WalkParams<unsigned> &p, int slot, int tid,
                                                unsigned long long now, bool guard,
                                                const uint32_t *twSel, const uint32_t *twTab,
                                                P131 *dp, P131 *ep) {
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
                for (int k = 0; k < ECC_JCOUNT; ++k) {
#if ECC_WITNESS
                    rec.counts[k] = p.counts[eccScalarCountIndex(slot, k, tid, p.threads)];
#else
                    rec.counts[k] = 0;
#endif
                }
                p.dp[dest] = rec;
            }
            p.dead[id] = 1;
        } else if (guard && now - p.startIter[id] >= p.maxIters) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            p.dead[id] = 1;
            atomicAdd(p.dpCount + 1, 1u);
        }
    }
    const unsigned tag = twSelect(x, yp, hw, p.hist + id, twSel, twTab, p.dpWeight
                                  ECC_CYCLE_PROFILE_ARG(p));
    twAddend(tag, xp, yp, twTab, dp, ep);
}
#if ECC_TABLE_SPLIT_FORWARD
#if !ECC_TABLE_BATCH_HINTS
__device__ __forceinline__ void tableSelectTagSlot(const WalkParams<unsigned> &p, int slot, int tid,
                                                   unsigned long long now, bool guard,
                                                   const uint32_t *twSel, const uint32_t *twTab) {
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
                for (int k = 0; k < ECC_JCOUNT; ++k) {
#if ECC_WITNESS
                    rec.counts[k] = p.counts[eccScalarCountIndex(slot, k, tid, p.threads)];
#else
                    rec.counts[k] = 0;
#endif
                }
                p.dp[dest] = rec;
            }
            p.dead[id] = 1;
        } else if (guard && now - p.startIter[id] >= p.maxIters) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            p.dead[id] = 1;
            atomicAdd(p.dpCount + 1, 1u);
        }
    }
    (void)twSelect(x, yp, hw, p.hist + id, twSel, twTab, p.dpWeight
                   ECC_CYCLE_PROFILE_ARG(p));
}
#else
// Record the raw tag immediately, but defer a hinted v3 point probe.  A later
// warp-reconverged loop resolves one pending slot per lane at a time, so hints
// found at different batch slots execute the same cold call concurrently.
__device__ __forceinline__ bool tableSelectRawTagSlot(const WalkParams<unsigned> &p, int slot, int tid,
                                                      unsigned long long now, bool guard,
                                                      const uint32_t *twSel) {
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
                for (int k = 0; k < ECC_JCOUNT; ++k) {
#if ECC_WITNESS
                    rec.counts[k] = p.counts[eccScalarCountIndex(slot, k, tid, p.threads)];
#else
                    rec.counts[k] = 0;
#endif
                }
                p.dp[dest] = rec;
            }
            p.dead[id] = 1;
        } else if (guard && now - p.startIter[id] >= p.maxIters) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            p.dead[id] = 1;
            atomicAdd(p.dpCount + 1, 1u);
        }
    }
    const unsigned long long old = p.hist[id];
    const unsigned raw = twRawTag(x, yp, hw, twSel);
    const bool hinted = eccTagFruitless(raw, old, 131);
    p.hist[id] = eccHistPush(old, raw);
    return hinted;
}

__device__ __forceinline__ void tableResolveHintSlot(const WalkParams<unsigned> &p, int slot, int tid,
                                                      const uint32_t *twSel, const uint32_t *twTab) {
    const size_t id = size_t(slot) * p.threads + tid;
    unsigned long long hist = p.hist[id];
    const unsigned raw = unsigned(hist & 0xffffull);
    const P131 xp = load(p.x, slot, tid, p.threads);
    const P131 yp = load(p.y, slot, tid, p.threads);
#if ECC_CYCLE_PROFILE
    const unsigned tag = twCycleTagProfile(xp, yp, raw, twSel, twTab, p.dpWeight,
                                           p.cycleProfile);
#else
    const unsigned tag = twCycleTag(xp, yp, raw, twSel, twTab, p.dpWeight);
#endif
    p.hist[id] = (hist & ~0xffffull) | tag;
}
#endif

__device__ __forceinline__ void tableAddendFromHist(const WalkParams<unsigned> &p, int slot, int tid,
                                                    const uint32_t *twTab, P131 *dp, P131 *ep) {
    const size_t id = size_t(slot) * p.threads + tid;
    const P131 xp = load(p.x, slot, tid, p.threads);
    const P131 yp = load(p.y, slot, tid, p.threads);
    const unsigned tag = unsigned(p.hist[id] & 0xffffull);
    twAddend(tag, xp, yp, twTab, dp, ep);
}
#endif
#endif

#if ECC_TABLE_GLOBAL_HINTS
// Queue ownership is flat slot*workers+worker. Each worker reserves a range
// once, then writes each hinted slot to a different entry. The following
// kernel consumes only after every selection block completes on this stream.
static __global__ void ECC_BOUNDS selectGlobalHints(WalkParams<unsigned> p,
                                                     unsigned *queue, unsigned *count) {
    extern __shared__ uint32_t table[];
    twLoadShared(table, p.twConsts);
    const unsigned tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid >= unsigned(p.threads)) return;
    const bool guard = p.maxIters && p.iterBase % ECC_GUARD_PERIOD == 0;
    unsigned long long pending = 0;
#pragma unroll 1
    for (int slot = 0; slot < ECC_BATCH; ++slot)
        if (tableSelectRawTagSlot(p, slot, int(tid), p.iterBase, guard, table))
            pending |= 1ull << slot;
    const unsigned n = __popcll(pending);
    if (!n) return;
    unsigned pos = atomicAdd(count, n);
    while (pending) {
        const unsigned slot = unsigned(__ffsll((long long)pending) - 1);
        queue[pos++] = slot * unsigned(p.threads) + tid;
        pending &= pending - 1;
    }
}

static __global__ __launch_bounds__(ECC_TABLE_GLOBAL_HINT_THREADS, 1)
void resolveGlobalHints(WalkParams<unsigned> p, const unsigned *queue, const unsigned *count) {
    extern __shared__ uint32_t table[];
    twLoadShared(table, p.twConsts);
    const unsigned n = *count;
    const size_t stride = size_t(gridDim.x) * blockDim.x;
    for (size_t pos = size_t(blockIdx.x) * blockDim.x + threadIdx.x; pos < n; pos += stride) {
        const unsigned owner = queue[pos];
        const int tid = int(owner % unsigned(p.threads));
        const int slot = int(owner / unsigned(p.threads));
        tableResolveHintSlot(p, slot, tid, table, table);
    }
}
#endif

#if ECC_SIGMA_FUSED
#if ECC_SIGMA_FUSED_SHARED_SLOTS
static __shared__ unsigned sigmaFusedSharedScratch131[
    SIGMA_FUSED_SCRATCH_FIELDS * ECC_SIGMA_FUSED_SHARED_SLOTS * 5 * ECC_THREADS];
#endif

template<int Field>
__device__ __forceinline__ P131 sigmaFusedScratchLoadOrGlobal131(
    const unsigned *global, int slot, int tid, int threads) {
#if ECC_SIGMA_FUSED_SHARED_SLOTS
    if (slot < ECC_SIGMA_FUSED_SHARED_SLOTS)
        return sigmaFusedScratchLoad131(sigmaFusedSharedScratch131, Field, slot,
                                        int(threadIdx.x), ECC_SIGMA_FUSED_SHARED_SLOTS,
                                        ECC_THREADS);
#endif
    return load(global, slot, tid, threads);
}

template<int Field>
__device__ __forceinline__ void sigmaFusedScratchStoreOrGlobal131(
    unsigned *global, int slot, int tid, int threads, P131 value) {
#if ECC_SIGMA_FUSED_SHARED_SLOTS
    if (slot < ECC_SIGMA_FUSED_SHARED_SLOTS) {
        sigmaFusedScratchStore131(sigmaFusedSharedScratch131, Field, slot,
                                  int(threadIdx.x), ECC_SIGMA_FUSED_SHARED_SLOTS,
                                  ECC_THREADS, value);
        return;
    }
#endif
    store(global, slot, tid, threads, value);
}

// Forward work for one polynomial-state sigma slot.  `xp` and `yp` are either
// freshly loaded launch inputs or the reverse pass's register outputs.  The
// latter is the fusion: selection and prefix construction do not reload the
// point that was just stored.
__device__ __forceinline__ void sigmaFusedSelect(const WalkParams<unsigned> &p,
                                                 const P131 &xp, const P131 &yp,
                                                 size_t id, int slot, int tid,
                                                 unsigned long long now, bool guard,
                                                 bool first, unsigned *denominators,
                                                 P131 *prod, uint32_t sigmaBase) {
    const P131 x = fromPolynomial131(xp);
#if !ECC_SIGMA_FUSED_LATE_Y && !ECC_PACKED_SIGMA_TABLE
    const P131 normalY = fromPolynomial131(yp);
#endif
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
#if ECC_SIGMA_FUSED_LATE_Y || ECC_PACKED_SIGMA_TABLE
                const P131 reportY = fromPolynomial131(yp);
                toLimbs(reportY, rec.y);
#else
                toLimbs(normalY, rec.y);
#endif
                for (int k = 0; k < ECC_JCOUNT; ++k) {
#if ECC_WITNESS
                    rec.counts[k] = p.counts[eccScalarCountIndex(slot, k, tid, p.threads)];
#else
                    rec.counts[k] = 0;
#endif
                }
                p.dp[dest] = rec;
            }
            p.dead[id] = 1;
        } else if (guard && now - p.startIter[id] >= p.maxIters) {
            if ((p.seed[id] & 0xffffull) == 0xffffull) atomicAdd(p.dpCount + 2, 1u);
            p.dead[id] = 1;
            atomicAdd(p.dpCount + 1, 1u);
        }
    }
    const int j = 3 + ((hw >> 1) & 7);
#if ECC_WITNESS
    if (!p.dead[id])
        p.counts[eccScalarCountIndex(slot, j - 3, tid, p.threads)] += 1u;
#endif
#if ECC_PACKED_SIGMA_TABLE
    // x + sigma^j(x) and y + sigma^j(y) straight from the polynomial
    // coordinates: no normal-basis y, no swap network, no conversions back.
    const P131 dp = sigmaPlusTable131(xp, j - 3, sigmaBase);
    const P131 ep = sigmaPlusTable131(yp, j - 3, sigmaBase);
#else
#if ECC_SIGMA_FUSED_LATE_Y
    // Keep normal Y out of the report/guard branch's live range on the hot
    // DP-weight-zero path. A rare reported point converts once for the record
    // above and again here for the step; arithmetic and record bytes are
    // unchanged.
    const P131 normalY = fromPolynomial131(yp);
#endif
#if ECC_PACKED_SHARED_SIGMA
    const SigmaWalkPair131 sigmas = sigmaWalkNetworkPairShared131(x, normalY, j - 3);
#else
    const SigmaWalkPair131 sigmas = sigmaWalkNetworkPair131(x, normalY, j - 3);
#endif
    const P131 dp = toPolynomial131(add131(x, sigmas.first));
    const P131 ep = toPolynomial131(add131(normalY, sigmas.second));
#endif
    if (!first) {
        const PolynomialPair pair = mulPolynomialPair131(*prod, ep, dp);
        sigmaFusedScratchStoreOrGlobal131<SIGMA_FUSED_SCRATCH_CHAIN>(
            p.pchain, slot, tid, p.threads, pair.first);
        *prod = pair.second;
    } else {
        *prod = dp;
        sigmaFusedScratchStoreOrGlobal131<SIGMA_FUSED_SCRATCH_CHAIN>(
            p.pchain, slot, tid, p.threads, ep);
    }
    P131 tagged = dp;
    tagged.v[4] |= unsigned(j - 3) << 3;
    sigmaFusedScratchStoreOrGlobal131<SIGMA_FUSED_SCRATCH_DENOMINATOR>(
        denominators, slot, tid, p.threads, tagged);
}
#endif  // ECC_SIGMA_FUSED

#if ECC_PACKED_XONLY_23 && ECC_WALK_TABLE
#error "ECC_PACKED_XONLY_23 and ECC_WALK_TABLE select different walks"
#endif
#if !ECC_PACKED_XONLY_23
#if ECC_PACKED_BATCH_SPLIT != 1 || ECC_PACKED_BLOCK_INVERSE || ECC_PACKED_SHARED_X_SLOTS || ECC_PACKED_LAST_SLOT_CACHE || ECC_PACKED_FUSED_SIGMA
#error "Split batches, block inversion, shared-X slots, the last-slot cache and fused sigma are implemented only by the x-only walk (ECC_PACKED_XONLY_23)"
#endif
#if ECC_SIGMA_FUSED
// Forward work and the reverse pass share the sigma fused walk below.
static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators) {
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
#if ECC_SIGMA_SQUARE_TABLE
    extern __shared__ uint32_t sigmaSquareShared[];
    for (unsigned i = threadIdx.x; i < unsigned(SQ_TAB_WORDS); i += blockDim.x)
        sigmaSquareShared[i] = p.twConsts[i];
    __syncthreads();
    const uint32_t *sigmaSquareTable = sigmaSquareShared;
#else
    const uint32_t *sigmaSquareTable = nullptr;
#endif
#if ECC_PACKED_SHARED_SIGMA
    initSigmaWalkShared131();
#endif
#if ECC_PACKED_SIGMA_TABLE
    // Whole-block staging must precede the per-thread exit below.
    const uint32_t sigmaBase = stageSigmaTable(eccSigmaTableDevice, eccSigmaSmem);
#else
    const uint32_t sigmaBase = 0;
#endif
    if (tid >= p.threads) return;
    P131 prod;
    {
        // Launch prologue: ordinary forward pass in slot order 0..B-1.
        const unsigned long long now = p.iterBase;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
#pragma unroll 1
        for (int slot = 0; slot < ECC_BATCH; ++slot) {
            const size_t id = size_t(slot) * p.threads + tid;
            sigmaFusedSelect(p, load(p.x, slot, tid, p.threads),
                             load(p.y, slot, tid, p.threads), id, slot, tid,
                             now, guard, slot == 0, denominators, &prod, sigmaBase);
        }
    }
#pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        const bool forward = (step & 1) != 0;
        const bool last = step + 1 == p.steps;
        const unsigned long long now = p.iterBase + step + 1;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
        P131 inv = invPolynomial131(prod), next;
#if ECC_UNROLL_SLOTS > 1
#pragma unroll 2
#else
#pragma unroll 1
#endif
        for (int i = 0; i < ECC_BATCH; ++i) {
            const int slot = forward ? i : ECC_BATCH - 1 - i;
            const size_t id = size_t(slot) * p.threads + tid;
            const P131 x = load(p.x, slot, tid, p.threads);
            const P131 y = load(p.y, slot, tid, p.threads);
            P131 dp = sigmaFusedScratchLoadOrGlobal131<SIGMA_FUSED_SCRATCH_DENOMINATOR>(
                denominators, slot, tid, p.threads);
            dp.v[4] &= 7;
            const P131 w = sigmaFusedScratchLoadOrGlobal131<SIGMA_FUSED_SCRATCH_CHAIN>(
                p.pchain, slot, tid, p.threads);
            P131 lambdaPoly;
            if (i + 1 < ECC_BATCH) {
                const PolynomialPair pair = mulPolynomialPair131(inv, w, dp);
                lambdaPoly = pair.first;
                inv = pair.second;
            } else {
                lambdaPoly = mulPolynomial131(inv, w);
            }
            const P131 nx = add131(add131(sigmaLambdaSquare131(lambdaPoly, sigmaSquareTable),
                                          lambdaPoly), dp);
            const P131 product = mulPolynomial131(lambdaPoly, add131(x, nx));
            const P131 ny = add131(add131(product, nx), y);
            store(p.x, slot, tid, p.threads, nx);
            store(p.y, slot, tid, p.threads, ny);
            if (!last)
                sigmaFusedSelect(p, nx, ny, id, slot, tid, now, guard, i == 0,
                                 denominators, &next, sigmaBase);
        }
        if (!last) prod = next;
    }
}
#elif ECC_TABLE_FUSED
// The forward-pass work for one point of one slot: the normal-basis weight and
// distinguished-point test, the table-walk selection with its history update,
// the addend, and this slot's contribution to the running prefix chain.
// `first` marks the first slot of the accumulation (its W is e alone).
__device__ __forceinline__ void fusedSelect(const WalkParams<unsigned> &p, const P131 &xp,
                                            const P131 &yp, size_t id, int slot, int tid,
                                            unsigned long long now, bool guard, bool first,
                                            unsigned long long hist, const uint32_t *twSel,
                                            const uint32_t *twTab, P131 *prod) {
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
    const unsigned tag = twSelectHist(x, yp, hw, &hist, twSel, twTab, p.dpWeight
                                      ECC_CYCLE_PROFILE_ARG(p));
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

static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators) {
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    (void)denominators;
#if ECC_TABLE_GLOBAL
    const uint32_t *twSel = p.twConsts;
    const uint32_t *twTab = p.twConsts;
#elif ECC_TABLE_ADDEND_GLOBAL
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts + TW_MASK_OFF, TW_SEL_WORDS);
    const uint32_t *twTab = p.twConsts;
#else
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts);
    const uint32_t *twTab = twSel;
#endif
    if (tid >= p.threads) return;
    P131 prod;
    {
        // The plain forward pass of the launch's first step, slot order 0..B-1.
        const unsigned long long now = p.iterBase;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
#pragma unroll 1
        for (int slot = 0; slot < ECC_BATCH; ++slot) {
            const size_t id = size_t(slot) * p.threads + tid;
            fusedSelect(p, load(p.x, slot, tid, p.threads), load(p.y, slot, tid, p.threads), id,
                        slot, tid, now, guard, slot == 0, p.hist[id], twSel, twTab, &prod);
        }
    }
#pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        // This pass visits the slots in the reverse of the order the chain was
        // accumulated: the prologue and every odd fused pass run 0..B-1, so
        // even fused passes run B-1..0.
        const bool forward = (step & 1) != 0;
        const bool last = step + 1 == p.steps;
        const unsigned long long now = p.iterBase + step + 1;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
        P131 inv = invPolynomial131(prod);
        P131 next;
#if ECC_TABLE_FUSED_PIPE
        // The slot's operands are loaded one iteration ahead, before this
        // iteration's stores: every state array is a plain pointer, so the
        // compiler must otherwise assume the stores alias the next loads and
        // cannot start them early.
        const int slot0 = forward ? 0 : ECC_BATCH - 1;
        P131 nxX = load(p.x, slot0, tid, p.threads), nxY = load(p.y, slot0, tid, p.threads);
        P131 nxW = load(p.pchain, slot0, tid, p.threads);
        unsigned long long nxHist = p.hist[size_t(slot0) * p.threads + tid];
#endif
#if ECC_UNROLL_SLOTS > 1
#pragma unroll 2
#else
#pragma unroll 1
#endif
        for (int i = 0; i < ECC_BATCH; ++i) {
            const int slot = forward ? i : ECC_BATCH - 1 - i;
            const size_t id = size_t(slot) * p.threads + tid;
#if ECC_TABLE_FUSED_PIPE
            const P131 x = nxX, y = nxY, w = nxW;
            const unsigned long long hist = nxHist;
            if (i + 1 < ECC_BATCH) {
                const int s1 = forward ? slot + 1 : slot - 1;
                nxX = load(p.x, s1, tid, p.threads);
                nxY = load(p.y, s1, tid, p.threads);
                nxW = load(p.pchain, s1, tid, p.threads);
                nxHist = p.hist[size_t(s1) * p.threads + tid];
            }
#else
            const P131 x = load(p.x, slot, tid, p.threads), y = load(p.y, slot, tid, p.threads);
            const unsigned long long hist = p.hist[id];
            const P131 w = load(p.pchain, slot, tid, p.threads);
#endif
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
            const P131 nx = add131(add131(twSquare(lambdaPoly, twSel), lambdaPoly), dp);
            const P131 product = mulPolynomial131(lambdaPoly, add131(x, nx));
            const P131 ny = add131(add131(product, nx), y);
            store(p.x, slot, tid, p.threads, nx);
            store(p.y, slot, tid, p.threads, ny);
            if (!last)
                fusedSelect(p, nx, ny, id, slot, tid, now, guard, i == 0, hist, twSel, twTab, &next);
        }
        prod = next;
    }
}
#elif ECC_PACKED_CHAINS == 2
static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators) {
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    (void)denominators;
#if ECC_TABLE_GLOBAL
    const uint32_t *twSel = p.twConsts;
    const uint32_t *twTab = p.twConsts;
#elif ECC_TABLE_ADDEND_GLOBAL
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts + TW_MASK_OFF, TW_SEL_WORDS);
    const uint32_t *twTab = p.twConsts;
#else
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts);
    const uint32_t *twTab = twSel;
#endif
    if (tid >= p.threads) return;
    // Chain A owns slots [0, L), chain B slots [L, 2L).
    constexpr int L = ECC_BATCH / 2;
    P131 prodA, prodB, invA, invB;
#pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        const unsigned long long now = p.iterBase + step;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
        ECC_PHASE_MARK(ph0);
        {
            // Forward pass.  Slot 0 of each chain seeds it (prod = d, W = e).
            // From slot 1 on, per slot: chain A's twelve clmads are issued,
            // chain A's next selection runs while they are in the unit, chain
            // B's twelve clmads are issued, chain A's two reductions and its W
            // store, chain B's next selection, chain B's reductions and store.
            // Every operand a product consumes was selected one slot earlier.
            P131 dA, eA, dB, eB;
            tableSelectSlot(p, 0, tid, now, guard, twSel, twTab, &dA, &eA);
            prodA = dA;
            store(p.pchain, 0, tid, p.threads, eA);
            tableSelectSlot(p, L, tid, now, guard, twSel, twTab, &dB, &eB);
            prodB = dB;
            store(p.pchain, L, tid, p.threads, eB);
            tableSelectSlot(p, 1, tid, now, guard, twSel, twTab, &dA, &eA);
            tableSelectSlot(p, L + 1, tid, now, guard, twSel, twTab, &dB, &eB);
#pragma unroll 1
            for (int i = 1; i < L; ++i) {
                uint32_t hcA[9], hbA[9], hcB[9], hbB[9];
                const P131 dA0 = dA, eA0 = eA, dB0 = dB, eB0 = eB;
                product131(prodA, dA0, hcA);
                product131(prodA, eA0, hbA);
                if (i + 1 < L) tableSelectSlot(p, i + 1, tid, now, guard, twSel, twTab, &dA, &eA);
                product131(prodB, dB0, hcB);
                product131(prodB, eB0, hbB);
                prodA = reducePolynomial131(hcA);
                store(p.pchain, i, tid, p.threads, reducePolynomial131(hbA));
                if (i + 1 < L) tableSelectSlot(p, L + i + 1, tid, now, guard, twSel, twTab, &dB, &eB);
                prodB = reducePolynomial131(hcB);
                store(p.pchain, L + i, tid, p.threads, reducePolynomial131(hbB));
            }
        }
        ECC_PHASE_MARK(ph1);
        {
            // Both inversions, link by link (inv131x2), so each of the eight
            // dependent links carries two independent chains.
            P131 ia, ib;
            inv131x2(fromPolynomial131(prodA), fromPolynomial131(prodB), &ia, &ib);
            invA = toPolynomial131(ia);
            invB = toPolynomial131(ib);
        }
#if ECC_PHASE_PROFILE
        if (invA.v[0] == 0xFFFFFFFFu && invA.v[1] == 0x12345678u && invB.v[0] == 0xFFFFFFFFu) phaseCycles[3] = 1ull;
#endif
        ECC_PHASE_MARK(ph2);
        // Reverse pass.  The chain product inv*d is issued first (it gates the
        // next slot), then lambda = inv*W, for A then B; the four reductions,
        // the two new points and the two y-products follow.
#pragma unroll 1
        for (int i = L - 1; i >= 0; --i) {
            const int sA = i, sB = L + i;
            const P131 xA = load(p.x, sA, tid, p.threads), yA = load(p.y, sA, tid, p.threads);
            const P131 xB = load(p.x, sB, tid, p.threads), yB = load(p.y, sB, tid, p.threads);
            P131 dA, dB;
            twDenominator(unsigned(p.hist[size_t(sA) * p.threads + tid] & 0xFFFFu), xA, twTab, &dA);
            twDenominator(unsigned(p.hist[size_t(sB) * p.threads + tid] & 0xFFFFu), xB, twTab, &dB);
            const P131 wA = load(p.pchain, sA, tid, p.threads), wB = load(p.pchain, sB, tid, p.threads);
            P131 lamA, lamB;
            if (i) {
                uint32_t haA[9], hbA[9], haB[9], hbB[9];
                product131(invA, dA, haA);
                product131(invA, wA, hbA);
                product131(invB, dB, haB);
                product131(invB, wB, hbB);
                invA = reducePolynomial131(haA);
                lamA = reducePolynomial131(hbA);
                invB = reducePolynomial131(haB);
                lamB = reducePolynomial131(hbB);
            } else {
                uint32_t hbA[9], hbB[9];
                product131(invA, wA, hbA);
                product131(invB, wB, hbB);
                lamA = reducePolynomial131(hbA);
                lamB = reducePolynomial131(hbB);
            }
            const P131 nxA = add131(add131(twSquare(lamA, twSel), lamA), dA);
            const P131 nxB = add131(add131(twSquare(lamB, twSel), lamB), dB);
            uint32_t hyA[9], hyB[9];
            product131(lamA, add131(xA, nxA), hyA);
            product131(lamB, add131(xB, nxB), hyB);
            const P131 nyA = add131(add131(reducePolynomial131(hyA), nxA), yA);
            const P131 nyB = add131(add131(reducePolynomial131(hyB), nxB), yB);
            store(p.x, sA, tid, p.threads, nxA);
            store(p.y, sA, tid, p.threads, nyA);
            store(p.x, sB, tid, p.threads, nxB);
            store(p.y, sB, tid, p.threads, nyB);
        }
        ECC_PHASE_MARK(ph3);
        ECC_PHASE_ADD(0, ph0, ph1);
        ECC_PHASE_ADD(1, ph1, ph2);
        ECC_PHASE_ADD(2, ph2, ph3);
    }
}
#else
static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators) {
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
#if ECC_WALK_TABLE
    // All block threads participate, including inactive partial-tile workers.
#if ECC_TABLE_GLOBAL
    const uint32_t *twSel = p.twConsts;
    const uint32_t *twTab = p.twConsts;
#elif ECC_TABLE_ADDEND_GLOBAL
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts + TW_MASK_OFF, TW_SEL_WORDS);
    const uint32_t *twTab = p.twConsts;
#else
    extern __shared__ uint32_t twSel[];
    twLoadShared(twSel, p.twConsts);
    const uint32_t *twTab = twSel;
#endif
#elif ECC_PACKED_SHARED_SIGMA
    initSigmaWalkShared131();
#endif
#if ECC_PACKED_SIGMA_TABLE
    // The table is exact for both sigma schedules. Stage it before the
    // inactive partial-block return, just as the fused path does.
    const uint32_t sigmaBase = stageSigmaTable(eccSigmaTableDevice, eccSigmaSmem);
#else
    const uint32_t sigmaBase = 0;
#endif
#if ECC_TABLE_BLOCK_HINTS && defined(__CUDA_ARCH__)
    __shared__ unsigned tableHintCount;
    __shared__ unsigned short tableHintQueue[ECC_TABLE_HINT_QUEUE];
    const bool tableBlockActive = tid < p.threads;
#else
    if (tid >= p.threads) return;
#endif
#if ECC_PACKED_POLY_CHAIN && !ECC_PACKED_POLY_STATE
    unsigned *polyDenominators=denominators+size_t(p.threads)*ECC_BATCH*5;
#endif
#if !ECC_PACKED_CACHE_DENOM
    unsigned char js[ECC_BATCH];
#endif
    P131 prod, inv;
#if ECC_PACKED_SLOT_PIPELINE
    P131 pipeX = load(p.x, 0, tid, p.threads);
    P131 pipeY = load(p.y, 0, tid, p.threads);
    P131 pipeD, pipeC;
#endif
#pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        const unsigned long long now = p.iterBase + step;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
        ECC_PHASE_MARK(ph0);
#if ECC_TABLE_PIPE_SELECT
#if ECC_TABLE_SPLIT_FORWARD && !ECC_TABLE_GLOBAL_HINTS
#if ECC_TABLE_BATCH_HINTS
        unsigned long long pendingHints = 0;
#if ECC_TABLE_BLOCK_HINTS && defined(__CUDA_ARCH__)
        if (threadIdx.x == 0) tableHintCount = 0;
        __syncthreads();
        if (tableBlockActive) {
#endif
#pragma unroll 1
        for (int slot = 0; slot < ECC_BATCH; ++slot)
            if (tableSelectRawTagSlot(p, slot, tid, now, guard, twSel))
                pendingHints |= 1ull << slot;
#if ECC_TABLE_BLOCK_HINTS && defined(__CUDA_ARCH__)
        }
        const unsigned pendingCount = __popcll(pendingHints);
        unsigned queueBase = 0;
        if (pendingCount) queueBase = atomicAdd(&tableHintCount, pendingCount);
        unsigned long long queueBits = pendingHints;
        unsigned queueOffset = 0;
        while (queueBits) {
            const int slot = __ffsll((long long)queueBits) - 1;
            const unsigned q = queueBase + queueOffset++;
            if (q < ECC_TABLE_HINT_QUEUE)
                tableHintQueue[q] = (unsigned short)(slot * ECC_THREADS + threadIdx.x);
            queueBits &= queueBits - 1;
        }
        __syncthreads();
        const unsigned queued = tableHintCount;
        if (queued <= ECC_TABLE_HINT_QUEUE) {
            for (unsigned q = threadIdx.x; q < queued; q += ECC_THREADS) {
                const unsigned entry = tableHintQueue[q];
                const int ownerThread = int(entry % ECC_THREADS);
                const int ownerSlot = int(entry / ECC_THREADS);
                const int ownerTid = int(blockIdx.x) * ECC_THREADS + ownerThread;
                tableResolveHintSlot(p, ownerSlot, ownerTid, twSel, twTab);
            }
        } else {
            const unsigned warpMask = __activemask();
            while (__any_sync(warpMask, pendingHints != 0)) {
                if (pendingHints) {
                    const int slot = __ffsll((long long)pendingHints) - 1;
                    tableResolveHintSlot(p, slot, tid, twSel, twTab);
                    pendingHints &= pendingHints - 1;
                }
            }
        }
        __syncthreads();
        if (!tableBlockActive) continue;
#else
#ifdef __CUDA_ARCH__
        const unsigned warpMask = __activemask();
        while (__any_sync(warpMask, pendingHints != 0)) {
            if (pendingHints) {
                const int slot = __ffsll((long long)pendingHints) - 1;
                tableResolveHintSlot(p, slot, tid, twSel, twTab);
                pendingHints &= pendingHints - 1;
            }
        }
#else
        while (pendingHints) {
            const int slot = __builtin_ctzll(pendingHints);
            tableResolveHintSlot(p, slot, tid, twSel, twTab);
            pendingHints &= pendingHints - 1;
        }
#endif
#endif
#else
#pragma unroll 1
        for (int slot = 0; slot < ECC_BATCH; ++slot)
            tableSelectTagSlot(p, slot, tid, now, guard, twSel, twTab);
#endif
#endif
        {
            P131 dpN, epN;
#if ECC_TABLE_SPLIT_FORWARD
            tableAddendFromHist(p, 0, tid, twTab, &dpN, &epN);
#else
            tableSelectSlot(p, 0, tid, now, guard, twSel, twTab, &dpN, &epN);
#endif
            prod = dpN;
            store(p.pchain, 0, tid, p.threads, epN);
#if ECC_TABLE_SPLIT_FORWARD
            tableAddendFromHist(p, 1, tid, twTab, &dpN, &epN);
#else
            tableSelectSlot(p, 1, tid, now, guard, twSel, twTab, &dpN, &epN);
#endif
            // Twelve CLMADs per slot: the chain product prod*d first, because
            // its reduction gates the next slot's products, then W = prod*e.
            // W's reduction and store are deferred by one slot, into the time
            // the next slot's products spend in the unit, and the next slot's
            // selection also runs while the products are in flight.  (A
            // two-buffer form without the copy below measured slower, 116
            // registers against 110.)
            uint32_t hbPrev[9];
#pragma unroll 1
            for (int slot = 1; slot < ECC_BATCH; ++slot) {
                const P131 dp = dpN, ep = epN;
                uint32_t hb[9], hc[9];
                product131(prod, dp, hc);
                product131(prod, ep, hb);
                if (slot > 1) store(p.pchain, slot - 1, tid, p.threads, reducePolynomial131(hbPrev));
                if (slot + 1 < ECC_BATCH) {
#if ECC_TABLE_SPLIT_FORWARD
                    tableAddendFromHist(p, slot + 1, tid, twTab, &dpN, &epN);
#else
                    tableSelectSlot(p, slot + 1, tid, now, guard, twSel, twTab, &dpN, &epN);
#endif
                }
                prod = reducePolynomial131(hc);
#pragma unroll
                for (int i = 0; i < 9; ++i) hbPrev[i] = hb[i];
            }
            store(p.pchain, ECC_BATCH - 1, tid, p.threads, reducePolynomial131(hbPrev));
        }
#else
#if ECC_UNROLL_SLOTS >= 8
#pragma unroll 8
#elif ECC_UNROLL_SLOTS >= 4
#pragma unroll 4
#elif ECC_UNROLL_SLOTS > 1
#pragma unroll 2
#else
#pragma unroll 1
#endif
        for (int slot = 0; slot < ECC_BATCH; ++slot) {
#if ECC_PACKED_SLOT_PREFETCH
            if (slot + 1 < ECC_BATCH) {
                prefetch(p.x, slot + 1, tid, p.threads);
                prefetch(p.y, slot + 1, tid, p.threads);
            }
#endif
#if ECC_PACKED_SLOT_PIPELINE
            P131 x = pipeX;
            const P131 xp = x;
            const P131 yp = pipeY;
            if (slot + 1 < ECC_BATCH) {
                pipeX = load(p.x, slot + 1, tid, p.threads);
                pipeY = load(p.y, slot + 1, tid, p.threads);
            }
#else
            P131 x = load(p.x, slot, tid, p.threads);
#if ECC_WALK_TABLE || ECC_PACKED_SIGMA_TABLE
            const P131 xp = x;
#endif
#endif
#if ECC_PACKED_POLY_STATE
            x = fromPolynomial131(x);
#endif
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
#if ECC_PACKED_POLY_STATE
                        toLimbs(fromPolynomial131(load(p.y, slot, tid, p.threads)), rec.y);
#else
                        toLimbs(load(p.y, slot, tid, p.threads), rec.y);
#endif
                        for (int k = 0; k < ECC_JCOUNT; ++k) {
#if ECC_WITNESS
                            rec.counts[k] = p.counts[eccScalarCountIndex(slot, k, tid, p.threads)];
#else
                            rec.counts[k] = 0;
#endif
                        }
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
#if ECC_WALK_TABLE
            // Table walk: the branch, phase and sign come from the normal-basis
            // x and one coordinate of y; the addend is read from the table in
            // the polynomial basis, so nothing is converted and no Frobenius
            // network runs.  The second pass only needs dp and the chain.
#if !ECC_PACKED_SLOT_PIPELINE
            const P131 yp = load(p.y, slot, tid, p.threads);
#endif
            const unsigned tag = twSelect(x, yp, hw, p.hist + id, twSel, twTab, p.dpWeight
                                          ECC_CYCLE_PROFILE_ARG(p));
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
#if !ECC_TABLE_TAG_DENOM
            store(denominators, slot, tid, p.threads, dp);
#endif
#else
            const int j = 3 + ((hw >> 1) & 7);
#if ECC_WITNESS
            // One read-modify-write of the counter this walk's branch selects.
            // A lane that reported this step is dead from here and the step
            // belongs to the trail replacing it, so the report above having
            // set p.dead[id] is what excludes it.
            if (!p.dead[id]) p.counts[eccScalarCountIndex(slot, j - 3, tid, p.threads)] += 1u;
#endif
#if !ECC_PACKED_CACHE_DENOM
            js[slot] = j;
#endif
#if ECC_PACKED_WEIGHTED_PREFIX
#if ECC_PACKED_WEIGHTED_PREFIX == 2
#if ECC_PACKED_SIGMA_TABLE
            const P131 yp = load(p.y, slot, tid, p.threads);
            P131 dp = sigmaPlusTable131(xp, j - 3, sigmaBase);
            P131 ep = sigmaPlusTable131(yp, j - 3, sigmaBase);
#else
            const P131 normalY = fromPolynomial131(load(p.y, slot, tid, p.threads));
#if ECC_PACKED_SHARED_SIGMA
            const SigmaWalkPair131 sigmas = sigmaWalkNetworkPairShared131(x, normalY, j - 3);
#else
            const SigmaWalkPair131 sigmas = sigmaWalkNetworkPair131(x, normalY, j - 3);
#endif
            P131 d = add131(x, sigmas.first);
            P131 ep = toPolynomial131(add131(normalY, sigmas.second));
            P131 dp = toPolynomial131(d);
#endif
#else
            const P131 normalY = fromPolynomial131(load(p.y, slot, tid, p.threads));
            P131 d = add131(x, sigma131(x, j));
            P131 ep = toPolynomial131(add131(normalY, sigma131(normalY, j)));
            P131 dp = toPolynomial131(d);
#endif
            // W_i = E_i * product_{k<i}(D_k). Every prefix slot is used.
            if (slot) {
                PolynomialPair pair = mulPolynomialPair131(prod, ep, dp);
                store(p.pchain, slot, tid, p.threads, pair.first);
                prod = pair.second;
            } else {
                prod = dp;
                store(p.pchain, slot, tid, p.threads, ep);
            }
#else
            P131 d = add131(x, sigma131(x, j));
#if ECC_PACKED_POLY_CHAIN
            P131 dp = toPolynomial131(d);
            prod = slot == 0 ? dp : mulPolynomial131(prod, dp);
#if !ECC_PACKED_POLY_STATE
            store(polyDenominators, slot, tid, p.threads, dp);
#endif
#else
            prod = slot == 0 ? d : mul131(prod, d);
#endif
            if (slot + 1 < ECC_BATCH) store(p.pchain, slot, tid, p.threads, prod);
#endif
#if ECC_PACKED_CACHE_DENOM
            // The high 29 bits are unused by the field. Keep the jump index
            // beside the denominator, eliminating the separate local array.
#if ECC_PACKED_POLY_STATE
            dp.v[4] |= unsigned(j - 3) << 3;
            store(denominators, slot, tid, p.threads, dp);
#else
            d.v[4] |= unsigned(j - 3) << 3;
            store(denominators, slot, tid, p.threads, d);
#endif
#endif
#endif  // ECC_WALK_TABLE
        }
#endif  // ECC_TABLE_PIPE_SELECT
        ECC_PHASE_MARK(ph1);
#if ECC_PACKED_POLY_CHAIN
        inv = invPolynomial131(prod);
#else
        inv = inv131(prod);
#endif
#if ECC_PHASE_PROFILE
        // The inversion's result is consumed by the first reverse-pass product;
        // make the clock read after its last instruction by using a lane's word.
        if (inv.v[0] == 0xFFFFFFFFu && inv.v[1] == 0x12345678u) phaseCycles[3] = 1ull;
#endif
        ECC_PHASE_MARK(ph2);
#if ECC_PACKED_SLOT_PIPELINE
        pipeX = load(p.x, ECC_BATCH - 1, tid, p.threads);
        pipeY = load(p.y, ECC_BATCH - 1, tid, p.threads);
        pipeD = load(denominators, ECC_BATCH - 1, tid, p.threads);
        pipeC = load(p.pchain, ECC_BATCH - 1, tid, p.threads);
#endif
#if ECC_UNROLL_SLOTS >= 8
#pragma unroll 8
#elif ECC_UNROLL_SLOTS >= 4
#pragma unroll 4
#elif ECC_UNROLL_SLOTS > 1
#pragma unroll 2
#else
#pragma unroll 1
#endif
        for (int slot = ECC_BATCH - 1; slot >= 0; --slot) {
#if ECC_PACKED_SLOT_PREFETCH
            if (slot > 0) {
                prefetch(p.x, slot - 1, tid, p.threads);
                prefetch(p.y, slot - 1, tid, p.threads);
                prefetch(p.pchain, slot - 1, tid, p.threads);
                prefetch(denominators, slot - 1, tid, p.threads);
            }
#endif
#if ECC_PACKED_SLOT_PIPELINE
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
#else
            P131 x = load(p.x, slot, tid, p.threads), y = load(p.y, slot, tid, p.threads);
#if ECC_PACKED_WEIGHTED_PREFIX
#if ECC_TABLE_TAG_DENOM
            // The forward pass pushed this step's tag into hist; x is still the
            // pre-step polynomial coordinate, so d = x + x_T comes back from the
            // table for the cost of a 5-word shared read instead of a 17-byte
            // global store and load.
            P131 dp;
            twDenominator(unsigned(p.hist[size_t(slot) * p.threads + tid] & 0xFFFFu), x, twTab, &dp);
#else
            P131 dp = load(denominators, slot, tid, p.threads);
            dp.v[4] &= 7;
#endif
            P131 lambdaPoly;
            if (slot) {
#if ECC_PACKED_CHAIN_FIRST
                // The chain product inv*d gates the next slot; issue it first.
                PolynomialPair pair = mulPolynomialPair131(inv, dp,
                    load(p.pchain, slot, tid, p.threads));
                lambdaPoly = pair.second;
                inv = pair.first;
#else
                PolynomialPair pair = mulPolynomialPair131(inv,
                    load(p.pchain, slot, tid, p.threads), dp);
                lambdaPoly = pair.first;
                inv = pair.second;
#endif
            } else {
                lambdaPoly = mulPolynomial131(inv, load(p.pchain, 0, tid, p.threads));
            }
#else
#if ECC_PACKED_POLY_STATE
            P131 dp = load(denominators, slot, tid, p.threads), ii;
            const int j = 3 + ((dp.v[4] >> 3) & 7);
            dp.v[4] &= 7;
#if ECC_PACKED_SIGMA_TABLE
            P131 ep = sigmaPlusTable131(y, j - 3, sigmaBase);
#else
            const P131 normalY = fromPolynomial131(y);
            P131 ep = toPolynomial131(add131(normalY, sigma131(normalY, j)));
#endif
#elif ECC_PACKED_CACHE_DENOM
            P131 d = load(denominators, slot, tid, p.threads), ii;
            const int j = 3 + ((d.v[4] >> 3) & 7);
            d.v[4] &= 7;
            P131 e = add131(y, sigma131(y, j));
#else
            const int j = js[slot];
            P131 d = add131(x, sigma131(x, j)), e = add131(y, sigma131(y, j)), ii;
#endif
            if (slot) {
#if ECC_PACKED_POLY_CHAIN
#if ECC_PACKED_PAIR_PRODUCTS
                PolynomialPair pair=mulPolynomialPair131(inv,
                    load(p.pchain,slot-1,tid,p.threads),
#if ECC_PACKED_POLY_STATE
                    dp);
#else
                    load(polyDenominators,slot,tid,p.threads));
#endif
                ii=pair.first; inv=pair.second;
#else
                ii = mulPolynomial131(inv, load(p.pchain, slot - 1, tid, p.threads));
#if ECC_PACKED_POLY_STATE
                inv = mulPolynomial131(inv, dp);
#else
                inv = mulPolynomial131(inv, load(polyDenominators, slot, tid, p.threads));
#endif
#endif
#else
                ii = mul131(inv, load(p.pchain, slot - 1, tid, p.threads));
                inv = mul131(inv, d);
#endif
            } else ii = inv;
#if ECC_PACKED_POLY_STATE
            P131 lambdaPoly = mulPolynomial131(ep, ii);
#endif
#endif
#endif
#if ECC_PACKED_POLY_STATE
#if ECC_WALK_TABLE
            P131 nx = add131(add131(twSquare(lambdaPoly, twSel), lambdaPoly), dp);
#else
            P131 nx = add131(add131(squarePolynomial131(lambdaPoly), lambdaPoly), dp);
#endif
            P131 product = mulPolynomial131(lambdaPoly, add131(x, nx));
            P131 ny = add131(add131(product, nx), y);
#else
#if ECC_PACKED_POLY_CHAIN
            P131 lambdaPoly = mulPolynomial131(toPolynomial131(e), ii);
            P131 lambda = fromPolynomial131(lambdaPoly);
#else
            P131 lambda = mul131(e, ii);
#endif
            P131 nx = add131(add131(sqr131(lambda), lambda), d);
#if ECC_PACKED_POLY_CHAIN
            P131 product = mulPolynomial131(lambdaPoly, toPolynomial131(add131(x, nx)));
            P131 ny = add131(add131(fromPolynomial131(product), nx), y);
#else
            P131 ny = add131(add131(mul131(lambda, add131(x, nx)), nx), y);
#endif
#endif
            store(p.x, slot, tid, p.threads, nx);
            store(p.y, slot, tid, p.threads, ny);
#if ECC_PACKED_SLOT_PIPELINE
            // Reverse pass ends at slot 0 with pipeX/pipeY still holding that
            // slot's pre-add coordinates. Refresh them so the next step's first
            // pass does not rebuild the addend from a stale point.
            if (!slot) {
                pipeX = nx;
                pipeY = ny;
            }
#endif
        }
        ECC_PHASE_MARK(ph3);
        ECC_PHASE_ADD(0, ph0, ph1);
        ECC_PHASE_ADD(1, ph1, ph2);
        ECC_PHASE_ADD(2, ph2, ph3);
    }
}
#endif  // ECC_TABLE_FUSED
#else
#include "packedxonly23.cuh"
#endif  // !ECC_PACKED_XONLY_23

#if ECC_PHASE_PROFILE
// Host: read and reset the phase counters.  `warpSteps` is the number of
// (warp, step) pairs the counters cover, so the result is cycles per warp-step.
inline void phaseProfileReport(double warpSteps) {
    unsigned long long h[4] = {0, 0, 0, 0};
    cudaMemcpyFromSymbol(h, phaseCycles, sizeof(h));
    const double tot = double(h[0] + h[1] + h[2]);
    fprintf(stderr,
            "phase profile: per warp-step %.0f cycles = forward %.0f (%.1f%%) + inversion %.0f (%.1f%%) + reverse %.0f (%.1f%%); per update %.1f warp-cycles\n",
            tot / warpSteps, h[0] / warpSteps, 100.0 * h[0] / tot, h[1] / warpSteps, 100.0 * h[1] / tot,
            h[2] / warpSteps, 100.0 * h[2] / tot, tot / warpSteps / ECC_BATCH);
    unsigned long long z[4] = {0, 0, 0, 0};
    cudaMemcpyToSymbol(phaseCycles, z, sizeof(z));
}
#endif
} // namespace eccPacked131
