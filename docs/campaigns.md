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
task. ECC2K-130 counts cover this isolated public gateway only. Historical
points in the earlier account are outside these totals until an audited import.

An uploaded point count is not a count of independently verified operations.
The status response therefore leaves
`estimated_completion_probability_percent` null. Before showing an estimated
percentage, a campaign must publish a frozen work counter with independently
checked checkpoints or equivalent receipts, a collision model for its exact
walk and orbit, and a calculation that states uncertainty. A Pollard rho search
has no deterministic fraction complete.

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
