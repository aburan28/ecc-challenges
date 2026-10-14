/*
 * hosttest.cpp - the packed GF(2^131) arithmetic against the golden model,
 * on the CPU, with no CUDA toolkit and no GPU.
 *
 *   hosttest [ROUNDS] [KAT]
 *
 * This is the check CI runs.  It compiles the same headers the GPU kernel is
 * built from as host code and holds every routine the walk uses to
 * fpga/model/ecc2k130.c: field multiply, square, inverse, the Frobenius
 * powers, the polynomial-basis conversions and products, the walk's
 * selection, and a few dozen steps of the walk itself.  Then it re-walks
 * synthetic reports, including ones that must fail, because a check that
 * cannot fail is not a check.
 *
 * For the campaign's sigma walk it also checks the challenge points and the
 * known answers in KAT (tests/campaign-kat.hex): records the campaign's own
 * client wrote, which the model must reproduce byte for byte from their
 * seeds.  That is the test that a point this client reports is a point of
 * that campaign.
 *
 * Last, the checks `ec2k-gpu health` makes of a device's reports
 * (healthcheck.h), against a model of the device's launch loop on the golden
 * model: every report a correct device makes passes, and each kind of
 * corruption fails the check that is there to catch it.
 */
#include "healthcheck.h"

#include <stdlib.h>

using namespace ec2k_gpu;

namespace
{

// The device's launch loop on the golden model, as packedkernels.cuh runs it:
// a lane is tested at the start of each step (now = base + step), reports
// (seed, now - startIter, x, y) and goes dead; between launches the dead lanes
// are revived from seed + 1 at the next launch's base.
struct ModelDevice {
    struct Lane {
        ec2k_pt r;
        unsigned long long seed, startIter, hist;
        bool dead;
    };
    const HostWalk *walk;
    int dpWeight, steps;
    std::vector<Lane> lanes;

    bool revive(Lane &l, unsigned long long seed, unsigned long long base)
    {
        l.seed = seed;
        l.startIter = base;
        l.hist = HIST_START;
        l.dead = false;
        return startPoint(*walk, seed, &l.r);
    }
    bool init(unsigned runId, size_t count)
    {
        lanes.resize(count);
        for (size_t i = 0; i < count; ++i)
            if (!revive(lanes[i], eccSeedFor(runId, i), 0)) return false;
        return true;
    }
    bool launch(unsigned index, std::vector<DpRecord> *out)
    {
        const unsigned long long base = (unsigned long long)index * steps;
        for (auto &l : lanes)
            if (l.dead && !revive(l, l.seed + 1, base)) return false;
        for (int step = 0; step < steps; ++step)
            for (auto &l : lanes) {
                if (l.dead) continue; // stepped on the device too, but never read again
                if ((int)ec2k_fe_weight(&l.r.x) <= dpWeight) {
                    DpRecord rec;
                    rec.seed = l.seed;
                    rec.iters = base + step - l.startIter;
                    toLimbs(l.r.x, rec.x);
                    toLimbs(l.r.y, rec.y);
                    out->push_back(rec);
                    l.dead = true;
                    continue;
                }
                if (!referenceStep(*walk, &l.r, &l.hist)) return false;
            }
        return true;
    }
};

void healthChecks(CheckResult &cr, const HostWalk &walk)
{
    // The packed curve test against the model's, on subgroup points and on
    // the same points with a coordinate bit flipped.
    for (uint64_t s = 1; s <= 64; ++s) {
        ec2k_pt p;
        ec2k_point_from_seed(&p, s * 0x9E3779B97F4A7C15ull);
        cr.note(onCurvePacked(toPacked(p.x), toPacked(p.y)), "onCurvePacked accepts a curve point");
        p.y.w[s % 3] ^= 1ull << (s % 3 == 2 ? s % 3 : s % 64);
        cr.note(!onCurvePacked(toPacked(p.x), toPacked(p.y)) && !ec2k_on_curve(&p),
                "onCurvePacked rejects a flipped bit, as the model does");
    }

    // A run of the model device: every report passes.  Weight 60 makes a lane
    // report every few steps, so four lanes over six launches exercise
    // revivals, restart counters and reports in every position of a launch.
    const unsigned run = 4247;
    ModelDevice dev{&walk, 60, 8, {}};
    cr.note(dev.init(run, 4), "model device seeds its lanes");
    ReportChecker checker;
    checker.reset(run, dev.dpWeight, (unsigned long long)dev.steps, dev.lanes.size());
    std::vector<DpRecord> probe;
    unsigned probeLaunch = 0;
    ReportChecker probeState;
    bool clean = true;
    for (unsigned launch = 0; launch < 6; ++launch) {
        std::vector<DpRecord> recs;
        cr.note(dev.launch(launch, &recs), "model device launch");
        for (const DpRecord &r : recs) {
            if (probe.empty() && launch >= 2 && (r.seed & 0xFFFFu) > 0) {
                probe.push_back(r);
                probeLaunch = launch;
                probeState = checker;
            }
            clean = checker.check(r, launch) == ReportChecker::OK && clean;
        }
    }
    cr.note(clean && checker.totalFaults() == 0, "every report of a correct device passes");
    cr.note(checker.checked >= 12, "the model device reported across several launches");
    cr.note(!probe.empty(), "a revived lane's report to corrupt");
    if (probe.empty()) return;

    // Each corruption fails the check that is there for it.
    const DpRecord good = probe[0];
    auto faultOf = [&](const DpRecord &r) {
        ReportChecker c = probeState;
        return c.check(r, probeLaunch);
    };
    cr.note(faultOf(good) == ReportChecker::OK, "the probe report passes where it was made");
    DpRecord bad = good;
    bad.y[1] ^= 1ull << 29;
    cr.note(faultOf(bad) == ReportChecker::CURVE, "a flipped y bit fails the curve check");
    bad = good;
    bad.x[0] ^= 1ull << 3;
    cr.note(faultOf(bad) == ReportChecker::CURVE || faultOf(bad) == ReportChecker::WEIGHT,
            "a flipped x bit fails the curve or weight check");
    bad = good;
    bad.x[0] = bad.x[1] = ~0ull;
    bad.x[2] = 7;
    cr.note(faultOf(bad) == ReportChecker::WEIGHT, "a heavy x fails the weight check");
    bad = good;
    bad.y[2] |= 1ull << 40;
    cr.note(faultOf(bad) == ReportChecker::CURVE, "a bit above the 131st fails the curve check");
    bad = good;
    bad.seed ^= 1ull << 48;
    cr.note(faultOf(bad) == ReportChecker::SEED, "another run id fails the seed check");
    bad = good;
    bad.seed = eccSeedFor(run, dev.lanes.size());
    cr.note(faultOf(bad) == ReportChecker::SEED, "a lane the run does not have fails");
    bad = good;
    bad.seed += 1;
    cr.note(faultOf(bad) == ReportChecker::SEQUENCE, "a skipped restart fails the sequence");
    bad = good;
    bad.seed -= 1;
    cr.note(faultOf(bad) == ReportChecker::SEQUENCE, "a repeated restart fails the sequence");
    bad = good;
    bad.iters += (unsigned long long)dev.steps;
    cr.note(faultOf(bad) == ReportChecker::ITERS, "an iteration count a launch late fails");
    bad = good;
    bad.iters = ~0ull - 3;
    cr.note(faultOf(bad) == ReportChecker::ITERS, "a wrapped iteration count fails");

    // The golden-model re-walker: the good report re-walks, the corrupted one
    // does not, and one longer than its limit is skipped rather than queued.
    Rewalker rw;
    rw.start(walk, 1, 1000);
    rw.offer(good);
    bad = good;
    bad.y[0] ^= 1;
    rw.offer(bad);
    bad = good;
    bad.iters = 1001;
    rw.offer(bad);
    rw.finish();
    cr.note(rw.rewalked == 2 && rw.mismatches == 1 && rw.skipped == 1,
            "re-walker: a good report matches, a corrupted one does not, a long one is skipped");

    // Rates: one launch a second at 1000 iterations each, a 10 s warm-up and
    // 10 s windows, then the same with the rate halved after 40 s and a 4 s
    // tail that folds into the last window.
    std::vector<double> at;
    std::vector<unsigned long long> done;
    for (int i = 0; i <= 60; ++i) {
        at.push_back(i);
        done.push_back(1000ull * (unsigned long long)i);
    }
    RateSummary s = summarizeRates(at, done, 10, 10);
    cr.note(s.steadyFrom == 10 && s.steady == 1000 && s.windows.size() == 5 && s.minOverMax == 1 &&
                s.lastOverFirst == 1,
            "rates: a steady run has five equal windows after the warm-up");
    at.clear();
    done.clear();
    unsigned long long total = 0;
    for (int i = 0; i <= 64; ++i) {
        at.push_back(i);
        done.push_back(total);
        total += i < 40 ? 1000 : 500;
    }
    s = summarizeRates(at, done, 10, 10);
    cr.note(s.windows.size() == 5 && s.windows.back().second == 64 && s.rates.front() == 1000 &&
                s.rates.back() == 500 && s.lastOverFirst == 0.5 && s.minOverMax == 0.5,
            "rates: a slowdown shows in the last window, the short tail folded into it");
    s = summarizeRates(at, done, 100, 10);
    cr.note(s.windows.empty() && s.steady == 0, "rates: a warm-up longer than the run leaves none");
}

} // namespace

int main(int argc, char **argv)
{
    const int rounds = argc > 1 ? atoi(argv[1]) : 64;
    ec2k_pt P, Q;
    ec2k_point_from_seed(&P, 1);
    ec2k_point_from_seed(&Q, 2);
    HostWalk *walk = new HostWalk;
    walk->build(P, Q);

    CheckResult cr = crossCheck(*walk, rounds, 0x243F6A8885A308D3ULL);

    /* A report the reference produces must re-walk; the same record with one
     * coordinate bit flipped, or under another seed, must not. */
    for (int i = 0; i < 4; ++i) {
        const unsigned long long seed = eccSeedFor(9u, (unsigned long long)i);
        ec2k_pt r;
        if (!startPoint(*walk, seed, &r)) continue;
        unsigned long long hist = HIST_START;
        const unsigned long long iters = 50 + 17ull * (unsigned long long)i;
        bool ok = true;
        for (unsigned long long k = 0; k < iters && ok; ++k) ok = referenceStep(*walk, &r, &hist);
        if (!ok) continue;
        DpRecord rec;
        rec.seed = seed;
        rec.iters = iters;
        toLimbs(r.x, rec.x);
        toLimbs(r.y, rec.y);
        cr.note(rewalk(*walk, rec, nullptr), "reference report re-walks");
        DpRecord bad = rec;
        bad.y[1] ^= 1ull << 17;
        cr.note(!rewalk(*walk, bad, nullptr), "a corrupted report is rejected");
        bad = rec;
        bad.seed ^= 1ull << 16;
        cr.note(!rewalk(*walk, bad, nullptr), "a report under another seed is rejected");
    }
    healthChecks(cr, *walk);
    delete walk;

#if !ECC_WALK_TABLE
    if (argc > 2)
        campaignCheck(cr, argv[2]);
    else
        cr.note(false, "the sigma walk's test needs the known-answer file (hosttest ROUNDS KAT)");
#endif

    if (cr.failures) {
        fprintf(stderr, "FAILED: %d of %d checks\n", cr.failures, cr.checks);
        return 1;
    }
    printf("{\"status\":\"ok\",\"checks\":%d,\"rounds\":%d}\n", cr.checks, rounds);
    return 0;
}
