# Current-head rho131 and nine-curve validation plan

Frozen on 2026-10-23 before new throughput trials. Source commit:
`b78fc2921e9b9a60174e1693aff15c7daf23f9c7`. The previously published
worker image is recorded in `live-catalog.json`; the new rho131 engine is not
in that image. The nine campaign IDs are `ecc2k-130`, `ecc2-131`,
`ecc2k-163`, `ecc2-163`, `ecc2-191`, `ecc2k-238`, `ecc2-238`,
`ecc2k-358`, and `ecc2-353`.

## Correctness gates

1. Run the current-head CPU tests for fast131, the production collision
   control, and all 24 native curve instances. Build the production Metal
   engine from this commit, run its parity suite on a detected M4 Pro device,
   and retain raw logs.
2. Run the native `check_native_curves.py` CPU/Metal controls on all 24
   instances, including every requested binary curve. The controls compare
   states and distinguished-point bytes and reject malformed inputs. Retain
   each input, output, receipt, and engine digest.
3. After a fresh provider credential is configured, build the current-head
   CUDA engine on an L4. Require walk-2 byte parity for every requested curve,
   independent CPU parity for ECC2-131 walks 3 and 4, a passing self-test,
   device identity, source and binary hashes, and complete raw outputs.
   A compile-only check or a local Metal result cannot substitute for this.

## Contributor canary

Use a newly issued, dedicated contributor key held in a mode-0600 local file
and a newly rotated Modal token. Never print either value. Run the published
image by immutable digest first; a new image is eligible only after its own
CUDA gate. For each of the nine curves, use a fresh volume and one L4 worker;
run curves sequentially to stay below the gateway account cap. Pin protocol 2,
the live namespace, curve identity, image digest, device, backend, lanes,
round size, and run ID. Bound each attempt to 20 minutes. Stop the sandbox on
error or deadline while retaining the volume and gateway run. For each curve,
check a valid checkpoint, advancement after restart, upload cursor, accepted
and indexed records, failed batches, closure, and checkpoint witness. If the
production DP predicate yields no point within the bound, record a censored
canary and retain its useful-step count; do not mark upload or index verified.

The public `live-status-before.json` is a gateway-wide baseline, not a
per-run receipt. Authenticated run status and provider receipts must bind each
canary to its own run ID. Existing public activity is excluded from per-run
rates. No credential contents belong in artifacts.

## Matched throughput and cost

For a throughput comparison, use the same L4 class, pinned images, curve,
walk version, DP threshold, input states, lanes, steps, and command. Bracket
each candidate with baseline trials, retain one warm-up and at least three
measured pairs per curve, plus an A/A timing control. Verify exact output and
actual useful-step counts before timing is interpreted. Record complete worker
invocation wall time, kernel time, GPU allocation time, accepted and indexed
points, gateway and verifier lag, transfer bytes, and billed provider time.
Report worker-only and full contributor-plus-verifier boundaries separately;
keep costs null where the provider or gateway does not expose them. A new
walk-4 namespace is a different workload: report its step rate and modeled
collision-work adjustment separately from walk-2 byte-parity comparisons.

Abort a trial on an arithmetic mismatch, rejected batch, incorrect namespace,
stalled checkpoint, or failed health check; retain the partial receipt.
Current-head CI must pass before a new image is published or advertised.
The final report will distinguish local parity, CUDA correctness, live
contribution, and end-to-end performance evidence.
