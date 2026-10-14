/*
 * ec2k_cpu.cpp - the ECC2K-130 Pollard rho client for host cores.
 *
 *   ec2k-cpu bench   [--steps S] [--launches L] [--workers N] [--batch B]
 *   ec2k-cpu walk    --run-id R [--dp-weight W] [--dp-file F] [--verify N] ...
 *   ec2k-cpu check   [--rounds N]          (arithmetic vs the golden model)
 *
 * The walk, the seeds, the table and the 64-byte reports are the CUDA
 * client's (src/ec2k_gpu.cu), so the two write one corpus: what changes is the
 * engine.  Here it is src/cpuwalk.h -- worker threads over the host's
 * carry-less multiplier, PMULL on Apple silicon and other AArch64, PCLMULQDQ
 * on x86-64, a software product where there is neither.  A laptop is two
 * orders of magnitude short of the card the CUDA client is tuned for; this
 * client is for developing and testing a campaign's pipeline against real
 * reports without renting one, and for hosts that have cores to spare.
 */
#include "cpuwalk.h"

using namespace ec2k_cpu;

namespace
{

int usage()
{
    fprintf(stderr,
            "usage: ec2k-cpu <bench|walk|check> [options]\n%s"
            "  --workers N      worker threads (default: one per core)\n"
            "  --batch B        lanes per batched inversion (default %d)\n"
            "  --chunks C       batches in flight (default: two per worker)\n",
            commonUsage(), CpuEngine::kDefaultBatch);
    return 2;
}

int cmdWalk(const Options &o, const HostTable &table, bool bench)
{
    CpuEngine e(table, o, bench);
    printf("host: %d workers, carry-less multiply: %s\n", e.workers(),
           ECC_HOST_CLMUL ? "hardware" : "software");
    printf("walks: %d batches x %d lanes = %zu, %d steps per launch, dp weight %d, run id %u\n",
           e.chunks(), e.batch(), e.laneCount(), o.steps, bench ? -1 : o.dpWeight, o.runId);
    e.prepare(); // the lanes' start points before cmdRun starts its clock
    return cmdRun(o, table, e, bench);
}

} // namespace

int main(int argc, char **argv)
{
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
