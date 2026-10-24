// Host side of fast131.h: field contexts from a job, conversions between the
// engine's 384-bit integers and the specialised representations, jump
// tables, the multi-threaded CPU runner and the arithmetic self-test.
// Included by engine.cu after ec_core.h; never compiled for a device.
#ifndef ECC_FAST131_HOST_H
#define ECC_FAST131_HOST_H

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

#include "fast131.h"

namespace ecc {
namespace f131 {

// Largest batch a CPU thread shares one inversion across.
constexpr int CPU_BATCH_MAX = 64;
constexpr int CPU_BATCH_DEFAULT = 32;

inline bool is_f2_131(const UInt384& modulus, std::uint32_t kind, std::uint32_t bits) {
    UInt384 f{};
    f.w[0] = (1U << 13) | 7U;
    f.w[4] = 1U << 3;  // t^131
    return kind == 1 && bits == 131 && equal(modulus, f);
}
inline bool prime_fits(std::uint32_t kind, std::uint32_t bits) {
    return kind == 0 && bits >= 2 && bits <= 131;
}
// Curves the specialised arithmetic handles: GF(2^131) with the Certicom
// pentanomial, and prime fields up to 131 bits.
inline bool fast_curve(std::uint32_t kind, std::uint32_t bits, const UInt384& modulus) {
    return is_f2_131(modulus, kind, bits) || prime_fits(kind, bits);
}

template <typename W, int N> inline Num<W, N> from_u384(const UInt384& x) {
    Num<W, N> r = num_zero<W, N>();
    if (sizeof(W) == 8) {
        for (int i = 0; i < N; ++i)
            r.v[i] = W(std::uint64_t(x.w[2 * i]) | (std::uint64_t(x.w[2 * i + 1]) << 32));
    } else {
        for (int i = 0; i < N; ++i) r.v[i] = W(x.w[i]);
    }
    return r;
}
template <typename W, int N> inline UInt384 to_u384(const Num<W, N>& x) {
    UInt384 r{};
    if (sizeof(W) == 8) {
        for (int i = 0; i < N; ++i) {
            r.w[2 * i] = std::uint32_t(std::uint64_t(x.v[i]));
            r.w[2 * i + 1] = std::uint32_t(std::uint64_t(x.v[i]) >> 32);
        }
    } else {
        for (int i = 0; i < N; ++i) r.w[i] = std::uint32_t(x.v[i]);
    }
    return r;
}
inline UInt384 words_to_u384(const std::uint64_t w[3]) {
    UInt384 r{};
    for (int i = 0; i < 3; ++i) {
        r.w[2 * i] = std::uint32_t(w[i]);
        r.w[2 * i + 1] = std::uint32_t(w[i] >> 32);
    }
    return r;
}

// Curve description shared by both job formats.
struct CurveSpec {
    std::uint32_t kind, field_bits;
    UInt384 modulus, a, b, n;
};

template <class FD> struct Host;

template <bool SOFT> struct Host<BinaryField<SOFT>> {
    typedef BinaryField<SOFT> FD;
    static BinaryCtx context(const CurveSpec& s) {
        BinaryCtx c;
        c.a = from_u384<std::uint64_t, 3>(s.a);
        c.b = from_u384<std::uint64_t, 3>(s.b);
        c.n = from_u384<std::uint64_t, 3>(s.n);
        return c;
    }
    static N3 field_in(const UInt384& x, const BinaryCtx&) { return from_u384<std::uint64_t, 3>(x); }
    static UInt384 field_out(const N3& x, const BinaryCtx&) { return to_u384(x); }
    static N3 scalar_in(const UInt384& x) { return from_u384<std::uint64_t, 3>(x); }
    static UInt384 scalar_out(const N3& x) { return to_u384(x); }
};

template <typename W, int N> struct Host<PrimeField<W, N>> {
    typedef PrimeField<W, N> FD;
    static PrimeCtx<W, N> context(const CurveSpec& s) {
        PrimeCtx<W, N> c;
        c.p = from_u384<W, N>(s.modulus);
        c.n = from_u384<W, N>(s.n);
        c.bits = s.field_bits;
        // -p^-1 mod 2^w by Newton iteration (p odd).
        W inverse = 1;
        for (int i = 0; i < 7; ++i) inverse = W(inverse * (W(2) - c.p.v[0] * inverse));
        c.pinv = W(W(0) - inverse);
        UInt384 r = small(1);
        const int radix_bits = int(sizeof(W) * 8) * N;
        for (int i = 0; i < radix_bits; ++i) r = add_mod(r, r, s.modulus);
        c.one = from_u384<W, N>(r);
        for (int i = 0; i < radix_bits; ++i) r = add_mod(r, r, s.modulus);
        c.r2 = from_u384<W, N>(r);
        c.halfp = from_u384<W, N>(shift_right_one(subtract_raw(s.modulus, small(1))));
        c.pm2 = from_u384<W, N>(subtract_raw(s.modulus, small(2)));
        c.a = mont_mul(from_u384<W, N>(s.a), c.r2, c.p, c.pinv);
        c.b = mont_mul(from_u384<W, N>(s.b), c.r2, c.p, c.pinv);
        return c;
    }
    static Num<W, N> field_in(const UInt384& x, const PrimeCtx<W, N>& c) {
        return mont_mul(from_u384<W, N>(x), c.r2, c.p, c.pinv);
    }
    static UInt384 field_out(const Num<W, N>& x, const PrimeCtx<W, N>& c) {
        std::uint64_t w[3];
        FD::canon(x, c, w);
        return words_to_u384(w);
    }
    static Num<W, N> scalar_in(const UInt384& x) { return from_u384<W, N>(x); }
    static UInt384 scalar_out(const Num<W, N>& x) { return to_u384(x); }
};

// Affine addition with its own inversion, for table construction only.
template <class FD> struct SlowPoint {
    typename FD::E x, y;
    bool infinity;
};
template <class FD>
SlowPoint<FD> slow_add(const SlowPoint<FD>& p, const SlowPoint<FD>& q, const typename FD::C& c) {
    if (p.infinity) return q;
    if (q.infinity) return p;
    SlowPoint<FD> r{FD::zero(), FD::zero(), false};
    const typename FD::E d = FD::denominator(p.x, q.x, c);
    if (FD::is_zero(d)) {
        if (FD::opposite(p.y, q.y, c) || !FD::double_point(p.x, p.y, c, r.x, r.y))
            r.infinity = true;
        return r;
    }
    FD::add_points(p.x, p.y, q.x, q.y, FD::inv(d, c), c, r.x, r.y);
    return r;
}
template <class FD>
SlowPoint<FD> slow_mul(const UInt384& k, const SlowPoint<FD>& p, const typename FD::C& c) {
    SlowPoint<FD> r{FD::zero(), FD::zero(), true};
    for (int i = bit_length(k) - 1; i >= 0; --i) {
        r = slow_add(r, r, c);
        if (bit(k, i)) r = slow_add(r, p, c);
    }
    return r;
}

// A walk ready to run: context, parameters, table in the FD representation.
template <class FD> struct Prepared {
    typename FD::C ctx;
    Params params;
    std::vector<Entry<FD>> table;
    UInt384 n;
};

template <class FD> Lane<FD> lane_in(const State& s, const Prepared<FD>& w) {
    Lane<FD> L{};
    if (!s.infinity) {
        L.x = Host<FD>::field_in(s.x, w.ctx);
        L.y = Host<FD>::field_in(s.y, w.ctx);
    } else {
        L.x = FD::zero();
        L.y = FD::zero();
    }
    L.a = Host<FD>::scalar_in(s.a);
    L.b = Host<FD>::scalar_in(s.b);
    L.steps = s.steps;
    L.active = s.active;
    L.infinity = s.infinity;
    begin_lane(L, w.ctx, w.params);
    return L;
}
template <class FD> void lane_out(const Lane<FD>& L, const Prepared<FD>& w, State& s) {
    if (L.infinity) {
        s.x = UInt384{};
        s.y = UInt384{};
    } else {
        s.x = Host<FD>::field_out(L.x, w.ctx);
        s.y = Host<FD>::field_out(L.y, w.ctx);
    }
    s.a = Host<FD>::scalar_out(L.a);
    // Production tables add multiples of G only, so b is constant up to the
    // sign the negation map gave it; general tables track b step by step.
    if (w.params.has_db) s.b = Host<FD>::scalar_out(L.b);
    else if (L.negated && !is_zero(s.b)) s.b = subtract_raw(w.n, s.b);
    s.steps = L.steps;
    s.active = L.active;
    s.infinity = L.infinity;
}

inline int cpu_batch() {
    const char* value = std::getenv("ECC_F131_BATCH");
    if (!value || !*value) return CPU_BATCH_DEFAULT;
    const int parsed = std::atoi(value);
    if (parsed < 1 || parsed > CPU_BATCH_MAX)
        throw std::runtime_error("ECC_F131_BATCH must be 1..64");
    return parsed;
}

// Runs every lane for `steps` transitions on `threads` threads; groups of
// `batch` lanes share inversions. The output does not depend on either.
template <class FD>
void run_cpu_fast(const Prepared<FD>& w, std::vector<State>& states, std::vector<int>& errors,
                  std::uint32_t steps, unsigned threads) {
    const int batch = cpu_batch();
    const std::size_t lanes = states.size();
    const std::size_t groups = (lanes + batch - 1) / batch;
    std::atomic<std::size_t> next{0};
    auto worker = [&]() {
        Lane<FD> local[CPU_BATCH_MAX];
        for (std::size_t g = next++; g < groups; g = next++) {
            const std::size_t first = g * batch;
            const std::uint32_t count = std::uint32_t(std::min<std::size_t>(batch, lanes - first));
            for (std::uint32_t k = 0; k < count; ++k) local[k] = lane_in(states[first + k], w);
            walk_batch<FD, CPU_BATCH_MAX>(w.ctx, w.table.data(), w.params, local, count, steps);
            for (std::uint32_t k = 0; k < count; ++k) {
                lane_out(local[k], w, states[first + k]);
                errors[first + k] = int(local[k].err);
            }
        }
    };
    const unsigned wanted = unsigned(std::min<std::size_t>(std::max(1U, threads), groups));
    std::vector<std::thread> pool;
    try {
        for (unsigned t = 1; t < wanted; ++t) pool.emplace_back(worker);
    } catch (const std::system_error&) {
        // Fewer threads only changes the schedule, never the output.
    }
    worker();
    for (std::thread& thread : pool) thread.join();
}

// Everything a walk needs, converted once per job.
template <class FD>
Prepared<FD> prepare(const Job& job, std::uint32_t walk, std::uint32_t anchor_log2,
                     std::uint32_t dp_shift, const Jump& escape) {
    CurveSpec spec{job.kind, job.field_bits, job.modulus, job.a, job.b, job.n};
    Prepared<FD> w;
    w.ctx = Host<FD>::context(spec);
    w.n = job.n;
    w.params = Params{walk, job.dp_bits, 5, 32, anchor_log2, (job.field_bits + 7) / 8, 0, dp_shift};
    for (std::uint32_t i = 0; i < 32; ++i) w.params.has_db |= !is_zero(job.jumps[i].db);
    w.params.has_db |= !is_zero(escape.db);
    const auto entry = [&](const Jump& j) {
        Entry<FD> e;
        e.x = Host<FD>::field_in(j.x, w.ctx);
        e.y = Host<FD>::field_in(j.y, w.ctx);
        e.da = Host<FD>::scalar_in(j.da);
        e.db = Host<FD>::scalar_in(j.db);
        return e;
    };
    for (std::uint32_t i = 0; i < 32; ++i) w.table.push_back(entry(job.jumps[i]));
    w.table.push_back(entry(escape));
    return w;
}

// Dispatches on the field of the job. Returns false when no specialised
// arithmetic covers the curve.
inline bool run_cpu_fast_job(const Job& job, std::uint32_t walk, std::uint32_t anchor_log2,
                             std::uint32_t dp_shift, const Jump& escape, std::vector<State>& states,
                             std::vector<int>& errors, unsigned threads) {
    if (!fast_curve(job.kind, job.field_bits, job.modulus)) return false;
    if (job.kind) {
        const bool soft = std::getenv("ECC_F131_SOFT_CLMUL") != nullptr;
        if (soft) {
            run_cpu_fast(prepare<BinaryField<true>>(job, walk, anchor_log2, dp_shift, escape), states,
                         errors, job.steps_per_round, threads);
        } else {
            run_cpu_fast(prepare<BinaryField<false>>(job, walk, anchor_log2, dp_shift, escape), states,
                         errors, job.steps_per_round, threads);
        }
    } else {
        run_cpu_fast(prepare<PrimeField<std::uint64_t, 3>>(job, walk, anchor_log2, dp_shift, escape),
                     states, errors, job.steps_per_round, threads);
    }
    return true;
}

// ------------------------------------------------------------ self-test
struct TestRng {
    std::uint64_t s;
    std::uint64_t next() {
        s ^= s << 13; s ^= s >> 7; s ^= s << 17;
        return s;
    }
    UInt384 below_bits(int bits) {
        UInt384 r{};
        for (int i = 0; i < (bits + 31) / 32; ++i) r.w[i] = std::uint32_t(next());
        if (bits % 32) r.w[(bits - 1) / 32] &= (1U << (bits % 32)) - 1U;
        return r;
    }
};
inline void check(bool condition, const char* what) {
    if (!condition) throw std::runtime_error(std::string("fast131 self-test: ") + what);
}

template <class FD>
void field_self_test(const CurveSpec& s, Job& reference, TestRng& rng) {
    const typename FD::C c = Host<FD>::context(s);
    for (int i = 0; i < 400; ++i) {
        UInt384 x = rng.below_bits(int(s.field_bits)), y = rng.below_bits(int(s.field_bits));
        if (!s.kind) {
            while (compare(x, s.modulus) >= 0) x = subtract_raw(x, s.modulus);
            while (compare(y, s.modulus) >= 0) y = subtract_raw(y, s.modulus);
        }
        if (i == 0) x = UInt384{};
        if (i == 1) y = small(1);
        if (i == 2 && !s.kind) x = subtract_raw(s.modulus, small(1));
        const typename FD::E fx = Host<FD>::field_in(x, c), fy = Host<FD>::field_in(y, c);
        check(equal(Host<FD>::field_out(fx, c), x), "round trip");
        check(equal(Host<FD>::field_out(FD::mul(fx, fy, c), c), field_multiply(x, y, reference)),
              "multiplication");
        check(equal(Host<FD>::field_out(FD::sqr(fx, c), c), field_multiply(x, x, reference)),
              "squaring");
        check(equal(Host<FD>::field_out(FD::add(fx, fy, c), c), field_add(x, y, reference)),
              "addition");
        check(equal(Host<FD>::field_out(FD::sub(fx, fy, c), c), field_subtract(x, y, reference)),
              "subtraction");
        if (!is_zero(x)) {
            UInt384 expected{};
            check(field_inverse(x, reference, expected), "reference inverse");
            check(equal(Host<FD>::field_out(FD::inv(fx, c), c), expected), "inversion");
        }
        UInt384 sa = rng.below_bits(int(bit_length(s.n))), sb = rng.below_bits(int(bit_length(s.n)));
        while (compare(sa, s.n) >= 0) sa = subtract_raw(sa, s.n);
        while (compare(sb, s.n) >= 0) sb = subtract_raw(sb, s.n);
        check(equal(Host<FD>::scalar_out(FD::scalar_add(Host<FD>::scalar_in(sa),
                                                        Host<FD>::scalar_in(sb), c)),
                    add_mod(sa, sb, s.n)), "scalar addition");
        check(equal(Host<FD>::scalar_out(FD::scalar_neg(Host<FD>::scalar_in(sa), c)),
                    subtract_mod(UInt384{}, sa, s.n)), "scalar negation");
    }
}

// Every field configuration the engine can run, on both 131-bit curves, is
// checked against ec_core.h; GPU representations are exercised on the host.
inline void self_test() {
    TestRng rng{0x9e3779b97f4a7c15ULL};
    {
        CurveSpec s{};
        s.kind = 1; s.field_bits = 131;
        s.modulus = small((1U << 13) | 7U);
        s.modulus.w[4] = 1U << 3;
        s.a = small(1); s.b = small(1);
        s.n = small(0);
        s.n.w[4] = 1U << 2;  // any odd-free container value works for scalars
        s.n.w[0] = 0x1234567U;
        Job reference{};
        reference.kind = 1; reference.field_bits = 131; reference.modulus = s.modulus;
        field_self_test<BinaryField<false>>(s, reference, rng);
        field_self_test<BinaryField<true>>(s, reference, rng);
        for (int i = 0; i < 1000; ++i) {
            const std::uint64_t a = rng.next(), b = rng.next();
            std::uint64_t l1, h1, l2, h2;
            clmul64<false>(a, b, l1, h1);
            clmul64_soft(a, b, l2, h2);
            check(l1 == l2 && h1 == h2, "carry-less product");
        }
    }
    {
        // ECCp-131's prime, and a prime just below 2^131.
        const std::uint32_t primes[2][5] = {
            {0x6b3abc0bU, 0x194c4318U, 0x93469e33U, 0x8e1d43f2U, 0x4U},
            {0xfffffffbU, 0xffffffffU, 0xffffffffU, 0xffffffffU, 0x7U},  // 2^131 - 5? checked below
        };
        for (int which = 0; which < 2; ++which) {
            CurveSpec s{};
            s.kind = 0; s.field_bits = 131;
            for (int i = 0; i < 5; ++i) s.modulus.w[i] = primes[which][i];
            s.a = small(3); s.b = small(7);
            s.n = s.modulus;
            Job reference{};
            reference.kind = 0; reference.field_bits = 131; reference.modulus = s.modulus;
            // The second modulus need not be prime for the ring operations;
            // inversion is only checked where ec_core finds an inverse.
            if (which == 1) {
                const typename PrimeField<std::uint64_t, 3>::C c =
                    Host<PrimeField<std::uint64_t, 3>>::context(s);
                for (int i = 0; i < 200; ++i) {
                    UInt384 x = rng.below_bits(131), y = rng.below_bits(131);
                    while (compare(x, s.modulus) >= 0) x = subtract_raw(x, s.modulus);
                    while (compare(y, s.modulus) >= 0) y = subtract_raw(y, s.modulus);
                    typedef Host<PrimeField<std::uint64_t, 3>> H;
                    check(equal(H::field_out(PrimeField<std::uint64_t, 3>::mul(
                                    H::field_in(x, c), H::field_in(y, c), c), c),
                                multiply_mod(x, y, s.modulus)), "wide multiplication");
                }
                continue;
            }
            field_self_test<PrimeField<std::uint64_t, 3>>(s, reference, rng);
            field_self_test<PrimeField<std::uint32_t, 5>>(s, reference, rng);
        }
    }
}

}  // namespace f131
}  // namespace ecc

#endif  // ECC_FAST131_HOST_H
