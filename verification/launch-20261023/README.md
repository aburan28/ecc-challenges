# Launch readiness review — 23 October 2026

Baseline: `3a71596ba85434bd98604cfda1e974c5b6c2dcc3` on `main`.
Review checkout: branch `codex/launch-readiness-20261023`.
Observations were taken on 23 October PDT (24 October UTC); live counts change.

The public site and configured services are healthy, and the local correctness
checks pass. An unrestricted fleet launch is not yet qualified. This change
fixes three confirmed issues; it does not deploy new images or resolve the
outstanding replay-capacity gate.

## Confirmed issues fixed

1. The original worker used urllib's default redirect handler for authenticated
   gateway requests. A redirect could forward its bearer credential to another
   origin. Five loopback-server regressions failed before the fix. The client
   now refuses redirects, consistent with the generic client and heartbeat.
   Tests cover 301, 302, 303, 307 and 308, including POST-to-GET redirection.
   Test credentials are synthetic; no production credentials were used.
2. Key creation returns the dashboard at POST `/keys`. Live refresh used the
   current URL, causing GET `/keys` to fail with 405. Dashboard responses now
   provide an explicit GET refresh URL containing the selected campaign and
   reporting threshold. Browser checks confirm that the displayed key and
   unsent form input survive refresh, and creation happens only once.
3. Terminal `unverified` legacy audits were included in queue age and worker
   pending counts even though reconciliation does not automatically retry them.
   These audits now remain explicitly unresolved and require attention; active
   queue age excludes them. Public and private pages explain that the archived
   batches are not indexed or queued for automatic retry. Counts are preserved.

## Coverage and results

- Full main suite with a fresh native Metal build: **288 passed, 3 skipped**.
  Skips: OpenMP unavailable in Apple Clang, unsupported OpenCL struct-copy
  emulation in that compiler, and absent offline Metal compiler. The actual
  Metal runtime checks passed. Two dependency deprecation warnings remain.
- Native CMake build and CTest: **2 passed**. The main suite includes all 24
  curves' CPU reference checks, Metal/CPU parity, and resumed-round parity.
- Legacy worker threshold, checkpoint and redirect tests: **18 passed**.
- Separate pool Python suite: **31 passed**; Rust suite: **9 proof tests passed**. The pool is a separate
  application, not the service behind the public website.
- Public browser checks: seven pages at 320, 390 and 1440 pixels, with no
  horizontal viewport overflow or JavaScript errors; manual status refresh
  passed. All 24 challenge routes and 10 published-result routes returned 200.
- Isolated authenticated browser checks: dashboard and worker detail at all
  three widths; key creation, selected-campaign refresh, secret/input
  preservation, simulated 503 and successful refresh recovery passed.
- Current-main GitHub CI run `40024266669`: integration, image builds,
  ARM64 image, templates and deployment-manifest checks all succeeded.
  The separately open fast-engine PR #47 is outside the reviewed baseline.

The source review followed website routing, OAuth/session/CSRF and key
ownership, run registration, checkpoint identity, worker restart/upload
handling, immutable batch admission, verification, repair/outbox delivery,
point indexing, collision witness checks and scalar validation. Existing
integration tests exercise invalid witnesses, failed-replay quarantine,
duplicate uploads, queue outages, crash recovery, and collision handling.
The main suite uses temporary PostgreSQL and mocked AWS storage/queues; it is
not a new production end-to-end contribution test. CUDA and Windows were not
rerun on hardware in this review, and this is not an exhaustive security audit.

## Live operational evidence

`production-health.json` records read-only AWS observations:

- Web: 2/2 tasks; audit: 12/12; index: 1/1; resolve: 1/1; reconcile: 1/1.
- All deployments reported completed, with no pending tasks.
- Audit, index and collision queues and their dead-letter queues reported zero
  visible and in-flight messages at the snapshot.
- Six existing queue/index alarms reported OK. `/health` returned `{"ok":true}`.

`public-status.json` nevertheless records **180 unresolved legacy batches**,
seven successful sampled batches, and zero failed/quarantined batches. The old
pending timestamp was 18 October. Code inspection establishes that an
`unverified` state means a replay error or timeout, is not indexed, and is not
automatically retried. A bounded five-page read of the preceding 24 hours of
audit logs found at least 23 `unverified` results; that was a capped sample,
not a complete daily count. Empty queues and OK queue alarms do not measure
this unresolved work.

## Remaining launch gates

1. Merge and release the fixes after the PR's required checks pass. Publish the
   rebuilt worker image, deploy the updated web image, and confirm the served
   image pins and post-release health. Existing workers need the new client for
   the redirect fix. This review performs no deployment or production mutation.
2. Diagnose unresolved replays using their stored audit events (timeout versus
   execution error, threshold, verifier digest and elapsed time), then define a
   bounded repair/replay plan. Preserve failed and capped evidence. Simply
   increasing a timeout or requeuing all records does not establish capacity.
3. Complete the sustained arrival/drain gate in
   `problems/dp32-verifier-and-ingest-capacity.md` at a declared fleet size and
   arrival rate, with a stop budget and verified accounting of acknowledged
   data. Successful arithmetic controls and empty queues are insufficient.
4. Verify the production OAuth callback, key creation/revocation, dashboard and
   worker-detail experience in a real signed-in browser. Local fixture coverage
   passed; production `/dashboard` correctly returned 401 without a login.

## Evidence and reproduction

- `browser-results.json`, `browser_check.py`: live public checks and isolated
  authenticated checks. Requires the test extras, Playwright, PostgreSQL
  binaries, and Chrome at the macOS path specified in the script.
- `public-status-320.png`, `public-status-390.png`, `public-status-1440.png`:
  production screenshots; these show the deployed version before these fixes.
- `public-status-mobile-preview.png`: crop of the mobile screenshot.
- `main-tests.txt`, `pool-tests.txt`, `pool-rust-tests.txt`,
  `redirect-before.txt`, `redirect-after.txt`: recorded verification results.

Main-suite reproduction:

```sh
uv sync --extra test
cmake -S worker -B /tmp/ecc-review-build -DECC_ENABLE_CUDA=OFF
cmake --build /tmp/ecc-review-build -j 2
ctest --test-dir /tmp/ecc-review-build --output-on-failure
ECC_TEST_ENGINE=/tmp/ecc-review-build/generic-engine uv run python -m pytest -q -ra
```
