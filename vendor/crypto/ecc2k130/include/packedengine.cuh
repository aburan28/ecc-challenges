// Included by main.cu after the common CUDA engine and checkpoint helpers.
#pragma once
#include "packedkernels.cuh"
#ifndef ECC_PACKED_XONLY_ARITHMETIC_ONLY
#define ECC_PACKED_XONLY_ARITHMETIC_ONLY 0
#endif
#ifndef ECC_PACKED_XONLY_POLY_SELECT
#define ECC_PACKED_XONLY_POLY_SELECT 0
#endif
#ifndef ECC_PACKED_XONLY_POLY_DP_CONVERT
#define ECC_PACKED_XONLY_POLY_DP_CONVERT 1
#endif

// Diagnostic builds only: mark a completed walk launch for Nsight range replay.
// Timed benchmark builds leave this undefined (zero).
#ifndef ECC_PROFILE_RANGE
#define ECC_PROFILE_RANGE 0
#endif
#ifndef ECC_PACKED_L2_PERSIST
#define ECC_PACKED_L2_PERSIST 0
#endif
#if ECC_PACKED_L2_PERSIST != 0 && ECC_PACKED_L2_PERSIST != 1
#error "ECC_PACKED_L2_PERSIST must be 0 or 1"
#endif
#if ECC_PROFILE_RANGE
#include <cuda_profiler_api.h>
#endif

struct PackedCudaEngine : CudaEngine<CfgF131> {
    static const int LANES = 1;
    bool restartPending = false;
    unsigned *denominators = nullptr;
    unsigned *twConsts = nullptr;
#if ECC_TABLE_GLOBAL_HINTS
    unsigned *globalHintQueue = nullptr, *globalHintCount = nullptr;
    int globalResolverBlocks = 0, globalResolverBlocksPerSm = 0;
#endif
#if ECC_PACKED_L2_PERSIST
    // One allocation: the access-policy window is a single contiguous range.
    unsigned *fieldBlob = nullptr;
#endif
    // The table walk's addends and coefficients come from the resolver's
    // TableWalk so device and re-walk share one table by construction.  The
    // sigma-square arm reuses twConsts for its smaller generated field table.
    const Solver<CfgF131> *sol = nullptr;

#if ECC_PHASE_PROFILE
    double profWarpSteps = 0;
#endif
    uint32_t *sigmaTable = nullptr;
    PackedCudaEngine() { P = {}; }
    ~PackedCudaEngine() override {
        cudaFree(sigmaTable);
#if ECC_CYCLE_PROFILE
        if (P.cycleProfile) reportCycleProfile();
#endif
#if ECC_PHASE_PROFILE
        cudaDeviceSynchronize();
        if (profWarpSteps > 0) eccPacked131::phaseProfileReport(profWarpSteps);
#endif
#if ECC_PACKED_L2_PERSIST
        cudaFree(fieldBlob);
#else
        cudaFree(P.x); cudaFree(P.y); cudaFree(P.pchain);
        cudaFree(denominators);
#endif
        cudaFree(P.dead);
        cudaFree(P.seed); cudaFree(P.startIter); cudaFree(P.dp); cudaFree(P.dpCount);
        cudaFree(P.hist); cudaFree(twConsts);
        cudaFree(P.counts);
#if ECC_TABLE_GLOBAL_HINTS
        cudaFree(globalHintQueue); cudaFree(globalHintCount);
#endif
#if ECC_CYCLE_PROFILE
        cudaFree(P.cycleProfile);
#endif
    }
#if ECC_WALK_TABLE && !ECC_TABLE_GLOBAL
    static size_t dynamicSharedBytes() { return eccPacked131::TW_SHARED_BYTES; }
    unsigned checkpointVersion() const override { return 35u; }
    int laneArrayCount() const override { return 3; }
    u64 *laneArray(int i) const override { return i == 2 ? P.hist : (i ? P.startIter : P.seed); }
#elif ECC_WALK_TABLE
    static size_t dynamicSharedBytes() { return 0; }
    unsigned checkpointVersion() const override { return 35u; }
    int laneArrayCount() const override { return 3; }
    u64 *laneArray(int i) const override { return i == 2 ? P.hist : (i ? P.startIter : P.seed); }
#elif ECC_SIGMA_SQUARE_TABLE
    static size_t dynamicSharedBytes() {
        return size_t(eccPacked131::SQ_TAB_WORDS) * sizeof(uint32_t);
    }
    unsigned checkpointVersion() const override { return 2u + ECC_CKPT_BUMP; }
#else
    // Zero without a sigma table; otherwise reserve its Frobenius nibble table.
    static size_t dynamicSharedBytes() { return eccPacked131::SIGMA_SMEM_BYTES; }
    unsigned checkpointVersion() const override {
        return (ECC_PACKED_XONLY_POLY_SELECT ? 8u :
            (ECC_PACKED_XONLY_ARITHMETIC_ONLY ? 7u :
            (ECC_PACKED_XONLY_BRIDGE_MOD72 ? 6u :
            (ECC_PACKED_XONLY_BRIDGE1_COMMON ? 5u :
            (ECC_PACKED_XONLY_BRIDGE3 ? 4u : (ECC_PACKED_XONLY_23 ? 3u : 2u)))))) + ECC_CKPT_BUMP;
    }
#endif
    size_t fieldCount() const override { return size_t(P.threads) * BATCH * 5; }
#if ECC_PACKED_STATE_TILE
    size_t physicalFieldCount() const override {
#if ECC_PACKED_COMPACT_STATE
        // Transfer words are opaque physical storage, not logical P131 limbs.
        return eccPacked131::compactPhysicalFieldWords(size_t(P.threads));
#else
        return eccPacked131::physicalStateThreads(size_t(P.threads)) * BATCH * 5;
#endif
    }
#endif
    size_t laneCount() const override { return size_t(P.threads) * BATCH; }
    // One worker is one walk here, so a counter is a number rather than
    // ECC_COUNT_BITS bitsliced words. That is the whole reason the witness
    // costs this backend a read-modify-write and the bitsliced one a
    // ripple-carry over all eight counters; see CAIRN-WITNESS.md.
    size_t countElems() const override { return eccScalarCountWords(P.threads, BATCH); }
    int checkpointLanes() const override { return 1; }
#if ECC_PACKED_POLY_STATE
    // Packed checkpoint v2 always stores normal-basis coordinates, including
    // when the running kernel stores polynomial coordinates. Transform only
    // the staging buffer, using the same slot/word/thread layout as load/store.
    void convertCheckpointField(std::vector<unsigned> &words, bool toPolynomial) const {
        const int threads = P.threads;
#pragma omp parallel for collapse(2) schedule(static)
        for (int slot = 0; slot < BATCH; ++slot) {
            for (int tid = 0; tid < threads; ++tid) {
                eccPacked131::P131 a;
                for (int word = 0; word < 5; ++word)
                    a.v[word] = words[(size_t(slot) * 5 + word) * threads + tid];
                const auto b = toPolynomial ? eccPacked131::toPolynomial131(a)
                                            : eccPacked131::fromPolynomial131(a);
                for (int word = 0; word < 5; ++word)
                    words[(size_t(slot) * 5 + word) * threads + tid] = b.v[word];
            }
        }
    }
    void exportCheckpointField(std::vector<unsigned> &words) const override {
#if ECC_PACKED_COMPACT_STATE
        std::vector<unsigned> logical(fieldCount());
        for (int slot = 0; slot < BATCH; ++slot)
            for (int tid = 0; tid < P.threads; ++tid) {
                const auto a = eccPacked131::compactLoad131(words.data(), slot, tid);
                for (int word = 0; word < 5; ++word)
                    logical[(size_t(slot) * 5 + word) * P.threads + tid] = a.v[word];
            }
        words.swap(logical);
#elif ECC_PACKED_STATE_TILE
        // The transfer contains every physical tile, including padding. Disk
        // coordinates remain logical SoA and use normal basis in version 2.
        std::vector<unsigned> logical(fieldCount());
        for (int slot = 0; slot < BATCH; ++slot)
            for (int word = 0; word < 5; ++word)
                for (int tid = 0; tid < P.threads; ++tid)
                    logical[(size_t(slot) * 5 + word) * P.threads + tid] =
                        words[eccPacked131::stateWordIndex(slot, word, tid)];
        words.swap(logical);
#endif
        convertCheckpointField(words, false);
    }
    void importCheckpointField(std::vector<unsigned> &words) const override {
        convertCheckpointField(words, true);
#if ECC_PACKED_COMPACT_STATE
        std::vector<unsigned> physical(physicalFieldCount(), 0u);
        for (int slot = 0; slot < BATCH; ++slot)
            for (int tid = 0; tid < P.threads; ++tid) {
                eccPacked131::P131 a;
                for (int word = 0; word < 5; ++word)
                    a.v[word] = words[(size_t(slot) * 5 + word) * P.threads + tid];
                eccPacked131::compactStore131(physical.data(), slot, tid, a);
            }
        words.swap(physical);
#elif ECC_PACKED_STATE_TILE
        // Start with zero padding. Keep device metadata indexed by logical
        // slot*P.threads+tid; only the coordinate field is tiled here.
        std::vector<unsigned> physical(physicalFieldCount(), 0u);
        for (int slot = 0; slot < BATCH; ++slot)
            for (int word = 0; word < 5; ++word)
                for (int tid = 0; tid < P.threads; ++tid)
                    physical[eccPacked131::stateWordIndex(slot, word, tid)] =
                        words[(size_t(slot) * 5 + word) * P.threads + tid];
        words.swap(physical);
#endif
    }
#endif
    static constexpr int denominatorFields = ECC_PACKED_CACHE_DENOM * (1 - ECC_TABLE_TAG_DENOM) *
        (1 + ECC_PACKED_POLY_CHAIN * (1 - ECC_PACKED_POLY_STATE));
    const char *name() const {
        return ECC_PACKED_XONLY_POLY_SELECT ? "cuda-packed131-xonly-bridge1-bridge3-poly12" :
            (ECC_PACKED_XONLY_ARITHMETIC_ONLY ? "cuda-packed131-xonly-bridge1-arith-only" :
            (ECC_PACKED_XONLY_BRIDGE_MOD72 ? "cuda-packed131-xonly-bridge1-bridge3-mod72" :
            (ECC_PACKED_XONLY_BRIDGE1_COMMON ? "cuda-packed131-xonly-bridge1-bridge3" :
            (ECC_PACKED_XONLY_BRIDGE3 ? "cuda-packed131-xonly23-bridge3" :
            (ECC_PACKED_XONLY_23 ? "cuda-packed131-xonly23" : "cuda-packed131")))));
    }
    u64 walksPerLaunch() const { return u64(P.threads) * BATCH; }
    bool needsReseed() const { return restartPending; }

#if ECC_CYCLE_PROFILE
    void reportCycleProfile() const {
        EccCycleProfile profile = {};
        CUDA_CHECK(cudaDeviceSynchronize());
        CUDA_CHECK(cudaMemcpy(&profile, P.cycleProfile, sizeof(profile),
                              cudaMemcpyDeviceToHost));
        const unsigned long long terminal = eccCycleProfileTerminalTotal(profile);
        printf("cycle profile: {\"hints\":%llu,\"fast2_hits\":%llu,"
               "\"general_cycles_by_length_1_8\":[%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu],"
               "\"open_eight_step_probes\":%llu,\"dp_aborts\":%llu,"
               "\"exceptional_denominator_aborts\":%llu,\"affine_next_calls\":%llu,"
               "\"anchor_exits\":%llu,\"terminal_total\":%llu,\"reconciled\":%s}\n",
               profile.hints, profile.fast2Hits,
               profile.generalCycles[0], profile.generalCycles[1],
               profile.generalCycles[2], profile.generalCycles[3],
               profile.generalCycles[4], profile.generalCycles[5],
               profile.generalCycles[6], profile.generalCycles[7],
               profile.openEightStepProbes, profile.dpAborts,
               profile.exceptionalDenominatorAborts, profile.affineNextCalls,
               profile.anchorExits, terminal,
               terminal == profile.hints ? "true" : "false");
    }
#endif

    static int autoThreads(int device) {
        cudaDeviceProp prop;
        CUDA_CHECK(cudaGetDeviceProperties(&prop, device));
        int blocks;
        prepareKernel();
        CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
            &blocks, eccPacked131::walk, ECC_THREADS, dynamicSharedBytes()));
        size_t freeBytes, totalBytes;
        CUDA_CHECK(cudaMemGetInfo(&freeBytes, &totalBytes));
        // The witness counters setup allocates alongside the walk state;
        // zero when ECC_WITNESS is compiled out.
        const size_t counterBytes = eccScalarCountWords(1, BATCH) * sizeof(unsigned);
        const size_t hintBytes = ECC_TABLE_GLOBAL_HINTS ? size_t(BATCH) * sizeof(unsigned) : 0;
#if ECC_PACKED_COMPACT_STATE
        // autoThreads rounds to complete 256-worker tiles; each stored field
        // consumes sixteen low bytes and one top byte per worker/slot.
        const size_t perThread = size_t(BATCH) *
            ((3 + denominatorFields) * 17 + sizeof(unsigned) + 2 * sizeof(u64)) + counterBytes + hintBytes;
#else
        const size_t perThread = size_t(BATCH) *
            ((3 + denominatorFields) * 5 * sizeof(unsigned) + sizeof(unsigned) + 2 * sizeof(u64)) + counterBytes + hintBytes;
#endif
        size_t threads = size_t(prop.multiProcessorCount) * eccPacked131::walkWorkersPerBlock131 * blocks;
        const size_t fits = (freeBytes - freeBytes / 4) / perThread;
        if (threads > fits) threads = fits;
        threads -= threads % ECC_THREADS;
        if (!threads) threads = ECC_THREADS;
        printf("device: %s, %d SMs, %d block(s) of %d packed threads resident per SM\n",
               prop.name, prop.multiProcessorCount, blocks, ECC_THREADS);
        return int(threads);
    }

    static eccPacked131::P131 pack(Ref<CfgF131>::Elem a) {
        eccPacked131::P131 result;
        for (int i = 0; i < 5; ++i) result.v[i] = unsigned(a.v[i / 2] >> (32 * (i & 1)));
        return result;
    }

    // Tables above the 48 KB static limit need the opt-in before any query.
    static void prepareKernel() {
        // A static block-hint queue reduces the default dynamic allowance even
        // though the table alone is just under 48 KiB. Opt in to the table's
        // full dynamic size whenever that queue is compiled into the kernel.
        if (dynamicSharedBytes() > 48 * 1024 || ECC_TABLE_BLOCK_HINTS)
            CUDA_CHECK(cudaFuncSetAttribute(eccPacked131::walk,
                cudaFuncAttributeMaxDynamicSharedMemorySize, int(dynamicSharedBytes())));
#if ECC_TABLE_GLOBAL_HINTS
        if (dynamicSharedBytes() > 48 * 1024) {
            CUDA_CHECK(cudaFuncSetAttribute(eccPacked131::selectGlobalHints,
                cudaFuncAttributeMaxDynamicSharedMemorySize, int(dynamicSharedBytes())));
            CUDA_CHECK(cudaFuncSetAttribute(eccPacked131::resolveGlobalHints,
                cudaFuncAttributeMaxDynamicSharedMemorySize, int(dynamicSharedBytes())));
        }
#endif
    }

#if ECC_PACKED_L2_PERSIST
    static int persistFieldCount() {
        return 3 + (ECC_PACKED_CACHE_DENOM ? denominatorFields : 0);
    }

    static void applyPackedL2Persist(void *base, size_t bytes) {
        int device = 0, maxPersist = 0;
        CUDA_CHECK(cudaGetDevice(&device));
        CUDA_CHECK(cudaDeviceGetAttribute(&maxPersist,
            cudaDevAttrMaxPersistingL2CacheSize, device));
        if (maxPersist <= 0 || bytes == 0) {
            printf("packed L2 persist window: skipped (cap %d, blob %zu)\n",
                   maxPersist, bytes);
            return;
        }
        CUDA_CHECK(cudaDeviceSetLimit(cudaLimitPersistingL2CacheSize,
                                      size_t(maxPersist)));
        CUDA_CHECK(cudaCtxResetPersistingL2Cache());
        // One window per stream. Cover the coordinate fields fully: x+y+pchain
        // at automatic occupancy is ~75 MiB, under the 80 MiB cap on this SKU.
        // Denominators sit after them and take whatever of the cap remains.
        cudaAccessPolicyWindow window = {};
        window.base_ptr = base;
        window.num_bytes = bytes < size_t(maxPersist) ? bytes : size_t(maxPersist);
        window.hitRatio = 1.0f;
        window.hitProp = cudaAccessPropertyPersisting;
        window.missProp = cudaAccessPropertyStreaming;
        cudaStreamAttrValue attr = {};
        attr.accessPolicyWindow = window;
        CUDA_CHECK(cudaStreamSetAttribute(0, cudaStreamAttributeAccessPolicyWindow, &attr));
        printf("packed L2 persist window: %zu of %zu field bytes, cap %d\n",
               window.num_bytes, bytes, maxPersist);
    }
#endif

    void setup(const Options &o, const u64 *px, const u64 *py, const u64 *qx, const u64 *qy) {
        prepareKernel();
#if ECC_PACKED_SIGMA_TABLE
        {
            // Built from the host arithmetic the tests verify; every walk block
            // stages this global copy into its shared memory.
            std::vector<uint32_t> table(eccPacked131::SIGMA_TABLE_WORDS);
            eccPacked131::buildSigmaTable(table.data());
            CUDA_CHECK(cudaMalloc(&sigmaTable, table.size() * sizeof(uint32_t)));
            CUDA_CHECK(cudaMemcpy(sigmaTable, table.data(), table.size() * sizeof(uint32_t), cudaMemcpyHostToDevice));
            const uint32_t *device = sigmaTable;
            CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::eccSigmaTableDevice, &device, sizeof(device)));
        }
#endif
        if (o.preferL1)
            CUDA_CHECK(cudaFuncSetCacheConfig(eccPacked131::walk, cudaFuncCachePreferL1));
        P.threads = o.threads; P.steps = o.steps; P.dpWeight = o.dpWeight;
        P.runId = o.runId; P.maxIters = o.maxIters; P.iterBase = 0; P.dpCap = o.dpCap;
        const size_t bytes = physicalFieldCount() * sizeof(unsigned);
#if ECC_PACKED_L2_PERSIST
        CUDA_CHECK(cudaMalloc(&fieldBlob, bytes * size_t(persistFieldCount())));
        P.x = fieldBlob;
        P.y = fieldBlob + physicalFieldCount();
        P.pchain = fieldBlob + 2 * physicalFieldCount();
#if ECC_PACKED_CACHE_DENOM && !ECC_TABLE_TAG_DENOM
        denominators = fieldBlob + 3 * physicalFieldCount();
#endif
        applyPackedL2Persist(fieldBlob, bytes * size_t(persistFieldCount()));
#else
        CUDA_CHECK(cudaMalloc(&P.x, bytes)); CUDA_CHECK(cudaMalloc(&P.y, bytes));
        CUDA_CHECK(cudaMalloc(&P.pchain, bytes));
#if ECC_PACKED_CACHE_DENOM && !ECC_TABLE_TAG_DENOM
        CUDA_CHECK(cudaMalloc(&denominators, bytes * denominatorFields));
#endif
#endif
        CUDA_CHECK(cudaMalloc(&P.dead, slotCount() * sizeof(unsigned)));
        P.counts = nullptr;
        if (countElems()) {
            CUDA_CHECK(cudaMalloc(&P.counts, countElems() * sizeof(unsigned)));
            CUDA_CHECK(cudaMemset(P.counts, 0, countElems() * sizeof(unsigned)));
        }
        CUDA_CHECK(cudaMalloc(&P.seed, laneCount() * sizeof(u64)));
        CUDA_CHECK(cudaMalloc(&P.startIter, laneCount() * sizeof(u64)));
#if ECC_WALK_TABLE
        if (!sol || !sol->walk.ready) { fprintf(stderr, "packed table walk: no resolver table\n"); exit(1); }
        {
            std::vector<uint32_t> consts(eccPacked131::TW_WORDS);
            eccPacked131::twFillConsts(sol->walk, consts.data());
            CUDA_CHECK(cudaMalloc(&twConsts, consts.size() * sizeof(uint32_t)));
            CUDA_CHECK(cudaMemcpy(twConsts, consts.data(), consts.size() * sizeof(uint32_t), cudaMemcpyHostToDevice));
            P.twConsts = twConsts;
        }
        CUDA_CHECK(cudaMalloc(&P.hist, laneCount() * sizeof(u64)));
#if ECC_TABLE_GLOBAL_HINTS
        if (laneCount() > 0xffffffffull) {
            fprintf(stderr, "GPU-wide hint keys require at most UINT32_MAX walks\n"); exit(2);
        }
        CUDA_CHECK(cudaMalloc(&globalHintQueue, laneCount() * sizeof(unsigned)));
        CUDA_CHECK(cudaMalloc(&globalHintCount, sizeof(unsigned)));
        int currentDevice = 0;
        cudaDeviceProp globalHintDevice;
        CUDA_CHECK(cudaGetDevice(&currentDevice));
        CUDA_CHECK(cudaGetDeviceProperties(&globalHintDevice, currentDevice));
        globalResolverBlocks = globalHintDevice.multiProcessorCount;
        CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
            &globalResolverBlocksPerSm, eccPacked131::resolveGlobalHints,
            ECC_TABLE_GLOBAL_HINT_THREADS, dynamicSharedBytes()));
        if (globalResolverBlocksPerSm < 1) {
            fprintf(stderr, "GPU-wide resolver geometry cannot resident one block per SM\n");
            exit(2);
        }
#endif
#elif ECC_SIGMA_SQUARE_TABLE
        {
            std::vector<uint32_t> consts(eccPacked131::SQ_TAB_WORDS);
            eccPacked131::fillSquareTable131(consts.data());
            CUDA_CHECK(cudaMalloc(&twConsts, consts.size() * sizeof(uint32_t)));
            CUDA_CHECK(cudaMemcpy(twConsts, consts.data(), consts.size() * sizeof(uint32_t),
                                  cudaMemcpyHostToDevice));
            P.twConsts = twConsts;
        }
#endif
        CUDA_CHECK(cudaMalloc(&P.dp, size_t(P.dpCap) * sizeof(DpRecord)));
        // Second counter signals overdue restarts without emitting false DPs.
        CUDA_CHECK(cudaMalloc(&P.dpCount, 3 * sizeof(unsigned)));
        CUDA_CHECK(cudaMemset(P.dpCount, 0, 3 * sizeof(unsigned)));
#if ECC_CYCLE_PROFILE
        CUDA_CHECK(cudaMalloc(&P.cycleProfile, sizeof(EccCycleProfile)));
        CUDA_CHECK(cudaMemset(P.cycleProfile, 0, sizeof(EccCycleProfile)));
#endif
        using R = Ref<CfgF131>;
        auto basis = R::make(R::fromLimbs(px), R::fromLimbs(py));
        eccPacked131::P131 ox[128], oy[128];
        for (int i = 0; i < 128; ++i) {
            auto point = R::frob(basis, i);
            ox[i] = pack(point.x); oy[i] = pack(point.y);
        }
        auto xq = pack(R::fromLimbs(qx)), yq = pack(R::fromLimbs(qy));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::orbitX, ox, sizeof(ox)));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::orbitY, oy, sizeof(oy)));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::targetX, &xq, sizeof(xq)));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::targetY, &yq, sizeof(yq)));
        cudaFuncAttributes attrs;
        CUDA_CHECK(cudaFuncGetAttributes(&attrs, eccPacked131::walk));
        printf("packed kernel: %d registers/thread, %zu local bytes/thread, %zu shared bytes/block, %s multiplier\n",
               attrs.numRegs, attrs.localSizeBytes, attrs.sharedSizeBytes,
               ECC_PACKED_SINGLE_PRODUCT ? "single-product" : "two-product");
        // Occupancy is a resource limit, not a measured instruction-issue rate.
        int activeDevice = -1, residentBlocks = 0;
        cudaDeviceProp deviceProps;
        CUDA_CHECK(cudaGetDevice(&activeDevice));
        CUDA_CHECK(cudaGetDeviceProperties(&deviceProps, activeDevice));
        CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
            &residentBlocks, eccPacked131::walk, ECC_THREADS, 0));
        const int gridBlocks = (P.threads + eccPacked131::walkWorkersPerBlock131 - 1) / eccPacked131::walkWorkersPerBlock131;
        printf("packed theoretical occupancy: %.1f%% (%d blocks/SM, %d threads/block, %d SMs); grid %d blocks\n",
               100.0 * residentBlocks * ECC_THREADS / deviceProps.maxThreadsPerMultiProcessor,
               residentBlocks, ECC_THREADS, deviceProps.multiProcessorCount, gridBlocks);
        printf("packed launch bounds: %d threads, %d min blocks\n", ECC_THREADS, ECC_MINBLOCKS);
        printf("packed sigma table: %d (%zu dynamic shared bytes)\n", ECC_PACKED_SIGMA_TABLE,
               size_t(eccPacked131::SIGMA_SMEM_BYTES));
#if ECC_PACKED_SHARED_SIGMA
        int diagnosticDevice = -1, driverReservedShared = -1;
        CUDA_CHECK(cudaGetDevice(&diagnosticDevice));
        CUDA_CHECK(cudaDeviceGetAttribute(&driverReservedShared,
            cudaDevAttrReservedSharedMemoryPerBlock, diagnosticDevice));
        printf("packed driver reserved shared bytes/block: %d, device %d\n",
               driverReservedShared, diagnosticDevice);
#endif
        printf("packed denominator cache: %d\n", ECC_PACKED_CACHE_DENOM);
        printf("packed last-slot cache mode: %d\n", ECC_PACKED_LAST_SLOT_CACHE);
        printf("packed multiply by value: %d\n", ECC_PACKED_BY_VALUE);
        printf("packed Frobenius network: %d\n", ECC_PACKED_PERM_SIGMA);
        printf("packed partial Frobenius routing mask: %d\n", ECC_PACKED_PARTIAL_SIGMA);
        printf("packed Frobenius stage order: %d\n", ECC_PACKED_SIGMA_ORDER);
        printf("packed byte-select Frobenius: %d\n", ECC_PACKED_BYTE_SIGMA);
        printf("packed seven-stage conversion: %d\n", ECC_PACKED_FAST_CONVERT);
        printf("packed polynomial chain: %d\n", ECC_PACKED_POLY_CHAIN);
        printf("packed polynomial state: %d\n", ECC_PACKED_POLY_STATE);
        printf("packed unrolled inversion: %d\n", ECC_PACKED_UNROLL_INV);
        printf("packed paired products: %d\n", ECC_PACKED_PAIR_PRODUCTS);
        printf("packed pair ilp: %d\n", ECC_PACKED_PAIR_ILP);
        printf("packed pair clmul: %d\n", ECC_PACKED_PAIR_CLMUL);
        printf("packed clmul flat: %d\n", ECC_PACKED_CLMUL_FLAT);
        printf("packed top hoist: %d\n", ECC_PACKED_TOP_HOIST);
        printf("packed onb inv: %d\n", ECC_PACKED_ONB_INV);
        printf("packed from reduced: %d\n", ECC_PACKED_FROM_REDUCED);
        printf("packed inline polynomial: %d\n", ECC_PACKED_INLINE_POLY);
        printf("packed slot unroll: %d\n", ECC_UNROLL_SLOTS);
        printf("packed chains: %d\n", ECC_PACKED_CHAINS);
        printf("packed slot prefetch: %d\n", ECC_PACKED_SLOT_PREFETCH);
        printf("packed slot pipeline: %d\n", ECC_PACKED_SLOT_PIPELINE);
        printf("packed sigma fused: %d\n", ECC_SIGMA_FUSED);
        printf("packed sigma fused late y: %d\n", ECC_SIGMA_FUSED_LATE_Y);
        printf("packed sigma fused shared slots: %d\n", ECC_SIGMA_FUSED_SHARED_SLOTS);
        printf("packed witness: %d\n", ECC_WITNESS);
        printf("packed L2 persist: %d\n", ECC_PACKED_L2_PERSIST);
        printf("packed direct reduction: %d\n", ECC_PACKED_DIRECT_REDUCE);
        printf("packed add combine: %d\n", ECC_PACKED_ADD_COMBINE);
        printf("packed generated product: %d\n", ECC_PACKED_GENERATED_PRODUCT);
        printf("packed native carryless multiply: %d\n", ECC_PACKED_CLMAD);
        printf("packed native full product: %d\n", ECC_PACKED_NATIVE_PRODUCT);
        printf("packed native reduction: %d\n", ECC_PACKED_NATIVE_REDUCE);
        printf("packed fused sigma: %d\n", ECC_PACKED_FUSED_SIGMA);
        printf("packed inline mask: %d\n", ECC_PACKED_INLINE);
        printf("packed weighted prefix: %d\n", ECC_PACKED_WEIGHTED_PREFIX);
        printf("packed compact state: %d\n", ECC_PACKED_COMPACT_STATE);
        printf("packed shared sigma: %d\n", ECC_PACKED_SHARED_SIGMA);
        printf("packed x-only 2/3 walk: %d\n", ECC_PACKED_XONLY_23);
        printf("packed sparse sigma^3 bridge: %d\n", ECC_PACKED_XONLY_BRIDGE3);
        printf("packed sigma^1 common path: %d\n", ECC_PACKED_XONLY_BRIDGE1_COMMON);
        printf("packed sparse bridge modulus 72: %d\n", ECC_PACKED_XONLY_BRIDGE_MOD72);
        printf("packed skip empty bridge phase: %d\n", ECC_PACKED_XONLY_SKIP_EMPTY_BRIDGE);
        printf("packed arithmetic-only diagnostic: %d\n", ECC_PACKED_XONLY_ARITHMETIC_ONLY);
        printf("packed polynomial-bit selector: %d\n", ECC_PACKED_XONLY_POLY_SELECT);
        printf("packed polynomial-bit Hamming DP convert: %d\n", ECC_PACKED_XONLY_POLY_DP_CONVERT);
        printf("packed state tile: %d\n", ECC_PACKED_STATE_TILE);
        printf("packed block inverse: %d\n", ECC_PACKED_BLOCK_INVERSE);
        printf("packed physical slots/thread: %d; logical workers/block: %d\n", ECC_BATCH / ECC_PACKED_BATCH_SPLIT, eccPacked131::walkWorkersPerBlock131);
        printf("packed native carryless square: %d\n", ECC_PACKED_CLMAD_SQUARE);
        printf("packed three-limb Karatsuba: %d\n", ECC_PACKED_KARAT3);
        printf("packed top clmad: %d\n", ECC_PACKED_TOP_CLMAD);
        printf("packed add combine: %d\n", ECC_PACKED_ADD_COMBINE);
        printf("packed alu square: %d\n", ECC_PACKED_ALU_SQUARE);
        printf("packed alu onb square: %d\n", ECC_PACKED_ALU_SQR);
        printf("packed square table: %d\n", ECC_PACKED_SQUARE_TABLE);
        printf("packed sigma square table: %d, %zu shared bytes\n",
               ECC_SIGMA_SQUARE_TABLE,
               ECC_SIGMA_SQUARE_TABLE ? dynamicSharedBytes() : size_t(0));
        printf("packed polynomial inversion: %d\n", ECC_PACKED_INV_POLY);
        printf("packed profile ranges: %d\n", ECC_PROFILE_RANGE);
        printf("packed table GPU-wide hints: %d\n", ECC_TABLE_GLOBAL_HINTS);
        printf("packed hint scheduling: table fused %d, pipe select %d, chain first %d, inline polynomial %d, phase profile %d, cycle profile %d\n",
               ECC_TABLE_FUSED, ECC_TABLE_PIPE_SELECT, ECC_PACKED_CHAIN_FIRST,
               ECC_PACKED_INLINE_POLY, ECC_PHASE_PROFILE, ECC_CYCLE_PROFILE);
#if ECC_TABLE_GLOBAL_HINTS
        printf("packed GPU-wide hint queue: %llu entries, %d resolver blocks of %d threads\n",
               (unsigned long long)laneCount(), globalResolverBlocks,
               ECC_TABLE_GLOBAL_HINT_THREADS);
        printf("packed GPU-wide hint memory: %llu queue bytes, 4 counter bytes\n",
               (unsigned long long)(laneCount() * sizeof(unsigned)));
        printf("packed GPU-wide resolver geometry: %d blocks, %d threads/block, %d warps/block, %d active block(s)/SM, %zu dynamic shared bytes, launch bounds %d x 1\n",
               globalResolverBlocks, ECC_TABLE_GLOBAL_HINT_THREADS,
               ECC_TABLE_GLOBAL_HINT_THREADS / 32, globalResolverBlocksPerSm,
               dynamicSharedBytes(), ECC_TABLE_GLOBAL_HINT_THREADS);
        for (int stage = 0; stage < 2; ++stage) {
            cudaFuncAttributes hintAttrs;
            if (stage == 0) CUDA_CHECK(cudaFuncGetAttributes(&hintAttrs, eccPacked131::selectGlobalHints));
            else CUDA_CHECK(cudaFuncGetAttributes(&hintAttrs, eccPacked131::resolveGlobalHints));
            printf("packed hint %s kernel: %d registers/thread, %zu local bytes/thread, %zu static shared bytes\n",
                   stage == 0 ? "select" : "resolve", hintAttrs.numRegs,
                   hintAttrs.localSizeBytes, hintAttrs.sharedSizeBytes);
        }
#endif
#if ECC_WALK_TABLE
        printf("packed table pivot bytes: %d, table shared bytes %zu\n", ECC_TABLE_PIVOT_BYTES, eccPacked131::TW_SHARED_BYTES);
        printf("packed table phase popc: %d\n", ECC_TABLE_PHASE_POPC);
        printf("packed table split forward: %d\n", ECC_TABLE_SPLIT_FORWARD);
        printf("packed table batch hints: %d\n", ECC_TABLE_BATCH_HINTS);
        printf("packed table block hints: %d, queue %d\n", ECC_TABLE_BLOCK_HINTS, ECC_TABLE_HINT_QUEUE);
        printf("packed cycle fast2: %d\n", ECC_CYCLE_FAST2);
        printf("packed cycle profile: %d\n", ECC_CYCLE_PROFILE);
        printf("packed table global: %d\n", ECC_TABLE_GLOBAL);
        printf("packed table addend global: %d\n", ECC_TABLE_ADDEND_GLOBAL);
#endif
        printf("packed table walk: %d (%d branches, %zu shared bytes)\n", ECC_WALK_TABLE,
               ECC_WALK_TABLE ? ECC_TABLE_BRANCHES : 0,
               ECC_WALK_TABLE ? dynamicSharedBytes() : size_t(0));
        const int blocks = int((laneCount() + ECC_THREADS - 1) / ECC_THREADS);
        eccPacked131::init<<<blocks, ECC_THREADS>>>(P, false);
        CUDA_CHECK(cudaGetLastError()); CUDA_CHECK(cudaDeviceSynchronize());
    }

    void launch(u64 iterBase) {
        P.iterBase = iterBase;
#if ECC_PROFILE_RANGE
        CUDA_CHECK(cudaProfilerStart());
#endif
#if ECC_TABLE_GLOBAL_HINTS
        WalkParams<unsigned> one = P;
        one.steps = 1;
        const int activeBlocks = (P.threads + ECC_THREADS - 1) / ECC_THREADS;
        for (int step = 0; step < P.steps; ++step) {
            one.iterBase = iterBase + (u64)step;
            CUDA_CHECK(cudaMemsetAsync(globalHintCount, 0, sizeof(unsigned)));
            eccPacked131::selectGlobalHints<<<activeBlocks, ECC_THREADS, dynamicSharedBytes()>>>(
                one, globalHintQueue, globalHintCount);
            CUDA_CHECK(cudaGetLastError());
            eccPacked131::resolveGlobalHints<<<globalResolverBlocks, ECC_TABLE_GLOBAL_HINT_THREADS, dynamicSharedBytes()>>>(
                one, globalHintQueue, globalHintCount);
            CUDA_CHECK(cudaGetLastError());
            eccPacked131::walk<<<activeBlocks, ECC_THREADS, dynamicSharedBytes()>>>(one, denominators);
            CUDA_CHECK(cudaGetLastError());
        }
#else
        eccPacked131::walk<<<(P.threads + ECC_THREADS - 1) / ECC_THREADS, ECC_THREADS,
                             dynamicSharedBytes()>>>(P, denominators);
        CUDA_CHECK(cudaGetLastError());
#endif
#if ECC_PHASE_PROFILE
        profWarpSteps += double(P.threads / 32) * P.steps;
#endif
#if ECC_PROFILE_RANGE
        CUDA_CHECK(cudaDeviceSynchronize());
        CUDA_CHECK(cudaProfilerStop());
#endif
    }
    void reseed(u64 iterBase) {
        P.iterBase = iterBase;
        eccPacked131::init<<<int((laneCount() + ECC_THREADS - 1) / ECC_THREADS), ECC_THREADS>>>(P, true);
        CUDA_CHECK(cudaGetLastError());
        restartPending = false;
    }
    unsigned fetch(std::vector<DpRecord> &out) {
        CUDA_CHECK(cudaDeviceSynchronize());
        unsigned counts[3];
        CUDA_CHECK(cudaMemcpy(counts, P.dpCount, sizeof(counts), cudaMemcpyDeviceToHost));
        const unsigned n = counts[0] < P.dpCap ? counts[0] : P.dpCap;
        out.resize(n);
        if (n) CUDA_CHECK(cudaMemcpy(out.data(), P.dp, size_t(n) * sizeof(DpRecord), cudaMemcpyDeviceToHost));
        restartPending = counts[1] != 0;
        if (counts[0] || counts[1] || counts[2]) CUDA_CHECK(cudaMemset(P.dpCount, 0, sizeof(counts)));
        return counts[2] ? ECC_SEED_EXHAUSTED : counts[0];
    }
};
