/*
 * ec2k_gpu.cu - the ECC2K-130 Pollard rho GPU client.
 *
 *   ec2k-gpu walk    --run-id R --dp-file F --checkpoint C [options]
 *   ec2k-gpu bench   [--steps S] [--launches L] [--threads T]
 *   ec2k-gpu check   [--rounds N] [--kat F]   (host only: arithmetic vs the model,
 *                                             and the campaign's known answers)
 *   ec2k-gpu health  [--seconds S] [--device D] (a timed, self-checking load: see
 *                                             cmdHealth and deploy/gpu-health/)
 *   ec2k-gpu devices                          (the CUDA devices, as JSON)
 *
 * One process drives one device.  The kernel (include/packedkernels.cuh) is
 * the packed GF(2^131) walk imported from github.com/aburan28/crypto: one walk
 * per thread-slot, five 32-bit words per coordinate, products on the
 * carry-less multiplier (clmad, sm_80+, CUDA 13.3+), one batched inversion
 * per 16 slots.  ecc2k130/README.md gives the measured rates; this file is
 * only the host that feeds it.
 *
 * The default build walks what the live ecc2k-130 campaign walks: the sigma
 * walk R' = R + sigma^j(R) from Certicom's challenge points, distinguished at
 * weight 32, lanes restarted after 2^32 steps, seeds (runId << 48) |
 * (lane << 16).  A point it writes to --dp-file is a record of that campaign,
 * byte for byte (tests/campaign-kat.hex is the proof: records the campaign's
 * own client wrote, which hosttest reproduces from their seeds).  WALK=table
 * builds the table walk instead, whose points collide only with each other.
 *
 * What the host does per launch: run the walk kernel for `steps` steps over
 * every lane, read back the distinguished points it found (a lane that finds
 * one marks itself dead), append them to the corpus, re-walk the first
 * `--verify` of them on the golden model in fpga/model, and revive the dead
 * lanes from their incremented seeds.  Every --checkpoint-every seconds, and
 * on SIGINT or SIGTERM, it syncs the corpus and then saves every lane, so a
 * stopped run resumes where it was and loses no work it has reported.
 */
#include "healthcheck.h"

#include <cuda_runtime.h>

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <string>
#include <vector>

#include "../include/packedkernels.cuh"

#define CUDA_CHECK(call)                                                                           \
    do {                                                                                           \
        cudaError_t err_ = (call);                                                                 \
        if (err_ != cudaSuccess) {                                                                 \
            fprintf(stderr, "CUDA error at %s:%d: %s\n", __FILE__, __LINE__,                       \
                    cudaGetErrorString(err_));                                                     \
            exit(3);                                                                               \
        }                                                                                          \
    } while (0)

using namespace ec2k_gpu;
using eccPacked131::P131;

namespace
{

#if ECC_WALK_TABLE
const char *const WALK_NAME = "table";
const int DEFAULT_DP_WEIGHT = EC2K_DP_WEIGHT;
const unsigned long long DEFAULT_MAX_ITERS = 0;
const size_t SHARED_BYTES = eccPacked131::TW_SHARED_BYTES;
#else
const char *const WALK_NAME = "sigma";
const int DEFAULT_DP_WEIGHT = CAMPAIGN_DP_WEIGHT;
const unsigned long long DEFAULT_MAX_ITERS = CAMPAIGN_MAX_ITERS;
const size_t SHARED_BYTES = 0; // the Frobenius masks are a static __shared__ array
#endif

struct Options {
    std::string cmd;
    int threads = 0; // 0: automatic, one wave of resident blocks
    int steps = 1024;
    int launches = -1; // -1: 32 for bench, until interrupted for walk
    int dpWeight = DEFAULT_DP_WEIGHT;
    unsigned dpCap = 1u << 18;
    unsigned runId = 1;
    bool runIdGiven = false;
    unsigned long long maxIters = DEFAULT_MAX_ITERS;
    int verify = 0;
    int rounds = 64;
    int device = 0;
    bool testPoints = false; // --p-seed / --q-seed given: not the challenge
    uint64_t pSeed = 1, qSeed = 2;
    std::string dpFile, dpFile64, checkpoint, kat;
    int checkpointEvery = 600;
    // health: which of the walk's defaults the command line set, the timed
    // load's length, and its re-walk sample on the golden model.
    bool stepsGiven = false, dpWeightGiven = false;
    double seconds = 60, warmup = -1, window = 10;
    int rewalkThreads = 1;
    unsigned long long rewalkMaxIters = 512;
};

// A health run's defaults.  Weight 42 ends a trail every 2^14.86 steps, so a
// lane reports every few seconds on any part and nearly every step a run takes
// is in a trail whose report the host checks; 4096-step launches keep the
// reseed kernel, whose lanes diverge, to a few percent of the device's time.
// Run id 65535 is one the campaign never hands out (its fleets use 1.. and
// 8000-9999, contributors 10000-65534), and a health run keeps no points.
const int HEALTH_DP_WEIGHT = 42;
const int HEALTH_STEPS = 4096;
const unsigned HEALTH_RUN_ID = 0xFFFFu;

int usage()
{
    fprintf(stderr,
            "usage: ec2k-gpu <walk|bench|check|health|devices> [options]\n"
            "  --run-id R       16-bit run id; distinct runs never share a trail\n"
            "  --dp-file F      append 32-byte campaign records (seed, orbit-minimum x) to F\n"
            "  --dp-file64 F    append 64-byte (seed, iters, x, y) records to F\n"
            "  --checkpoint C   save every lane to C, and resume from it if it exists\n"
            "  --checkpoint-every S   seconds between checkpoints (default 600)\n"
            "  --steps S        iterations per launch (default 1024)\n"
            "  --launches L     launches (default: bench 32, walk until interrupted)\n"
            "  --threads T      worker threads (default: automatic, multiple of 256)\n"
            "  --dp-weight W    distinguished when HW(x) <= W (default %d; bench: none)\n"
            "  --dp-cap N       report buffer capacity per launch (default 262144)\n"
            "  --max-iters N    restart a lane that walked N steps without a report\n"
            "                   (default %llu; 0 never)\n"
            "  --verify N       re-walk the first N reports on the golden model (slow at\n"
            "                   the campaign's weight: a report is ~2^28 steps on the CPU)\n"
            "  --p-seed S, --q-seed S   test points from seeds instead of the challenge's\n"
            "  --rounds N       check: random inputs per routine (default 64)\n"
            "  --kat F          check: also replay the campaign's known answers in F\n"
            "                   (campaign-kat.hex, shipped beside the binary)\n"
            "  --device D       CUDA device index\n"
            "health: a timed load whose every report is checked on the host (seed, restart\n"
            "  counter, step window, weight, on the curve) and a sample re-walked on the\n"
            "  golden model; defaults --dp-weight %d --steps %d, run id %u, nothing kept\n"
            "  --seconds S      length of the load (default 60)\n"
            "  --warmup S       seconds left out of the steady rate (default: S/6, at most 10)\n"
            "  --window S       length of the rate windows (default 10)\n"
            "  --rewalk-threads N    host threads re-walking reports (default 1; 0 none)\n"
            "  --rewalk-max-iters N  longest report worth a re-walk (default 512)\n"
            "walk: %s\n",
            DEFAULT_DP_WEIGHT, DEFAULT_MAX_ITERS, HEALTH_DP_WEIGHT, HEALTH_STEPS, HEALTH_RUN_ID,
            WALK_NAME);
    return 2;
}

const char *opt(int argc, char **argv, const char *name)
{
    for (int i = 2; i + 1 < argc; i++)
        if (!strcmp(argv[i], name)) return argv[i + 1];
    return nullptr;
}

unsigned long long optU64(int argc, char **argv, const char *name, unsigned long long def)
{
    const char *v = opt(argc, argv, name);
    return v ? strtoull(v, nullptr, 0) : def;
}

double now()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

volatile sig_atomic_t stopRequested = 0;

// The first SIGINT or SIGTERM finishes the launch in flight, checkpoints and
// exits; a second one is the default action, for a run that must die now.
void onStop(int sig)
{
    stopRequested = 1;
    signal(sig, SIG_DFL);
}

bool syncFile(FILE *f) { return fflush(f) == 0 && fsync(fileno(f)) == 0; }

bool syncDirOf(const std::string &path)
{
    const size_t slash = path.rfind('/');
    const std::string dir = slash == std::string::npos ? "." : path.substr(0, slash + 1);
    const int fd = open(dir.c_str(), O_RDONLY);
    if (fd < 0) return false;
    const bool ok = fsync(fd) == 0;
    close(fd);
    return ok;
}

/* ---- the device engine ------------------------------------------------- */

struct Engine {
    WalkParams<unsigned> P{};
    unsigned *fieldBlob = nullptr;
    unsigned *denominators = nullptr; // sigma: the forward pass's d, per slot
    unsigned *twConsts = nullptr;
    size_t fieldWords = 0;

    size_t laneCount() const { return size_t(P.threads) * ECC_BATCH; }

    static int autoThreads(int device)
    {
        cudaDeviceProp prop;
        CUDA_CHECK(cudaGetDeviceProperties(&prop, device));
        int blocks = 0;
        CUDA_CHECK(cudaOccupancyMaxActiveBlocksPerMultiprocessor(&blocks, eccPacked131::walk,
                                                                 ECC_THREADS, SHARED_BYTES));
        if (blocks < 1) blocks = 1;
        size_t threads = size_t(prop.multiProcessorCount) * ECC_THREADS * blocks;
        threads -= threads % 256;
        printf("device: %s, %d SMs, %d block(s) of %d threads resident per SM, %zu threads\n",
               prop.name, prop.multiProcessorCount, blocks, ECC_THREADS, threads);
        return int(threads);
    }

    // Allocate the lanes and copy the walk's constants; the lanes themselves
    // are seeded by init() or restored by Checkpoint::load.
    void setup(const Options &o, const HostWalk &walk)
    {
        if (SHARED_BYTES > 48 * 1024)
            CUDA_CHECK(cudaFuncSetAttribute(eccPacked131::walk,
                                            cudaFuncAttributeMaxDynamicSharedMemorySize,
                                            int(SHARED_BYTES)));
        P.threads = o.threads;
        P.steps = o.steps;
        P.dpWeight = o.dpWeight;
        P.runId = o.runId;
        P.maxIters = o.maxIters;
        P.iterBase = 0;
        P.dpCap = o.dpCap;

        // x, y, the prefix chain and (sigma) the denominators in one
        // contiguous allocation so that one L2 access-policy window covers
        // them.  Compact storage: 16-byte low records plus a tail-byte plane
        // per 256-thread tile.
        const int arrays = ECC_WALK_TABLE ? 3 : 4;
        fieldWords = eccPacked131::compactPhysicalFieldWords(size_t(P.threads));
        const size_t bytes = fieldWords * sizeof(unsigned);
        CUDA_CHECK(cudaMalloc(&fieldBlob, bytes * arrays));
        CUDA_CHECK(cudaMemset(fieldBlob, 0, bytes * arrays));
        P.x = fieldBlob;
        P.y = fieldBlob + fieldWords;
        P.pchain = fieldBlob + 2 * fieldWords;
        if (!ECC_WALK_TABLE) denominators = fieldBlob + 3 * fieldWords;
        persist(fieldBlob, bytes * arrays);

        CUDA_CHECK(cudaMalloc(&P.dead, laneCount() * sizeof(unsigned)));
        CUDA_CHECK(cudaMalloc(&P.seed, laneCount() * sizeof(unsigned long long)));
        CUDA_CHECK(cudaMalloc(&P.startIter, laneCount() * sizeof(unsigned long long)));
        CUDA_CHECK(cudaMalloc(&P.hist, laneCount() * sizeof(unsigned long long)));
        CUDA_CHECK(cudaMemset(P.hist, 0, laneCount() * sizeof(unsigned long long)));
        CUDA_CHECK(cudaMalloc(&P.dp, size_t(P.dpCap) * sizeof(DpRecord)));
        CUDA_CHECK(cudaMalloc(&P.dpCount, 4 * sizeof(unsigned)));
        CUDA_CHECK(cudaMemset(P.dpCount, 0, 4 * sizeof(unsigned)));

#if ECC_WALK_TABLE
        const std::vector<uint32_t> consts = walk.deviceConsts();
        CUDA_CHECK(cudaMalloc(&twConsts, consts.size() * sizeof(uint32_t)));
        CUDA_CHECK(cudaMemcpy(twConsts, consts.data(), consts.size() * sizeof(uint32_t),
                              cudaMemcpyHostToDevice));
#endif
        P.twConsts = twConsts;

        // The start-point terms sigma^i(P) and the target Q, in the normal
        // basis: the init kernel adds them with the ONB arithmetic.
        P131 ox[128], oy[128];
        for (int i = 0; i < 128; ++i) {
            ox[i] = toPacked(walk.orbitP[i].x);
            oy[i] = toPacked(walk.orbitP[i].y);
        }
        const P131 qx = toPacked(walk.Q.x), qy = toPacked(walk.Q.y);
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::orbitX, ox, sizeof(ox)));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::orbitY, oy, sizeof(oy)));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::targetX, &qx, sizeof(qx)));
        CUDA_CHECK(cudaMemcpyToSymbol(eccPacked131::targetY, &qy, sizeof(qy)));

        cudaFuncAttributes attrs;
        CUDA_CHECK(cudaFuncGetAttributes(&attrs, eccPacked131::walk));
        printf("kernel: %s walk, %d registers/thread, %zu local bytes/thread, %zu dynamic "
               "shared bytes/block, batch %d, block %d x %d\n",
               WALK_NAME, attrs.numRegs, attrs.localSizeBytes, SHARED_BYTES, ECC_BATCH, ECC_THREADS,
               ECC_MINBLOCKS);
    }

    // Keep the walk state resident in L2 where the device has a persisting
    // window (80 MiB on the RTX PRO 6000).
    static void persist(void *base, size_t bytes)
    {
        int device = 0, maxPersist = 0;
        CUDA_CHECK(cudaGetDevice(&device));
        CUDA_CHECK(
            cudaDeviceGetAttribute(&maxPersist, cudaDevAttrMaxPersistingL2CacheSize, device));
        if (maxPersist <= 0) {
            printf("L2 persist window: not supported on this device\n");
            return;
        }
        CUDA_CHECK(cudaDeviceSetLimit(cudaLimitPersistingL2CacheSize, size_t(maxPersist)));
        CUDA_CHECK(cudaCtxResetPersistingL2Cache());
        cudaAccessPolicyWindow window = {};
        window.base_ptr = base;
        window.num_bytes = std::min(bytes, size_t(maxPersist));
        window.hitRatio = 1.0f;
        window.hitProp = cudaAccessPropertyPersisting;
        window.missProp = cudaAccessPropertyStreaming;
        cudaStreamAttrValue attr = {};
        attr.accessPolicyWindow = window;
        CUDA_CHECK(cudaStreamSetAttribute(0, cudaStreamAttributeAccessPolicyWindow, &attr));
        printf("L2 persist window: %zu of %zu state bytes, cap %d\n", window.num_bytes, bytes,
               maxPersist);
    }

    // (Re)seed the lanes for walks that begin at global step `iterBase`, the
    // base of the next launch: a report's iteration count is measured from it.
    void init(bool reseed, unsigned long long iterBase)
    {
        P.iterBase = iterBase;
        const int blocks = int((laneCount() + ECC_THREADS - 1) / ECC_THREADS);
        eccPacked131::init<<<blocks, ECC_THREADS>>>(P, reseed);
        CUDA_CHECK(cudaGetLastError());
    }

    void launch(unsigned long long iterBase)
    {
        P.iterBase = iterBase;
        eccPacked131::
            walk<<<(P.threads + ECC_THREADS - 1) / ECC_THREADS, ECC_THREADS, SHARED_BYTES>>>(
                P, denominators);
        CUDA_CHECK(cudaGetLastError());
    }

    // Reports of the last launch; counts[1] overdue restarts, counts[2] a lane
    // whose 16-bit restart counter is exhausted.
    void fetch(std::vector<DpRecord> &out, unsigned counts[3])
    {
        CUDA_CHECK(cudaDeviceSynchronize());
        CUDA_CHECK(cudaMemcpy(counts, P.dpCount, 3 * sizeof(unsigned), cudaMemcpyDeviceToHost));
        const unsigned n = std::min(counts[0], P.dpCap);
        out.resize(n);
        if (n)
            CUDA_CHECK(
                cudaMemcpy(out.data(), P.dp, size_t(n) * sizeof(DpRecord), cudaMemcpyDeviceToHost));
        if (counts[0] || counts[1] || counts[2])
            CUDA_CHECK(cudaMemset(P.dpCount, 0, 3 * sizeof(unsigned)));
    }

    ~Engine()
    {
        cudaFree(fieldBlob);
        cudaFree(twConsts);
        cudaFree(P.dead);
        cudaFree(P.seed);
        cudaFree(P.startIter);
        cudaFree(P.hist);
        cudaFree(P.dp);
        cudaFree(P.dpCount);
    }
};

/* ---- checkpoints --------------------------------------------------------- */

// The first 40 bytes are the campaign client's CkptHeader (crypto
// ecc2k130/src/main.cu), which is all the campaign's ingest reads of a
// checkpoint: iterBase * threads * batch * lanes is the work a slot has done.
// The version says what follows, which is this client's own layout and loads
// only into this client: the walk's binding (cutoff, restart limit, points),
// then every lane's x and y in the compact device layout, its dead flag, seed
// and start step, and for the table walk its history.
struct CkptHeader {
    char magic[8];
    uint32_t version, m, threads, batch, lanes, runId;
    uint64_t iterBase;
};
static_assert(sizeof(CkptHeader) == 40, "the campaign's checkpoint header is 40 bytes");

struct CkptBinding {
    int32_t dpWeight;
    uint32_t fieldWords;
    uint64_t maxIters;
    uint64_t px[3], qx[3];
};

const uint32_t CKPT_VERSION = 0x100u + ECC_WALK_TABLE; // 0x100 sigma, 0x101 table

struct Checkpoint {
    // Device arrays in file order: pointer and bytes.
    static std::vector<std::pair<void *, size_t>> arrays(Engine &e)
    {
        const size_t lanes = e.laneCount(), field = e.fieldWords * sizeof(unsigned);
        std::vector<std::pair<void *, size_t>> a = {
            {e.P.x, field},
            {e.P.y, field},
            {e.P.dead, lanes * sizeof(unsigned)},
            {e.P.seed, lanes * sizeof(unsigned long long)},
            {e.P.startIter, lanes * sizeof(unsigned long long)},
        };
        if (ECC_WALK_TABLE) a.push_back({e.P.hist, lanes * sizeof(unsigned long long)});
        return a;
    }

    static CkptBinding binding(const Engine &e, const HostWalk &walk)
    {
        CkptBinding b = {};
        b.dpWeight = e.P.dpWeight;
        b.fieldWords = uint32_t(e.fieldWords);
        b.maxIters = e.P.maxIters;
        for (int i = 0; i < 3; ++i) {
            b.px[i] = walk.P.x.w[i];
            b.qx[i] = walk.Q.x.w[i];
        }
        return b;
    }

    // Write to a temporary file, sync it, rename it over the old one and sync
    // the directory: a crash leaves either the old checkpoint or the new one.
    static bool save(const std::string &path, Engine &e, const HostWalk &walk,
                     unsigned long long iterBase)
    {
        CUDA_CHECK(cudaDeviceSynchronize());
        const std::string tmp = path + ".tmp";
        FILE *f = fopen(tmp.c_str(), "wb");
        if (!f) return false;
        CkptHeader h = {};
        memcpy(h.magic, "ECC2K130", 8);
        h.version = CKPT_VERSION;
        h.m = 131;
        h.threads = uint32_t(e.P.threads);
        h.batch = ECC_BATCH;
        h.lanes = 1;
        h.runId = e.P.runId;
        h.iterBase = iterBase;
        const CkptBinding b = binding(e, walk);
        bool ok = fwrite(&h, sizeof(h), 1, f) == 1 && fwrite(&b, sizeof(b), 1, f) == 1;
        std::vector<unsigned char> buf;
        for (const auto &a : arrays(e)) {
            buf.resize(a.second);
            CUDA_CHECK(cudaMemcpy(buf.data(), a.first, a.second, cudaMemcpyDeviceToHost));
            ok = ok && fwrite(buf.data(), 1, buf.size(), f) == buf.size();
        }
        ok = ok && syncFile(f);
        ok = (fclose(f) == 0) && ok;
        ok = ok && rename(tmp.c_str(), path.c_str()) == 0 && syncDirOf(path);
        if (!ok) unlink(tmp.c_str());
        return ok;
    }

    // Restore every lane.  Returns 1 on success, 0 when there is no
    // checkpoint, -1 when there is one that does not belong to this run.
    static int load(const std::string &path, Engine &e, const HostWalk &walk,
                    unsigned long long *iterBase)
    {
        FILE *f = fopen(path.c_str(), "rb");
        if (!f) return errno == ENOENT ? 0 : -1;
        CkptHeader h;
        CkptBinding b;
        const CkptBinding want = binding(e, walk);
        const char *why = nullptr;
        if (fread(&h, sizeof(h), 1, f) != 1 || fread(&b, sizeof(b), 1, f) != 1 ||
            memcmp(h.magic, "ECC2K130", 8) != 0)
            why = "not a checkpoint of this client";
        else if (h.version != CKPT_VERSION)
            why = "written by another client or for another walk";
        else if (h.m != 131 || h.batch != ECC_BATCH || h.lanes != 1 ||
                 h.threads != uint32_t(e.P.threads) || b.fieldWords != want.fieldWords)
            why = "another geometry (threads or batch)";
        else if (h.runId != e.P.runId)
            why = "another run id";
        else if (b.dpWeight != want.dpWeight)
            why = "another cutoff";
        else if (memcmp(b.px, want.px, sizeof(b.px)) || memcmp(b.qx, want.qx, sizeof(b.qx)))
            why = "other base and target points";
        std::vector<unsigned char> buf;
        for (const auto &a : arrays(e)) {
            if (why) break;
            buf.resize(a.second);
            if (fread(buf.data(), 1, buf.size(), f) != buf.size()) {
                why = "truncated";
                break;
            }
            CUDA_CHECK(cudaMemcpy(a.first, buf.data(), a.second, cudaMemcpyHostToDevice));
        }
        if (!why && fgetc(f) != EOF) why = "longer than this geometry's state";
        fclose(f);
        if (why) {
            fprintf(stderr, "checkpoint %s: %s; refusing to overwrite it\n", path.c_str(), why);
            return -1;
        }
        // The restart limit only decides when an unreported trail is
        // abandoned; the points a lane reaches do not depend on it, so a
        // checkpoint written under another limit continues under this one.
        if (b.maxIters != want.maxIters)
            printf("note: checkpoint %s was written with --max-iters %llu; its lanes continue "
                   "under %llu\n",
                   path.c_str(), (unsigned long long)b.maxIters, (unsigned long long)want.maxIters);
        *iterBase = h.iterBase;
        return 1;
    }
};

/* ---- the corpus ---------------------------------------------------------- */

// Append-only record files, locked so that two processes cannot interleave
// into one, and synced before every checkpoint so that a checkpoint never
// claims work whose points are not on disk.  After a crash the lanes resume
// from the last checkpoint and report again whatever they reported since;
// those records are byte-identical to the first copies, which the campaign's
// store keeps once (the same seed and point is a re-report, not a collision).
struct Corpus {
    FILE *f32 = nullptr, *f64 = nullptr;
    unsigned long long written = 0;
    bool failed = false;

    static FILE *openLocked(const std::string &path, size_t recordBytes)
    {
        struct stat st;
        if (lstat(path.c_str(), &st) == 0 && !S_ISREG(st.st_mode)) return nullptr;
        FILE *f = fopen(path.c_str(), "ab");
        if (!f) return nullptr;
        if (flock(fileno(f), LOCK_EX | LOCK_NB) != 0 || fstat(fileno(f), &st) != 0 ||
            st.st_size % (off_t)recordBytes != 0) {
            fclose(f);
            return nullptr;
        }
        return f;
    }

    bool open(const Options &o)
    {
        if (!o.dpFile.empty() && !(f32 = openLocked(o.dpFile, EC2K_RECORD_BYTES))) {
            fprintf(stderr,
                    "cannot append to %s: not a regular file of 32-byte records, or another "
                    "process holds it\n",
                    o.dpFile.c_str());
            return false;
        }
        if (!o.dpFile64.empty() && !(f64 = openLocked(o.dpFile64, sizeof(DpRecord)))) {
            fprintf(stderr,
                    "cannot append to %s: not a regular file of 64-byte records, or another "
                    "process holds it\n",
                    o.dpFile64.c_str());
            return false;
        }
        return true;
    }
    void append(const std::vector<DpRecord> &recs)
    {
        for (const DpRecord &r : recs) {
            if (f32) {
                uint8_t buf[EC2K_RECORD_BYTES];
                campaignRecord(buf, r);
                if (fwrite(buf, 1, sizeof(buf), f32) != sizeof(buf)) failed = true;
            }
            if (f64 && fwrite(&r, sizeof(r), 1, f64) != 1) failed = true;
            written++;
        }
    }
    bool sync()
    {
        if (f32 && !syncFile(f32)) failed = true;
        if (f64 && !syncFile(f64)) failed = true;
        return !failed;
    }
    ~Corpus()
    {
        if (f32) fclose(f32);
        if (f64) fclose(f64);
    }
};

/* ---- commands ------------------------------------------------------------ */

// The host checks, from the binary itself: the arithmetic against the model
// and, with --kat, the campaign's known answers.  A downloaded binary that
// passes both walks the campaign's walk on this machine's CPU; the device
// kernel is checked by `walk --verify`.
int cmdCheck(const Options &o, const HostWalk &walk)
{
    CheckResult cr = crossCheck(walk, o.rounds, 0x243F6A8885A308D3ULL);
#if !ECC_WALK_TABLE
    if (!o.kat.empty())
        campaignCheck(cr, o.kat.c_str());
    else
        printf("note: no --kat file; the campaign's known answers were not checked\n");
#endif
    if (cr.failures) {
        fprintf(stderr, "FAILED: %d of %d checks\n", cr.failures, cr.checks);
        return 1;
    }
    printf("{\"status\":\"ok\",\"walk\":\"%s\",\"checks\":%d,\"rounds\":%d,\"knownAnswers\":%s}\n",
           WALK_NAME, cr.checks, o.rounds, !ECC_WALK_TABLE && !o.kat.empty() ? "true" : "false");
    return 0;
}

// Say plainly when a run's points cannot be points of the campaign.
void campaignNotice(const Options &o)
{
    if (ECC_WALK_TABLE)
        printf("note: the table walk is not the campaign's walk; these points collide only "
               "with other table-walk points\n");
    else if (o.testPoints)
        printf("note: test points from --p-seed/--q-seed, not the challenge's; these points "
               "are not points of the campaign\n");
    else if (o.dpWeight != CAMPAIGN_DP_WEIGHT)
        printf("note: dp weight %d is not the campaign's %d; walks end at other points, which "
               "mostly cannot collide with the campaign's\n",
               o.dpWeight, CAMPAIGN_DP_WEIGHT);
    else if (o.maxIters != CAMPAIGN_MAX_ITERS)
        printf("note: --max-iters %llu is not the campaign's %llu; the points are the "
               "campaign's, the restart rate is not\n",
               o.maxIters, CAMPAIGN_MAX_ITERS);
    else
        printf("campaign: ecc2k-130 sigma walk from the challenge points, dp weight %d\n",
               o.dpWeight);
}

int cmdRun(Options o, const HostWalk &walk, bool bench)
{
    CUDA_CHECK(cudaSetDevice(o.device));
    if (o.threads <= 0) o.threads = Engine::autoThreads(o.device);
    if (o.threads % 256) {
        fprintf(stderr, "--threads must be a multiple of 256 (compact state tiles)\n");
        return 2;
    }
    if (bench) {
        o.dpWeight = -1; // no lane ever reports
        o.checkpoint.clear();
    } else {
        if (!o.runIdGiven) {
            fprintf(stderr, "walk needs --run-id: two runs under one id walk the same trails\n");
            return 2;
        }
        if (o.dpFile.empty() && o.dpFile64.empty())
            fprintf(stderr, "warning: no --dp-file; the points this run finds are not kept\n");
        if (o.verify > 0 && o.dpWeight <= 36)
            fprintf(stderr,
                    "warning: --verify %d re-walks on the CPU, about 2^%d steps per "
                    "report at weight %d; collection normally runs --verify 0\n",
                    o.verify, o.dpWeight <= 32 ? 28 : 26, o.dpWeight);
        campaignNotice(o);
    }
    const int launches = o.launches >= 0 ? o.launches : (bench ? 32 : 0);
    Engine e;
    e.setup(o, walk);
    unsigned long long iterBase = 0;
    const int restored =
        o.checkpoint.empty() ? 0 : Checkpoint::load(o.checkpoint, e, walk, &iterBase);
    if (restored < 0) return 6;
    if (restored)
        printf("resumed from %s at step %llu of every lane\n", o.checkpoint.c_str(), iterBase);
    else
        e.init(false, 0);
    Corpus corpus;
    if (!bench && !corpus.open(o)) return 1;
    printf("walks: %d threads x %d slots = %zu, %d steps per launch, dp weight %d, run id %u\n",
           o.threads, ECC_BATCH, e.laneCount(), o.steps, o.dpWeight, o.runId);

    if (!bench) {
        signal(SIGINT, onStop);
        signal(SIGTERM, onStop);
    }
    CUDA_CHECK(cudaDeviceSynchronize());
    const double t0 = now();
    double lastCkpt = t0;
    const unsigned long long iterations0 = iterBase * e.laneCount();
    unsigned long long iterations = iterations0, reports = 0, verified = 0, badReports = 0,
                       restarts = 0, dropped = 0;
    int rc = 0;
    std::vector<DpRecord> recs;
    for (int l = 0; launches == 0 || l < launches; ++l) {
        e.launch(iterations / e.laneCount());
        unsigned counts[3] = {0, 0, 0};
        e.fetch(recs, counts);
        iterations += e.laneCount() * (unsigned long long)o.steps;
        reports += recs.size();
        restarts += counts[1];
        if (counts[0] > o.dpCap) dropped += counts[0] - o.dpCap;
        for (size_t i = 0; i < recs.size() && verified < (unsigned long long)o.verify; ++i) {
            verified++;
            if (!rewalk(walk, recs[i], nullptr)) {
                badReports++;
                fprintf(stderr, "report does not re-walk: seed %llu, %llu iterations\n",
                        recs[i].seed, recs[i].iters);
            }
        }
        if (!bench) corpus.append(recs);
        if (counts[2]) {
            fprintf(stderr, "a lane exhausted its 16-bit restart counter; use a new run id\n");
            rc = 1;
            break;
        }
        if (!recs.empty() || counts[1]) e.init(true, iterations / e.laneCount());
        const double t = now();
        const bool last = stopRequested || (launches && l + 1 == launches);
        if (!o.checkpoint.empty() && (last || t - lastCkpt >= o.checkpointEvery)) {
            // Points first: a checkpoint must never be ahead of the corpus.
            if (!corpus.sync() ||
                !Checkpoint::save(o.checkpoint, e, walk, iterations / e.laneCount())) {
                fprintf(stderr, "persistence failure: %s; stopping\n",
                        corpus.failed ? "writing the corpus" : "writing the checkpoint");
                rc = 8;
                break;
            }
            lastCkpt = t;
        }
        const double dt = t - t0;
        if (bench || (l & 7) == 7 || last)
            printf("  %7.1f s  %10.3f M it/s  %llu iterations  %llu dp  %llu restarts  %llu "
                   "dropped\n",
                   dt, dt > 0 ? (double)(iterations - iterations0) / dt / 1e6 : 0.0, iterations,
                   reports, restarts, dropped);
        fflush(stdout);
        if (stopRequested) break;
    }
    CUDA_CHECK(cudaDeviceSynchronize());
    const double dt = now() - t0;
    if (!bench && !corpus.sync()) {
        fprintf(stderr, "writing the corpus failed\n");
        rc = 8;
    }
    const char *status = badReports ? "mismatch" : rc ? "error" : "ok";
    if (!rc && badReports) rc = 1;
    printf("{\"status\":\"%s\",\"cmd\":\"%s\",\"walk\":\"%s\",\"iterations\":%llu,"
           "\"seconds\":%.3f,\"iterationsPerSecond\":%.0f,\"reports\":%llu,\"verified\":%llu,"
           "\"badReports\":%llu,\"restarts\":%llu,\"dropped\":%llu,\"threads\":%d,\"batch\":%d,"
           "\"steps\":%d,\"dpWeight\":%d,\"runId\":%u,\"stopped\":%s}\n",
           status, bench ? "bench" : "walk", WALK_NAME, iterations, dt,
           dt > 0 ? (double)(iterations - iterations0) / dt : 0.0, reports, verified, badReports,
           restarts, dropped, o.threads, ECC_BATCH, o.steps, o.dpWeight, o.runId,
           stopRequested ? "true" : "false");
    return rc;
}

/* ---- health -------------------------------------------------------------- */

std::string jsonString(const char *s)
{
    std::string r = "\"";
    for (; *s; ++s) {
        const unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            r += '\\';
            r += char(c);
        } else if (c < 0x20) {
            char b[8];
            snprintf(b, sizeof(b), "\\u%04x", c);
            r += b;
        } else {
            r += char(c);
        }
    }
    return r + "\"";
}

// A device as NVML names it too, so that a caller can join this with
// nvidia-smi: the UUID in "GPU-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" form and
// the PCI bus id.
std::string deviceJson(int d)
{
    cudaDeviceProp p;
    CUDA_CHECK(cudaGetDeviceProperties(&p, d));
    char bus[32] = "";
    CUDA_CHECK(cudaDeviceGetPCIBusId(bus, sizeof(bus), d));
    int clockKHz = 0;
    CUDA_CHECK(cudaDeviceGetAttribute(&clockKHz, cudaDevAttrClockRate, d));
    const unsigned char *u = reinterpret_cast<const unsigned char *>(p.uuid.bytes);
    char uuid[48];
    snprintf(uuid, sizeof(uuid),
             "GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", u[0], u[1],
             u[2], u[3], u[4], u[5], u[6], u[7], u[8], u[9], u[10], u[11], u[12], u[13], u[14],
             u[15]);
    char buf[512];
    snprintf(buf, sizeof(buf),
             "{\"index\":%d,\"name\":%s,\"uuid\":\"%s\",\"pciBusId\":\"%s\","
             "\"computeCapability\":\"%d.%d\",\"sms\":%d,\"clockKHz\":%d,\"memoryBytes\":%zu}",
             d, jsonString(p.name).c_str(), uuid, bus, p.major, p.minor, p.multiProcessorCount,
             clockKHz, p.totalGlobalMem);
    return buf;
}

// The CUDA devices this process can see, one JSON object; a node that should
// have eight GPUs and shows seven fails here, before any load.
int cmdDevices()
{
    int driver = 0, runtime = 0, n = 0;
    cudaDriverGetVersion(&driver);
    cudaRuntimeGetVersion(&runtime);
    const cudaError_t err = cudaGetDeviceCount(&n);
    if (err != cudaSuccess) {
        printf("{\"status\":\"error\",\"error\":%s,\"driverVersion\":%d,\"runtimeVersion\":%d,"
               "\"devices\":[]}\n",
               jsonString(cudaGetErrorString(err)).c_str(), driver, runtime);
        return 3;
    }
    printf("{\"status\":\"ok\",\"driverVersion\":%d,\"runtimeVersion\":%d,\"devices\":[", driver,
           runtime);
    for (int d = 0; d < n; ++d) printf("%s%s", d ? "," : "", deviceJson(d).c_str());
    printf("]}\n");
    return 0;
}

// A timed load that checks itself, for telling whether a GPU is fit to use.
//
// It walks the campaign's sigma walk (the challenge points unless --p-seed or
// --q-seed) at weight 42, where a lane reports every 2^14.86 steps, so every
// lane reports every few seconds.  The host checks every report while the
// device runs the next launch (healthcheck.h: seed, restart counter, step
// window, weight, on the curve) and re-walks on the golden model the longest
// report of each launch within --rewalk-max-iters.
// The rate is complete iterations per second, as for bench and walk, with a
// warm-up left out and the rest cut into windows, so that a card that slows
// as it heats shows it.  Nothing is written to disk.
//
// Exit status: 0 every check passed; 1 a report failed a check or a re-walk;
// 4 the run proves nothing (stopped early, no report checked, none re-walked,
// or a report lost); 3 a CUDA error, from CUDA_CHECK: the device faulted, is
// not there, or the driver is too old.
int cmdHealth(Options o, const HostWalk &walk)
{
    if (!o.stepsGiven) o.steps = HEALTH_STEPS;
    if (!o.dpWeightGiven) o.dpWeight = HEALTH_DP_WEIGHT;
    if (!o.runIdGiven) o.runId = HEALTH_RUN_ID;
    if (o.warmup < 0) o.warmup = std::min(10.0, o.seconds / 6);
    if (!(o.seconds > 0) || !(o.warmup >= 0) || o.warmup >= o.seconds || !(o.window > 0) ||
        o.rewalkThreads < 0 || o.dpWeight > 131) {
        fprintf(stderr, "health: needs --seconds > --warmup >= 0, --window > 0, "
                        "--rewalk-threads >= 0 and --dp-weight <= 131\n");
        return 2;
    }
    // Wait on the device by blocking rather than spinning: a node checks all
    // of its GPUs at once, and the host's cores are for the checks.
    if (cudaInitDevice(o.device, cudaDeviceScheduleBlockingSync, 0) != cudaSuccess)
        (void)cudaGetLastError();
    CUDA_CHECK(cudaSetDevice(o.device));
    if (o.threads <= 0) o.threads = Engine::autoThreads(o.device);
    if (o.threads % 256) {
        fprintf(stderr, "--threads must be a multiple of 256 (compact state tiles)\n");
        return 2;
    }
    // No restart limit, so a lane's counter moves only when it reports; and a
    // lane reports at most once a launch (it is dead until revived), so one
    // record per lane is a report buffer that cannot drop one.
    o.maxIters = 0;
    o.dpCap = unsigned(size_t(o.threads) * ECC_BATCH);
    const std::string device = deviceJson(o.device);
    Engine e;
    e.setup(o, walk);
    e.init(false, 0);
    const size_t lanes = e.laneCount();
    const unsigned long long perLaunch = (unsigned long long)lanes * (unsigned long long)o.steps;
    ReportChecker checker;
    checker.reset(o.runId, o.dpWeight, (unsigned long long)o.steps, lanes);
    Rewalker rewalker;
    rewalker.start(walk, o.rewalkThreads, o.rewalkMaxIters);
    signal(SIGINT, onStop);
    signal(SIGTERM, onStop);
    printf("health: %s walk, %zu lanes, %d steps per launch, dp weight %d, run id %u, %.0f s "
           "with %.0f s warm-up\n",
           WALK_NAME, lanes, o.steps, o.dpWeight, o.runId, o.seconds, o.warmup);
    fflush(stdout);

    CUDA_CHECK(cudaDeviceSynchronize());
    const double t0 = now();
    std::vector<double> at = {0.0};                // launch boundaries, seconds from t0
    std::vector<unsigned long long> done = {0ull}; // iterations completed by each
    std::vector<DpRecord> recs;
    unsigned long long reports = 0;
    const char *lost = nullptr;
    double lastPrint = 0;
    e.launch(0);
    for (unsigned launch = 0;; ++launch) {
        unsigned counts[3] = {0, 0, 0};
        e.fetch(recs, counts);
        const double t = now() - t0;
        at.push_back(t);
        done.push_back(done.back() + perLaunch);
        reports += recs.size();
        if (counts[0] > recs.size())
            lost = "the report buffer overflowed";
        else if (counts[1])
            lost = "a lane was restarted without reporting";
        else if (counts[2])
            lost = "a lane exhausted its 16-bit restart counter";
        const bool last = lost || stopRequested || t >= o.seconds;
        if (!last) {
            const unsigned long long next = (unsigned long long)(launch + 1) * o.steps;
            if (!recs.empty()) e.init(true, next);
            e.launch(next);
        }

        // The host's share, while the device runs the next launch: every report
        // checked, and the longest within the re-walk limit sampled.
        const DpRecord *sample = nullptr;
        for (const DpRecord &r : recs) {
            const ReportChecker::Fault f = checker.check(r, launch);
            if (f != ReportChecker::OK && checker.totalFaults() <= 8)
                fprintf(stderr,
                        "launch %u: report fails the %s check: seed %016llx, %llu iterations, "
                        "x %016llx%016llx%016llx, y %016llx%016llx%016llx\n",
                        launch, ReportChecker::name(f), r.seed, r.iters, r.x[2], r.x[1], r.x[0],
                        r.y[2], r.y[1], r.y[0]);
            if (r.iters <= o.rewalkMaxIters && (!sample || r.iters > sample->iters)) sample = &r;
        }
        if (sample) rewalker.offer(*sample);
        if (t - lastPrint >= 5 || last) {
            unsigned long long rewalked = 0, mismatched = 0;
            rewalker.progress(&rewalked, &mismatched);
            printf("  %7.1f s  %10.3f M it/s  %llu reports  %llu checked  %llu re-walked  "
                   "%llu faults\n",
                   t, t > 0 ? (double)done.back() / t / 1e6 : 0.0, reports, checker.checked,
                   rewalked, checker.totalFaults() + mismatched);
            fflush(stdout);
            lastPrint = t;
        }
        if (last) break;
    }
    CUDA_CHECK(cudaDeviceSynchronize());
    rewalker.finish();

    const RateSummary rs = summarizeRates(at, done, o.warmup, o.window);
    std::string wjson;
    for (size_t k = 0; k < rs.windows.size(); ++k) {
        char b[128];
        snprintf(b, sizeof(b), "%s{\"start\":%.3f,\"end\":%.3f,\"iterationsPerSecond\":%.0f}",
                 k ? "," : "", at[rs.windows[k].first], at[rs.windows[k].second], rs.rates[k]);
        wjson += b;
    }
    const unsigned long long faults = checker.totalFaults() + rewalker.mismatches;
    const bool proved = !lost && !stopRequested && checker.checked > 0 &&
                        (o.rewalkThreads == 0 || rewalker.rewalked > 0);
    const char *status = faults ? "fault" : !proved ? "inconclusive" : "ok";
    std::string fjson;
    for (int f = ReportChecker::SEED; f < ReportChecker::KINDS; ++f) {
        char b[64];
        snprintf(b, sizeof(b), "\"%s\":%llu,", ReportChecker::name(f), checker.faults[f]);
        fjson += b;
    }
    printf("{\"status\":\"%s\",\"cmd\":\"health\",\"walk\":\"%s\",\"device\":%s,"
           "\"seconds\":%.3f,\"warmupSeconds\":%.3f,\"launches\":%zu,\"iterations\":%llu,"
           "\"iterationsPerSecond\":%.0f,\"steadyIterationsPerSecond\":%.0f,"
           "\"windowMinOverMax\":%.4f,\"windowLastOverFirst\":%.4f,\"windows\":[%s],"
           "\"reports\":%llu,\"checked\":%llu,\"checkedSteps\":%llu,\"checkedFraction\":%.4f,"
           "\"faults\":{%s\"rewalk\":%llu},\"rewalk\":{\"offered\":%llu,\"skipped\":%llu,"
           "\"rewalked\":%llu,\"steps\":%llu,\"threads\":%d,\"maxIters\":%llu},"
           "\"lanes\":%zu,\"threads\":%d,\"batch\":%d,\"steps\":%d,\"dpWeight\":%d,\"runId\":%u,"
           "\"stopped\":%s,\"lost\":%s}\n",
           status, WALK_NAME, device.c_str(), at.back(), at[rs.steadyFrom], at.size() - 1,
           done.back(), at.back() > 0 ? (double)done.back() / at.back() : 0.0, rs.steady,
           rs.minOverMax, rs.lastOverFirst, wjson.c_str(), reports, checker.checked,
           checker.checkedSteps, done.back() ? (double)checker.checkedSteps / done.back() : 0.0,
           fjson.c_str(), rewalker.mismatches, rewalker.offered, rewalker.skipped,
           rewalker.rewalked, rewalker.rewalkedSteps, o.rewalkThreads, o.rewalkMaxIters, lanes,
           o.threads, ECC_BATCH, o.steps, o.dpWeight, o.runId, stopRequested ? "true" : "false",
           lost ? jsonString(lost).c_str() : "null");
    return faults ? 1 : proved ? 0 : 4;
}

} // namespace

int main(int argc, char **argv)
{
    if (argc < 2) return usage();
    Options o;
    o.cmd = argv[1];
    o.steps = (int)optU64(argc, argv, "--steps", (unsigned long long)o.steps);
    o.launches = opt(argc, argv, "--launches") ? (int)optU64(argc, argv, "--launches", 0) : -1;
    o.threads = (int)optU64(argc, argv, "--threads", 0);
    o.dpWeight = (int)optU64(argc, argv, "--dp-weight", (unsigned long long)o.dpWeight);
    o.dpCap = (unsigned)optU64(argc, argv, "--dp-cap", o.dpCap);
    o.runIdGiven = opt(argc, argv, "--run-id") != nullptr;
    const unsigned long long runId = optU64(argc, argv, "--run-id", o.runId);
    o.maxIters = optU64(argc, argv, "--max-iters", o.maxIters);
    o.verify = (int)optU64(argc, argv, "--verify", 0);
    o.rounds = (int)optU64(argc, argv, "--rounds", (unsigned long long)o.rounds);
    o.device = (int)optU64(argc, argv, "--device", 0);
    o.testPoints = opt(argc, argv, "--p-seed") || opt(argc, argv, "--q-seed");
    o.pSeed = optU64(argc, argv, "--p-seed", 1);
    o.qSeed = optU64(argc, argv, "--q-seed", 2);
    o.checkpointEvery = (int)optU64(argc, argv, "--checkpoint-every", 600);
    if (const char *v = opt(argc, argv, "--dp-file")) o.dpFile = v;
    if (const char *v = opt(argc, argv, "--dp-file64")) o.dpFile64 = v;
    if (const char *v = opt(argc, argv, "--checkpoint")) o.checkpoint = v;
    if (const char *v = opt(argc, argv, "--kat")) o.kat = v;
    o.stepsGiven = opt(argc, argv, "--steps") != nullptr;
    o.dpWeightGiven = opt(argc, argv, "--dp-weight") != nullptr;
    if (const char *v = opt(argc, argv, "--seconds")) o.seconds = strtod(v, nullptr);
    if (const char *v = opt(argc, argv, "--warmup")) o.warmup = strtod(v, nullptr);
    if (const char *v = opt(argc, argv, "--window")) o.window = strtod(v, nullptr);
    o.rewalkThreads =
        (int)optU64(argc, argv, "--rewalk-threads", (unsigned long long)o.rewalkThreads);
    o.rewalkMaxIters = optU64(argc, argv, "--rewalk-max-iters", o.rewalkMaxIters);
    if (o.steps <= 0 || o.dpCap == 0 || o.checkpointEvery <= 0) return usage();
    if (runId > 0xFFFFu) {
        fprintf(stderr, "--run-id is 16 bits: at most 65535\n");
        return 2;
    }
    o.runId = unsigned(runId);
    if (o.cmd == "devices") return cmdDevices();

    ec2k_pt P, Q;
    if (o.testPoints) {
        ec2k_point_from_seed(&P, o.pSeed);
        ec2k_point_from_seed(&Q, o.qSeed);
    } else {
        challengePoints(&P, &Q);
    }
    HostWalk *walk = new HostWalk;
    walk->build(P, Q);

    int rc;
    if (o.cmd == "check")
        rc = cmdCheck(o, *walk);
    else if (o.cmd == "bench")
        rc = cmdRun(o, *walk, true);
    else if (o.cmd == "walk")
        rc = cmdRun(o, *walk, false);
    else if (o.cmd == "health")
        rc = cmdHealth(o, *walk);
    else
        rc = usage();
    delete walk;
    return rc;
}
