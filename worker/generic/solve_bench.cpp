// Measurement driver for the walk comparison in verification/negation-walk-20261024.
// It is not part of any shipped build. It includes the engine and so runs the
// very same walk_lane / walk_lane_neg code that the CPU engine and the CUDA
// kernel use, with host-only operation counters (ECC_COUNT_OPS).
//
//   g++ -std=c++17 -O2 -x c++ solve_bench.cpp -o solve_bench
//   solve_bench solve MODE JOB LANES ROUND_STEPS DP_BITS SOLVES SEED FIRST LIMIT
//   solve_bench rate  MODE JOB LANES ROUND_STEPS STEPS SEED
//   solve_bench addtime JOB COUNT          (cost of one affine group addition)
//
// MODE 0 is the v2 adding walk (JOB: ECCJOB03), 1 the negation-class walk and
// 2 the same walk without cycle handling (ablation; JOB: ECCJOB04). `solve`
// plants a fresh target Q = dG for every solve index, runs LANES walks in
// rounds of ROUND_STEPS steps against a distinguished-point table, and stops
// at the first collision whose solved scalar equals the planted d. Steps are
// counted in lockstep time: all lanes are assumed to advance together, so the
// count at a collision is the sum over lanes of the steps each had taken at
// the moment of the second distinguished point. JSON lines go to stdout.
#define ECC_COUNT_OPS 1
#define main ecc_engine_main
#include "engine.cu"
#undef main

#include <chrono>
#include <cinttypes>
#include <cstdio>
#include <map>
#include <unordered_map>

namespace {
using namespace ecc;
using Clock = std::chrono::steady_clock;

struct Rng {
    std::uint64_t s;
    std::uint64_t next() {
        std::uint64_t z = (s += 0x9e3779b97f4a7c15ULL);
        z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
        z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
        return z ^ (z >> 31);
    }
};

UInt384 random_below(Rng& rng, const UInt384& n, bool nonzero) {
    const int bits = bit_length(n);
    for (;;) {
        UInt384 r{};
        for (int i = 0; i < WORDS; ++i) r.w[i] = std::uint32_t(rng.next());
        for (int i = bits; i < WORDS * 32; ++i) r.w[i / 32] &= ~(1U << (i % 32));
        if (compare(r, n) < 0 && (!nonzero || !is_zero(r))) return r;
    }
}

Point mul(const Job& job, const Point& p, const UInt384& k) {
    Point r = identity();
    for (int i = bit_length(k) - 1; i >= 0; --i) {
        Point t{};
        if (!point_add(r, r, job, t)) throw std::runtime_error("doubling failed");
        r = t;
        if (bit(k, i)) {
            if (!point_add(r, p, job, t)) throw std::runtime_error("addition failed");
            r = t;
        }
    }
    return r;
}

Point add(const Job& job, const Point& p, const Point& q) {
    Point r{};
    if (!point_add(p, q, job, r)) throw std::runtime_error("addition failed");
    return r;
}

std::string hex(const UInt384& v) {
    char buf[WORDS * 8 + 1];
    int pos = 0;
    bool started = false;
    for (int i = WORDS - 1; i >= 0; --i) {
        if (!started && !v.w[i] && i) continue;
        pos += std::snprintf(buf + pos, sizeof buf - pos, started ? "%08x" : "%x", v.w[i]);
        started = true;
    }
    return std::string(buf, pos);
}

struct Entry { UInt384 a, b; };

struct Lane {
    State state{};
    std::uint64_t base = 0;        // steps of finished trails
    std::uint64_t trail_cap_restarts = 0;
};


// A fresh trail: R = aG + bQ, as a class representative for the negation walk.
State fresh_state(const Job& job, Rng& rng, const Point& G, const Point& Q,
                  std::uint64_t trail, bool b_zero) {
    State s{};
    for (;;) {
        s.a = random_below(rng, job.n, false);
        s.b = b_zero ? UInt384{} : random_below(rng, job.n, true);
        Point r = add(job, mul(job, G, s.a), mul(job, Q, s.b));
        if (r.infinity) continue;
        if (job.reserved && compare(r.y, negated_y(r.x, r.y, job)) > 0) {
            r.y = negated_y(r.x, r.y, job);
            s.a = negate_scalar(s.a, job);
            s.b = negate_scalar(s.b, job);
        }
        s.x = r.x; s.y = r.y; s.infinity = 0; s.active = 1;
        s.trail_id = trail; s.steps = 0;
        return s;
    }
}

int run_solve(Job job, unsigned mode, unsigned lanes, unsigned round_steps, unsigned dp_bits,
              unsigned solves, std::uint64_t seed, unsigned first, std::uint64_t limit) {
    job.lanes = lanes; job.steps_per_round = round_steps; job.dp_bits = dp_bits;
    job.reserved = mode ? mode : 0;
    const Point G{job.gx, job.gy, 0};
    const std::uint64_t cap = 32ULL << dp_bits;
    for (unsigned i = first; i < first + solves; ++i) {
        Rng planted{seed * 1000003ULL + i * 2 + 1}, starts{seed * 7919ULL + i * 2};
        const UInt384 d = random_below(planted, job.n, true);
        const Point Q = mul(job, G, d);
        job.qx = Q.x; job.qy = Q.y;
        std::vector<Lane> pool(lanes);
        std::uint64_t next_trail = 0;
        for (auto& lane : pool) lane.state = fresh_state(job, starts, G, Q, next_trail++, false);
        std::unordered_map<std::string, Entry> table;
        std::uint64_t rounds = 0, dps = 0, duplicates = 0, degenerate = 0, caps = 0, phys = 0;
        std::uint64_t walk_ns = 0;
        const std::uint64_t adds0 = g_point_adds, look0 = g_lookaheads, esc0 = g_escapes;
        bool done = false, ok = false, gave_up = false;
        std::uint64_t lockstep = 0;
        std::string wrong;
        while (!done) {
            struct Event { std::uint64_t s; unsigned lane; State st; };
            std::vector<Event> events;
            std::vector<std::uint64_t> start_cum(lanes), taken(lanes);
            for (unsigned l = 0; l < lanes; ++l) {
                Lane& lane = pool[l];
                start_cum[l] = lane.base + lane.state.steps;
                const std::uint64_t before = lane.state.steps;
                const auto t0 = Clock::now();
                const int rc = walk_lane_any(job, lane.state, round_steps);
                walk_ns += std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - t0).count();
                if (rc) throw std::runtime_error("walk error " + std::to_string(rc));
                taken[l] = lane.state.steps - before;
                phys += taken[l];
                if (!lane.state.active && !lane.state.infinity)
                    events.push_back({taken[l], l, lane.state});
            }
            std::stable_sort(events.begin(), events.end(),
                             [](const Event& x, const Event& y) { return x.s < y.s; });
            for (const Event& e : events) {
                ++dps;
                std::string key(reinterpret_cast<const char*>(&e.st.x), sizeof(UInt384));
                key.append(reinterpret_cast<const char*>(&e.st.y), sizeof(UInt384));
                auto found = table.find(key);
                if (found == table.end()) { table.emplace(key, Entry{e.st.a, e.st.b}); continue; }
                const Entry& old = found->second;
                if (equal(old.a, e.st.a) && equal(old.b, e.st.b)) { ++duplicates; continue; }
                const UInt384 denominator = subtract_mod(e.st.b, old.b, job.n);
                if (is_zero(denominator)) { ++degenerate; continue; }
                UInt384 inverse{};
                if (!inverse_mod(denominator, job.n, inverse)) { ++degenerate; continue; }
                const UInt384 value = multiply_mod(subtract_mod(old.a, e.st.a, job.n), inverse, job.n);
                ok = equal(value, d);
                if (!ok) wrong = hex(value);
                done = true;
                for (unsigned l = 0; l < lanes; ++l)
                    lockstep += start_cum[l] + std::min<std::uint64_t>(e.s, taken[l]);
                break;
            }
            if (!done) {
                for (unsigned l = 0; l < lanes; ++l) {
                    Lane& lane = pool[l];
                    const bool capped = lane.state.active && lane.state.steps >= cap;
                    if (!lane.state.active || capped) {
                        if (capped) ++caps;
                        lane.base += lane.state.steps;
                        lane.state = fresh_state(job, starts, G, Q, next_trail++, false);
                    }
                }
                ++rounds;
                if (phys > limit) { gave_up = true; done = true; }
            }
        }
        std::printf("{\"solve\":%u,\"mode\":%u,\"ok\":%d,\"gave_up\":%d,\"steps\":%" PRIu64
                    ",\"phys_steps\":%" PRIu64 ",\"rounds\":%" PRIu64 ",\"dps\":%" PRIu64
                    ",\"duplicates\":%" PRIu64 ",\"degenerate\":%" PRIu64 ",\"cap_restarts\":%" PRIu64
                    ",\"walk_ns\":%" PRIu64 ",\"adds\":%" PRIu64 ",\"lookaheads\":%" PRIu64
                    ",\"escapes\":%" PRIu64 ",\"d\":\"%s\",\"wrong\":\"%s\"}\n",
                    i, mode, ok ? 1 : 0, gave_up ? 1 : 0, lockstep, phys, rounds, dps, duplicates,
                    degenerate, caps, walk_ns,
                    mode ? g_point_adds - adds0 : phys, g_lookaheads - look0, g_escapes - esc0,
                    hex(d).c_str(), wrong.c_str());
        std::fflush(stdout);
    }
    return 0;
}

int run_rate(Job job, unsigned mode, unsigned lanes, unsigned round_steps,
             std::uint64_t steps, std::uint64_t seed) {
    job.lanes = lanes; job.steps_per_round = round_steps; job.dp_bits = 40;
    job.reserved = mode;
    const Point G{job.gx, job.gy, 0}, Q{job.qx, job.qy, 0};
    Rng rng{seed};
    std::vector<State> states(lanes);
    for (unsigned l = 0; l < lanes; ++l) states[l] = fresh_state(job, rng, G, Q, l, true);
    const std::uint64_t adds0 = g_point_adds, look0 = g_lookaheads, esc0 = g_escapes;
    std::uint64_t phys = 0, ns = 0, trapped_rounds = 0, max_round_escapes = 0;
    for (std::uint64_t done = 0; done < steps; done += round_steps) {
        for (unsigned l = 0; l < lanes; ++l) {
            const std::uint64_t before = states[l].steps, escapes_before = g_escapes;
            const auto t0 = Clock::now();
            const int rc = walk_lane_any(job, states[l], round_steps);
            ns += std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - t0).count();
            if (rc) throw std::runtime_error("walk error " + std::to_string(rc));
            phys += states[l].steps - before;
            // A lane that escapes more than 12 times in one 256-step round is trapped.
            const std::uint64_t escapes_now = g_escapes - escapes_before;
            max_round_escapes = std::max(max_round_escapes, escapes_now);
            trapped_rounds += escapes_now > 12;
        }
    }
    std::printf("{\"mode\":%u,\"lanes\":%u,\"round_steps\":%u,\"steps\":%" PRIu64 ",\"ns\":%" PRIu64
                ",\"ns_per_step\":%.3f,\"adds\":%" PRIu64 ",\"lookaheads\":%" PRIu64
                ",\"escapes\":%" PRIu64 ",\"trapped_lane_rounds\":%" PRIu64
                ",\"max_lane_round_escapes\":%" PRIu64 "}\n",
                mode, lanes, round_steps, phys, ns, double(ns) / double(phys),
                mode ? g_point_adds - adds0 : phys, g_lookaheads - look0, g_escapes - esc0,
                trapped_rounds, max_round_escapes);
    return 0;
}
// Time of COUNT dependent affine additions of the job's jump points: the GAE
// (group addition equivalent) unit used to price a walk's native overhead.
int run_addtime(const Job& job, std::uint64_t count) {
    Point p{job.gx, job.gy, 0};
    std::uint64_t fingerprint = 0;
    const auto t0 = Clock::now();
    for (std::uint64_t i = 0; i < count; ++i) {
        const Jump& jump = job.jumps[i & 31U];
        const Point delta{jump.x, jump.y, 0};
        Point next{};
        if (!point_add(p, delta, job, next)) throw std::runtime_error("addition failed");
        p = next;
        fingerprint ^= p.x.w[0];
    }
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - t0).count();
    std::printf("{\"adds\":%" PRIu64 ",\"ns\":%" PRId64 ",\"ns_per_add\":%.3f,\"fingerprint\":%" PRIu64 "}\n",
                count, static_cast<std::int64_t>(ns), double(ns) / double(count), fingerprint);
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc >= 11 && std::string(argv[1]) == "solve") {
            return run_solve(load_job(argv[3]), unsigned(std::stoul(argv[2])),
                             unsigned(std::stoul(argv[4])), unsigned(std::stoul(argv[5])),
                             unsigned(std::stoul(argv[6])), unsigned(std::stoul(argv[7])),
                             std::stoull(argv[8]), unsigned(std::stoul(argv[9])),
                             std::stoull(argv[10]));
        }
        if (argc >= 8 && std::string(argv[1]) == "rate") {
            return run_rate(load_job(argv[3]), unsigned(std::stoul(argv[2])),
                            unsigned(std::stoul(argv[4])), unsigned(std::stoul(argv[5])),
                            std::stoull(argv[6]), std::stoull(argv[7]));
        }
        if (argc >= 4 && std::string(argv[1]) == "addtime")
            return run_addtime(load_job(argv[2]), std::stoull(argv[3]));
        std::fprintf(stderr, "usage: see the header comment of solve_bench.cpp\n");
        return 2;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "solve_bench: %s\n", e.what());
        return 1;
    }
}
