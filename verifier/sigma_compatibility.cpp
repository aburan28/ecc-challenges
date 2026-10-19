// Operator-only sigma continuation and mixed-threshold collision resolution.
// The independent FPGA-model verifier checks final scalars separately.
#include "sigma_compatibility.h"
#include <cstdio>
#include <cstring>
#include <stdexcept>

static int weight(const char *text, bool unknown = false) {
    if (unknown && std::strcmp(text, "unknown") == 0) return 0;
    if (std::strcmp(text, "30") == 0) return 30;
    if (std::strcmp(text, "32") == 0) return 32;
    if (std::strcmp(text, "34") == 0) return 34;
    throw std::invalid_argument("threshold");
}
int main(int argc, char **argv) {
    using S = Solver<CfgF131>;
    try {
        if (argc != 4) return 3;
        const bool solve = std::strcmp(argv[1], "solve") == 0;
        const bool extend = std::strcmp(argv[1], "continue") == 0;
        if (!solve && !extend) return 3;
        const int leftWeight = solve ? weight(argv[2], true) : 34;
        const int rightWeight = solve ? weight(argv[3], true) : weight(argv[2]);
        uint64_t maxExtra = 0;
        if (extend) {
            std::string bound(argv[3]);
            if (bound.empty() || bound.find_first_not_of("0123456789") != std::string::npos) return 3;
            maxExtra = std::stoull(bound);
            if (rightWeight >= 34 || maxExtra > 4294967296ULL) return 3;
        }
        sigma_compat::Record a{}, b{};
        if (std::fread(a.data(), 1, 32, stdin) != 32) return 3;
        if (solve && std::fread(b.data(), 1, 32, stdin) != 32) return 3;
        if (std::fgetc(stdin) != EOF) return 3;
        S s;
        s.setup(eccF131::PX, eccF131::PY, eccF131::QX, eccF131::QY,
                eccF131::ELL_DEC, eccF131::S_DEC, 34, 4294967296ULL);
        S::WalkResult A{}, B{};
        int status = sigma_compat::replay(s, a, leftWeight, A);
        if (status) return status;
        if (extend) {
            const auto initialSteps = A.iters;
            status = sigma_compat::extend(s, A, rightWeight, maxExtra);
            if (status) {
                std::printf("{\"state\":\"bounded\",\"extra_steps\":%llu}\n", A.iters - initialSteps);
                return status;
            }
            std::printf("{\"state\":\"complete\",\"record\":\"%s\",\"steps\":%llu,\"extra_steps\":%llu,\"target_weight\":%d}\n",
                        sigma_compat::hex(sigma_compat::encode<CfgF131>(A)).c_str(), A.iters,
                        A.iters - initialSteps, rightWeight);
            return 0;
        }
        status = sigma_compat::replay(s, b, rightWeight, B);
        if (status) return status;
        U192 k;
        std::string why;
        if (!s.solve(A, B, &k, &why)) {
            std::puts("{\"verified\":false,\"reason\":\"no_nondegenerate_solution\"}");
            return 0;
        }
        std::printf("{\"verified\":true,\"scalar\":\"%s\",\"left_steps\":%llu,\"right_steps\":%llu}\n",
                    u192_to_dec(k).c_str(), A.iters, B.iters);
        return 0;
    } catch (const std::exception &) {
        std::fputs("invalid compatibility request\n", stderr);
        return 3;
    }
}
