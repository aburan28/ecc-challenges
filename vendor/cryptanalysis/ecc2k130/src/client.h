// client.h - what the host-side clients of the packed walk share: the command
// line, the corpus, and the launch loop around an engine.
//
// An engine is whatever advances the lanes: worker threads over the host's
// carry-less multiplier (cpuwalk.h) or a Metal compute pipeline
// (metalwalk.h).  Both run the walk of tablewalk.h from the seeds of kernel.h
// over the table of hostcheck.h, so the 64-byte reports they write are the
// CUDA client's -- same seed, same iteration count, same point -- and one
// corpus, one `--verify` re-walk on the golden model and one `ec2k merge`
// serve all three.  src/ec2k_gpu.cu carries its own copy of this loop: it
// builds only with nvcc, and is left as it was measured.
#pragma once

#include "hostcheck.h"

#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <string>
#include <vector>

namespace ec2k_client
{

using namespace ec2k_gpu;

struct Options {
    std::string cmd;
    int steps = 1024;
    int launches = 32;
    int dpWeight = EC2K_DP_WEIGHT;
    unsigned runId = 1;
    unsigned long long maxIters = 0;
    int verify = 0;
    int rounds = 64;
    uint64_t pSeed = 1, qSeed = 2;
    std::string dpFile, dpFile32;
    // CPU engine: worker threads, lanes per batched inversion, batches in flight.
    int workers = 0, batch = 0, chunks = 0;
    // Metal engine: GPU threads (each owns ECC_BATCH lanes), report buffer, device.
    int threads = 0, device = 0;
    unsigned dpCap = 1u << 18;
};

inline const char *opt(int argc, char **argv, const char *name)
{
    for (int i = 2; i + 1 < argc; i++)
        if (!strcmp(argv[i], name)) return argv[i + 1];
    return nullptr;
}

inline unsigned long long optU64(int argc, char **argv, const char *name, unsigned long long def)
{
    const char *v = opt(argc, argv, name);
    return v ? strtoull(v, nullptr, 0) : def;
}

// The options every client takes; false when they do not make sense.
inline bool parseOptions(int argc, char **argv, Options *o)
{
    o->cmd = argv[1];
    o->steps = (int)optU64(argc, argv, "--steps", (unsigned long long)o->steps);
    o->launches = (int)optU64(argc, argv, "--launches", (unsigned long long)o->launches);
    o->dpWeight = (int)optU64(argc, argv, "--dp-weight", (unsigned long long)o->dpWeight);
    o->runId = (unsigned)optU64(argc, argv, "--run-id", o->runId) & 0xFFFFu;
    o->maxIters = optU64(argc, argv, "--max-iters", 0);
    o->verify = (int)optU64(argc, argv, "--verify", 0);
    o->rounds = (int)optU64(argc, argv, "--rounds", (unsigned long long)o->rounds);
    o->pSeed = optU64(argc, argv, "--p-seed", 1);
    o->qSeed = optU64(argc, argv, "--q-seed", 2);
    o->workers = (int)optU64(argc, argv, "--workers", 0);
    o->batch = (int)optU64(argc, argv, "--batch", 0);
    o->chunks = (int)optU64(argc, argv, "--chunks", 0);
    o->threads = (int)optU64(argc, argv, "--threads", 0);
    o->device = (int)optU64(argc, argv, "--device", 0);
    o->dpCap = (unsigned)optU64(argc, argv, "--dp-cap", o->dpCap);
    if (const char *v = opt(argc, argv, "--dp-file")) o->dpFile = v;
    if (const char *v = opt(argc, argv, "--dp-file32")) o->dpFile32 = v;
    return o->steps > 0 && o->launches >= 0 && o->dpCap > 0 && o->workers >= 0 && o->batch >= 0 &&
           o->chunks >= 0 && o->threads >= 0;
}

inline const char *commonUsage()
{
    return "  --steps S        iterations per lane per launch (default 1024)\n"
           "  --launches L     launches (default 32; 0 = run until interrupted)\n"
           "  --dp-weight W    distinguished when HW(x) <= W (default 34; bench: none)\n"
           "  --run-id R       16-bit run id; distinct runs never share a trail\n"
           "  --max-iters N    restart a lane that walked N steps without a report\n"
           "  --verify N       re-walk the first N reports on the golden model\n"
           "  --dp-file F      append 64-byte (seed, iters, x, y) records to F\n"
           "  --dp-file32 F    append fpga/host-compatible 32-byte (seed, orbit-min x) records\n"
           "  --p-seed S, --q-seed S   the base and target points, from seeds (1, 2)\n"
           "  --rounds N       check: random inputs per routine (default 64)\n";
}

inline double now()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + 1e-9 * (double)ts.tv_nsec;
}

// ^C ends a `--launches 0` run at the next launch boundary, with its summary.
inline volatile sig_atomic_t &stopRequested()
{
    static volatile sig_atomic_t flag = 0;
    return flag;
}
inline void onStopSignal(int) { stopRequested() = 1; }

/* ---- the corpus ---------------------------------------------------------- */

struct Corpus {
    FILE *f64 = nullptr, *f32 = nullptr;
    unsigned long long written = 0;
    bool failed = false;

    bool open(const Options &o)
    {
        if (!o.dpFile.empty() && !(f64 = fopen(o.dpFile.c_str(), "ab"))) return false;
        if (!o.dpFile32.empty() && !(f32 = fopen(o.dpFile32.c_str(), "ab"))) return false;
        return true;
    }
    void append(const std::vector<DpRecord> &recs)
    {
        for (const DpRecord &r : recs) {
            if (f64 && fwrite(&r, sizeof(r), 1, f64) != 1) failed = true;
            if (f32) {
                // The FPGA core's record: seed, then the Frobenius-orbit
                // minimum of x, which is the negation- and Frobenius-class
                // invariant `ec2k merge` compares.
                ec2k_record rec;
                rec.seed = r.seed;
                ec2k_fe x;
                x.w[0] = r.x[0];
                x.w[1] = r.x[1];
                x.w[2] = r.x[2];
                ec2k_orbit_min(&rec.x, &x);
                uint8_t buf[EC2K_RECORD_BYTES];
                ec2k_record_encode(buf, &rec);
                if (fwrite(buf, 1, sizeof(buf), f32) != sizeof(buf)) failed = true;
            }
            written++;
        }
        if (f64) fflush(f64);
        if (f32) fflush(f32);
    }
    ~Corpus()
    {
        if (f64) fclose(f64);
        if (f32) fclose(f32);
    }
};

/* ---- the launch loop ------------------------------------------------------- */

// What a launch reports besides its distinguished points.
struct LaunchCounts {
    unsigned long long iterations = 0; // lane-steps taken
    unsigned long long restarts = 0;   // lanes restarted by --max-iters
    unsigned long long dropped = 0;    // reports that did not fit the engine's buffer
    bool exhausted = false;            // a lane used up its 16-bit restart counter
};

inline int cmdCheck(const Options &o, const HostTable &table)
{
    const CheckResult cr = crossCheck(table, o.rounds, 0x243F6A8885A308D3ULL);
    if (cr.failures) {
        fprintf(stderr, "FAILED: %d of %d checks\n", cr.failures, cr.checks);
        return 1;
    }
    printf("{\"status\":\"ok\",\"checks\":%d,\"rounds\":%d}\n", cr.checks, o.rounds);
    return 0;
}

// Engine: size_t laneCount(); void launch(int steps, std::vector<DpRecord> *out,
// LaunchCounts *counts); const char *name(); std::string describe() (JSON
// members of its geometry, with a leading comma).
template <class Engine> int cmdRun(const Options &o, const HostTable &table, Engine &e, bool bench)
{
    Corpus corpus;
    if (!bench && !corpus.open(o)) {
        fprintf(stderr, "cannot open the corpus file for appending\n");
        return 1;
    }
    signal(SIGINT, onStopSignal);
    signal(SIGTERM, onStopSignal);

    const double t0 = now();
    unsigned long long iterations = 0, reports = 0, verified = 0, badReports = 0, restarts = 0,
                       dropped = 0;
    std::vector<DpRecord> recs;
    for (int l = 0; (o.launches == 0 || l < o.launches) && !stopRequested(); ++l) {
        LaunchCounts counts;
        recs.clear();
        e.launch(o.steps, &recs, &counts);
        iterations += counts.iterations;
        reports += recs.size();
        restarts += counts.restarts;
        dropped += counts.dropped;
        if (counts.exhausted) {
            fprintf(stderr, "a lane exhausted its 16-bit restart counter; use a new run id\n");
            return 1;
        }
        for (size_t i = 0; i < recs.size() && verified < (unsigned long long)o.verify; ++i) {
            verified++;
            if (!rewalk(table, recs[i], nullptr)) {
                badReports++;
                fprintf(stderr, "report does not re-walk: seed %llu, %llu iterations\n",
                        recs[i].seed, recs[i].iters);
            }
        }
        if (!bench) corpus.append(recs);
        const double dt = now() - t0;
        if (bench || (l & 7) == 7 || (o.launches && l + 1 == o.launches))
            printf("  %7.1f s  %10.3f M it/s  %llu iterations  %llu dp  %llu restarts  %llu "
                   "dropped\n",
                   dt, dt > 0 ? (double)iterations / dt / 1e6 : 0.0, iterations, reports, restarts,
                   dropped);
    }
    const double dt = now() - t0;
    if (corpus.failed) {
        fprintf(stderr, "writing the corpus failed\n");
        return 1;
    }
    printf("{\"status\":\"%s\",\"client\":\"%s\",\"cmd\":\"%s\",\"iterations\":%llu,"
           "\"seconds\":%.3f,\"iterationsPerSecond\":%.0f,\"reports\":%llu,\"verified\":%llu,"
           "\"badReports\":%llu,\"restarts\":%llu,\"dropped\":%llu,\"lanes\":%zu,\"steps\":%d,"
           "\"dpWeight\":%d,\"runId\":%u%s}\n",
           badReports ? "mismatch" : "ok", e.name(), bench ? "bench" : "walk", iterations, dt,
           dt > 0 ? (double)iterations / dt : 0.0, reports, verified, badReports, restarts, dropped,
           e.laneCount(), o.steps, bench ? -1 : o.dpWeight, o.runId, e.describe().c_str());
    return badReports ? 1 : 0;
}

} // namespace ec2k_client
