/*
 * metalprof.mm - time the Metal walk kernel, or a variant of it, on the GPU's
 * own clock.
 *
 *   metalprof <source.metal> [--batch B] [--threads T] [--steps S] [--reps R]
 *             [--tg N] [--kernel NAME] [-D NAME=VALUE ...]
 *
 * The source is a full shader (build/walk_metal.metal, or a copy with a
 * changed kernel); the -D macros go to the compiler beside ECC_BATCH, so one
 * file can carry several variants behind #if.  Lanes start at random field
 * elements, not curve points: the walk's work does not depend on that, and no
 * report is ever taken (dp weight -1).  Each rep is one command buffer of S
 * steps; the result is the median rep's GPUEndTime - GPUStartTime per lane
 * step, which leaves out the host's seeding and the CPU's view of the queue.
 */
#include "metalwalk.h"

#include <algorithm>

using namespace ec2k_metal;

namespace
{

struct Args {
    const char *source = nullptr;
    const char *kernel = "walk";
    int batch = 32, threads = 32768, steps = 64, reps = 7, tg = 0;
    // Normalise per thread-step instead of per lane-step (--per-thread N: N
    // operations per thread per step, for the microbenchmarks).
    int perThread = 0;
    NSMutableDictionary *macros = [NSMutableDictionary new];
};

bool parse(int argc, char **argv, Args *a)
{
    for (int i = 1; i < argc; ++i) {
        const std::string s = argv[i];
        const bool more = i + 1 < argc;
        if (s == "--batch" && more)
            a->batch = atoi(argv[++i]);
        else if (s == "--threads" && more)
            a->threads = atoi(argv[++i]);
        else if (s == "--steps" && more)
            a->steps = atoi(argv[++i]);
        else if (s == "--reps" && more)
            a->reps = atoi(argv[++i]);
        else if (s == "--tg" && more)
            a->tg = atoi(argv[++i]);
        else if (s == "--per-thread" && more)
            a->perThread = atoi(argv[++i]);
        else if (s == "--kernel" && more)
            a->kernel = argv[++i];
        else if (s == "-D" && more) {
            const std::string d = argv[++i];
            const size_t eq = d.find('=');
            NSString *name = [NSString stringWithUTF8String:d.substr(0, eq).c_str()];
            a->macros[name] = eq == std::string::npos ? @1 : @(atoi(d.substr(eq + 1).c_str()));
        } else if (!a->source && s[0] != '-')
            a->source = argv[i];
        else
            return false;
    }
    return a->source != nullptr;
}

} // namespace

int main(int argc, char **argv)
{
    @autoreleasepool {
        Args a;
        if (!parse(argc, argv, &a)) {
            fprintf(stderr, "usage: metalprof <source.metal> [--batch B] [--threads T] "
                            "[--steps S] [--reps R] [--tg N] [--kernel K] [-D NAME=VALUE]\n");
            return 2;
        }
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            fprintf(stderr, "no Metal device\n");
            return 3;
        }
        NSError *err = nil;
        NSString *source = [NSString stringWithContentsOfFile:@(a.source)
                                                     encoding:NSUTF8StringEncoding
                                                        error:&err];
        if (!source) {
            fprintf(stderr, "cannot read %s\n", a.source);
            return 3;
        }
        MTLCompileOptions *options = [MTLCompileOptions new];
        a.macros[@"ECC_BATCH"] = @(a.batch);
        options.preprocessorMacros = a.macros;
        id<MTLLibrary> lib = [device newLibraryWithSource:source options:options error:&err];
        if (!lib) {
            fprintf(stderr, "%s\n", [[err localizedDescription] UTF8String]);
            return 3;
        }
        std::string error;
        id<MTLComputePipelineState> ps = pipelineFor(device, lib, a.kernel, &error);
        if (!ps) {
            fprintf(stderr, "%s\n", error.c_str());
            return 3;
        }

        ec2k_pt P, Q;
        Options o;
        ec2k_point_from_seed(&P, o.pSeed);
        ec2k_point_from_seed(&Q, o.qSeed);
        HostTable *table = new HostTable;
        table->build(P, Q);
        const std::vector<uint32_t> consts = table->deviceConsts();
        delete table;

        const size_t lanes = size_t(a.threads) * size_t(a.batch);
        auto shared = [&](size_t bytes) {
            id<MTLBuffer> b = [device newBufferWithLength:bytes
                                                  options:MTLResourceStorageModeShared];
            memset(b.contents, 0, bytes);
            return b;
        };
        id<MTLBuffer> x = shared(lanes * 5 * 4), y = shared(lanes * 5 * 4),
                      hist = shared(lanes * 8), seed = shared(lanes * 8), start = shared(lanes * 8),
                      dead = shared(lanes * 4), dp = shared(64 * 16), counts = shared(16);
        id<MTLBuffer> tw = [device newBufferWithBytes:consts.data()
                                               length:consts.size() * 4
                                              options:MTLResourceStorageModeShared];
        uint64_t rng = 0x9E3779B97F4A7C15ULL;
        auto next = [&]() {
            rng ^= rng << 13;
            rng ^= rng >> 7;
            rng ^= rng << 17;
            return uint32_t(rng >> 16);
        };
        for (id<MTLBuffer> b : @[ x, y ]) {
            uint32_t *w = (uint32_t *)b.contents;
            for (size_t i = 0; i < lanes * 5; ++i) w[i] = i % 5 == 4 ? next() & 7u : next();
        }
        uint64_t *h = (uint64_t *)hist.contents;
        for (size_t i = 0; i < lanes; ++i) h[i] = ECC_HIST_EMPTY;

        WalkArgs args{};
        args.threads = (uint32_t)a.threads;
        args.steps = (uint32_t)a.steps;
        args.dpWeight = -1;
        args.dpCap = 16;
        args.guardPeriod = 1;
        MTLSize tg = threadgroupFor(ps);
        if (a.tg > 0) tg = MTLSizeMake((NSUInteger)a.tg, 1, 1);

        std::vector<double> ns;
        for (int rep = 0; rep < a.reps + 1; ++rep) {
            id<MTLCommandQueue> queue = [device newCommandQueue];
            id<MTLCommandBuffer> cb = [queue commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            [enc setComputePipelineState:ps];
            [enc setBytes:&args length:sizeof(args) atIndex:0];
            id<MTLBuffer> buffers[] = {x, y, hist, seed, start, dead, dp, counts, tw};
            for (NSUInteger i = 0; i < 9; ++i) [enc setBuffer:buffers[i] offset:0 atIndex:i + 1];
            [enc dispatchThreads:MTLSizeMake((NSUInteger)a.threads, 1, 1) threadsPerThreadgroup:tg];
            [enc endEncoding];
            [cb commit];
            [cb waitUntilCompleted];
            if (cb.status != MTLCommandBufferStatusCompleted) {
                fprintf(stderr, "command buffer failed: %s\n",
                        cb.error ? [[cb.error localizedDescription] UTF8String] : "?");
                return 3;
            }
            args.iterBase += (uint64_t)a.steps;
            if (rep) // the first is a warm-up
                ns.push_back(
                    (cb.GPUEndTime - cb.GPUStartTime) * 1e9 /
                    (double(a.perThread ? size_t(a.threads) * size_t(a.perThread) : lanes) *
                     double(a.steps)));
        }
        std::sort(ns.begin(), ns.end());
        const double med = ns[ns.size() / 2];
        printf("%-10s batch %d threads %d tg %lu: %.3f ns/it (min %.3f max %.3f)  %.1f M it/s  "
               "maxThreads %lu  width %lu\n",
               a.kernel, a.batch, a.threads, (unsigned long)tg.width, med, ns.front(), ns.back(),
               1e3 / med, (unsigned long)ps.maxTotalThreadsPerThreadgroup,
               (unsigned long)ps.threadExecutionWidth);
        return 0;
    }
}
