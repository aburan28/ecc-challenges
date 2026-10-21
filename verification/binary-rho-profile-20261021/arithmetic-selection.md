# CUDA bit-length engineering decision, 2026-10-21

## Observation

The [first PTX A/B panel](first-panel-insight.md),
[native A/B replication](native-ab-insight.md), and
[native A/A control](native-aa-insight.md) each completed 54 bounded worker
invocations across the same nine pinned binary curves. Independent receipt
replay verified exact output, distinguished-point hashes and actual steps in
all three panels. The candidate is a CUDA-only `__clz` calculation for the
first nonzero word in `bit_length`; CPU and other backend arithmetic retain the
existing loop.

| Panel | Stable baseline brackets | Median full worker candidate/baseline | Median kernel candidate/baseline | Result |
| --- | ---: | ---: | ---: | --- |
| PTX A/B | 12/18 | 0.824 | 0.732 across all 18 | 11/12 stable pairs favored candidate |
| Native L4 A/B | 13/18 | 0.839 | 0.730 across all 18 | 13/13 stable pairs favored candidate |
| Native L4 A/A | 16/18 | 1.004 | 1.000 across all 18 | Identical source and binary in both arms |

Stable means the larger of the two adjacent baseline times divided by the
smaller was at most 1.05. The candidate/baseline ratio uses the median of the
adjacent baselines. The PTX A/B panel's first ECC2K-130 pair had baseline drift
3.73 and a reversed ratio; it was retained in the raw record and excluded only
from the predeclared stable subset. Native A/B has 18/18 ratios below 1 even
without the drift filter. Native compiler output reports 160 registers for the
candidate versus 159 for baseline, unchanged stack and no spills.

## Selection and boundary

Retain the CUDA change as the faster of the **measured implementations for this
L4 worker workload**. The native stable-pair median ratio 0.839 corresponds to
about 16.1% less full worker time per invocation, derived as `1 - 0.839`.
The A/A stable-pair range (0.971–1.022) is much narrower than the native A/B
gap; this is a comparison of observed ranges, not a statistical confidence
interval. The measured boundary includes process startup, input validation,
host/device transfers, the walk kernel and output serialization. It excludes
long-running worker orchestration, distinguished-point submission, persistent
collection, server verification, and campaign completion probability. No
end-to-end campaign gain is asserted.

## Next discriminating measurement

Build the selected worker image and run a sustained nine-curve GPU canary with
real checkpoint and submission paths, recording useful steps, accepted points,
wall time, GPU time, bytes, verifier cost and restart behavior against the
current image. Use matched curve/input controls and an A/A panel. A durable
campaign-level improvement requires the full pipeline metric and its own
independent verification. Other arithmetic candidates or hardware may exceed
this measured L4 implementation; that remains open.
