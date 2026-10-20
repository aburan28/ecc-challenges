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
    int weight = CAMPAIGN_DP_WEIGHT;
    bool untilEndpoint = argc == 2 && strcmp(argv[1], "--until-endpoint") == 0;
    if (argc == 3 && strcmp(argv[1], "--dp-weight") == 0) {
        if (strcmp(argv[2], "30") == 0) weight = 30;
        else if (strcmp(argv[2], "32") == 0) weight = 32;
        else if (strcmp(argv[2], "34") == 0) weight = 34;
        else return 3;
    } else if (argc != 1 && !untilEndpoint) return 3;
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
    if (untilEndpoint) {
        // Historical records can lack their original threshold. Never infer
        // it from endpoint weight: the seed may have passed earlier DP32s.
        ec2k_pt point;
        if (!startPoint(walk, seed, &point)) return 3;
        unsigned long long history = HIST_START;
        report.seed = seed;
        for (unsigned long long i = 0; i <= CAMPAIGN_MAX_ITERS; ++i) {
            if (ec2k_fe_weight(&point.x) <= 34) {
                report.iters = i;
                toLimbs(point.x, report.x);
                toLimbs(point.y, report.y);
                uint8_t actual[EC2K_RECORD_BYTES];
                campaignRecord(actual, report);
                if (memcmp(input, actual, sizeof(input)) == 0) {
                    printf("{\"match\":true,\"steps\":%llu,\"original_threshold\":null}\n", i);
                    return 0;
                }
            }
            if (i == CAMPAIGN_MAX_ITERS || !referenceStep(walk, &point, &history)) break;
        }
        puts("{\"match\":false,\"reason\":\"endpoint_not_reached_within_guard\"}");
        return 3; // unresolved within the bound, not evidence of a bad record
    }
    if (!referenceReport(walk, seed, weight, CAMPAIGN_MAX_ITERS, &report)) {
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
