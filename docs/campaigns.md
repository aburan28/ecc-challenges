# Certicom campaigns and public status

The [Certicom ECC Challenge document, updated November 2009](https://www.certicom.com/content/dam/certicom/images/pdfs/challenge-2009.pdf)
is the source for the 24 names, field sizes, solution snapshot and historical
elliptic-curve-operation estimates in `src/ecc_challenges/catalog.py`.
Section 4 names the challenges, section 6.1 lists the ten solutions then known,
and section 6.2 gives the operation estimates. Section 6.2 mislabels the
ECC2K-95 and ECC2-97 solved rows as ECC2-79; their solution dates in section
6.1 identify those rows. The website labels all of these as historical. It
does not assert that a challenge remains unsolved today.

The public [status page](https://ecc-challenges.com/status) and
`GET /v1/status` are served by this application. The database query reads the
bounded run table and 16 pressure shards, and is cached for 60 seconds per web
task. Runs, queue depth and `total_archived_points` cover this isolated public
gateway only. Points imported from the earlier account (338,034,791 distinct
DP32 points on 2026-10-17) are reported beside them as `total_historical_points`
and the ECC2K-130 row's `historical_points`, read from the
`ecc_public.historical_import_totals` view; the page adds them to the point
totals and says so. Every new public point is matched against them. If the web
role can't read the view, those fields are `null` and the page falls back to
gateway-only counts.

An uploaded point count is not a count of independently verified operations,
so work is reported as an estimate (`src/ecc_challenges/work.py`). Each stored
point stands for the mean walk length between distinguished points: 2^28.41
steps on the ECC2K-130 sigma walk, where DP32 means normal-basis Hamming weight
<= 32 among the 2^130 even-weight x (not 32 zero bits), and 2^d for a v2
namespace pinned to d DP bits. Each walk on a curve (the sigma walk, and each
pinned v2 namespace) is counted separately. Each open row carries:

- `estimated_operations_2009_log2`: Certicom's estimate as a power of two.
- `estimated_work_operations` and `estimated_work_log2`: estimated steps behind
  the stored points.
- `expected_operations_log2`: expected steps for the campaign's default walk,
  sqrt(pi*n/(4*131)) for the sigma walk and sqrt(pi*n/2) for the v2 walk, which
  has no automorphism quotient yet.
- `expected_work_percent` and `estimated_completion_probability_percent`: work as
  a share of that expectation, and 1 - exp(-(pi/4)*(k/E)^2), the chance rho would
  have produced a collision by now. Separate walks on one curve add their
  (k/E)^2 terms.
- `points_per_hour`, `operations_per_second_log2` and `hours_to_expected_work`,
  over a 24-hour window.

These top-level figures count this gateway's public runs; ECC2K-130's
`including_historical` repeats them with the imported points added to the
sigma walk. The snapshot adds `total_estimated_work_log2` and
`total_estimated_work_log2_including_historical`. The estimates assume honest
walks and a random-looking walk, and lag the steps still inside unfinished
trails. A frozen work counter with independently checked checkpoints remains
the way to turn them into verified figures. A Pollard rho search has no
deterministic fraction complete; `/pollard-rho` explains the model on the site.

## Contribution compatibility

The current public image runs the ECC2K-130 Frobenius walk. Its state directory
is bound to `ecc2k-130`; the worker sends that campaign identity to the
gateway and refuses another selection. The gateway rejects registration for
other campaigns and returns HTTP 409 for their configuration requests. The
catalog still lists them so the site can represent the complete published set
without admitting unverifiable data.

The same image and API key can be run with Docker on a local GPU, a RunPod
container, or any host with an NVIDIA runtime, persistent `/data`, and
outbound HTTPS. [The Modal guide](modal-workers.md) uses a Modal Secret and
Volume to provide those same two things. Each container needs a distinct
persistent state directory. The image is pinned by digest on the dashboard.
For a [RunPod Pod](https://docs.runpod.io/runpodctl/reference/runpodctl-remove-pods),
select that image, attach a persistent or network volume at `/data`, set
`ECC_STATE_DIR=/data` and `ECC_CAMPAIGN=ecc2k-130`, and inject the website
key as `ECC_API_KEY`. The worker removes that variable before launching the
CUDA process. Provider settings that contain the key remain sensitive; a
read-only mounted file at `/run/secrets/ecc_api_key` is preferred where
available.

Adding a new campaign requires, in order:

1. Import and independently check Certicom's exact field, curve, subgroup and
   target points; pin the parameter transcript and a known-answer test.
2. Implement the worker walk, record format, checkpoint, and independent replay
   for that campaign. A name in the catalog is not an executable worker.
3. Give the campaign its own run namespace, archive prefix, point index,
   collision queue, rate and storage budgets. Include the campaign and walk
   version in every signed or hashed identity and every database uniqueness
   key. No ECC2K-130 seed or orbit key can be reused across curves.
4. Exercise one contributor end to end: key issue, registration, upload,
   archival, replay, indexing, collision lead and independent scalar check.
   Run sustained arrival and drain measurements before opening a large fleet.
5. Mark the catalog entry's `contribution` ready only after those checks pass.

The existing `pool/` prototype offers a prime-field reference walk and
audit ideas. Its SQLite server and generated sample curves are not a Certicom
production backend. The current SQL schema, DP32 binary record, CUDA image,
replay binary and scalar resolver are specific to ECC2K-130 and must be
generalized or separately deployed for other curves. The concrete exit gate
is tracked in [the multi-campaign problem record](../problems/multi-campaign-worker-and-verifier.md).

## V2 implementation

The generic all 24 path now lives under `src/ecc_challenges/multicurve/` and
`worker/generic/`. See [storage and validation](multicurve-storage.md) and
[provider commands](modal-workers.md). The exact registry supersedes field widths
in the summary table when they disagree: notably Appendix A's ECCp-191 modulus
has 191 bits. All v2 namespaces bind that exact instance, target and walk.
The v1 routes remain specific to the original ECC2K-130 Frobenius encoding.
A new empty volume selects v2; an existing `state.json` resumes v1 unless the
operator explicitly selects another protocol. A directory from one campaign or
protocol is never silently reassigned.
