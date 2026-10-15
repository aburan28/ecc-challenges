// X-only [2]P/[3]P walk for y^2 + xy = x^3 + 1.
// Included inside namespace eccPacked131 by packedkernels.cuh.
#pragma once

#ifndef ECC_PACKED_XONLY_DOUBLE_ONLY
#define ECC_PACKED_XONLY_DOUBLE_ONLY 0
#endif
#if ECC_PACKED_XONLY_DOUBLE_ONLY != 0 && ECC_PACKED_XONLY_DOUBLE_ONLY != 1
#error "ECC_PACKED_XONLY_DOUBLE_ONLY must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE
#define ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE 0
#endif
#if ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE != 0 && ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE != 1
#error "ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_ARITHMETIC_ONLY
#define ECC_PACKED_XONLY_ARITHMETIC_ONLY 0
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY != 0 && ECC_PACKED_XONLY_ARITHMETIC_ONLY != 1
#error "ECC_PACKED_XONLY_ARITHMETIC_ONLY must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT
#define ECC_PACKED_XONLY_POLY_SELECT 0
#endif
#if ECC_PACKED_XONLY_POLY_SELECT != 0 && ECC_PACKED_XONLY_POLY_SELECT != 1
#error "ECC_PACKED_XONLY_POLY_SELECT must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_POLY_DP_CONVERT
#define ECC_PACKED_XONLY_POLY_DP_CONVERT 1
#endif
#if ECC_PACKED_XONLY_POLY_DP_CONVERT != 0 && ECC_PACKED_XONLY_POLY_DP_CONVERT != 1
#error "ECC_PACKED_XONLY_POLY_DP_CONVERT must be 0 or 1"
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT_MASK
#define ECC_PACKED_XONLY_POLY_SELECT_MASK 0xfffu
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT_TARGET
#define ECC_PACKED_XONLY_POLY_SELECT_TARGET 14u
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY && ECC_PACKED_XONLY_BRIDGE3
#error "Arithmetic-only diagnostic cannot share the sparse bridge queue"
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY && ECC_PACKED_XONLY_POLY_SELECT
#error "Arithmetic-only and polynomial-bit selection are mutually exclusive"
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY && ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE
#error "Arithmetic-only has no rare phase to skip"
#endif
#if ECC_PACKED_XONLY_POLY_SELECT && !ECC_PACKED_XONLY_BRIDGE3
#error "Polynomial-bit selection requires the sparse bridge-3 queue"
#endif
#if ECC_PACKED_XONLY_POLY_SELECT && ECC_PACKED_XONLY_BRIDGE_MOD72
#error "Polynomial-bit selection replaces the modulus-72 Hamming selector"
#endif
#if !ECC_PACKED_XONLY_POLY_DP_CONVERT && \
    !ECC_PACKED_XONLY_POLY_SELECT && !ECC_PACKED_XONLY_ARITHMETIC_ONLY
#error "Skipping Hamming conversion requires polynomial-bit selection or arithmetic-only"
#endif

#if !ECC_PACKED_BLOCK_INVERSE || !ECC_PACKED_POLY_STATE || \
    ECC_PACKED_WEIGHTED_PREFIX != 2 || !ECC_PACKED_CACHE_DENOM
#error "The x-only 2/3 walk requires polynomial state, cached denominators, weighted prefixes, and block inversion"
#endif

static __device__ __forceinline__ PolynomialPair sparseBridge3Rational131(P131 x) {
    const P131 x2 = squarePolynomial131(x);
    const P131 x4 = squarePolynomial131(x2);
    const P131 x3 = mulPolynomial131(x, x2);
    const P131 x5 = mulPolynomial131(x, x4);
    const P131 x6 = mulPolynomial131(x2, x4);
    const P131 x7 = mulPolynomial131(x, x6);
    const P131 one{{1, 0, 0, 0, 0}};
    const P131 a = add131(add131(add131(add131(add131(x7, x6), x4), x3), x), one);
    const P131 b = add131(add131(add131(add131(add131(add131(x6, x5), x4), x3), x2), x), one);
    return PolynomialPair{squarePolynomial131(a),
        mulPolynomial131(x, squarePolynomial131(b))};
}

static __device__ __forceinline__ bool polySelectBridge3_131(P131 x) {
    return (x.v[0] & ECC_PACKED_XONLY_POLY_SELECT_MASK) ==
           ECC_PACKED_XONLY_POLY_SELECT_TARGET;
}

static __global__ void ECC_BOUNDS walk(WalkParams<unsigned> p, unsigned *denominators) {
#if ECC_PACKED_BATCH_SPLIT == 2
    const int tid = blockIdx.x * walkWorkersPerBlock131 + threadIdx.x % walkWorkersPerBlock131;
    const int firstSlot = threadIdx.x / walkWorkersPerBlock131;
#else
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    const int firstSlot = 0;
#endif
    const bool active = tid < p.threads;
    __shared__ uint32_t inverseTree[blockInverseWords131 +
        (ECC_PACKED_BATCH_SPLIT == 2 ? blockInverseFlagWords131 : 0)];
#if !ECC_PACKED_XONLY_ARITHMETIC_ONLY
    __shared__ unsigned bridgeCount131;
    __shared__ unsigned short bridgeQueue131[ECC_THREADS *
        (ECC_BATCH / ECC_PACKED_BATCH_SPLIT)];
    __shared__ unsigned char bridgeMasks131[ECC_THREADS];
#endif
#if ECC_PACKED_SHARED_X_SLOTS
    __shared__ uint4 sharedXLow[ECC_PACKED_SHARED_X_SLOTS * ECC_THREADS];
    __shared__ unsigned char sharedXTail[ECC_PACKED_SHARED_X_SLOTS * ECC_THREADS];
    if (active && p.steps > 0) {
#pragma unroll
        for (int cacheSlot = 0; cacheSlot < ECC_PACKED_SHARED_X_SLOTS; ++cacheSlot) {
            const int slot = (firstSharedXLocalSlot131 + cacheSlot) * ECC_PACKED_BATCH_SPLIT + firstSlot;
            const int index = cacheSlot * ECC_THREADS + threadIdx.x;
            storeSharedX131(sharedXLow, sharedXTail, index, load(p.x, slot, tid, p.threads));
        }
    }
#endif
    P131 prod, inv;
#if !ECC_PACKED_XONLY_ARITHMETIC_ONLY
    // Prime the classification pipeline with the launch's initial state.
    if (p.steps > 0) {
        const unsigned long long now = p.iterBase;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;

        // Classify first so warp 0 can materialize only rare rational data
        // before the common denominator tree begins.
        if (threadIdx.x == 0) bridgeCount131 = 0;
        __syncthreads();
        unsigned localBridgeMask = 0;
        if (active) {
#pragma unroll 1
            for (int localSlot = 0;
                 localSlot < ECC_BATCH / ECC_PACKED_BATCH_SPLIT; ++localSlot) {
                const int slot = localSlot * ECC_PACKED_BATCH_SPLIT + firstSlot;
#if ECC_PACKED_SHARED_X_SLOTS
                const P131 x = localSlot >= firstSharedXLocalSlot131
                    ? loadSharedX131(sharedXLow, sharedXTail,
                        (localSlot - firstSharedXLocalSlot131) * ECC_THREADS +
                            threadIdx.x)
                    : load(p.x, slot, tid, p.threads);
#else
                const P131 x = load(p.x, slot, tid, p.threads);
#endif
                const size_t id = size_t(slot) * p.threads + tid;
#if ECC_PACKED_XONLY_POLY_SELECT && !ECC_PACKED_XONLY_POLY_DP_CONVERT
                if (!p.dead[id]) {
                    if ((x.v[0] | x.v[1] | x.v[2] | x.v[3] | x.v[4]) == 0u) {
                        if ((p.seed[id] & 0xffffull) == 0xffffull)
                            atomicAdd(p.dpCount + 2, 1u);
                        const unsigned dest = atomicAdd(p.dpCount, 1u);
                        if (dest < p.dpCap) {
                            DpRecord rec{};
                            rec.seed = p.seed[id];
                            rec.iters = now - p.startIter[id];
                            toLimbs(x, rec.x);
                            p.dp[dest] = rec;
                        }
                        p.dead[id] = 1;
                    } else if (guard && now - p.startIter[id] >= p.maxIters) {
                        if ((p.seed[id] & 0xffffull) == 0xffffull)
                            atomicAdd(p.dpCount + 2, 1u);
                        p.dead[id] = 1;
                        atomicAdd(p.dpCount + 1, 1u);
                    }
                }
#else
                const P131 normalX = fromPolynomial131(x);
                const int hw = weight(normalX);
                if (!p.dead[id]) {
                    if (hw <= p.dpWeight) {
                        if ((p.seed[id] & 0xffffull) == 0xffffull)
                            atomicAdd(p.dpCount + 2, 1u);
                        const unsigned dest = atomicAdd(p.dpCount, 1u);
                        if (dest < p.dpCap) {
                            DpRecord rec{};
                            rec.seed = p.seed[id];
                            rec.iters = now - p.startIter[id];
                            toLimbs(normalX, rec.x);
                            p.dp[dest] = rec;
                        }
                        p.dead[id] = 1;
                    } else if (guard && now - p.startIter[id] >= p.maxIters) {
                        if ((p.seed[id] & 0xffffull) == 0xffffull)
                            atomicAdd(p.dpCount + 2, 1u);
                        p.dead[id] = 1;
                        atomicAdd(p.dpCount + 1, 1u);
                    }
                }
#endif
#if ECC_PACKED_XONLY_POLY_SELECT
                if (polySelectBridge3_131(x)) {
#else
                if (ECC_PACKED_XONLY_BRIDGE3 && ECC_PACKED_XONLY_IS_BRIDGE3(hw)) {
#endif
                    localBridgeMask |= 1u << localSlot;
                    const unsigned event = atomicAdd(&bridgeCount131, 1u);
                    bridgeQueue131[event] =
                        static_cast<unsigned short>(localSlot * ECC_THREADS +
                                                    threadIdx.x);
                }
            }
        }
        bridgeMasks131[threadIdx.x] =
            static_cast<unsigned char>(localBridgeMask);
        __syncthreads();
    }
#endif
#pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        const unsigned long long now = p.iterBase + step;

#if !ECC_PACKED_XONLY_ARITHMETIC_ONLY
        // Rare arithmetic has a separate lifetime and is executed by one warp.
        // With modulus 72 the queue is empty on most block-steps; skip that
        // warp and the following barriers when no event is queued.
#if ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE
        const unsigned rareCount = bridgeCount131;
        if (rareCount != 0) {
#endif
        if (threadIdx.x < 32) {
            const int lane = threadIdx.x;
            for (unsigned base = 0; base < bridgeCount131; base += 32) {
                if (base + lane < bridgeCount131) {
                    const unsigned encoded = bridgeQueue131[base + lane];
                    const int localSlot = int(encoded / ECC_THREADS);
                    const int owner = int(encoded % ECC_THREADS);
                    const int ownerFirstSlot = owner / walkWorkersPerBlock131;
                    const int ownerTid = blockIdx.x * walkWorkersPerBlock131 +
                        owner % walkWorkersPerBlock131;
                    const int slot = localSlot * ECC_PACKED_BATCH_SPLIT +
                        ownerFirstSlot;
#if ECC_PACKED_SHARED_X_SLOTS
                    const P131 x = localSlot >= firstSharedXLocalSlot131
                        ? loadSharedX131(sharedXLow, sharedXTail,
                            (localSlot - firstSharedXLocalSlot131) *
                                ECC_THREADS + owner)
                        : load(p.x, slot, ownerTid, p.threads);
#else
                    const P131 x = load(p.x, slot, ownerTid, p.threads);
#endif
                    const PolynomialPair rational =
                        sparseBridge3Rational131(x);
                    store(p.y, slot, ownerTid, p.threads, rational.first);
                    store(denominators, slot, ownerTid, p.threads,
                          rational.second);
                }
            }
        }
        __syncthreads();
        if (step + 1 < p.steps) {
            if (threadIdx.x == 0) bridgeCount131 = 0;
            __syncthreads();
        }
#if ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE
        }
#endif
#endif
#if ECC_PACKED_LAST_SLOT_CACHE
        P131 lastDenominator{};
#if ECC_PACKED_LAST_SLOT_CACHE == 2
        P131 lastWeightedPrefix{};
#endif
#endif
        prod = P131{{1, 0, 0, 0, 0}};
        if (active) {
#pragma unroll 1
            for (int localSlot = 0; localSlot < ECC_BATCH / ECC_PACKED_BATCH_SPLIT; ++localSlot) {
                const int slot = localSlot * ECC_PACKED_BATCH_SPLIT + firstSlot;
#if ECC_PACKED_SHARED_X_SLOTS
                P131 x = localSlot >= firstSharedXLocalSlot131
                    ? loadSharedX131(sharedXLow, sharedXTail,
                        (localSlot - firstSharedXLocalSlot131) * ECC_THREADS + threadIdx.x)
                    : load(p.x, slot, tid, p.threads);
#else
                P131 x = load(p.x, slot, tid, p.threads);
#endif
#if !ECC_PACKED_XONLY_DOUBLE_ONLY
                const P131 normalX = fromPolynomial131(x);
                const int hw = weight(normalX);
#endif
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY
                const bool sparseBridge = false;
#else
                const bool sparseBridge =
                    (bridgeMasks131[threadIdx.x] >> localSlot) & 1u;
#endif
#if ECC_PACKED_XONLY_DOUBLE_ONLY
                // Ordinary states use x; warp 0 precomputed rare
                // bridge denominators into the existing scratch field.
                const P131 denominator = sparseBridge
                    ? load(denominators, slot, tid, p.threads) : x;
                if (localSlot) {
                    store(p.pchain, slot, tid, p.threads, prod);
                    prod = mulPolynomial131(prod, denominator);
                } else {
                    prod = denominator;
                }
#if ECC_PACKED_LAST_SLOT_CACHE
                if (localSlot == lastLocalSlot131) lastDenominator = denominator;
                else
#endif
                store(denominators, slot, tid, p.threads, denominator);
#else
                // Polynomial-basis forms of:
                // [2]: (x^4 + 1) / x^2
                // [3]: x (x^4 + x + 1)^2 / (x^4 + x^3 + 1)^2
                const P131 x2 = squarePolynomial131(x);
                const P131 x4 = squarePolynomial131(x2);
                const P131 one{{1, 0, 0, 0, 0}};
                P131 numerator, denominator;
                if ((hw >> 1) & 1) {
                    const P131 x3 = mulPolynomial131(x, x2);
                    const P131 a = add131(add131(x4, x), one);
                    numerator = mulPolynomial131(x, squarePolynomial131(a));
                    const P131 b = add131(add131(x4, x3), one);
                    denominator = squarePolynomial131(b);
                } else {
                    numerator = add131(x4, one);
                    denominator = x2;
                }

                if (localSlot) {
                    const PolynomialPair pair =
                        mulPolynomialPair131(prod, numerator, denominator);
#if ECC_PACKED_LAST_SLOT_CACHE == 2
                    if (localSlot == lastLocalSlot131) lastWeightedPrefix = pair.first;
                    else
#endif
                    store(p.pchain, slot, tid, p.threads, pair.first);
                    prod = pair.second;
                } else {
                    prod = denominator;
                    store(p.pchain, slot, tid, p.threads, numerator);
                }
#if ECC_PACKED_LAST_SLOT_CACHE
                if (localSlot == lastLocalSlot131) lastDenominator = denominator;
                else
#endif
                store(denominators, slot, tid, p.threads, denominator);
#endif
            }
        }

        // Inactive threads still enter the block-wide inverse collectives.
        inv = blockInverse131<(ECC_PACKED_BATCH_SPLIT == 2)>(prod, inverseTree);
#if !ECC_PACKED_XONLY_ARITHMETIC_ONLY
        unsigned nextBridgeMask = 0;
#endif
        if (active) {
#pragma unroll 1
            for (int localSlot = ECC_BATCH / ECC_PACKED_BATCH_SPLIT - 1;
                 localSlot >= 0; --localSlot) {
                const int slot = localSlot * ECC_PACKED_BATCH_SPLIT + firstSlot;
#if ECC_PACKED_LAST_SLOT_CACHE
                const P131 denominator = localSlot == lastLocalSlot131
                    ? lastDenominator : load(denominators, slot, tid, p.threads);
#else
                const P131 denominator = load(denominators, slot, tid, p.threads);
#endif
#if ECC_PACKED_XONLY_DOUBLE_ONLY
#if ECC_PACKED_XONLY_ARITHMETIC_ONLY
                const bool sparseBridge = false;
#else
                const bool sparseBridge =
                    (bridgeMasks131[threadIdx.x] >> localSlot) & 1u;
#endif
#if ECC_PACKED_SHARED_X_SLOTS
                const P131 x = localSlot >= firstSharedXLocalSlot131
                    ? loadSharedX131(sharedXLow, sharedXTail,
                        (localSlot - firstSharedXLocalSlot131) * ECC_THREADS + threadIdx.x)
                    : load(p.x, slot, tid, p.threads);
#else
                const P131 x = load(p.x, slot, tid, p.threads);
#endif
                P131 inverseX;
                if (localSlot) {
                    const PolynomialPair pair = mulPolynomialPair131(inv,
                        load(p.pchain, slot, tid, p.threads), denominator);
                    inverseX = pair.first;
                    inv = pair.second;
                } else {
                    inverseX = inv;
                }
                const P131 nx = sparseBridge
                    ? mulPolynomial131(inverseX,
                        load(p.y, slot, tid, p.threads))
#if ECC_PACKED_XONLY_BRIDGE1_COMMON
                    : add131(x, inverseX);
#else
                    : squarePolynomial131(add131(x, inverseX));
#endif
#else
                P131 nx;
                if (localSlot) {
                    const PolynomialPair pair = mulPolynomialPair131(inv,
#if ECC_PACKED_LAST_SLOT_CACHE == 2
                        localSlot == lastLocalSlot131
                            ? lastWeightedPrefix : load(p.pchain, slot, tid, p.threads),
#else
                        load(p.pchain, slot, tid, p.threads),
#endif
                        denominator);
                    nx = pair.first;
                    inv = pair.second;
                } else {
                    nx = mulPolynomial131(
                        inv, load(p.pchain, firstSlot, tid, p.threads));
                }
#endif
#if ECC_PACKED_SHARED_X_SLOTS
                if (localSlot >= firstSharedXLocalSlot131)
                    storeSharedX131(sharedXLow, sharedXTail,
                        (localSlot - firstSharedXLocalSlot131) * ECC_THREADS +
                            threadIdx.x,
                        nx);
                else
                    store(p.x, slot, tid, p.threads, nx);
#else
                store(p.x, slot, tid, p.threads, nx);
#endif
#if !ECC_PACKED_XONLY_ARITHMETIC_ONLY
                if (step + 1 < p.steps) {
                    const unsigned long long nextNow = now + 1;
                    const bool nextGuard = p.maxIters &&
                        nextNow % ECC_GUARD_PERIOD == 0;
                    const size_t id = size_t(slot) * p.threads + tid;
#if ECC_PACKED_XONLY_POLY_SELECT && !ECC_PACKED_XONLY_POLY_DP_CONVERT
                    if (!p.dead[id]) {
                        if ((nx.v[0] | nx.v[1] | nx.v[2] | nx.v[3] | nx.v[4]) == 0u) {
                            if ((p.seed[id] & 0xffffull) == 0xffffull)
                                atomicAdd(p.dpCount + 2, 1u);
                            const unsigned dest = atomicAdd(p.dpCount, 1u);
                            if (dest < p.dpCap) {
                                DpRecord rec{};
                                rec.seed = p.seed[id];
                                rec.iters = nextNow - p.startIter[id];
                                toLimbs(nx, rec.x);
                                p.dp[dest] = rec;
                            }
                            p.dead[id] = 1;
                        } else if (nextGuard &&
                                   nextNow - p.startIter[id] >= p.maxIters) {
                            if ((p.seed[id] & 0xffffull) == 0xffffull)
                                atomicAdd(p.dpCount + 2, 1u);
                            p.dead[id] = 1;
                            atomicAdd(p.dpCount + 1, 1u);
                        }
                    }
#else
                    const P131 normalX = fromPolynomial131(nx);
                    const int hw = weight(normalX);
                    if (!p.dead[id]) {
                        if (hw <= p.dpWeight) {
                            if ((p.seed[id] & 0xffffull) == 0xffffull)
                                atomicAdd(p.dpCount + 2, 1u);
                            const unsigned dest = atomicAdd(p.dpCount, 1u);
                            if (dest < p.dpCap) {
                                DpRecord rec{};
                                rec.seed = p.seed[id];
                                rec.iters = nextNow - p.startIter[id];
                                toLimbs(normalX, rec.x);
                                p.dp[dest] = rec;
                            }
                            p.dead[id] = 1;
                        } else if (nextGuard &&
                                   nextNow - p.startIter[id] >= p.maxIters) {
                            if ((p.seed[id] & 0xffffull) == 0xffffull)
                                atomicAdd(p.dpCount + 2, 1u);
                            p.dead[id] = 1;
                            atomicAdd(p.dpCount + 1, 1u);
                        }
                    }
#endif
#if ECC_PACKED_XONLY_POLY_SELECT
                    if (polySelectBridge3_131(nx)) {
#else
                    if (ECC_PACKED_XONLY_BRIDGE3 && ECC_PACKED_XONLY_IS_BRIDGE3(hw)) {
#endif
                        nextBridgeMask |= 1u << localSlot;
                        const unsigned event = atomicAdd(&bridgeCount131, 1u);
                        bridgeQueue131[event] = static_cast<unsigned short>(
                            localSlot * ECC_THREADS + threadIdx.x);
                    }
                }
#endif
            }
        }
#if !ECC_PACKED_XONLY_ARITHMETIC_ONLY
        if (step + 1 < p.steps) {
            bridgeMasks131[threadIdx.x] =
                static_cast<unsigned char>(nextBridgeMask);
            __syncthreads();
        }
#endif
    }
#if ECC_PACKED_SHARED_X_SLOTS
    if (active && p.steps > 0) {
#pragma unroll
        for (int cacheSlot = 0; cacheSlot < ECC_PACKED_SHARED_X_SLOTS; ++cacheSlot) {
            const int slot = (firstSharedXLocalSlot131 + cacheSlot) * ECC_PACKED_BATCH_SPLIT + firstSlot;
            const int index = cacheSlot * ECC_THREADS + threadIdx.x;
            store(p.x, slot, tid, p.threads,
                  loadSharedX131(sharedXLow, sharedXTail, index));
        }
    }
#endif
}
