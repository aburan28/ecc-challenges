/*
 * ec2k_metal.mm - the ECC2K-130 Pollard rho client for Apple GPUs.
 *
 *   ec2k-metal bench   [--steps S] [--launches L] [--threads T] [--batch B]
 *   ec2k-metal walk    --run-id R [--dp-weight W] [--dp-file F] [--verify N] ...
 *   ec2k-metal check   [--rounds N]          (host arithmetic vs the golden model)
 *
 * The walk, the seeds, the table and the 64-byte reports are the CUDA
 * client's (src/ec2k_gpu.cu), so all clients write one corpus; the engine is
 * src/metalwalk.h, the same packed kernel compiled as Metal Shading Language
 * at start-up.  An Apple GPU has no carry-less multiplier, so this is the
 * kernel's software-product configuration: it is for developing and testing a
 * campaign's pipeline on a Mac, not for the campaign's throughput.
 */
#include "metalwalk.h"

using namespace ec2k_metal;

namespace
{

int usage()
{
    fprintf(stderr,
            "usage: ec2k-metal <bench|walk|check> [options]\n%s"
            "  --threads T      GPU threads, each owning one batch of lanes (default 32768)\n"
            "  --batch B        lanes per thread and per inversion (default 32)\n"
            "  --dp-cap N       report buffer capacity per launch (default 262144)\n"
            "  --device D       index into MTLCopyAllDevices (default: the system default)\n",
            commonUsage());
    return 2;
}

int cmdWalk(const Options &o, const HostTable &table, bool bench)
{
    MetalEngine e(table, o, bench);
    if (!e.ok()) {
        fprintf(stderr, "Metal: %s\n", e.error().c_str());
        return 3;
    }
    printf("device: %s\n", e.deviceName().c_str());
    printf("walks: %d threads x %d lanes = %zu, %d steps per launch, dp weight %d, run id %u\n",
           e.threads(), e.batch(), e.laneCount(), o.steps, bench ? -1 : o.dpWeight, o.runId);
    const int rc = cmdRun(o, table, e, bench);
    if (!e.error().empty()) return 3;
    return rc;
}

} // namespace

int main(int argc, char **argv)
{
    @autoreleasepool {
        if (argc < 2) return usage();
        Options o;
        if (!parseOptions(argc, argv, &o)) return usage();

        ec2k_pt P, Q;
        ec2k_point_from_seed(&P, o.pSeed);
        ec2k_point_from_seed(&Q, o.qSeed);
        HostTable *table = new HostTable;
        table->build(P, Q);

        int rc;
        if (o.cmd == "check")
            rc = cmdCheck(o, *table);
        else if (o.cmd == "bench")
            rc = cmdWalk(o, *table, true);
        else if (o.cmd == "walk")
            rc = cmdWalk(o, *table, false);
        else
            rc = usage();
        delete table;
        return rc;
    }
}
