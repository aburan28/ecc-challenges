// kernel.h - the launch parameters and the seed schedule of the packed walk.
//
// The source repository's kernel.h is the driver of its bitsliced backend and
// carries much more.  The packed kernel needs only what is here: the parameter
// block, the distinguished-point record, the per-lane seed derivation and the
// two guard constants.  Everything is kept bit-for-bit compatible with the
// source so that a report written by this client re-walks under either tree's
// reference.
#pragma once

#include <stdint.h>

#include "bitslice.h"

#ifndef ECC_BATCH
#    define ECC_BATCH 16
#endif
#ifndef ECC_THREADS
#    define ECC_THREADS 512
#endif
#ifndef ECC_MINBLOCKS
#    define ECC_MINBLOCKS 1
#endif
// The overdue-walk guard (`--max-iters`) is evaluated once in this many steps.
#ifndef ECC_GUARD_PERIOD
#    define ECC_GUARD_PERIOD 4096
#endif

// A distinguished point as the device reports it and as the corpus stores
// it: 64 bytes, little-endian, the coordinates in the permuted type-II normal
// basis as three 64-bit limbs (bit i-1 of the 131 is the coefficient of
// beta_i, the same convention as fpga/model/ecc2k130.h).
struct DpRecord {
    unsigned long long seed;
    unsigned long long iters;
    unsigned long long x[3];
    unsigned long long y[3];
};

// The PRF behind every walk's start point: two 64-bit words per seed select
// which of sigma^0(P) .. sigma^127(P) are added to Q.
ECC_HD unsigned long long eccPrf(unsigned long long seed, int idx)
{
    unsigned long long z = seed + 0x9E3779B97F4A7C15ull * (unsigned long long)(idx + 1);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

// Seed of lane `walkIndex` of run `runId`: the low 16 bits count restarts of
// that lane, so two runs with different ids never share a trail.
ECC_HD unsigned long long eccSeedFor(unsigned runId, unsigned long long walkIndex)
{
    return ((unsigned long long)runId << 48) | ((walkIndex & 0xFFFFFFFFull) << 16);
}

template <class W> struct WalkParams {
    int threads;
    int steps;
    int dpWeight;
    unsigned runId;
    unsigned long long maxIters; // 0 disables the overdue guard
    unsigned long long iterBase;
    W *x;
    W *y;
    W *pchain;
    unsigned long long *seed;      // one per lane
    unsigned long long *startIter; // one per lane
    W *dead;                       // one word per lane: awaiting a restart
    DpRecord *dp;
    unsigned *dpCount; // reports, overdue restarts, exhausted-seed flag
    unsigned dpCap;
    // Table walk: the last four step tags of every lane, and the flat constant
    // buffer packedtablewalk.cuh copies into shared memory.
    unsigned long long *hist;
    const unsigned *twConsts;
};

#if defined(__CUDACC__)
#    define ECC_BOUNDS __launch_bounds__(ECC_THREADS, ECC_MINBLOCKS)
#else
#    define ECC_BOUNDS
#endif
