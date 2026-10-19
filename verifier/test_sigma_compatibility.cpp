// Reverse-direction experiment: old rare endpoints reused by a denser reporter.
// GF(2^23) thresholds 6/8/10 model legacy Hamming-weight DP30/DP32/DP34.
#include "sigma_compatibility.h"
#include <cstdio>
#include <map>
#include <string>

using S = Solver<CfgF23>;
using R = Ref<CfgF23>;
static unsigned long long key(const R::Point &p) { return R::canonical(p.x).v[0]; }

int main() {
    unsigned archived = 0, direct = 0, bridged = 0, extra = 0, earlier = 0, guards = 0;
    for (int oldThreshold : {6, 8}) {
        for (unsigned target = 0; target < 4; ++target) {
            S oldWalk, newWalk;
            auto p = R::make(R::fromLimbs(eccF23::PX), R::fromLimbs(eccF23::PY));
            auto expected = u192_from(12345 + 7919 * target);
            auto q = R::scalarMul(p, expected);
            oldWalk.setup(eccF23::PX, eccF23::PY, q.x.v, q.y.v,
                          eccF23::ELL_DEC, eccF23::S_DEC, oldThreshold, 20000);
            newWalk.setup(eccF23::PX, eccF23::PY, q.x.v, q.y.v,
                          eccF23::ELL_DEC, eccF23::S_DEC, 10, 20000);
            std::string why;
            if (!oldWalk.checkSetup(&why) || !newWalk.checkSetup(&why)) return 1;
            std::map<unsigned long long, S::WalkResult> archive;
            for (unsigned long long seed = 1; seed <= 128; ++seed) {
                auto old = oldWalk.rewalk(seed);
                if (!old.ok) { ++guards; continue; }
                if (R::weight(old.endPoint.x) > 10) return 2;
                const auto record = sigma_compat::encode<CfgF23>(old);
                S::WalkResult checked;
                if (sigma_compat::replay(oldWalk, record, oldThreshold, checked) != 0 ||
                    !R::eq(checked.endPoint, old.endPoint)) return 8;
                if (sigma_compat::replay(oldWalk, record, 0, checked) != 0 ||
                    !R::eq(checked.endPoint, old.endPoint)) return 9;
                auto corrupt = record;
                corrupt[8] ^= 1;
                if (sigma_compat::replay(oldWalk, corrupt, oldThreshold, checked) != 2) return 10;
                archive.emplace(key(old.endPoint), old);
                auto firstNew = newWalk.rewalk(seed);
                if (!firstNew.ok || firstNew.iters > old.iters) return 3;
                if (firstNew.iters < old.iters) {
                    ++earlier;
                    if (sigma_compat::replay(newWalk, record, 10, checked) != 2) return 11;
                }
            }
            archived += archive.size();
            auto match = [&](const S::WalkResult &result) {
                auto it = archive.find(key(result.endPoint));
                if (it == archive.end()) return false;
                U192 recovered;
                if (!oldWalk.solve(result, it->second, &recovered, &why)) {
                    if (why != "degenerate collision (identical walk)") std::exit(4);
                    return false;
                }
                if (!u192_is_zero(mod_sub(recovered, expected, oldWalk.ell))) std::exit(5);
                return true;
            };
            for (unsigned long long seed = 10000; seed < 10256; ++seed) {
                auto first = newWalk.rewalk(seed);
                if (!first.ok) { ++guards; continue; }
                bool directHit = match(first);
                if (directHit) ++direct;
                // Continuing from this point to the original threshold is
                // identical to rewalking this seed with that threshold.
                auto continued = oldWalk.rewalk(seed);
                if (!continued.ok) { ++guards; continue; }
                auto extended = first;
                if (sigma_compat::extend(oldWalk, extended, oldThreshold, 20000) != 0) return 12;
                if (extended.iters != continued.iters || !R::eq(extended.endPoint, continued.endPoint) ||
                    !u192_is_zero(mod_sub(extended.a, continued.a, oldWalk.ell)) ||
                    !u192_is_zero(mod_sub(extended.b, continued.b, oldWalk.ell))) return 6;
                if (R::weight(first.endPoint.x) > oldThreshold) {
                    auto bounded = first;
                    if (sigma_compat::extend(oldWalk, bounded, oldThreshold, 0) != 3 || bounded.ok) return 13;
                }
                if (match(continued)) {
                    ++bridged;
                    if (!directHit) ++extra;
                }
            }
        }
    }
    if (!direct || !extra || !earlier) return 7;
    std::printf("{\"curve_bits\":23,\"old_thresholds\":[6,8],\"new_threshold\":10,"
                "\"planted_targets\":4,\"indexed_old_endpoints\":%u,"
                "\"direct_matches_verified\":%u,\"continued_matches_verified\":%u,"
                "\"additional_matches_after_continuation\":%u,"
                "\"old_seeds_stopping_earlier_at_new_threshold\":%u,\"guarded_walks\":%u}\n",
                archived, direct, bridged, extra, earlier, guards);
}
