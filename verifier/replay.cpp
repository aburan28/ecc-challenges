// Exact ECC2K-130 sigma-walk replay from one submitted 32-byte record.
// Built against a pinned cryptanalysis source commit and the independent
// fpga/model implementation; stdin is one record, exit 0 means byte match.
#include "hostcheck.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>

using namespace ec2k_gpu;

int main() {
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
