// Selected walk body, renamed only.
#pragma once
#include "packedblockinversereference131.cuh"
namespace eccPacked131 {
static __global__ void ECC_BOUNDS walkSharedXReference131(WalkParams<unsigned> p, unsigned *denominators) {
#if ECC_PACKED_BATCH_SPLIT == 2
    // Paired physical threads own alternating slots of one logical worker.
    const int tid = blockIdx.x * walkWorkersPerBlock131 + threadIdx.x % walkWorkersPerBlock131;
    const int firstSlot = threadIdx.x / walkWorkersPerBlock131;
#else
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    const int firstSlot = 0;
#endif
#if ECC_PACKED_SHARED_SIGMA && !ECC_PACKED_FUSED_SIGMA
    // All block threads participate, including inactive partial-tile workers.
    initSigmaWalkShared131();
#endif
#if ECC_PACKED_FUSED_SIGMA
    initFusedDelta131();
#endif
#if ECC_PACKED_BLOCK_INVERSE
    const bool active = tid < p.threads;
    __shared__ uint32_t inverseTree[blockInverseReferenceWords131 + (ECC_PACKED_BATCH_SPLIT == 2 ? ECC_THREADS : 0)];
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
#pragma unroll 1
    for (int step = 0; step < p.steps; ++step) {
        const unsigned long long now = p.iterBase + step;
        const bool guard = p.maxIters && now % ECC_GUARD_PERIOD == 0;
#if ECC_PACKED_LAST_SLOT_CACHE
        P131 lastDenominator{};
#if ECC_PACKED_LAST_SLOT_CACHE == 2
        P131 lastWeightedPrefix{};
#endif
#endif
#if ECC_PACKED_BLOCK_INVERSE
        prod = P131{{1,0,0,0,0}};
        if (active) {
#endif
#pragma unroll 1
        for (int localSlot = 0; localSlot < ECC_BATCH / ECC_PACKED_BATCH_SPLIT; ++localSlot) {
            const int slot = localSlot * ECC_PACKED_BATCH_SPLIT + firstSlot;
            P131 x = load(p.x, slot, tid, p.threads);
#if ECC_PACKED_FUSED_SIGMA
            const P131 polyX=x;
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
            const int j = 3 + ((hw >> 1) & 7);
#if !ECC_PACKED_CACHE_DENOM
            js[slot] = j;
#endif
#if ECC_PACKED_WEIGHTED_PREFIX
#if ECC_PACKED_FUSED_SIGMA
            const auto deltas=fusedDeltaPair131(polyX,load(p.y,slot,tid,p.threads),j-3);
            P131 dp=deltas.first, ep=deltas.second;
#else
            const P131 normalY = fromPolynomial131(load(p.y, slot, tid, p.threads));
#if ECC_PACKED_WEIGHTED_PREFIX == 2
#if ECC_PACKED_SHARED_SIGMA
            const SigmaWalkPair131 sigmas = sigmaWalkNetworkPairShared131(x, normalY, j - 3);
#else
            const SigmaWalkPair131 sigmas = sigmaWalkNetworkPair131(x, normalY, j - 3);
#endif
            P131 d = add131(x, sigmas.first);
            P131 ep = toPolynomial131(add131(normalY, sigmas.second));
#else
            P131 d = add131(x, sigma131(x, j));
            P131 ep = toPolynomial131(add131(normalY, sigma131(normalY, j)));
#endif
            P131 dp = toPolynomial131(d);
#endif
            // W_i = E_i * product_{k<i}(D_k). Every prefix slot is used.
            if (localSlot) {
                PolynomialPair pair = mulPolynomialPair131(prod, ep, dp);
#if ECC_PACKED_LAST_SLOT_CACHE == 2
                if (localSlot == lastLocalSlot131) lastWeightedPrefix = pair.first;
                else
#endif
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
#if ECC_PACKED_LAST_SLOT_CACHE
            if (localSlot == lastLocalSlot131) lastDenominator = dp;
            else
#endif
            store(denominators, slot, tid, p.threads, dp);
#else
            d.v[4] |= unsigned(j - 3) << 3;
            store(denominators, slot, tid, p.threads, d);
#endif
#endif
        }
#if ECC_PACKED_BLOCK_INVERSE
        } // active forward state access
        inv = blockInverseReference131<(ECC_PACKED_BATCH_SPLIT == 2)>(prod, inverseTree);
#elif ECC_PACKED_POLY_CHAIN
        inv = toPolynomial131(inv131(fromPolynomial131(prod)));
#else
        inv = inv131(prod);
#endif
#if ECC_PACKED_BLOCK_INVERSE
        if (active) {
#endif
#pragma unroll 1
        for (int localSlot = ECC_BATCH / ECC_PACKED_BATCH_SPLIT - 1; localSlot >= 0; --localSlot) {
            const int slot = localSlot * ECC_PACKED_BATCH_SPLIT + firstSlot;
            P131 x = load(p.x, slot, tid, p.threads), y = load(p.y, slot, tid, p.threads);
#if ECC_PACKED_WEIGHTED_PREFIX
#if ECC_PACKED_LAST_SLOT_CACHE
            P131 dp = localSlot == lastLocalSlot131 ? lastDenominator : load(denominators, slot, tid, p.threads);
#else
            P131 dp = load(denominators, slot, tid, p.threads);
#endif
            dp.v[4] &= 7;
            P131 lambdaPoly;
            if (localSlot) {
                PolynomialPair pair = mulPolynomialPair131(inv,
#if ECC_PACKED_LAST_SLOT_CACHE == 2
                    localSlot == lastLocalSlot131 ? lastWeightedPrefix : load(p.pchain, slot, tid, p.threads), dp);
#else
                    load(p.pchain, slot, tid, p.threads), dp);
#endif
                lambdaPoly = pair.first;
                inv = pair.second;
            } else {
                lambdaPoly = mulPolynomial131(inv, load(p.pchain, firstSlot, tid, p.threads));
            }
#else
#if ECC_PACKED_POLY_STATE
            P131 dp = load(denominators, slot, tid, p.threads), ii;
            const int j = 3 + ((dp.v[4] >> 3) & 7);
            dp.v[4] &= 7;
            const P131 normalY = fromPolynomial131(y);
            P131 ep = toPolynomial131(add131(normalY, sigma131(normalY, j)));
#elif ECC_PACKED_CACHE_DENOM
            P131 d = load(denominators, slot, tid, p.threads), ii;
            const int j = 3 + ((d.v[4] >> 3) & 7);
            d.v[4] &= 7;
            P131 e = add131(y, sigma131(y, j));
#else
            const int j = js[slot];
            P131 d = add131(x, sigma131(x, j)), e = add131(y, sigma131(y, j)), ii;
#endif
            if (localSlot) {
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
#if ECC_PACKED_POLY_STATE
            P131 nx = add131(add131(squarePolynomial131(lambdaPoly), lambdaPoly), dp);
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
        }
#if ECC_PACKED_BLOCK_INVERSE
        } // active backward state access
#endif
    }
}
} // namespace eccPacked131
