// healthcheck.h - what `ec2k-gpu health` checks on the host about every report
// the device makes, and the re-walks it samples on the golden model.
//
// A health run walks the campaign's sigma walk at a test weight, so that a
// lane reports every few seconds, and holds each report to what a correct
// device must have produced:
//
//   seed      the run id and a lane index the launch actually has
//   sequence  the lane's restart counter (the seed's low 16 bits) is the one
//             after its previous report: a lane that reports is revived from
//             seed + 1, so a skipped, repeated or foreign counter means the
//             device lost or invented a report
//   iters     the lane's start step plus the iteration count lands inside the
//             launch that reported it
//   weight    HW(x) <= the run's cutoff: the device's distinguished-point test
//   curve     (x, y) are 131-bit field elements on y^2 + xy = x^3 + 1
//
// The curve check is what makes this a data-corruption test and not only a
// bookkeeping one.  A bit flipped anywhere in a lane's state -- a register, a
// shared-memory Frobenius mask, the state in L2 or DRAM, an ALU or
// carry-less-multiplier result -- leaves the point off the curve with
// probability 1 - 2^-131, and the addition law does not bring it back: every
// later step of that trail is off the curve too, and so is the point it
// eventually reports.  So one check per report covers every step of the
// trail behind it, at 0.3 us of host time on the packed arithmetic.  A fault
// that keeps the point on the curve (a wrong branch j, a wrong start point)
// is left to the re-walks: a sample of reports is replayed from its seed on
// fpga/model's golden model, which shares no code with the device, and must
// arrive at the same point.
#pragma once

#include "hostcheck.h"

#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>

namespace ec2k_gpu
{

// On y^2 + xy = x^3 + 1, in the normal basis, on the packed arithmetic's host
// paths (hosttest holds those to the golden model).  In the type-II optimal
// normal basis 1 is the all-ones vector.
inline bool onCurvePacked(const P131 &x, const P131 &y)
{
    using namespace eccPacked131;
    ec2k_fe one;
    ec2k_fe_one(&one);
    const P131 lhs = add131(sqr131(y), mul131(x, y));
    const P131 rhs = add131(mul131(sqr131(x), x), toPacked(one));
    const P131 d = add131(lhs, rhs);
    return (d.v[0] | d.v[1] | d.v[2] | d.v[3] | (d.v[4] & 7u)) == 0;
}

// The per-report checks, and the state they need: for every lane, the restart
// counter its next report must carry and the launch its current trail began
// in.  A lane that fails a check is resynchronised to what it reported, so one
// fault is counted once rather than again at every later report of the lane.
struct ReportChecker {
    enum Fault { OK = 0, SEED, SEQUENCE, ITERS, WEIGHT, CURVE, KINDS };
    static const char *name(int f)
    {
        static const char *const names[KINDS] = {"ok",    "seed",   "sequence",
                                                 "iters", "weight", "curve"};
        return names[f];
    }

    unsigned runId = 0;
    int dpWeight = 0;
    unsigned long long steps = 0;
    std::vector<uint16_t> nextRestart;
    std::vector<uint32_t> startLaunch;
    unsigned long long checked = 0, checkedSteps = 0, faults[KINDS] = {};

    void reset(unsigned run, int weight, unsigned long long stepsPerLaunch, size_t lanes)
    {
        runId = run;
        dpWeight = weight;
        steps = stepsPerLaunch;
        nextRestart.assign(lanes, 0);
        startLaunch.assign(lanes, 0);
        checked = checkedSteps = 0;
        for (auto &f : faults) f = 0;
    }

    unsigned long long totalFaults() const
    {
        unsigned long long n = 0;
        for (int f = SEED; f < KINDS; ++f) n += faults[f];
        return n;
    }

    // `launch` is the launch that reported `r`, counted from 0; its steps are
    // [launch * steps, (launch + 1) * steps).
    Fault check(const DpRecord &r, unsigned launch)
    {
        const unsigned run = unsigned(r.seed >> 48);
        const unsigned long long lane = (r.seed >> 16) & 0xFFFFFFFFull;
        const unsigned restart = unsigned(r.seed & 0xFFFFu);
        Fault f = OK;
        if (run != runId || lane >= nextRestart.size()) {
            f = SEED;
        } else {
            const unsigned long long begin = (unsigned long long)startLaunch[lane] * steps;
            const unsigned long long at = begin + r.iters, lo = (unsigned long long)launch * steps;
            if (restart != nextRestart[lane])
                f = SEQUENCE;
            else if (at < begin || at < lo || at >= lo + steps)
                f = ITERS;
            else if (weightOf(toPacked(feOf(r.x))) > dpWeight)
                f = WEIGHT;
            else if (((r.x[2] | r.y[2]) >> 3) != 0 ||
                     !onCurvePacked(toPacked(feOf(r.x)), toPacked(feOf(r.y))))
                f = CURVE; // bits above the 131st are not a field element either
            // The device revives the lane from seed + 1 at the next launch's base.
            nextRestart[lane] = uint16_t(restart + 1);
            startLaunch[lane] = launch + 1;
        }
        checked++;
        faults[f]++;
        if (f == OK) checkedSteps += r.iters;
        return f;
    }
};

// Re-walks on the golden model, on background threads so the device never
// waits for them.  The model is written for clarity -- about 23 ms for a start
// point and 0.4 ms a step on one core -- so a run re-walks a sample: offer()
// takes a report only while a small queue has room and it is no longer than
// `maxIters`, and finish() drains the queue and joins.  The caller offers the
// longest report within that limit, so that each re-walk covers as many of
// the device's branch selections as its cost allows, not only a start point.
struct Rewalker {
    const HostWalk *walk = nullptr;
    unsigned long long maxIters = 0;
    size_t capacity = 0;
    std::mutex mu;
    std::condition_variable cv;
    std::deque<DpRecord> queue;
    std::vector<std::thread> threads;
    bool closing = false;
    unsigned long long offered = 0, skipped = 0, rewalked = 0, rewalkedSteps = 0, mismatches = 0;

    void start(const HostWalk &w, int workers, unsigned long long cap)
    {
        walk = &w;
        maxIters = cap;
        capacity = size_t(workers) * 4;
        for (int i = 0; i < workers; ++i) threads.emplace_back([this] { run(); });
    }

    void offer(const DpRecord &r)
    {
        std::lock_guard<std::mutex> lock(mu);
        offered++;
        if (threads.empty() || r.iters > maxIters || queue.size() >= capacity) {
            skipped++;
            return;
        }
        queue.push_back(r);
        cv.notify_one();
    }

    void run()
    {
        for (;;) {
            DpRecord r;
            {
                std::unique_lock<std::mutex> lock(mu);
                cv.wait(lock, [this] { return closing || !queue.empty(); });
                if (queue.empty()) return;
                r = queue.front();
                queue.pop_front();
            }
            const bool same = rewalk(*walk, r, nullptr);
            std::lock_guard<std::mutex> lock(mu);
            rewalked++;
            rewalkedSteps += r.iters;
            if (!same) {
                mismatches++;
                fprintf(stderr,
                        "report does not re-walk on the golden model: seed %llu, %llu iterations\n",
                        r.seed, r.iters);
            }
        }
    }

    // Counts while the workers run: re-walked reports, and those that failed.
    void progress(unsigned long long *done, unsigned long long *failed)
    {
        std::lock_guard<std::mutex> lock(mu);
        *done = rewalked;
        *failed = mismatches;
    }

    void finish()
    {
        {
            std::lock_guard<std::mutex> lock(mu);
            closing = true;
        }
        cv.notify_all();
        for (auto &t : threads) t.join();
        threads.clear();
    }

    ~Rewalker()
    {
        if (!threads.empty()) finish();
    }
};

// The rates of a timed run, from its launch boundaries: at[i] seconds after
// the start, done[i] iterations completed by then, at[0] = done[0] = 0.  The
// steady rate runs from the first boundary at or after the warm-up to the
// end; the windows cut that span into consecutive runs of launches at least
// `window` seconds long, a tail shorter than half a window folded into the
// last one.  Two ratios summarise them: the slowest window over the fastest,
// and the last over the first, which is where a card that heats up and
// throttles shows.
struct RateSummary {
    size_t steadyFrom = 0;
    double steady = 0, minOverMax = 0, lastOverFirst = 0;
    std::vector<std::pair<size_t, size_t>> windows;
    std::vector<double> rates;
};

inline RateSummary summarizeRates(const std::vector<double> &at,
                                  const std::vector<unsigned long long> &done, double warmup,
                                  double window)
{
    RateSummary s;
    if (at.size() < 2 || at.size() != done.size()) return s;
    auto rate = [&](size_t i, size_t j) {
        return at[j] > at[i] ? (double)(done[j] - done[i]) / (at[j] - at[i]) : 0.0;
    };
    size_t w0 = 0;
    while (w0 + 1 < at.size() && at[w0] < warmup) ++w0;
    s.steadyFrom = w0;
    s.steady = rate(w0, at.size() - 1);
    for (size_t i = w0; i + 1 < at.size();) {
        size_t j = i + 1;
        while (j + 1 < at.size() && at[j] - at[i] < window) ++j;
        if (at[j] - at[i] < window / 2 && !s.windows.empty()) {
            s.windows.back().second = j;
            break;
        }
        s.windows.emplace_back(i, j);
        i = j;
    }
    double lo = 0, hi = 0;
    for (size_t k = 0; k < s.windows.size(); ++k) {
        const double r = rate(s.windows[k].first, s.windows[k].second);
        s.rates.push_back(r);
        lo = k ? std::min(lo, r) : r;
        hi = k ? std::max(hi, r) : r;
    }
    if (hi > 0) s.minOverMax = lo / hi;
    if (!s.rates.empty() && s.rates.front() > 0) s.lastOverFirst = s.rates.back() / s.rates.front();
    return s;
}

} // namespace ec2k_gpu
