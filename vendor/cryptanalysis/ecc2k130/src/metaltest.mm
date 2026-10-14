/*
 * metaltest.mm - the Metal kernels against the host, which src/cputest.cpp
 * holds to the golden model.
 *
 *   - selftest kernel: every routine the walk kernel calls -- multiply, square
 *     and inverse in the normal basis, both basis conversions, the polynomial
 *     product, pair and squaring, the table walk's selection with its cycle
 *     rule and the addend -- on random inputs, word for word against the host.
 *   - the engine: after launches with reports, --max-iters restarts and
 *     revived lanes, every report and every lane re-walked from its seed on
 *     the golden model.
 *
 * With no Metal device (a CI runner, a sandbox that hides the GPU) there is
 * nothing to hold to anything: the test says so and passes.
 */
#include "metalwalk.h"

using namespace ec2k_metal;
using namespace eccPacked131;

namespace
{

const uint32_t kSelfIn = 22, kSelfOut = 53;

void put(uint32_t *dst, const P131 &a) { memcpy(dst, a.v, sizeof(a.v)); }
bool same(const uint32_t *got, const P131 &want)
{
    return memcmp(got, want.v, sizeof(want.v)) == 0;
}

void checkSelftest(id<MTLDevice> device, const HostTable &table, int cases, CheckResult *cr)
{
    std::string error;
    id<MTLLibrary> lib = compileWalkLibrary(device, 16, &error);
    cr->note(lib != nil, "the shader source compiles");
    if (!lib) {
        fprintf(stderr, "%s\n", error.c_str());
        return;
    }
    id<MTLComputePipelineState> ps = pipelineFor(device, lib, "selftest", &error);
    cr->note(ps != nil, "the selftest pipeline builds");
    if (!ps) {
        fprintf(stderr, "%s\n", error.c_str());
        return;
    }
    const std::vector<uint32_t> consts = table.deviceConsts();
    std::vector<uint32_t> in(size_t(cases) * kSelfIn);
    std::vector<ec2k_pt> points((size_t)cases);
    std::vector<unsigned long long> hists((size_t)cases);
    uint64_t rng = 0xA4093822299F31D0ULL;
    for (int i = 0; i < cases; ++i) {
        ec2k_fe a, b;
        do randomFe(&a, &rng);
        while (ec2k_fe_is_zero(&a));
        randomFe(&b, &rng);
        ec2k_point_from_seed(&points[i], 500 + (uint64_t)i);
        // Histories that make the cycle rule fire: the tag this point selects
        // with its sign flipped, as the last step or as the one before a
        // matching pair.
        const unsigned plain = referenceTag(table, points[i], ECC_HIST_EMPTY) ^ ECC_TAG_EPS;
        hists[i] =
            i % 3 == 0 ? ECC_HIST_EMPTY
            : i % 3 == 1
                ? eccHistPush(ECC_HIST_EMPTY, plain)
                : eccHistPush(eccHistPush(eccHistPush(ECC_HIST_EMPTY, 0x1123u), plain), 0x0123u);
        uint32_t *c = &in[size_t(i) * kSelfIn];
        put(c, toPacked(a));
        put(c + 5, toPacked(b));
        put(c + 10, toPacked(points[i].x));
        put(c + 15, toPacked(points[i].y));
        c[20] = uint32_t(hists[i]);
        c[21] = uint32_t(hists[i] >> 32);
    }
    id<MTLBuffer> inBuf = [device newBufferWithBytes:in.data()
                                              length:in.size() * 4
                                             options:MTLResourceStorageModeShared];
    id<MTLBuffer> outBuf = [device newBufferWithLength:size_t(cases) * kSelfOut * 4
                                               options:MTLResourceStorageModeShared];
    id<MTLBuffer> twBuf = [device newBufferWithBytes:consts.data()
                                              length:consts.size() * 4
                                             options:MTLResourceStorageModeShared];
    const uint32_t n = (uint32_t)cases;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> cb = [queue commandBuffer];
    id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
    [enc setComputePipelineState:ps];
    [enc setBuffer:inBuf offset:0 atIndex:0];
    [enc setBuffer:outBuf offset:0 atIndex:1];
    [enc setBuffer:twBuf offset:0 atIndex:2];
    [enc setBytes:&n length:sizeof(n) atIndex:3];
    [enc dispatchThreads:MTLSizeMake(n, 1, 1) threadsPerThreadgroup:threadgroupFor(ps)];
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    cr->note(cb.status == MTLCommandBufferStatusCompleted, "the selftest kernel runs");
    if (cb.status != MTLCommandBufferStatusCompleted) return;

    const uint32_t *out = (const uint32_t *)outBuf.contents;
    for (int i = 0; i < cases; ++i) {
        const uint32_t *c = &in[size_t(i) * kSelfIn], *o = out + size_t(i) * kSelfOut;
        P131 a, b, px, py;
        memcpy(a.v, c, 20);
        memcpy(b.v, c + 5, 20);
        memcpy(px.v, c + 10, 20);
        memcpy(py.v, c + 15, 20);
        const P131 qa = toPolynomial131(a), qb = toPolynomial131(b);
        cr->note(same(o, mul131(a, b)), "GPU mul131 == host");
        cr->note(same(o + 5, sqr131(a)), "GPU sqr131 == host");
        cr->note(same(o + 10, inv131(a)), "GPU inv131 == host");
        cr->note(same(o + 15, qa), "GPU toPolynomial131 == host");
        cr->note(same(o + 20, a), "GPU polynomial basis round trip");
        cr->note(same(o + 25, mulPolynomial131(qa, qb)), "GPU mulPolynomial131 == host");
        cr->note(same(o + 30, squarePolynomial131(qa)), "GPU squarePolynomial131 == host");
        cr->note(same(o + 35, squarePolynomial131(qa)), "GPU mulPolynomialPair131.second == host");
        unsigned long long h = hists[i];
        const unsigned want = referenceTag(table, points[i], h);
        cr->note(o[40] == want, "GPU selection == reference tag");
        cr->note((o[41] | ((unsigned long long)o[42] << 32)) == eccHistPush(h, want),
                 "GPU history push");
        P131 d, e;
        twAddend(want, toPolynomial131(px), toPolynomial131(py), consts.data(), &d, &e);
        cr->note(same(o + 43, d) && same(o + 48, e), "GPU addend == host");
    }
}

void checkEngine(const HostTable &table, CheckResult *cr)
{
    Options o;
    o.dpWeight = 52;
    o.maxIters = 40;
    o.runId = 21;
    o.threads = 37; // not a multiple of any threadgroup size
    o.dpCap = 4096;
    MetalEngine e(table, o, false, 8, 16);
    cr->note(e.ok(), "the engine starts");
    if (!e.ok()) {
        fprintf(stderr, "%s\n", e.error().c_str());
        return;
    }
    std::vector<DpRecord> reports;
    LaunchCounts counts;
    for (int l = 0; l < 3; ++l) e.launch(50, &reports, &counts);
    cr->note(counts.iterations == 3ull * 50ull * e.laneCount(), "iterations are counted exactly");
    cr->note(!counts.exhausted && counts.dropped == 0, "no lane exhausted, no report dropped");
    cr->note(!reports.empty(), "a relaxed weight produces reports");
    cr->note(counts.restarts > 0, "--max-iters restarts overdue lanes");
    int rewalked = 0;
    for (const DpRecord &r : reports) rewalked += rewalk(table, r, nullptr) ? 1 : 0;
    cr->note(rewalked == (int)reports.size(), "every report re-walks on the golden model");
    int lanesOk = 0;
    for (size_t lane = 0; lane < e.laneCount(); ++lane)
        lanesOk += rewalk(table, e.laneRecord(lane), nullptr) ? 1 : 0;
    cr->note(lanesOk == (int)e.laneCount(), "every lane is where the golden model puts its seed");
}

} // namespace

int main(int argc, char **argv)
{
    @autoreleasepool {
        const int cases = argc > 1 ? atoi(argv[1]) : 96;
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) {
            printf("{\"status\":\"skipped\",\"reason\":\"no Metal device\"}\n");
            return 0;
        }
        ec2k_pt P, Q;
        ec2k_point_from_seed(&P, 1);
        ec2k_point_from_seed(&Q, 2);
        HostTable *table = new HostTable;
        table->build(P, Q);
        CheckResult cr;
        checkSelftest(device, *table, cases, &cr);
        if (!cr.failures) checkEngine(*table, &cr);
        delete table;
        if (cr.failures) {
            fprintf(stderr, "FAILED: %d of %d checks\n", cr.failures, cr.checks);
            return 1;
        }
        printf("{\"status\":\"ok\",\"checks\":%d,\"device\":\"%s\"}\n", cr.checks,
               [[device name] UTF8String]);
        return 0;
    }
}
