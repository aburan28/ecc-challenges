// Exact ECC2K-130 sigma-walk replay from one submitted 32-byte record.
// Built against a pinned cryptanalysis source commit and the independent
// fpga/model implementation; stdin is one record, exit 0 means byte match.
#include "hostcheck.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

using namespace ec2k_gpu;

static int verifyScalar() {
    char decimal[80];
    if (!fgets(decimal, sizeof(decimal), stdin)) return 3;
    size_t size = strlen(decimal);
    if (size && decimal[size - 1] == '\n') decimal[--size] = 0;
    if (!size || fgetc(stdin) != EOF) return 3;
    uint64_t words[3] = {0, 0, 0};
    for (size_t pos = 0; pos < size; ++pos) {
        if (decimal[pos] < '0' || decimal[pos] > '9') return 3;
        unsigned __int128 carry = (unsigned)(decimal[pos] - '0');
        for (int limb = 0; limb < 3; ++limb) {
            const unsigned __int128 value = (unsigned __int128)words[limb] * 10 + carry;
            words[limb] = (uint64_t)value;
            carry = value >> 64;
        }
        if (carry) return 3;
    }
    ec2k_pt P, Q, got;
    challengePoints(&P, &Q);
    ec2k_pt_mul(&got, &P, words, 3);
    const bool same = !got.inf && sameFe(got.x, Q.x) && sameFe(got.y, Q.y);
    printf("{\"scalar_match\":%s}\n", same ? "true" : "false");
    return same ? 0 : 2;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--verify-scalar") == 0) return verifyScalar();
    if (argc != 1) return 3;
    uint8_t input[EC2K_RECORD_BYTES];
    if (fread(input, 1, sizeof(input), stdin) != sizeof(input) || fgetc(stdin) != EOF) {
        fprintf(stderr, "expected exactly one 32-byte record\n");
        return 3;
    }
    uint64_t seed = 0;
    for (int i = 0; i < 8; ++i) seed |= uint64_t(input[i]) << (8 * i);
    ec2k_pt P, Q;
    challengePoints(&P, &Q);
    HostWalk walk;
    walk.build(P, Q);
    DpRecord report{};
    if (!referenceReport(walk, seed, CAMPAIGN_DP_WEIGHT, CAMPAIGN_MAX_ITERS, &report)) {
        puts("{\"match\":false,\"reason\":\"no_report_within_guard\"}");
        return 2;
    }
    uint8_t actual[EC2K_RECORD_BYTES];
    campaignRecord(actual, report);
    bool same = memcmp(input, actual, sizeof(input)) == 0;
    printf("{\"match\":%s,\"steps\":%llu}\n", same ? "true" : "false",
           (unsigned long long)report.iters);
    return same ? 0 : 2;
}
