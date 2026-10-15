// ECC2K-130 client: Pollard rho with the Frobenius-based iteration function,
// bitsliced over the permuted type-II optimal normal basis.
//
// Build as CUDA (nvcc/clang) or as plain C++ with -DECC_NO_CUDA; the walk code
// is identical in both cases, only the word width differs (32 bits on the
// device, 64 on the host).
#include <signal.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include <algorithm>
#include <string>
#include <map>
#include <vector>

#include "../include/curveparams.h"
#include "../include/kernel.h"
#include "../include/solver.h"
#include "../include/durable.h"

#ifndef ECC_NO_CUDA
#include <cuda_runtime.h>
#define CUDA_CHECK(x)                                                                   \
    do {                                                                                \
        cudaError_t e_ = (x);                                                           \
        if (e_ != cudaSuccess) {                                                        \
            fprintf(stderr, "CUDA error at %s:%d: %s\n", __FILE__, __LINE__,            \
                    cudaGetErrorString(e_));                                            \
            exit(2);                                                                    \
        }                                                                               \
    } while (0)
typedef unsigned int DeviceWord;
#endif

#ifdef _OPENMP
#include <omp.h>
#endif



// Set by SIGINT/SIGTERM so a run stopped by its deadline still checkpoints.
// Without it every timed run throws away the walks in flight, which for this
// workload is about a quarter of the whole computation.
static volatile sig_atomic_t gStop = 0;
static void onStop(int) { gStop = 1; }

// A corpus record: the seed that produced the point, and the canonical
// representative of its orbit, which is the collision key.  Fixed 32 bytes so
// the file can be appended to, reloaded and merged without parsing.
struct DpFileRecord {
    unsigned long long seed;
    unsigned long long canon[3];
};
static_assert(sizeof(DpFileRecord) == 32, "corpus records must remain 32 bytes");

// Corpus v2: the same point, plus the witness that makes it payable.
//
// v1 is a headerless stream of 32-byte records and every corpus on disk is
// one, so v2 announces itself with a magic rather than with its size.  Sizes
// would have been cheaper and wrong: a v1 file truncated mid-record, or a v2
// file read by an older build, would each look like a valid file of the other
// format and silently mis-frame every record after the first.
//
// `iters` is here so the witness can be checked without the curve: the counts
// sum to it, so a wrapped 32-bit counter or a witness-less build is visible
// from the record alone.  That costs 8 bytes a record and saves a consumer
// from having to trust the producer's build flags.
struct DpFileRecordV2 {
    unsigned long long seed;
    unsigned long long iters;
    unsigned long long canon[3];
    unsigned counts[ECC_JCOUNT];
};

struct DpFileHeader {
    char magic[8];
    unsigned version;
    unsigned recordBytes;
};

static const char DP_MAGIC_V2[8] = {'E', 'C', 'C', '2', 'K', 'D', 'P', '2'};

static const char DP_MAGIC_TABLE3[8] = {'E','C','C','2','K','D','T','3'};
static size_t dpHeaderBytes(bool v2) { return (v2 || ECC_WALK_TABLE) ? sizeof(DpFileHeader) : 0; }

// The table rule changed the seed-to-endpoint function. Never replay a legacy
// table/sigma corpus as v3 or append a new rule to an old stream.
static bool dpFileIsV2(FILE *in) {
    DpFileHeader h{};
    const size_t n = fread(&h, 1, sizeof h, in);
    if (!n) { rewind(in); return false; } // a fresh append target
    const bool v2 = n >= 8 && memcmp(h.magic, DP_MAGIC_V2, 8) == 0;
    const bool table3 = n >= 8 && memcmp(h.magic, DP_MAGIC_TABLE3, 8) == 0;
    if (v2 || table3) {
        const unsigned version = table3 ? 3u : 2u;
        const unsigned stride = table3 ? sizeof(DpFileRecord) : sizeof(DpFileRecordV2);
        if (n != sizeof h || h.version != version || h.recordBytes != stride) {
            fprintf(stderr, "persistence failure: invalid corpus header\n"); exit(8);
        }
    }
    if (bool(ECC_WALK_TABLE) != table3) {
        if (ECC_WALK_TABLE || table3) {
            fprintf(stderr, "incompatible corpus: table rule v3 requires its own corpus; preserve old data and use its matching client\n");
            exit(2);
        }
    }
    if (!v2 && !table3) rewind(in);
    return v2;
}

static double nowSeconds() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

struct Options {
    int curve = 131;
    int threads = 0;
    int blocks = 0;
    int steps = 64;
    int dpWeight = -1;
    long launches = 0;
    int instance = -1;
    unsigned runId = 1;
    u64 maxIters = 1ull << 32;
    bool bench = false;
    bool packed = false;
    bool preferL1 = false;
    bool test = false;
    bool polyBasis = false;
    bool selfCheck = false;
    bool refEngine = false;
    unsigned dpCap = 1u << 16;
    int device = 0;
    int verify = 0;
    std::string dpFile;
    // A corpus to re-walk instead of searching: --replay (see runReplay).
    std::string replayFile;
    std::vector<std::string> loadFiles;
    unsigned long long loadMax = 0;
    std::string ckptFile;
    double ckptSeconds = 300.0;
};


// Checkpoint layout: a header naming the configuration the state belongs to,
// then the walk arrays (packed coordinates use normal basis on disk).
// A checkpoint is only loadable back into the same
// curve, thread count, batch and lane width, so a resumed run continues exactly
// the walks it left off rather than silently starting new ones.
struct CkptHeader {
    char magic[8];
    unsigned version;
    unsigned m;
    unsigned threads;
    unsigned batch;
    unsigned lanes;
    unsigned runId;
    unsigned long long iterBase;
};

static bool ckptHeaderMatches(const CkptHeader &h, int m, int threads, int batch, int lanes,
                              unsigned runId, unsigned version = 1u) {
    return memcmp(h.magic, "ECC2K130", 8) == 0 && h.version == version && h.m == (unsigned)m &&
           h.threads == (unsigned)threads && h.batch == (unsigned)batch &&
           h.lanes == (unsigned)lanes && h.runId == runId;
}

// Refuse a checkpoint whose payload is not exactly the size this configuration
// writes, and leave the file positioned after the header if it is.  A file cut
// short by a container that died mid-write has a perfectly good header, and
// reading it would leave half the walks restored and half still at their start
// points -- a state no run ever occupied. Such checkpoints must be rejected.
static bool ckptPayloadIsWhole(FILE *f, size_t payload) {
    if (fseek(f, 0, SEEK_END) != 0) return false;
    const long total = ftell(f);
    if (total < 0 || (unsigned long)total != sizeof(CkptHeader) + payload) return false;
    return fseek(f, (long)sizeof(CkptHeader), SEEK_SET) == 0;
}

// ---------------------------------------------------------------------------
// host backend
// ---------------------------------------------------------------------------
template <class Cfg>
struct HostEngine {
    typedef ECC_HOST_WORD W;
    typedef Kernel<Cfg, W> K;
    static const int M = Cfg::M;
    static const int LANES = WordTraits<W>::LANES;
    static const int BATCH = ECC_BATCH;

    std::vector<W> x, y, pchain, dead, counts;
    std::vector<u64> seed, startIter;

    // Present so runCurve can size either backend the same way; on the host the
    // count is one walk thread per core and main has already worked it out.
    static int autoThreads(int) {
#ifdef _OPENMP
        return omp_get_max_threads();
#else
        return 1;
#endif
    }
    std::vector<DpRecord> dp;
    unsigned dpCount[3] = {0, 0, 0};
    bool restartPending = false;
    WalkParams<W> P;
    std::vector<u64> px, py, qx, qy;

    void setup(const Options &o, const unsigned long long *cpx, const unsigned long long *cpy,
               const unsigned long long *cqx, const unsigned long long *cqy) {
        const size_t T = (size_t)o.threads;
        x.assign(T * BATCH * M, 0);
        y.assign(T * BATCH * M, 0);
        pchain.assign(T * BATCH * M, 0);
        dead.assign(T * BATCH, 0);
        counts.assign(K::countWords(o.threads), 0);
        seed.assign(T * BATCH * LANES, 0);
        startIter.assign(T * BATCH * LANES, 0);
        dp.assign(o.dpCap, DpRecord());
        px.assign(cpx, cpx + 3);
        py.assign(cpy, cpy + 3);
        qx.assign(cqx, cqx + 3);
        qy.assign(cqy, cqy + 3);
        P.threads = o.threads;
        P.steps = o.steps;
        P.dpWeight = o.dpWeight;
        P.runId = o.runId;
        P.maxIters = o.maxIters;
        P.iterBase = 0;
        P.x = x.data();
        P.y = y.data();
        P.pchain = pchain.data();
        P.seed = seed.data();
        P.startIter = startIter.data();
        P.dead = dead.data();
        P.counts = counts.data();
        P.dp = dp.data();
        P.dpCount = dpCount;
        P.dpCap = o.dpCap;
        P.consts.px = px.data();
        P.consts.py = py.data();
        P.consts.qx = qx.data();
        P.consts.qy = qy.data();
        const WalkParams<W> pp = P;
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
        for (int t = 0; t < o.threads; ++t) K::init(t, pp);
    }

    void launch(u64 iterBase) {
        P.iterBase = iterBase;
        const WalkParams<W> pp = P;
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
        for (int t = 0; t < pp.threads; ++t) K::run(t, pp);
    }

    // revive the lanes that reported during the last launch
    void reseed(u64 iterBase) {
        P.iterBase = iterBase;
        const WalkParams<W> pp = P;
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
        for (int t = 0; t < pp.threads; ++t) K::reseed(t, pp);
    }

    // Walk a list of corpus seeds instead of the run-id namespace.  Only the
    // seeding changes: the walk, the counters and the reports are the
    // production ones, which is the point -- a replay built out of different
    // machinery would be evidence about that machinery, not about this walk.
    void setReplay(const u64 *seeds, u64 n) {
        P.replaySeeds = seeds;
        P.replayCount = n;
    }

    // Re-run init for the next chunk of seeds.  reseed() would be wrong here:
    // it revives a dead lane onto the NEXT seed in its own namespace, which is
    // exactly what a replay must not do.
    void reinit() {
        const WalkParams<W> pp = P;
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
        for (int t = 0; t < pp.threads; ++t) K::init(t, pp);
    }

    void synchronize() const {}

    unsigned fetch(std::vector<DpRecord> &out) {
        const unsigned n = dpCount[0];
        const bool exhausted = dpCount[2] != 0;
        restartPending = dpCount[1] != 0;
        const unsigned m = n < P.dpCap ? n : P.dpCap;
        out.assign(dp.begin(), dp.begin() + m);
        dpCount[0] = dpCount[1] = dpCount[2] = 0;
        return exhausted ? ECC_SEED_EXHAUSTED : n;
    }

    bool save(const char *path, u64 iterBase, unsigned runId) const {
        const std::string tmp = std::string(path) + ".tmp";
        FILE *f = fopen(tmp.c_str(), "wb");
        if (!f) return false;
        CkptHeader h;
        memcpy(h.magic, "ECC2K130", 8);
        h.version = 1u + ECC_CKPT_BUMP;
        h.m = (unsigned)M;
        h.threads = (unsigned)P.threads;
        h.batch = (unsigned)BATCH;
        h.lanes = (unsigned)LANES;
        h.runId = runId;
        h.iterBase = iterBase;
        bool ok = fwrite(&h, sizeof h, 1, f) == 1;
        ok = ok && fwrite(x.data(), sizeof(W), x.size(), f) == x.size();
        ok = ok && fwrite(y.data(), sizeof(W), y.size(), f) == y.size();
        ok = ok && fwrite(dead.data(), sizeof(W), dead.size(), f) == dead.size();
        ok = ok && fwrite(counts.data(), sizeof(W), counts.size(), f) == counts.size();
        ok = ok && fwrite(seed.data(), sizeof(u64), seed.size(), f) == seed.size();
        ok = ok && fwrite(startIter.data(), sizeof(u64), startIter.size(), f) == startIter.size();
        ok = ok && durableFlush(f);
        if (fclose(f) != 0) ok = false;
        if (!ok) { remove(tmp.c_str()); return false; }
        // rename last so a checkpoint is either the old one or the new one,
        // never a half-written file
        return rename(tmp.c_str(), path) == 0 && syncParent(path);
    }

    bool restore(const char *path, u64 *iterBase, unsigned runId) {
        FILE *f = fopen(path, "rb");
        if (!f) return false;
        CkptHeader h;
        const size_t payload = (x.size() + y.size() + dead.size() + counts.size()) * sizeof(W) +
                               (seed.size() + startIter.size()) * sizeof(u64);
        bool ok = fread(&h, sizeof h, 1, f) == 1 &&
                  ckptHeaderMatches(h, M, P.threads, BATCH, LANES, runId, 1u + ECC_CKPT_BUMP) &&
                  ckptPayloadIsWhole(f, payload);
        ok = ok && fread(x.data(), sizeof(W), x.size(), f) == x.size();
        ok = ok && fread(y.data(), sizeof(W), y.size(), f) == y.size();
        ok = ok && fread(dead.data(), sizeof(W), dead.size(), f) == dead.size();
        ok = ok && fread(counts.data(), sizeof(W), counts.size(), f) == counts.size();
        ok = ok && fread(seed.data(), sizeof(u64), seed.size(), f) == seed.size();
        ok = ok && fread(startIter.data(), sizeof(u64), startIter.size(), f) == startIter.size();
        fclose(f);
        if (ok) *iterBase = h.iterBase;
        return ok;
    }

    const char *name() const { return "cpu"; }
    bool needsReseed() const { return restartPending; }
    u64 walksPerLaunch() const { return (u64)P.threads * BATCH * LANES; }
};

// ---------------------------------------------------------------------------
// reference backend
// ---------------------------------------------------------------------------
// A scalar engine on the reference arithmetic, walking with whichever
// iteration the build selects.  It exists to run the whole pipeline -- seeds,
// distinguished points, collision, resolution -- on the toy curves with a
// handful of walks, where the planted logarithm says whether the walk and its
// resolver agree and the iteration count can be compared between walks on
// identical seeds.  Under ECC_WALK_TABLE it is the only non-packed engine, the
// bitsliced kernels implementing sigma^j + 1 only; otherwise --ref selects it.
// It is slow; it checkpoints so that the campaign's certification suite can
// exercise resume on it.
template <class Cfg>
struct RefEngine {
    typedef Ref<Cfg> R;
    typedef typename R::Point Point;
    static const int M = Cfg::M;
    static const int BATCH = ECC_BATCH;
    struct Lane { Point p; u64 seed, startIter, hist; unsigned dead; };

    std::vector<Lane> lanes;
    std::vector<DpRecord> dp;
    unsigned dpCount[3] = {0, 0, 0};
    bool restartPending = false;
    const Solver<Cfg> *sol = nullptr;
    int threads = 0, steps = 0, dpWeight = 0;
    unsigned runId = 0, dpCap = 0;
    u64 maxIters = 0;

    static int autoThreads(int) {
#ifdef _OPENMP
        return omp_get_max_threads();
#else
        return 1;
#endif
    }
    void start(size_t id, u64 seed, u64 iterBase) {
        Lane &l = lanes[id];
        l.seed = seed;
        l.p = R::startPoint(seed, sol->basis, sol->target, 0, sol->ell, sol->spow);
        l.startIter = iterBase;
        l.hist = ECC_HIST_EMPTY;
        l.dead = 0;
    }
    void setup(const Options &o, const unsigned long long *, const unsigned long long *,
               const unsigned long long *, const unsigned long long *) {
        threads = o.threads; steps = o.steps; dpWeight = o.dpWeight; runId = o.runId;
        maxIters = o.maxIters; dpCap = o.dpCap;
        lanes.resize(size_t(threads) * BATCH);
        dp.assign(o.dpCap, DpRecord());
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
        for (long id = 0; id < (long)lanes.size(); ++id) start(id, eccSeedFor(runId, id), 0);
    }
    void launch(u64 iterBase) {
#ifdef _OPENMP
#pragma omp parallel for schedule(dynamic, 16)
#endif
        for (long id = 0; id < (long)lanes.size(); ++id) {
            Lane &l = lanes[id];
            for (int step = 0; step < steps && !l.dead; ++step) {
                const u64 now = iterBase + step;
                const int hw = R::weight(l.p.x);
                if (hw <= dpWeight) {
                    if ((l.seed & 0xffffull) == 0xffffull) eccAtomicInc(dpCount + 2);
                    const unsigned dest = eccAtomicInc(dpCount);
                    if (dest < dpCap) {
                        DpRecord rec;
                        rec.seed = l.seed;
                        rec.iters = now - l.startIter;
                        for (int i = 0; i < 3; ++i) { rec.x[i] = l.p.x.v[i]; rec.y[i] = l.p.y.v[i]; }
                        dp[dest] = rec;
                    }
                    l.dead = 1;
                    break;
                }
                if (maxIters && now % ECC_GUARD_PERIOD == 0 && now - l.startIter >= maxIters) {
                    if ((l.seed & 0xffffull) == 0xffffull) eccAtomicInc(dpCount + 2);
                    l.dead = 1;
                    eccAtomicInc(dpCount + 1);
                    break;
                }
#if ECC_WALK_TABLE
                l.p = sol->walk.step(l.p, hw, &l.hist, 0, 0, sol->ell, sol->spow);
#else
                l.p = R::step(l.p, hw);
#endif
            }
        }
    }
    void reseed(u64 iterBase) {
#ifdef _OPENMP
#pragma omp parallel for schedule(static)
#endif
        for (long id = 0; id < (long)lanes.size(); ++id)
            if (lanes[id].dead) start(id, lanes[id].seed + 1, iterBase);
        restartPending = false;
    }
    void synchronize() const {}
    unsigned fetch(std::vector<DpRecord> &out) {
        const unsigned n = dpCount[0];
        const bool exhausted = dpCount[2] != 0;
        restartPending = dpCount[1] != 0;
        const unsigned m = n < dpCap ? n : dpCap;
        out.assign(dp.begin(), dp.begin() + m);
        dpCount[0] = dpCount[1] = dpCount[2] = 0;
        return exhausted ? ECC_SEED_EXHAUSTED : n;
    }
    // One fixed-width record per lane: both coordinates as three words each,
    // the seed, the start iteration, the cycle history and the dead flag.  The
    // header's lane width is 1 and its version 2, so a bitsliced checkpoint of
    // the same curve and thread count is refused rather than misread.
    static const size_t LANE_WORDS = 3 + 3 + 1 + 1 + 1 + 1;
    static const unsigned CKPT_VERSION = ECC_WALK_TABLE ? 34u : 2u;
    void pack(std::vector<u64> &buf) const {
        buf.resize(lanes.size() * LANE_WORDS);
        for (size_t id = 0; id < lanes.size(); ++id) {
            const Lane &l = lanes[id];
            u64 *w = &buf[id * LANE_WORDS];
            for (int i = 0; i < 3; ++i) { w[i] = l.p.x.v[i]; w[3 + i] = l.p.y.v[i]; }
            w[6] = l.seed; w[7] = l.startIter; w[8] = l.hist; w[9] = l.dead;
        }
    }
    void unpack(const std::vector<u64> &buf) {
        for (size_t id = 0; id < lanes.size(); ++id) {
            Lane &l = lanes[id];
            const u64 *w = &buf[id * LANE_WORDS];
            for (int i = 0; i < 3; ++i) { l.p.x.v[i] = w[i]; l.p.y.v[i] = w[3 + i]; }
            l.seed = w[6]; l.startIter = w[7]; l.hist = w[8]; l.dead = (unsigned)w[9];
        }
    }
    bool save(const char *path, u64 iterBase, unsigned runId) const {
        const std::string tmp = std::string(path) + ".tmp";
        FILE *f = fopen(tmp.c_str(), "wb");
        if (!f) return false;
        CkptHeader h;
        memcpy(h.magic, "ECC2K130", 8);
        h.version = CKPT_VERSION;
        h.m = (unsigned)M;
        h.threads = (unsigned)threads;
        h.batch = (unsigned)BATCH;
        h.lanes = 1u;
        h.runId = runId;
        h.iterBase = iterBase;
        std::vector<u64> buf;
        pack(buf);
        bool ok = fwrite(&h, sizeof h, 1, f) == 1;
        ok = ok && fwrite(buf.data(), sizeof(u64), buf.size(), f) == buf.size();
        ok = ok && durableFlush(f);
        if (fclose(f) != 0) ok = false;
        if (!ok) { remove(tmp.c_str()); return false; }
        return rename(tmp.c_str(), path) == 0 && syncParent(path);
    }
    bool restore(const char *path, u64 *iterBase, unsigned runId) {
        FILE *f = fopen(path, "rb");
        if (!f) return false;
        CkptHeader h;
        std::vector<u64> buf(lanes.size() * LANE_WORDS);
        bool ok = fread(&h, sizeof h, 1, f) == 1 &&
                  ckptHeaderMatches(h, M, threads, BATCH, 1, runId, CKPT_VERSION) &&
                  ckptPayloadIsWhole(f, buf.size() * sizeof(u64));
        ok = ok && fread(buf.data(), sizeof(u64), buf.size(), f) == buf.size();
        fclose(f);
        if (!ok) return false;
        unpack(buf);
        *iterBase = h.iterBase;
        return true;
    }
    const char *name() const { return ECC_WALK_TABLE ? "reference-table-walk" : "reference"; }
    bool needsReseed() const { return restartPending; }
    u64 walksPerLaunch() const { return (u64)lanes.size(); }
};

#ifndef ECC_NO_CUDA
// ---------------------------------------------------------------------------
// device backend
// ---------------------------------------------------------------------------
template <class Cfg>
struct CudaEngine {
    typedef DeviceWord W;
    static const int M = Cfg::M;
    static const int LANES = WordTraits<W>::LANES;
    static const int BATCH = ECC_BATCH;
    WalkParams<W> P;
    std::vector<DpRecord> staging;
    bool restartPending = false;

    // Bytes of device memory one walk thread needs: x, y and pchain are a field
    // element per slot, plus the per-lane bookkeeping.
    static size_t bytesPerThread() {
        return (size_t)BATCH * M * sizeof(W) * 3
             + (size_t)BATCH * sizeof(W)
#if ECC_WITNESS
             + (size_t)BATCH * ECC_JCOUNT * ECC_COUNT_BITS * sizeof(W)
#endif
             + (size_t)BATCH * LANES * sizeof(u64) * 2;
    }

    // How many threads actually fill this device.
    //
    // The old answer was SMs * ECC_THREADS * 2, which divides back to exactly
    // two blocks per SM no matter what the build asked for -- so a kernel
    // compiled for four resident blocks launched half of them, paid the
    // register cut that buys the fourth block, and got no occupancy for it.
    // Ask the driver instead: cudaOccupancyMaxActiveBlocksPerMultiprocessor
    // reports what ptxas's register allocation actually admits for this exact
    // kernel, so the launch tracks the build.
    //
    // One wave is the right size.  Every thread runs the same number of steps,
    // so a second wave adds no work-stealing, only memory.
    static int autoThreads(int device) {
        cudaDeviceProp prop;
        CUDA_CHECK(cudaGetDeviceProperties(&prop, device));
        int perSm = 0;
        CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
            &perSm, eccWalkKernel<Cfg, W>, ECC_THREADS, 0));
        if (perSm < 1) perSm = 1;
        long long want = (long long)prop.multiProcessorCount * ECC_THREADS * perSm;
        // Each thread carries BATCH field elements three times over, which at
        // batch 32 is about 50 KB; a full wave at high occupancy can ask for
        // more than the card has.  Leave a quarter free for the DP buffer and
        // the driver, and shrink to whole blocks rather than failing to malloc.
        size_t freeB = 0, totalB = 0;
        CUDA_CHECK(cudaMemGetInfo(&freeB, &totalB));
        const size_t budget = freeB - freeB / 4;
        const long long fits = (long long)(budget / bytesPerThread());
        long long got = want < fits ? want : fits;
        got -= got % ECC_THREADS;
        if (got < ECC_THREADS) got = ECC_THREADS;
        printf("device: %s, %d SMs, %d block(s) of %d threads resident per SM\n",
               prop.name, prop.multiProcessorCount, perSm, (int)ECC_THREADS);
        printf("launch: %lld threads (%lld blocks), %.1f GB of walk state%s\n",
               got, got / ECC_THREADS,
               got * (double)bytesPerThread() / (1024.0 * 1024.0 * 1024.0),
               got < want ? "  [capped by device memory]" : "");
        return (int)got;
    }

    void setup(const Options &o, const unsigned long long *cpx, const unsigned long long *cpy,
               const unsigned long long *cqx, const unsigned long long *cqy) {
        const size_t T = (size_t)o.threads;
        const size_t fw = T * BATCH * M * sizeof(W);
        const size_t lw = T * BATCH * LANES * sizeof(u64);
        P.threads = o.threads;    // countElems() below sizes from it
        CUDA_CHECK(cudaMalloc(&P.x, fw));
        CUDA_CHECK(cudaMalloc(&P.y, fw));
        CUDA_CHECK(cudaMalloc(&P.pchain, fw));
        CUDA_CHECK(cudaMalloc(&P.dead, T * BATCH * sizeof(W)));
        CUDA_CHECK(cudaMemset(P.dead, 0, T * BATCH * sizeof(W)));
        P.counts = nullptr;
        if (countElems()) {
            CUDA_CHECK(cudaMalloc(&P.counts, countElems() * sizeof(W)));
            CUDA_CHECK(cudaMemset(P.counts, 0, countElems() * sizeof(W)));
        }
        CUDA_CHECK(cudaMalloc(&P.seed, lw));
        CUDA_CHECK(cudaMalloc(&P.startIter, lw));
        CUDA_CHECK(cudaMalloc(&P.dp, (size_t)o.dpCap * sizeof(DpRecord)));
        CUDA_CHECK(cudaMalloc(&P.dpCount, 3 * sizeof(unsigned)));
        CUDA_CHECK(cudaMemset(P.dpCount, 0, 3 * sizeof(unsigned)));
        cudaFuncAttributes attrs;
        CUDA_CHECK(cudaFuncGetAttributes(&attrs, eccWalkKernel<Cfg, W>));
        cudaDeviceProp prop;
        CUDA_CHECK(cudaGetDeviceProperties(&prop, o.device));
        printf("kernel: %d SMs, %d registers/thread, %zu local bytes/thread, "
               "%zu shared bytes/block, stream Karatsuba %d, prefer L1 %d\n",
               prop.multiProcessorCount, attrs.numRegs, attrs.localSizeBytes,
               attrs.sharedSizeBytes, (int)ECC_STREAM_KARAT, (int)o.preferL1);
        unsigned long long *dk;
        CUDA_CHECK(cudaMalloc(&dk, 12 * sizeof(u64)));
        u64 hk[12];
        memcpy(hk + 0, cpx, 3 * sizeof(u64));
        memcpy(hk + 3, cpy, 3 * sizeof(u64));
        memcpy(hk + 6, cqx, 3 * sizeof(u64));
        memcpy(hk + 9, cqy, 3 * sizeof(u64));
        CUDA_CHECK(cudaMemcpy(dk, hk, sizeof hk, cudaMemcpyHostToDevice));
        P.consts.px = dk;
        P.consts.py = dk + 3;
        P.consts.qx = dk + 6;
        P.consts.qy = dk + 9;
        P.steps = o.steps;
        P.dpWeight = o.dpWeight;
        P.runId = o.runId;
        P.maxIters = o.maxIters;
        P.iterBase = 0;
        P.dpCap = o.dpCap;
        staging.resize(o.dpCap);
        const int blocks = (o.threads + ECC_THREADS - 1) / ECC_THREADS;
        eccInitKernel<Cfg, W><<<blocks, ECC_THREADS, 0, 0>>>(P);
        CUDA_CHECK(cudaGetLastError());
        CUDA_CHECK(cudaDeviceSynchronize());
    }

    void launch(u64 iterBase) {
        P.iterBase = iterBase;
        const int blocks = (P.threads + ECC_THREADS - 1) / ECC_THREADS;
        eccWalkKernel<Cfg, W><<<blocks, ECC_THREADS, 0, 0>>>(P);
        CUDA_CHECK(cudaGetLastError());
    }

    void reseed(u64 iterBase) {
        P.iterBase = iterBase;
        const int blocks = (P.threads + ECC_THREADS - 1) / ECC_THREADS;
        eccReseedKernel<Cfg, W><<<blocks, ECC_THREADS, 0, 0>>>(P);
        CUDA_CHECK(cudaGetLastError());
    }

    void synchronize() const { CUDA_CHECK(cudaDeviceSynchronize()); }

    unsigned fetch(std::vector<DpRecord> &out) {
        CUDA_CHECK(cudaDeviceSynchronize());
        unsigned counts[3] = {0, 0, 0};
        CUDA_CHECK(cudaMemcpy(counts, P.dpCount, sizeof counts, cudaMemcpyDeviceToHost));
        const unsigned n = counts[0];
        restartPending = counts[1] != 0;
        const unsigned m = n < P.dpCap ? n : P.dpCap;
        out.resize(m);
        if (m) CUDA_CHECK(cudaMemcpy(out.data(), P.dp, (size_t)m * sizeof(DpRecord), cudaMemcpyDeviceToHost));
        if (n || restartPending || counts[2]) CUDA_CHECK(cudaMemset(P.dpCount, 0, sizeof counts));
        return counts[2] ? ECC_SEED_EXHAUSTED : n;
    }

    virtual ~CudaEngine() = default;
    virtual size_t fieldCount() const { return (size_t)P.threads * BATCH * M; }
    // Device field allocations may include physical-layout padding. Checkpoint
    // headers and payload sizes continue to use the logical fieldCount().
    virtual size_t physicalFieldCount() const { return fieldCount(); }
    size_t slotCount() const { return (size_t)P.threads * BATCH; }
    virtual size_t laneCount() const { return (size_t)P.threads * BATCH * LANES; }
    virtual unsigned checkpointVersion() const { return 1u + ECC_CKPT_BUMP; }
    // Bitsliced: ECC_COUNT_BITS words per counter per slot. Packed overrides
    // this, because there one worker is one walk and a counter is a number.
    virtual size_t countElems() const {
#if ECC_WITNESS
        return (size_t)P.threads * BATCH * ECC_JCOUNT * ECC_COUNT_BITS;
#else
        return 0;
#endif
    }
    virtual int checkpointLanes() const { return LANES; }
    // Backends may use a different coordinate representation on the device.
    // These hooks preserve the checkpoint representation without touching
    // live device state; the default backend already stores checkpoint words.
    virtual void exportCheckpointField(std::vector<W> &) const {}
    virtual void importCheckpointField(std::vector<W> &) const {}
    // Per-lane 64-bit arrays in the checkpoint, in order: seed, startIter,
    // and whatever a backend adds (the table walk's step history).
    virtual int laneArrayCount() const { return 2; }
    virtual u64 *laneArray(int i) const { return i ? P.startIter : P.seed; }

    bool save(const char *path, u64 iterBase, unsigned runId) const {
        CUDA_CHECK(cudaDeviceSynchronize());
        const std::string tmp = std::string(path) + ".tmp";
        FILE *f = fopen(tmp.c_str(), "wb");
        if (!f) return false;
        CkptHeader h;
        memcpy(h.magic, "ECC2K130", 8);
        h.version = checkpointVersion();
        h.m = (unsigned)M;
        h.threads = (unsigned)P.threads;
        h.batch = (unsigned)BATCH;
        h.lanes = (unsigned)checkpointLanes();
        h.runId = runId;
        h.iterBase = iterBase;
        bool ok = fwrite(&h, sizeof h, 1, f) == 1;
        std::vector<W> fbuf;
        std::vector<W> sbuf(slotCount());
        std::vector<u64> lbuf(laneCount());
        const W *fields[2] = {P.x, P.y};
        for (int i = 0; i < 2 && ok; ++i) {
            // Export may resize staging to the logical checkpoint field size.
            // Restore the physical transfer length independently for X and Y.
            fbuf.resize(physicalFieldCount());
            CUDA_CHECK(cudaMemcpy(fbuf.data(), fields[i], fbuf.size() * sizeof(W), cudaMemcpyDeviceToHost));
            exportCheckpointField(fbuf);
            if (fbuf.size() != fieldCount()) { ok = false; break; }
            ok = fwrite(fbuf.data(), sizeof(W), fbuf.size(), f) == fbuf.size();
        }
        if (ok) {
            CUDA_CHECK(cudaMemcpy(sbuf.data(), P.dead, sbuf.size() * sizeof(W), cudaMemcpyDeviceToHost));
            ok = fwrite(sbuf.data(), sizeof(W), sbuf.size(), f) == sbuf.size();
        }
        if (ok && countElems()) {
            std::vector<W> cbuf(countElems());
            CUDA_CHECK(cudaMemcpy(cbuf.data(), P.counts, cbuf.size() * sizeof(W), cudaMemcpyDeviceToHost));
            ok = fwrite(cbuf.data(), sizeof(W), cbuf.size(), f) == cbuf.size();
        }
        for (int i = 0; i < laneArrayCount() && ok; ++i) {
            CUDA_CHECK(cudaMemcpy(lbuf.data(), laneArray(i), lbuf.size() * sizeof(u64), cudaMemcpyDeviceToHost));
            ok = fwrite(lbuf.data(), sizeof(u64), lbuf.size(), f) == lbuf.size();
        }
        ok = ok && durableFlush(f);
        if (fclose(f) != 0) ok = false;
        if (!ok) { remove(tmp.c_str()); return false; }
        return rename(tmp.c_str(), path) == 0 && syncParent(path);
    }

    bool restore(const char *path, u64 *iterBase, unsigned runId) {
        FILE *f = fopen(path, "rb");
        if (!f) return false;
        CkptHeader h;
        const size_t payload = (2 * fieldCount() + slotCount() + countElems()) * sizeof(W) +
                               size_t(laneArrayCount()) * laneCount() * sizeof(u64);
        bool ok = fread(&h, sizeof h, 1, f) == 1 &&
                  ckptHeaderMatches(h, M, P.threads, BATCH, checkpointLanes(), runId, checkpointVersion()) &&
                  ckptPayloadIsWhole(f, payload);
        if (!ok) { fclose(f); return false; }
        std::vector<W> fbuf(fieldCount());
        std::vector<W> sbuf(slotCount());
        std::vector<u64> lbuf(laneCount());
        W *fields[2] = {P.x, P.y};
        for (int i = 0; i < 2 && ok; ++i) {
            // Import may expand staging into a padded physical field.
            fbuf.resize(fieldCount());
            ok = fread(fbuf.data(), sizeof(W), fbuf.size(), f) == fbuf.size();
            if (ok) {
                importCheckpointField(fbuf);
                if (fbuf.size() != physicalFieldCount()) { ok = false; break; }
                CUDA_CHECK(cudaMemcpy(fields[i], fbuf.data(), fbuf.size() * sizeof(W), cudaMemcpyHostToDevice));
            }
        }
        if (ok) {
            ok = fread(sbuf.data(), sizeof(W), sbuf.size(), f) == sbuf.size();
            if (ok) CUDA_CHECK(cudaMemcpy(P.dead, sbuf.data(), sbuf.size() * sizeof(W), cudaMemcpyHostToDevice));
        }
        if (ok && countElems()) {
            std::vector<W> cbuf(countElems());
            ok = fread(cbuf.data(), sizeof(W), cbuf.size(), f) == cbuf.size();
            if (ok) CUDA_CHECK(cudaMemcpy(P.counts, cbuf.data(), cbuf.size() * sizeof(W), cudaMemcpyHostToDevice));
        }
        for (int i = 0; i < laneArrayCount() && ok; ++i) {
            ok = ok && fread(lbuf.data(), sizeof(u64), lbuf.size(), f) == lbuf.size();
            if (ok) CUDA_CHECK(cudaMemcpy(laneArray(i), lbuf.data(), lbuf.size() * sizeof(u64), cudaMemcpyHostToDevice));
        }
        fclose(f);
        if (ok) *iterBase = h.iterBase;
        return ok;
    }

    const char *name() const { return "cuda"; }
    bool needsReseed() const { return restartPending; }
    u64 walksPerLaunch() const { return (u64)P.threads * BATCH * LANES; }
};
#include "../include/packedengine.cuh"
#endif

// ---------------------------------------------------------------------------
// validation
// ---------------------------------------------------------------------------
static int gFail = 0;
static void report(const char *what, bool ok) {
    printf("  %-46s %s\n", what, ok ? "ok" : "FAILED");
    if (!ok) gFail++;
}

struct Rng {
    u64 s;
    explicit Rng(u64 seed) : s(seed ? seed : 1) {}
    u64 next() {
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        return s;
    }
};

template <class Cfg>
static typename Ref<Cfg>::Elem randomElem(Rng &rng) {
    typename Ref<Cfg>::Elem e = Ref<Cfg>::zero();
    for (int i = 0; i < Cfg::M; ++i)
        if (rng.next() & 1) Ref<Cfg>::setBit(e, i);
    return e;
}

// random point of order l on y^2 + xy = x^3 + 1
template <class Cfg>
static typename Ref<Cfg>::Point randomPoint(Rng &rng, const U192 &ell) {
    typedef Ref<Cfg> R;
    for (;;) {
        const typename R::Elem x = randomElem<Cfg>(rng);
        if (R::isZero(x)) continue;
        const typename R::Elem c = R::add(x, R::inv(R::mul(x, x)));
        if (R::trace(c)) continue;
        const typename R::Elem z = R::halfTrace(c);
        typename R::Point p = R::make(x, R::mul(x, z));
        if (!R::onCurve(p)) continue;
        p = R::dbl(R::dbl(p));
        if (p.inf) continue;
        if (!R::scalarMul(p, ell).inf) continue;
        return p;
    }
}

template <class Cfg>
static void testField(Rng &rng) {
    typedef Ref<Cfg> R;
    typedef ECC_HOST_WORD W;
    typedef typename Cfg::template Field<W> F;
    const int M = Cfg::M;
    const int LANES = WordTraits<W>::LANES;
    std::vector<typename R::Elem> a(LANES), b(LANES);
    std::vector<W> ba(M, ECC_ZERO), bb(M, ECC_ZERO), bc(M, ECC_ZERO), bd(M, ECC_ZERO);
    for (int l = 0; l < LANES; ++l) {
        a[l] = randomElem<Cfg>(rng);
        b[l] = randomElem<Cfg>(rng);
        F::setLane(ba.data(), l, a[l].v);
        F::setLane(bb.data(), l, b[l].v);
    }
    bool okMul = true, okSqr = true, okInv = true, okHw = true, okSig = true;
    F::mul(ba.data(), bb.data(), bc.data());
    for (int l = 0; l < LANES; ++l) {
        typename R::Elem got;
        F::getLane(bc.data(), l, got.v);
        if (!(got == R::mul(a[l], b[l]))) okMul = false;
    }
    F::sqr(ba.data(), bc.data());
    for (int l = 0; l < LANES; ++l) {
        typename R::Elem got;
        F::getLane(bc.data(), l, got.v);
        if (!(got == R::sqr(a[l]))) okSqr = false;
    }
    F::inv(ba.data(), bc.data());
    for (int l = 0; l < LANES; ++l) {
        typename R::Elem got;
        F::getLane(bc.data(), l, got.v);
        if (!R::isZero(a[l]) && !(got == R::inv(a[l]))) okInv = false;
    }
    F::sigmaRun(ba.data(), 7, bc.data());
    for (int l = 0; l < LANES; ++l) {
        typename R::Elem got;
        F::getLane(bc.data(), l, got.v);
        if (!(got == R::sigma(a[l], 7))) okSig = false;
    }
    std::vector<W> hb(Cfg::HWBITS > 4 ? Cfg::HWBITS : 4, ECC_ZERO);
    Cfg::hamming(ba.data(), hb.data());
    for (int l = 0; l < LANES; ++l) {
        int got = 0;
        for (int i = 0; i < Cfg::HWBITS; ++i) got |= laneBit<W>(hb[i], l) << i;
        if (got != R::weight(a[l])) okHw = false;
    }
    report("bitsliced multiply matches reference", okMul);
    report("bitsliced squaring matches reference", okSqr);
    report("bitsliced inversion matches reference", okInv);
    report("bitsliced sigma^7 matches reference", okSig);
    report("bitsliced Hamming weight matches reference", okHw);
}

template <class Cfg>
static void testOrbit(Rng &rng, const Solver<Cfg> &sol) {
    typedef Ref<Cfg> R;
    const U192 &ell = sol.ell;
    bool okFrob = true, okNeg = true, okWeight = true, okTrace = true;
#if ECC_WALK_TABLE
    if (!TableWalk<Cfg>::applicable()) {
        printf("  %-46s (table walk needs a type-II normal basis)\n", "  ... not applicable for GF(2^m) in a polynomial basis");
        return;
    }
    // The table walk's class covariance rests on three identities of the
    // coordinate functions (tablewalk.h); check them, then the step itself
    // with an empty history and with a history that trips the cycle rule.
    bool okPhase = true, okEpsFrob = true, okEpsNeg = true, okTables = sol.walk.consts.consistent();
    for (int t = 0; t < 64; ++t) {
        const typename R::Point p = randomPoint<Cfg>(rng, ell);
        const int hw = R::weight(p.x);
        const int c = 1 + (int)(rng.next() % (Cfg::M - 1));
        const typename R::Point pc = R::frob(p, c), pn = R::neg(p);
        const typename R::Elem xn = R::nbCoords(p.x), yn = R::nbCoords(p.y);
        const typename R::Elem xc = R::nbCoords(pc.x), yc = R::nbCoords(pc.y), ynn = R::nbCoords(pn.y);
        const int k = sol.walk.phase(xn, hw), kc = sol.walk.phase(xc, hw);
        if (kc != (k + c) % Cfg::M) okPhase = false;
        if (sol.walk.negationBit(xc, yc, kc) != sol.walk.negationBit(xn, yn, k)) okEpsFrob = false;
        if (sol.walk.negationBit(xn, ynn, k) != 1 - sol.walk.negationBit(xn, yn, k)) okEpsNeg = false;
        u64 h0 = ECC_HIST_EMPTY, h1 = h0, h2 = h0;
        const typename R::Point f = sol.walk.step(p, hw, &h0, 0, 0, ell, sol.spow);
        if (!R::eq(sol.walk.step(pc, R::weight(pc.x), &h1, 0, 0, ell, sol.spow), R::frob(f, c))) okFrob = false;
        if (!R::eq(sol.walk.step(pn, R::weight(pn.x), &h2, 0, 0, ell, sol.spow), R::neg(f))) okNeg = false;
        // Trails meeting as R and sigma^c(-R) carry conjugate histories; the
        // cycle rule must fire for both or neither.
        const unsigned tag = unsigned(h0 & 0xFFFF);
        const u64 undo = eccHistPush(ECC_HIST_EMPTY, tag ^ ECC_TAG_EPS);
        const u64 undoC = eccHistPush(ECC_HIST_EMPTY, unsigned(h1 & 0xFFFF) ^ ECC_TAG_EPS);
        const u64 undoN = eccHistPush(ECC_HIST_EMPTY, unsigned(h2 & 0xFFFF) ^ ECC_TAG_EPS);
        u64 g0 = undo, g1 = undoC, g2 = undoN;
        const typename R::Point fa = sol.walk.step(p, hw, &g0, 0, 0, ell, sol.spow);
        // A synthetic history is only a hint; it need not change this point.
        if (!R::eq(sol.walk.step(pc, R::weight(pc.x), &g1, 0, 0, ell, sol.spow), R::frob(fa, c))) okFrob = false;
        if (!R::eq(sol.walk.step(pn, R::weight(pn.x), &g2, 0, 0, ell, sol.spow), R::neg(fa))) okNeg = false;
        // The same with a history that closes a tau-relation (WALK-CONSTANT.md
        // section 11): the last three steps are the tag itself and the two
        // phases after it on the same branch, so sigma^2 + sigma + 2 = 0 would
        // return the walk.  The rule must refuse it for R, sigma^c(R) and -R.
        auto tau = [&](u64 h) {
            const unsigned t = unsigned(h & 0xFFFF);
            return eccHistPush(eccHistPush(eccHistPush(ECC_HIST_EMPTY, eccTagAdvanceK(t, 2, Cfg::M)),
                                           eccTagAdvanceK(t, 1, Cfg::M)), t);
        };
        u64 r0 = tau(h0), r1 = tau(h1), r2 = tau(h2);
        const typename R::Point fr = sol.walk.step(p, hw, &r0, 0, 0, ell, sol.spow);
        // A tau hint alone does not establish a raw cycle at this point.
        if (!R::eq(sol.walk.step(pc, R::weight(pc.x), &r1, 0, 0, ell, sol.spow), R::frob(fr, c))) okFrob = false;
        if (!R::eq(sol.walk.step(pn, R::weight(pn.x), &r2, 0, 0, ell, sol.spow), R::neg(fr))) okNeg = false;
        if (R::weight(pc.x) != hw) okWeight = false;
        if (R::trace(p.x) != 0) okTrace = false;
    }
    report("coordinate logarithm table is a bijection", okTables);
    report("Frobenius phase advances by one under sigma", okPhase);
    report("negation bit is sigma-invariant", okEpsFrob);
    report("negation bit flips under negation", okEpsNeg);
#else
    for (int t = 0; t < 8; ++t) {
        const typename R::Point p = randomPoint<Cfg>(rng, ell);
        const int hw = R::weight(p.x);
        const typename R::Point f = R::step(p, hw);
        const int c = (int)(rng.next() % Cfg::M);
        const typename R::Point pc = R::frob(p, c);
        if (!R::eq(R::step(pc, R::weight(pc.x)), R::frob(f, c))) okFrob = false;
        const typename R::Point pn = R::neg(p);
        if (!R::eq(R::step(pn, R::weight(pn.x)), R::neg(f))) okNeg = false;
        if (R::weight(pc.x) != hw) okWeight = false;
        if (R::trace(p.x) != 0) okTrace = false;
    }
#endif
#if ECC_PACKED_XONLY_POLY_SELECT
    (void)okFrob;
    report("polynomial-bit selector is not Frobenius-invariant", true);
#else
    report("iteration commutes with Frobenius", okFrob);
#endif
    report("iteration commutes with negation", okNeg);
    report("weight is constant on an orbit", okWeight);
    report("subgroup x-coordinates have even weight", okTrace);
}

// The equation the whole witness rests on, checked here rather than believed.
//
// A trail is [mu]R_0 with mu = prod_j (1 + s^j)^{n_j}, so a point plus eight
// counts is a claim about where [mu] takes the start point.  A cairn checker
// spells the same claim [mu*alpha_0]P + [mu]Q, which is that one composed with
// R_0 = [alpha_0]P + Q -- and testStartPoint above already checks that half, so
// checking this one completes the chain at half the scalar multiplications.
// If the algebra were wrong, or the counts counted the wrong thing, every
// record this client emits would verify locally and be refused by everyone
// else.  CAIRN-WITNESS.md is the design.
template <class Cfg>
static void testWitness(Rng &rng, Solver<Cfg> &sol) {
    typedef Ref<Cfg> R;
    const int STEPS = 50;
    const int TRAILS = 4;
    bool okSum = true, okMu = true;
    int walked = 0, verified = 0;
    for (int t = 0; t < 2 * TRAILS && walked < TRAILS; ++t) {
        U192 alpha0;
        bool degenerate = false;
        const u64 seed = rng.next();
        const typename R::Point start =
            R::startPoint(seed, sol.basis, sol.target, &alpha0, sol.ell, sol.spow, &degenerate);
        if (degenerate) continue;     // toy fields only; see testStartPoint
        typename R::Point p = start;
        unsigned long long counts[ECC_JCOUNT] = {0};
        unsigned long long taken = 0;
        bool ran = true;
        for (int i = 0; i < STEPS; ++i) {
            const int hw = R::weight(p.x);
            counts[R::jOf(hw) - 3]++;
            p = R::step(p, hw);
            ++taken;
            if (p.inf) { ran = false; break; }
        }
        if (!ran) continue;
        ++walked;
        unsigned long long total = 0;
        for (int k = 0; k < ECC_JCOUNT; ++k) total += counts[k];
        if (total != taken) okSum = false;
        // The endpoint identity costs one scalar multiplication, and the
        // reference's is O(m^3) -- at m = 131 it is seconds, so one trail is
        // checked rather than all four.  The counting half above is free and
        // runs on every trail.
        if (verified == 0) {
            if (!R::eq(R::scalarMul(start, sol.multiplier(counts)), p)) okMu = false;
            ++verified;
        }
    }
    report("witness counts sum to the trail length", okSum && walked > 0);
    report("[mu] times the start point is the trail's endpoint", okMu && verified > 0);
}

template <class Cfg>
static void testStartPoint(Rng &rng, Solver<Cfg> &sol) {
    typedef Ref<Cfg> R;
    typedef ECC_HOST_WORD W;
    typedef typename Cfg::template Field<W> F;
    typedef Walk<Cfg, W> WK;
    const int M = Cfg::M;
    const int LANES = WordTraits<W>::LANES;
    std::vector<u64> seeds(LANES);
    for (int l = 0; l < LANES; ++l) seeds[l] = rng.next();
    std::vector<W> x(M, ECC_ZERO), y(M, ECC_ZERO);
    CurveConsts K;
    K.px = sol.basis.x.v;
    K.py = sol.basis.y.v;
    K.qx = sol.target.x.v;
    K.qy = sol.target.y.v;
    WK::startPoint(seeds.data(), K, x.data(), y.data());
    // The reference is the slow path here, so a handful of lanes is plenty.
    const int CHECK = LANES < 16 ? LANES : 16;
    bool ok = true, okAlpha = true;
    int skipped = 0;
    for (int l = 0; l < CHECK; ++l) {
        typename R::Elem gx, gy;
        F::getLane(x.data(), l, gx.v);
        F::getLane(y.data(), l, gy.v);
        U192 alpha;
        bool degenerate = false;
        const typename R::Point want =
            R::startPoint(seeds[l], sol.basis, sol.target, &alpha, sol.ell, sol.spow, &degenerate);
        if (!(gx == want.x) || !(gy == want.y)) ok = false;
        // start = [alpha] P + Q.  The client adds without special cases, so
        // this identity only holds while no two summands share an abscissa.
        // That needs a birthday collision among a few hundred points, so it
        // never happens on the challenge curves and does on a toy field.
        if (degenerate) {
            ++skipped;
            continue;
        }
        const typename R::Point chk = R::addPt(R::scalarMul(sol.basis, alpha), sol.target);
        if (!R::eq(chk, want)) okAlpha = false;
    }
    report("bitsliced start point matches reference", ok);
    report("tracked start scalar reproduces the point", okAlpha);
    if (skipped)
        printf("  %-46s %d of %d lanes (degenerate addition on a toy field)\n",
               "  ... not applicable for", skipped, CHECK);
}

template <class Cfg>
static void testSolveAlgebra(Rng &rng, const Solver<Cfg> &base) {
    typedef Ref<Cfg> R;
    // Plant a discrete log of our own, synthesise two walks that meet with
    // known exponents, and check the solver recovers it.
    Solver<Cfg> sol = base;
    U192 knownK = u192_zero();
    knownK.v[0] = rng.next();
    knownK.v[1] = rng.next();
    knownK = mod_reduce(knownK, sol.ell);
    if (u192_is_zero(knownK)) knownK = u192_from(7);
    sol.target = R::scalarMul(sol.basis, knownK);
    bool ok = true;
    auto randomScalar = [&]() {
        U192 r = u192_zero();
        r.v[0] = rng.next();
        r.v[1] = rng.next() & 0xFFFF;
        return mod_reduce(r, sol.ell);
    };
    for (int t = 0; t < 4 && ok; ++t) {
        typename Solver<Cfg>::WalkResult A, B;
        A.ok = B.ok = true;
        // Endpoints are a P + b Q.  The sigma^j + 1 walk has b = mu, a product
        // of (1 + s^j); the table walk's b is an arbitrary residue.
#if ECC_WALK_TABLE
        A.b = randomScalar();
        B.b = randomScalar();
#else
        for (int j = 0; j < 8; ++j) {
            A.counts[j] = rng.next() % 7;
            B.counts[j] = rng.next() % 7;
        }
        A.b = sol.multiplier(A.counts);
        B.b = sol.multiplier(B.counts);
#endif
        B.a = randomScalar();
        const int c = (int)(rng.next() % Cfg::M);
        const int eps = (rng.next() & 1) ? 1 : -1;
        U192 sc = sol.spow[c];
        if (eps < 0) sc = mod_neg(sc, sol.ell);
        // choose a_A so that  a_A + b_A k = eps s^c (a_B + b_B k)
        const U192 rhs = mod_mul(sc, mod_add(B.a, mod_mul(B.b, knownK, sol.ell), sol.ell), sol.ell);
        A.a = mod_sub(rhs, mod_mul(A.b, knownK, sol.ell), sol.ell);
        A.endPoint = R::addPt(R::scalarMul(sol.basis, A.a), R::scalarMul(sol.target, A.b));
        B.endPoint = R::addPt(R::scalarMul(sol.basis, B.a), R::scalarMul(sol.target, B.b));
        if (!R::eq(A.endPoint, eps > 0 ? R::frob(B.endPoint, c) : R::neg(R::frob(B.endPoint, c)))) {
            ok = false;
            break;
        }
        U192 got;
        std::string why;
        if (!sol.solve(A, B, &got, &why) || !u192_eq(got, knownK)) ok = false;
    }
    report("collision solver recovers a known discrete log", ok);
}

// ---------------------------------------------------------------------------
// search loop
// ---------------------------------------------------------------------------
template <class Cfg, class Engine>
static int runSearch(const Options &o, Engine &eng, Solver<Cfg> &sol, const U192 *knownK) {
    typedef Ref<Cfg> R;
    std::vector<DpRecord> recs;
    u64 iterBase = 0;
    u64 totalDp = 0, lost = 0, verified = 0, verifyBudget = (u64)(o.verify < 0 ? 0 : o.verify);
    CorpusOutput output;
    if (eng.walksPerLaunch() >= ECC_SEED_EXHAUSTED) {
        fprintf(stderr, "too many walks for the report counter or seed namespace\n");
        return 1;
    }
    // A fresh corpus gets the v2 header; an existing one keeps whatever format
    // it already is, because appending v2 records to a v1 stream would produce
    // a file neither reader can frame.  Refusing is the only safe answer: the
    // witness has to go somewhere the reader will find it.
    const bool dpOutV2 = ECC_WITNESS != 0;
    if (!o.dpFile.empty()) {
        // Read the file's shape from a separate handle rather than from the
        // append handle.  A stream opened "ab" cannot be read at all, and its
        // ftell is an implementation-defined position rather than a length
        // until the first write -- on the library that reports 0 that would
        // put a v2 header in the middle of an existing corpus and mis-frame
        // everything after it.
        long long probed = 0;
        bool probedV2 = false;
        FILE *probe = fopen(o.dpFile.c_str(), "rb");
        if (probe) {
            struct stat ps;
            if (fstat(fileno(probe), &ps) == 0 && S_ISREG(ps.st_mode))
                probed = (long long)ps.st_size;
            probedV2 = dpFileIsV2(probe);
            fclose(probe);
        }
        // Integrity before compatibility.  A corpus whose tail is a partial
        // record is corrupt whichever build opens it, and that is the more
        // urgent thing to report than which format it happens to be in --
        // "refusing to append v2 to a v1 corpus" is true of a truncated v1
        // file and tells an operator the wrong thing to go and fix.  Judge it
        // in its OWN framing, since that is the writer it has to be whole for.
        const size_t probedBase = dpHeaderBytes(probedV2);
        const size_t probedRec = probedV2 ? sizeof(DpFileRecordV2) : sizeof(DpFileRecord);
        if (probed != 0 && ((unsigned long long)probed < probedBase ||
                            ((unsigned long long)probed - probedBase) % probedRec != 0)) {
            fprintf(stderr, "persistence failure: cannot lock/open aligned regular corpus %s\n",
                    o.dpFile.c_str());
            return 8;
        }
        if (probed != 0 && probedV2 != dpOutV2) {
            fprintf(stderr, "refusing to append %s records to a %s corpus: %s\n",
                    dpOutV2 ? "v2" : "v1", probedV2 ? "v2" : "v1", o.dpFile.c_str());
            return 2;
        }
        if (!output.openFile(o.dpFile, dpOutV2 ? sizeof(DpFileRecordV2) : sizeof(DpFileRecord),
                             dpHeaderBytes(dpOutV2))) {
            fprintf(stderr, "persistence failure: cannot lock/open aligned regular corpus %s\n", o.dpFile.c_str());
            return 8;
        }
        // The size measured under the lock decides the header, not the probe
        // above: the probe runs before the lock exists.
        if ((dpOutV2 || ECC_WALK_TABLE) && output.bytes == 0) {
            DpFileHeader h;
            memcpy(h.magic, ECC_WALK_TABLE ? DP_MAGIC_TABLE3 : DP_MAGIC_V2, sizeof h.magic);
            h.version = ECC_WALK_TABLE ? 3u : 2u;
            h.recordBytes = (unsigned)(dpOutV2 ? sizeof(DpFileRecordV2) : sizeof(DpFileRecord));
            if (fwrite(&h, sizeof h, 1, output.file) != 1) {
                fprintf(stderr, "persistence failure: cannot write the corpus header to %s\n", o.dpFile.c_str());
                return 8;
            }
            // Reload opens a separate handle below; publish the framing first.
            if (fflush(output.file) != 0) {
                fprintf(stderr, "persistence failure: cannot flush corpus header\n");
                return 8;
            }
        }
    }

    // Reload every corpus file first, so a collision against work done by an
    // earlier run or another worker is found the moment it happens rather than
    // only by an offline merge.
    size_t reloaded = 0;
    std::vector<std::string> corpus = o.loadFiles;
    if (!o.dpFile.empty()) corpus.push_back(o.dpFile);

    // The dp file is normally also named by --load, and two --load flags can
    // reach one corpus through different paths.  Reading a file twice costs
    // time and reports a misleading count; the second pass is all duplicates.
    for (size_t ci = 0; ci < corpus.size(); ++ci) {
        char *real = realpath(corpus[ci].c_str(), 0);
        if (!real) continue;
        corpus[ci] = real;
        free(real);
    }
    std::sort(corpus.begin(), corpus.end());
    corpus.erase(std::unique(corpus.begin(), corpus.end()), corpus.end());

    // Newest first, so that a --load-max cap keeps the most recent work rather
    // than whichever file sorts first by name.  A store entry costs far more
    // than the 32 bytes it occupies on disk, so an unbounded reload is what
    // ends a long collection run: the corpus grows every pass, and without a
    // cap each pass tries to hold every point every earlier pass ever wrote.
    // What the cap gives up is finding a collision in process; the corpus is
    // still complete on disk, and the offline merge still finds it there.
    std::sort(corpus.begin(), corpus.end(),
              [](const std::string &a, const std::string &b) {
                  struct stat sa, sb;
                  const bool oa = stat(a.c_str(), &sa) == 0, ob = stat(b.c_str(), &sb) == 0;
                  if (!oa || !ob) return oa > ob;
                  return sa.st_mtime > sb.st_mtime;
              });
    size_t skippedFiles = 0;
    for (size_t ci = 0; ci < corpus.size(); ++ci) {
        if (o.loadMax && reloaded >= o.loadMax) { skippedFiles = corpus.size() - ci; break; }
        FILE *in = fopen(corpus[ci].c_str(), "rb");
        struct stat inputStat;
        if (!in || fstat(fileno(in), &inputStat) != 0 || !S_ISREG(inputStat.st_mode)) {
            if (in) fclose(in);
            fprintf(stderr, "persistence failure: missing, non-regular or truncated corpus %s\n", corpus[ci].c_str());
            return 8;
        }
        // Frame the file before judging its length: a v2 corpus is a header
        // plus 72-byte records, so a v1 record-size check would call every
        // valid v2 corpus truncated.
        const bool v2 = dpFileIsV2(in);
        const long base = (long)dpHeaderBytes(v2);
        const size_t recBytes = v2 ? sizeof(DpFileRecordV2) : sizeof(DpFileRecord);
        if (inputStat.st_size < base ||
            (unsigned long long)(inputStat.st_size - base) % recBytes != 0) {
            fclose(in);
            fprintf(stderr, "persistence failure: missing, non-regular or truncated corpus %s\n", corpus[ci].c_str());
            return 8;
        }
        // A single --dp-file is append-only across passes, so the newest
        // records sit at the end.  When the remaining cap is smaller than
        // the file, start there rather than keeping the oldest prefix.
        if (o.loadMax && reloaded < o.loadMax && fseek(in, 0, SEEK_END) == 0) {
            const long sz = ftell(in) - base;
            unsigned long long skip = 0;
            if (sz > 0) {
                const unsigned long long nrec = (unsigned long long)sz / recBytes;
                const unsigned long long remain = o.loadMax - (unsigned long long)reloaded;
                if (nrec > remain) skip = nrec - remain;
            }
            if (fseek(in, base + (long)(skip * recBytes), SEEK_SET) != 0)
                fseek(in, base, SEEK_SET);
        } else if (fseek(in, base, SEEK_SET) != 0) {
            rewind(in);
        }
        DpFileRecordV2 fr;
        while (fread(&fr, recBytes, 1, in) == 1) {
            if (!v2) {
                // A v1 record is the first 32 bytes of a v2 one only by
                // accident of field order, so unpack rather than alias.
                const DpFileRecord *v1 = (const DpFileRecord *)&fr;
                const unsigned long long seed = v1->seed;
                const unsigned long long c0 = v1->canon[0], c1 = v1->canon[1], c2 = v1->canon[2];
                fr.seed = seed;
                fr.iters = 0;
                fr.canon[0] = c0; fr.canon[1] = c1; fr.canon[2] = c2;
                for (int k = 0; k < ECC_JCOUNT; ++k) fr.counts[k] = 0;
            }
            if (o.loadMax && reloaded >= o.loadMax) break;
            typename Solver<Cfg>::Key key;
            key.v[0] = fr.canon[0];
            key.v[1] = fr.canon[1];
            key.v[2] = fr.canon[2];
            typename Solver<Cfg>::Entry other;
            ++reloaded;
            if (!sol.insertKey(key, fr.seed, 0, &other)) continue;
            printf("collision found while reloading %s: seeds %016llx and %016llx\n",
                   corpus[ci].c_str(), (unsigned long long)fr.seed,
                   (unsigned long long)other.seed);
            const typename Solver<Cfg>::WalkResult A = sol.rewalk(fr.seed);
            const typename Solver<Cfg>::WalkResult B = sol.rewalk(other.seed);
            U192 k;
            std::string why;
            if (sol.solve(A, B, &k, &why)) {
                printf("  k = %s\n  verified [k]P == Q\n", u192_to_dec(k).c_str());
                if (knownK) printf("  matches the published solution: %s\n",
                                   u192_eq(k, *knownK) ? "yes" : "NO");
                fclose(in);
                return (knownK && !u192_eq(k, *knownK)) ? 4 : 0;
            }
            printf("  unusable (%s)\n", why.c_str());
        }
        const bool readFailed = ferror(in) != 0;
        fclose(in);
        if (readFailed) return 8;
    }
    if (reloaded) {
        printf("reloaded %zu points from %zu file(s), %zu distinct orbits\n",
               reloaded, corpus.size() - skippedFiles, sol.inserted);
        if (o.loadMax && reloaded >= o.loadMax)
            printf("  stopped at the --load-max %llu cap; %zu file(s) not read, "
                   "their collisions are left to the offline merge\n",
                   (unsigned long long)o.loadMax, skippedFiles);
    }

    // Resume the walks themselves.  Without this a restart abandons every walk
    // in flight, which at the usual cutoff is about a quarter of the whole run.
    if (!o.ckptFile.empty()) {
        if (eng.restore(o.ckptFile.c_str(), &iterBase, o.runId))
            printf("resumed from %s at iteration %llu\n", o.ckptFile.c_str(),
                   (unsigned long long)iterBase);
        else {
            struct stat existing;
            if (stat(o.ckptFile.c_str(), &existing) == 0 || errno != ENOENT) {
                fprintf(stderr, "checkpoint %s is incompatible or incomplete; use the matching backend/settings or a new checkpoint path\n", o.ckptFile.c_str());
                return 6;
            }
            printf("no checkpoint at %s, starting fresh\n", o.ckptFile.c_str());
        }
    }

    if (o.verify > 0) {
        // A normal-cutoff replay is a 2^28–2^32-step scalar walk on the CPU.
        // The next device launch is queued first so the GPU is not idle for it.
        printf("WARNING: --verify %d replays reports on the CPU; campaign "
               "collection uses --verify 0 so a cutoff-32 trail cannot stall the GPU\n",
               o.verify);
        fflush(stdout);
    }

    const u64 timedIterBase = iterBase;
    const double t0 = nowSeconds();
    double lastPrint = t0;
    double lastCkpt = t0;
    FILE *dpOut = output.file;
    bool nextInFlight = false;
    for (long launch = 0; o.launches == 0 || launch < o.launches; ++launch) {
        if (!nextInFlight) eng.launch(iterBase);
        nextInFlight = false;
        const unsigned n = eng.fetch(recs);
        iterBase += (u64)o.steps;
        if (n == ECC_SEED_EXHAUSTED) {
            fprintf(stderr, "SEED EXHAUSTED: refusing counter wrap into another walk; retire this run-id\n");
            return 9;
        }
        if (n > recs.size()) {
            fprintf(stderr, "DP OVERFLOW: %u reports, capacity %zu; checkpoint NOT advanced. Increase --dp-cap or lower --dp-weight.\n", n, recs.size());
            return 7;
        }
        if (n || eng.needsReseed()) eng.reseed(iterBase);

        // Start the next walk before host DP handling. fetch() already copied
        // this launch's reports, so the next kernel cannot overwrite them.
        // Skip when a checkpoint is due: save() captures walk state and must
        // not race an in-flight launch.
        const double nowBeforeHost = nowSeconds();
        const bool leaving = gStop || (o.launches && launch + 1 == o.launches);
        const bool ckptDue = !o.ckptFile.empty() &&
                             (leaving || nowBeforeHost - lastCkpt > o.ckptSeconds);
        if (!leaving && !ckptDue) {
            eng.launch(iterBase);
            nextInFlight = true;
        }

        totalDp += recs.size();
        for (size_t i = 0; i < recs.size(); ++i) {
            const DpRecord &rec = recs[i];
            if (verifyBudget) {
                --verifyBudget;
                const typename Solver<Cfg>::WalkResult w = sol.rewalk(rec.seed);
                const bool same = w.ok && w.iters == rec.iters &&
                                  w.endPoint.x == R::fromLimbs(rec.x)
#if !ECC_PACKED_XONLY_23
                                  && w.endPoint.y == R::fromLimbs(rec.y)
#endif
                                  ;
                if (!same) {
                    printf("MISMATCH: seed %016llx was not reproduced by the reference walk\n",
                           (unsigned long long)rec.seed);
                    return 3;
                }
#if ECC_WITNESS
                // The witness is the only thing the walk now carries that
                // nothing else would catch: a wrong count still produces a
                // well-formed record, a well-formed claim, and a mu that lands
                // on some other orbit.  The reference walk counts the same
                // branches, so this is the oracle for it.
                u64 witnessed = 0;
                for (int k = 0; k < ECC_JCOUNT; ++k) {
                    if ((u64)rec.counts[k] != w.counts[k]) {
                        printf("MISMATCH: seed %016llx witness[%d] = %u, the reference walk "
                               "took %llu steps on that branch\n",
                               (unsigned long long)rec.seed, k, rec.counts[k],
                               (unsigned long long)w.counts[k]);
                        return 3;
                    }
                    witnessed += rec.counts[k];
                }
                if (witnessed != rec.iters) {
                    printf("MISMATCH: seed %016llx witness sums to %llu over %llu steps\n",
                           (unsigned long long)rec.seed, (unsigned long long)witnessed,
                           (unsigned long long)rec.iters);
                    return 3;
                }
#endif
                ++verified;
            }
            if (dpOut) {
                const typename R::Elem cx = R::canonical(R::fromLimbs(rec.x));
                bool wrote;
                if (dpOutV2) {
                    DpFileRecordV2 fr;
                    fr.seed = rec.seed;
                    fr.iters = rec.iters;
                    fr.canon[0] = cx.v[0];
                    fr.canon[1] = cx.v[1];
                    fr.canon[2] = cx.v[2];
                    for (int k = 0; k < ECC_JCOUNT; ++k) fr.counts[k] = rec.counts[k];
                    wrote = fwrite(&fr, sizeof fr, 1, dpOut) == 1;
                } else {
                    DpFileRecord fr;
                    fr.seed = rec.seed;
                    fr.canon[0] = cx.v[0];
                    fr.canon[1] = cx.v[1];
                    fr.canon[2] = cx.v[2];
                    wrote = fwrite(&fr, sizeof fr, 1, dpOut) == 1;
                }
                if (!wrote) {
                    fprintf(stderr, "persistence failure: corpus write; checkpoint NOT advanced\n");
                    return 8;
                }
            }
            typename Solver<Cfg>::Entry other;
            if (!sol.insert(rec, &other)) continue;
            printf("collision: seeds %016llx and %016llx meet after %llu and %llu steps\n",
                   (unsigned long long)rec.seed, (unsigned long long)other.seed,
                   (unsigned long long)rec.iters, (unsigned long long)other.iters);
            const double tr = nowSeconds();
            const typename Solver<Cfg>::WalkResult A = sol.rewalk(rec.seed);
            const typename Solver<Cfg>::WalkResult B = sol.rewalk(other.seed);
            U192 k;
            std::string why;
            if (!sol.solve(A, B, &k, &why)) {
                printf("  unusable (%s), continuing\n", why.c_str());
                continue;
            }
            if (nextInFlight) {
                eng.synchronize();
                iterBase += (u64)o.steps;
                nextInFlight = false;
            }
            printf("  recomputed both walks in %.2f s\n", nowSeconds() - tr);
            printf("  k = %s\n", u192_to_dec(k).c_str());
            printf("  verified [k]P == Q\n");
            if (knownK) printf("  matches the planted discrete log: %s\n",
                               u192_eq(k, *knownK) ? "yes" : "NO");
            const double el = nowSeconds() - t0;
            printf("  solved after %llu iterations of %llu walks in %.2f s (%llu distinguished points)\n",
                   (unsigned long long)iterBase, (unsigned long long)eng.walksPerLaunch(), el,
                   (unsigned long long)totalDp);
            if (!output.closeFile()) return 8;
            if (!o.ckptFile.empty() && !eng.save(o.ckptFile.c_str(), iterBase, o.runId)) return 8;
            return (knownK && !u192_eq(k, *knownK)) ? 4 : 0;
        }
        const double now = nowSeconds();
        // Flush reports and checkpoint on a timer, and always on the way out,
        // so a container stopped by its deadline loses seconds of work rather
        // than hours of it.
        if (dpOut && (leaving || ckptDue || now - lastCkpt > o.ckptSeconds) && !durableFlush(dpOut)) {
            fprintf(stderr, "persistence failure: corpus flush; checkpoint NOT advanced\n");
            return 8;
        }
        if (ckptDue) {
            if (!eng.save(o.ckptFile.c_str(), iterBase, o.runId)) {
                fprintf(stderr, "persistence failure: checkpoint %s\n", o.ckptFile.c_str());
                return 8;
            }
            lastCkpt = now;
            if (!leaving) {
                eng.launch(iterBase);
                nextInFlight = true;
            }
        }
        if (gStop) {
            printf("stopping: %llu iterations of %llu walks, %llu points reported\n",
                   (unsigned long long)iterBase, (unsigned long long)eng.walksPerLaunch(),
                   (unsigned long long)totalDp);
            break;
        }
        if (now - lastPrint > 2.0 || (o.launches && launch + 1 == o.launches)) {
            const double el = now - t0;
            const double it = (double)(iterBase - timedIterBase) * (double)eng.walksPerLaunch();
            printf("  %8.1f s  %10.3f M it/s  %10llu iterations  %8llu dp  %8llu stored"
                   "  %8llu dropped\n",
                   el, it / el / 1e6, (unsigned long long)it, (unsigned long long)totalDp,
                   (unsigned long long)sol.inserted, (unsigned long long)lost);
            fflush(stdout);
            lastPrint = now;
        }
    }
    // Include any final asynchronous reseed in the completed-run rate.
    eng.synchronize();
    const double el = nowSeconds() - t0;
    const double it = (double)(iterBase - timedIterBase) * (double)eng.walksPerLaunch();
    printf("  finished: %.3f M it/s, %llu distinguished points (%llu verified against the reference, %llu dropped)\n",
           it / el / 1e6, (unsigned long long)totalDp, (unsigned long long)verified, (unsigned long long)lost);
    if (!output.closeFile()) return 8;
    return 0;
}

// ---------------------------------------------------------------------------
// replay: a corpus in, the same corpus with its witness in
//
// A serial replay costs the SUM of the trail lengths.  This costs the LONGEST
// one in each chunk, because the bitsliced walk steps every lane of a word at
// once and a corpus supplies exactly the independent trails those lanes want.
// On the 128-record ECC2K-130 corpus that is 77,146 steps against 5,039,383.
//
// Same kernel, same counters, same reports as a search -- only the seeds come
// from a file.  That matters: a replay assembled out of different machinery
// would be evidence about that machinery rather than about this walk.
//
// Every record is checked against what the corpus already claims: the replay
// must land on the orbit the record names.  On a v2 corpus the counts are
// checked too, which makes `--replay` a full-corpus audit of the carried
// witness rather than only a way to add one.
template <class Cfg, class Engine>
static int runReplay(const Options &o, Engine &eng, Solver<Cfg> &sol) {
    typedef Ref<Cfg> R;
    (void)sol;

    // ---- read the corpus, either format ----------------------------------
    FILE *in = fopen(o.replayFile.c_str(), "rb");
    struct stat st;
    if (!in || fstat(fileno(in), &st) != 0 || !S_ISREG(st.st_mode)) {
        fprintf(stderr, "cannot read corpus %s\n", o.replayFile.c_str());
        if (in) fclose(in);
        return 8;
    }
    const bool v2 = dpFileIsV2(in);
    const long base = (long)dpHeaderBytes(v2);
    const size_t stride = v2 ? sizeof(DpFileRecordV2) : sizeof(DpFileRecord);
    if (st.st_size < base || (unsigned long long)(st.st_size - base) % stride != 0) {
        fprintf(stderr, "corpus %s is not a whole number of %s records\n",
                o.replayFile.c_str(), v2 ? "v2" : "v1");
        fclose(in);
        return 8;
    }
    const size_t total = (size_t)((st.st_size - base) / stride);
    std::vector<u64> seeds(total);
    std::vector<u64> claimX(total * 3);
    std::vector<unsigned> claimJ(total * ECC_JCOUNT, 0);
    std::vector<u64> claimIters(total, 0);
    if (fseek(in, base, SEEK_SET) != 0) { fclose(in); return 8; }
    for (size_t i = 0; i < total; ++i) {
        DpFileRecordV2 fr;
        if (fread(&fr, stride, 1, in) != 1) {
            fprintf(stderr, "short read on %s\n", o.replayFile.c_str());
            fclose(in);
            return 8;
        }
        if (!v2) {
            const DpFileRecord *r1 = (const DpFileRecord *)&fr;
            const u64 sd = r1->seed, c0 = r1->canon[0], c1 = r1->canon[1], c2 = r1->canon[2];
            fr.seed = sd; fr.iters = 0;
            fr.canon[0] = c0; fr.canon[1] = c1; fr.canon[2] = c2;
            for (int k = 0; k < ECC_JCOUNT; ++k) fr.counts[k] = 0;
        }
        seeds[i] = fr.seed;
        claimX[i * 3 + 0] = fr.canon[0];
        claimX[i * 3 + 1] = fr.canon[1];
        claimX[i * 3 + 2] = fr.canon[2];
        claimIters[i] = fr.iters;
        for (int k = 0; k < ECC_JCOUNT; ++k) claimJ[i * ECC_JCOUNT + k] = fr.counts[k];
    }
    fclose(in);
    printf("replaying %zu %s records from %s\n", total, v2 ? "v2" : "v1", o.replayFile.c_str());
    if (!total) return 0;

    // Where each seed sits, so a report can be matched back to its record.
    // A corpus may legitimately hold one seed twice (two runs, same namespace),
    // so this maps to a list and reports are matched first-unmatched-first.
    std::map<u64, std::vector<size_t> > where;
    for (size_t i = 0; i < total; ++i) where[seeds[i]].push_back(i);

    // ---- the output corpus ------------------------------------------------
    CorpusOutput out;
    if (!o.dpFile.empty()) {
        if (!out.openFile(o.dpFile, sizeof(DpFileRecordV2), sizeof(DpFileHeader))) {
            fprintf(stderr, "persistence failure: cannot lock/open aligned regular corpus %s\n",
                    o.dpFile.c_str());
            return 8;
        }
        if (out.bytes != 0) {
            fprintf(stderr, "refusing to write a replay into the existing corpus %s: "
                            "a replay rewrites records, it does not append to a search\n",
                    o.dpFile.c_str());
            return 2;
        }
        DpFileHeader h;
        memcpy(h.magic, DP_MAGIC_V2, sizeof h.magic);
        h.version = 2u;
        h.recordBytes = (unsigned)sizeof(DpFileRecordV2);
        if (fwrite(&h, sizeof h, 1, out.file) != 1) {
            fprintf(stderr, "persistence failure: cannot write the corpus header to %s\n",
                    o.dpFile.c_str());
            return 8;
        }
    }

    // ---- walk it, a wordful of trails at a time --------------------------
    const u64 chunk = eng.walksPerLaunch();
    const u64 cap = o.maxIters ? o.maxIters + ECC_GUARD_PERIOD : ((u64)1 << 34);
    std::vector<DpRecord> recs;
    std::vector<char> seen(total, 0);
    u64 replayed = 0, steps = 0, mismatched = 0;
    const double t0 = nowSeconds();

    for (u64 begin = 0; begin < total; begin += chunk) {
        const u64 n = (total - begin) < chunk ? (u64)(total - begin) : chunk;
        eng.setReplay(seeds.data() + begin, n);
        eng.reinit();
        u64 got = 0, iterBase = 0;
        while (got < n && iterBase <= cap) {
            eng.launch(iterBase);
            iterBase += (u64)o.steps;
            const unsigned k = eng.fetch(recs);
            if (k == ECC_SEED_EXHAUSTED) {
                fprintf(stderr, "SEED EXHAUSTED during replay; this should not happen\n");
                return 9;
            }
            if (k > recs.size()) {
                fprintf(stderr, "DP OVERFLOW: %u reports, capacity %u; raise --dp-cap to at "
                                "least the %llu walks a launch carries\n",
                        k, o.dpCap, (unsigned long long)chunk);
                return 7;
            }
            for (size_t r = 0; r < recs.size(); ++r) {
                const DpRecord &rec = recs[r];
                std::map<u64, std::vector<size_t> >::iterator it = where.find(rec.seed);
                if (it == where.end() || it->second.empty()) {
                    fprintf(stderr, "replay reported seed %016llx, which the corpus does not hold\n",
                            (unsigned long long)rec.seed);
                    return 3;
                }
                const size_t idx = it->second.front();
                it->second.erase(it->second.begin());
                seen[idx] = 1;
                ++got;
                ++replayed;
                steps += rec.iters;

                // The replay must reach the orbit the record names, or the
                // corpus and this binary disagree about the walk.
                const typename R::Elem cx = R::canonical(R::fromLimbs(rec.x));
                if (cx.v[0] != claimX[idx * 3] || cx.v[1] != claimX[idx * 3 + 1] ||
                    cx.v[2] != claimX[idx * 3 + 2]) {
                    fprintf(stderr, "MISMATCH: seed %016llx replays to a different orbit than "
                                    "its record names\n", (unsigned long long)rec.seed);
                    return 3;
                }
                // On a v2 corpus the witness is checked too, which is what
                // makes this an audit of the carried counters and not just a
                // way to produce them.
                if (v2) {
                    if (rec.iters != claimIters[idx]) {
                        fprintf(stderr, "MISMATCH: seed %016llx replays in %llu steps, the record "
                                        "claims %llu\n", (unsigned long long)rec.seed,
                                (unsigned long long)rec.iters,
                                (unsigned long long)claimIters[idx]);
                        return 3;
                    }
                    for (int kk = 0; kk < ECC_JCOUNT; ++kk) {
                        if (rec.counts[kk] != claimJ[idx * ECC_JCOUNT + kk]) {
                            fprintf(stderr, "MISMATCH: seed %016llx witness[%d] replays as %u, the "
                                            "record claims %u\n", (unsigned long long)rec.seed, kk,
                                    rec.counts[kk], claimJ[idx * ECC_JCOUNT + kk]);
                            return 3;
                        }
                    }
                }
                if (out.file) {
                    DpFileRecordV2 fr;
                    fr.seed = rec.seed;
                    fr.iters = rec.iters;
                    fr.canon[0] = cx.v[0];
                    fr.canon[1] = cx.v[1];
                    fr.canon[2] = cx.v[2];
                    for (int kk = 0; kk < ECC_JCOUNT; ++kk) fr.counts[kk] = rec.counts[kk];
                    if (fwrite(&fr, sizeof fr, 1, out.file) != 1) {
                        fprintf(stderr, "persistence failure: corpus write\n");
                        return 8;
                    }
                }
            }
        }
        if (got < n) {
            // Trails that did not reach a distinguished point inside the cap.
            // Never negative evidence about the walk: it is a budget, and the
            // records stay unwitnessed rather than being written wrong.
            mismatched += (n - got);
            fprintf(stderr, "%llu of %llu records in this chunk did not reach a distinguished "
                            "point within %llu steps; raise --max-iters\n",
                    (unsigned long long)(n - got), (unsigned long long)n,
                    (unsigned long long)cap);
        }
        const double el = nowSeconds() - t0;
        printf("  %llu/%zu replayed, %llu steps, %.1f s\n",
               (unsigned long long)replayed, total, (unsigned long long)steps, el);
        fflush(stdout);
    }

    const double el = nowSeconds() - t0;
    printf("replayed %llu of %zu records, %llu steps, %.2f s (%.0f steps/s)\n",
           (unsigned long long)replayed, total, (unsigned long long)steps, el,
           el > 0 ? (double)steps / el : 0.0);
    if (v2) printf("every replayed witness matched the corpus's own\n");
    if (!o.dpFile.empty() && !out.closeFile()) return 8;
    return mismatched ? 6 : 0;
}

template <class Cfg>
static int runCurve(const Options &oIn, const unsigned long long *px, const unsigned long long *py,
                    const unsigned long long *qx, const unsigned long long *qy, const char *ellDec,
                    const char *sDec, int defaultW, const char *knownKDec) {
    Options o = oIn;
    if (o.dpWeight < 0) o.dpWeight = defaultW;
    if (!o.replayFile.empty() && (o.packed || o.refEngine || ECC_WALK_TABLE)) {
        // The packed kernel seeds its own lanes and the table walk has no
        // (1 + s^j)^{n_j} factorisation to count, so neither can produce the
        // witness a replay exists to produce.
        fprintf(stderr, "--replay needs the bitsliced sigma^j + 1 walk: "
                        "not --packed, not --ref-engine, not WALK_TABLE=1\n");
        return 1;
    }
    Solver<Cfg> sol;
    sol.setup(px, py, qx, qy, ellDec, sDec, o.dpWeight,
              // Guards are checked on global ECC_GUARD_PERIOD boundaries.
              // A valid first DP may occur in the bounded overshoot window
              // after maxIters, so reference replay must include that window.
              o.maxIters ? o.maxIters + ECC_GUARD_PERIOD - 1 : (u64)1 << 40);
    std::string why;
    if (!sol.checkSetup(&why)) {
        printf("parameter check failed: %s\n", why.c_str());
        return 5;
    }
    U192 knownK;
    bool haveK = false;
    if (knownKDec) {
        knownK = u192_from_dec(knownKDec);
        haveK = true;
    }

    if (o.test) {
        printf("GF(2^%d), n = %d, l = %s\n", Cfg::M, Cfg::NRING, ellDec);
        report("published parameters are consistent", true);
        if (haveK) {
            // ECC2K-95: Harley's group published this in 1998
            report("published challenge solution satisfies [k]P == Q",
                   Ref<Cfg>::eq(Ref<Cfg>::scalarMul(sol.basis, knownK), sol.target));
        }
        Rng rng(0x1234567 + Cfg::M);
        testField<Cfg>(rng);
        testOrbit<Cfg>(rng, sol);
        testStartPoint<Cfg>(rng, sol);
        testWitness<Cfg>(rng, sol);
        testSolveAlgebra<Cfg>(rng, sol);
        return 0;
    }

#ifndef ECC_NO_CUDA
    if (o.packed) {
        if constexpr (Cfg::M == 131) {
            PackedCudaEngine eng;
            eng.sol = &sol;
            if (o.threads <= 0) o.threads = eng.autoThreads(o.device);
            eng.setup(o, px, py, qx, qy);
            printf("backend %s: %d threads x %d slots x 1 lanes = %llu walks, dp weight %d, %d steps per launch\n",
                   eng.name(), o.threads, (int)ECC_BATCH, eng.walksPerLaunch(), o.dpWeight, o.steps);
            return runSearch<Cfg>(o, eng, sol, haveK ? &knownK : NULL);
        } else {
            fprintf(stderr, "--packed is supported only for GF(2^131)\n");
            return 1;
        }
    }
#endif
    if (ECC_WALK_TABLE || o.refEngine) {
        // The bitsliced kernels walk with sigma^j + 1; only the packed
        // GF(2^131) kernel and the reference implement the table walk.
        if (ECC_WALK_TABLE && !TableWalk<Cfg>::applicable()) {
            fprintf(stderr, "the table walk is defined on type-II normal-basis fields only\n");
            return 1;
        }
        RefEngine<Cfg> eng;
        eng.sol = &sol;
        if (o.threads <= 0) o.threads = eng.autoThreads(o.device);
        eng.setup(o, px, py, qx, qy);
        printf("backend %s: %d threads x %d slots x 1 lanes = %llu walks, dp weight %d, %d steps per launch\n",
               eng.name(), o.threads, (int)ECC_BATCH, (unsigned long long)eng.walksPerLaunch(), o.dpWeight, o.steps);
        return runSearch<Cfg>(o, eng, sol, haveK ? &knownK : NULL);
    }
#ifndef ECC_NO_CUDA
    CudaEngine<Cfg> eng;
    if (o.preferL1)
        CUDA_CHECK(cudaFuncSetCacheConfig(eccWalkKernel<Cfg, DeviceWord>, cudaFuncCachePreferL1));
#else
    HostEngine<Cfg> eng;
#endif
    if (o.threads <= 0) o.threads = eng.autoThreads(o.device);
    if (!o.replayFile.empty() && (u64)o.dpCap < eng.walksPerLaunch()) {
        // Every lane of a chunk can reach its distinguished point in the same
        // launch, so the report buffer has to hold a whole chunk or the
        // overflow silently drops witnesses.
        o.dpCap = (unsigned)eng.walksPerLaunch();
    }
    eng.setup(o, px, py, qx, qy);
    printf("backend %s: %d threads x %d slots x %d lanes = %llu walks, dp weight %d, %d steps per launch\n",
           eng.name(), o.threads, (int)ECC_BATCH, (int)WordTraits<typename decltype(eng)::W>::LANES,
           (unsigned long long)eng.walksPerLaunch(), o.dpWeight, o.steps);
#ifdef ECC_NO_CUDA
    if (!o.replayFile.empty()) return runReplay<Cfg>(o, eng, sol);
#else
    if (!o.replayFile.empty()) {
        // The seeds would have to reach the device, which is a buffer this
        // machine cannot test.  Refusing beats a GPU replay nobody has run.
        fprintf(stderr, "--replay is implemented for the host backend only; "
                        "build with ECC_NO_CUDA=1 or run it on a CPU box\n");
        return 1;
    }
#endif
    return runSearch<Cfg>(o, eng, sol, haveK ? &knownK : NULL);
}

// ---------------------------------------------------------------------------
static void usage() {
    printf(
        "ecc2k130 - Pollard rho client for the Certicom ECC2K-130 challenge\n"
        "\n"
        "  --curve M        131 (ECC2K-130) or 97 (ECC2K-95); 83/41/23/19/13 are tests\n"
        "  --poly-basis     for curve 41, use the polynomial-basis backend\n"
        "  --instance I     use planted test instance I (small curves only)\n"
        "  --threads T      worker threads (device: total threads)\n"
        "  --steps S        iterations per launch (default 64)\n"
        "  --launches L     stop after L launches (0 = until solved)\n"
        "  --dp-weight W    distinguished point when the normal-basis weight is <= W\n"
        "  --max-iters N    restart after N steps without a report (default 2^32; 0 disables)\n"
        "  --run-id R       16-bit salt making seeds unique across processes\n"
        "  --verify N       recompute the first N reported points with the reference\n"
        "                   (default 0: collection must not stall the GPU on a CPU rewalk)\n"
        "  --ref            walk on the scalar reference arithmetic (toy curves; slow)\n"
        "  --dp-file F      append distinguished points to F (binary, 32 bytes each)\n"
        "  --replay C       re-walk corpus C instead of searching, writing the witness\n"
        "                   to --dp-file.  Costs the longest trail in a chunk, not the\n"
        "                   sum: a v1 corpus gains its counts, a v2 corpus is audited\n"
        "                   against the ones it carries.  Host backend only.\n"
        "  --load F         preload a corpus file so collisions with earlier runs count\n"
        "  --load-max N     stop reloading after N points (0 = no limit), newest file first\n"
        "  --checkpoint F   save and resume walk state through F\n"
        "  --checkpoint-every S   seconds between checkpoints (default 300)\n"
        "  --bench          throughput only, no distinguished-point handling\n"
        "  --packed         packed CUDA backend for GF(2^131); one walk per slot\n"
        "  --prefer-l1      request more L1 cache for the CUDA walk kernel\n"
        "  --test           run the validation suite and exit\n"
        "  --device D       CUDA device index\n"
        "\n"
        "Build-time: ECC_BATCH=%d slots per thread, ECC_THREADS block size.\n",
        (int)ECC_BATCH);
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);   // stream progress when piped to a file
    signal(SIGINT, onStop);
    signal(SIGTERM, onStop);
    Options o;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        const char *nx = (i + 1 < argc) ? argv[i + 1] : NULL;
        if (a == "--curve" && nx) o.curve = atoi(argv[++i]);
        else if (a == "--instance" && nx) o.instance = atoi(argv[++i]);
        else if (a == "--threads" && nx) o.threads = atoi(argv[++i]);
        else if (a == "--steps" && nx) o.steps = atoi(argv[++i]);
        else if (a == "--launches" && nx) o.launches = atol(argv[++i]);
        else if (a == "--dp-weight" && nx) o.dpWeight = atoi(argv[++i]);
        else if (a == "--max-iters" && nx) o.maxIters = strtoull(argv[++i], NULL, 10);
        else if (a == "--run-id" && nx) o.runId = (unsigned)atoi(argv[++i]);
        else if (a == "--verify" && nx) o.verify = atoi(argv[++i]);
        else if (a == "--ref") o.refEngine = true;
        else if (a == "--dp-cap" && nx) o.dpCap = (unsigned)atoi(argv[++i]);
        else if (a == "--dp-file" && nx) o.dpFile = argv[++i];
        else if (a == "--replay" && nx) o.replayFile = argv[++i];
        else if (a == "--load" && nx) o.loadFiles.push_back(argv[++i]);
        else if (a == "--load-max" && nx) o.loadMax = strtoull(argv[++i], 0, 10);
        else if (a == "--checkpoint" && nx) o.ckptFile = argv[++i];
        else if (a == "--checkpoint-every" && nx) o.ckptSeconds = atof(argv[++i]);
        else if (a == "--device" && nx) o.device = atoi(argv[++i]);
        else if (a == "--bench") o.bench = true;
        else if (a == "--packed") o.packed = true;
        else if (a == "--prefer-l1") o.preferL1 = true;
        else if (a == "--poly-basis") o.polyBasis = true;
        else if (a == "--test") o.test = true;
        else if (a == "--help" || a == "-h") { usage(); return 0; }
        else { printf("unknown option %s\n", a.c_str()); usage(); return 1; }
    }
    if (o.packed && (o.curve != 131 || o.polyBasis || o.test)) {
        fprintf(stderr, "--packed selects the GF(2^131) CUDA walk; use --verify for device report validation and make test-packed for host arithmetic\n");
        return 1;
    }
    if (o.runId > 65535 || o.steps <= 0 || o.launches < 0 || o.dpCap == 0 ||
        o.dpWeight < -1 || o.dpWeight > o.curve || !(o.ckptSeconds > 0) ||
        o.maxIters > ~u64(0) - (ECC_GUARD_PERIOD - 1)) {
        fprintf(stderr, "invalid run-id, launch, DP or checkpoint parameters\n");
        return 1;
    }
    // The device thread count is left at 0 here on purpose: it depends on the
    // register allocation of a kernel that is only instantiated once the curve
    // is known, so CudaEngine::autoThreads decides it in runCurve.
#ifdef ECC_NO_CUDA
    if (o.packed) { fprintf(stderr, "--packed requires the CUDA client\n"); return 1; }
    if (o.threads <= 0) {
#ifdef _OPENMP
        o.threads = omp_get_max_threads();
#else
        o.threads = 1;
#endif
    }
#endif
#ifndef ECC_NO_CUDA
    CUDA_CHECK(cudaSetDevice(o.device));
#endif
    if (o.bench) o.dpWeight = 0;

    if (o.test) {
        printf("ECC2K-130 validation suite\n");
        int rc = 0;
        rc |= runCurve<CfgF23>(o, eccF23::PX, eccF23::PY, eccF23::QX, eccF23::QY,
                               eccF23::ELL_DEC, eccF23::S_DEC, eccF23::DP_WEIGHT, NULL);
        rc |= runCurve<CfgF41>(o, eccF41::PX, eccF41::PY, eccF41::QX, eccF41::QY,
                               eccF41::ELL_DEC, eccF41::S_DEC, eccF41::DP_WEIGHT, NULL);
        rc |= runCurve<CfgF83>(o, eccF83::PX, eccF83::PY, eccF83::QX, eccF83::QY,
                               eccF83::ELL_DEC, eccF83::S_DEC, eccF83::DP_WEIGHT, NULL);
        rc |= runCurve<CfgF131>(o, eccF131::PX, eccF131::PY, eccF131::QX, eccF131::QY,
                                eccF131::ELL_DEC, eccF131::S_DEC, eccF131::DP_WEIGHT, NULL);
        rc |= runCurve<CfgP13>(o, eccP13::PX, eccP13::PY, eccP13::QX, eccP13::QY,
                               eccP13::ELL_DEC, eccP13::S_DEC, eccP13::DP_WEIGHT, NULL);
        rc |= runCurve<CfgP19>(o, eccP19::PX, eccP19::PY, eccP19::QX, eccP19::QY,
                               eccP19::ELL_DEC, eccP19::S_DEC, eccP19::DP_WEIGHT, NULL);
        rc |= runCurve<CfgP41>(o, eccP41::PX, eccP41::PY, eccP41::QX, eccP41::QY,
                               eccP41::ELL_DEC, eccP41::S_DEC, eccP41::DP_WEIGHT, NULL);
        rc |= runCurve<CfgP97>(o, eccP97::PX, eccP97::PY, eccP97::QX, eccP97::QY,
                               eccP97::ELL_DEC, eccP97::S_DEC, eccP97::DP_WEIGHT, eccP97::KNOWN_K);
        printf("%s (%d failures)\n", gFail ? "VALIDATION FAILED" : "all checks passed", gFail);
        return (rc || gFail) ? 1 : 0;
    }

#define ECC_DISPATCH(NS, CFG)                                                                 \
    do {                                                                                      \
        const unsigned long long *px = NS::PX, *py = NS::PY, *qx = NS::QX, *qy = NS::QY;       \
        const char *kk = NULL;                                                                 \
        if (o.instance >= 0 && o.instance < NS::NUM_INSTANCES) {                              \
            px = NS::INSTANCE_PX[o.instance];                                                  \
            py = NS::INSTANCE_PY[o.instance];                                                  \
            qx = NS::INSTANCE_QX[o.instance];                                                  \
            qy = NS::INSTANCE_QY[o.instance];                                                  \
            kk = NS::INSTANCE_K[o.instance];                                                   \
        } else if (o.instance >= 0) {                                                          \
            printf("curve %d has %d planted instances\n", o.curve, NS::NUM_INSTANCES);        \
            return 1;                                                                          \
        }                                                                                      \
        return runCurve<CFG>(o, px, py, qx, qy, NS::ELL_DEC, NS::S_DEC, NS::DP_WEIGHT, kk);   \
    } while (0)

    if (o.curve == 131) ECC_DISPATCH(eccF131, CfgF131);
    if (o.curve == 83) ECC_DISPATCH(eccF83, CfgF83);
    if (o.curve == 41 && !o.polyBasis) ECC_DISPATCH(eccF41, CfgF41);
    if (o.curve == 23) ECC_DISPATCH(eccF23, CfgF23);
    if (o.curve == 97) ECC_DISPATCH(eccP97, CfgP97);
    if (o.curve == 41 && o.polyBasis) ECC_DISPATCH(eccP41, CfgP41);
    if (o.curve == 19) ECC_DISPATCH(eccP19, CfgP19);
    if (o.curve == 13) ECC_DISPATCH(eccP13, CfgP13);
    printf("unsupported curve %d\n", o.curve);
    return 1;
}
