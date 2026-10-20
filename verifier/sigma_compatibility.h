#pragma once
#include "curveparams.h"
#include "solver.h"
#include <array>
#include <cstdint>
#include <string>

namespace sigma_compat {
using Record = std::array<uint8_t, 32>;
inline uint64_t word(const uint8_t *p) {
    uint64_t value = 0;
    for (int i = 0; i < 8; ++i) value |= uint64_t(p[i]) << (8 * i);
    return value;
}
template<class Cfg> Record encode(const typename Solver<Cfg>::WalkResult &r) {
    Record out{};
    const auto x = Ref<Cfg>::canonical(r.endPoint.x);
    uint64_t words[4] = {r.seed, x.v[0], x.v[1], x.v[2]};
    for (int w = 0; w < 4; ++w)
        for (int b = 0; b < 8; ++b) out[w * 8 + b] = uint8_t(words[w] >> (8 * b));
    return out;
}
template<class Cfg> void coefficients(const Solver<Cfg> &s, typename Solver<Cfg>::WalkResult &r) {
    r.b = s.multiplier(r.counts);
    r.a = mod_mul(r.b, r.alpha0, s.ell);
}
// Result codes: 0 reached; 2 wrong first endpoint; 3 bounded/incomplete.
// weight=0 means the original reporting threshold was not recorded.
template<class Cfg> int replay(const Solver<Cfg> &s, const Record &record, int weight,
                              typename Solver<Cfg>::WalkResult &out) {
    using R = Ref<Cfg>;
    out = {};
    out.seed = word(record.data());
    out.startPt = R::startPoint(out.seed, s.basis, s.target, &out.alpha0, s.ell, s.spow);
    auto p = out.startPt;
    for (uint64_t i = 0; i <= s.maxIters; ++i) {
        if (p.inf) return 3;
        const int hw = R::weight(p.x);
        if ((weight && hw <= weight) || (!weight && hw <= 34)) {
            out.endPoint = p;
            out.iters = i;
            if (encode<Cfg>(out) == record) {
                out.ok = true;
                coefficients(s, out);
                return 0;
            }
            if (weight) return 2;
        }
        if (i == s.maxIters) break;
        ++out.counts[R::jOf(hw) - 3];
        p = R::step(p, hw);
    }
    return 3;
}
// Continue a verified result without restarting or changing its seed. Keep
// the witness counters so the extended point retains its aP+bQ relation.
template<class Cfg> int extend(const Solver<Cfg> &s, typename Solver<Cfg>::WalkResult &r,
                              int targetWeight, uint64_t maxExtra) {
    using R = Ref<Cfg>;
    if (!r.ok) return 3;
    r.ok = false;
    for (uint64_t i = 0; i <= maxExtra; ++i) {
        if (r.endPoint.inf) return 3;
        const int hw = R::weight(r.endPoint.x);
        if (hw <= targetWeight) {
            coefficients(s, r);
            r.ok = true;
            return 0;
        }
        if (i == maxExtra || r.iters >= s.maxIters) break;
        ++r.counts[R::jOf(hw) - 3];
        r.endPoint = R::step(r.endPoint, hw);
        ++r.iters;
    }
    return 3;
}
inline std::string hex(const Record &r) {
    const char *digits = "0123456789abcdef";
    std::string text;
    for (auto b : r) { text += digits[b >> 4]; text += digits[b & 15]; }
    return text;
}
} // namespace sigma_compat
