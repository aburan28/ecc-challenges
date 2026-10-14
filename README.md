# ECC-challenges

A pool for distributed Pollard rho on elliptic-curve discrete-log challenges,
in which the host can check that submitted distinguished points are real work.
It works like mining-pool shares, adapted to rho.

- **Every share is checked cheaply**: the DP is on the curve and distinguished,
  and `dp == a·G + b·Q`. No share can fake a solution.
- **Walks are bound to the API key**: the start point is
  `H(salt, key_id, nonce)`, so shares are attributable, and stolen DPs are
  worthless under another key.
- **Random audits check usefulness**: shares carry checkpoints, and the
  server replays a random segment (sometimes the whole walk) after the share
  is committed. The design doc explains why a valid DP is not automatically
  a useful one.
- **STARK proofs** ([docs/STARK.md](docs/STARK.md)): on curves over the
  Goldilocks field, a job can require a STARK proof of every walk
  (`stark`: every credited share proven, no trust needed), or have random
  segments proven on demand (`stark-challenge`). The prover and verifier
  are Rust ([Winterfell](https://github.com/facebook/winterfell)) in `stark/`.
- **API keys, rate limits, sanctions**: keys stored as HMAC hashes; per-key
  and per-IP token buckets; strikes → suspension; a failed audit or proof
  → revocation plus forfeiture of all credit.

Read [docs/DESIGN.md](docs/DESIGN.md) for the threat model,
[docs/STARK.md](docs/STARK.md) for the proof system and its measured cost,
and [docs/OPERATIONS.md](docs/OPERATIONS.md) to run a pool.

## Quick start

```sh
pip install -e '.[dev]'
cargo build --release --manifest-path stark/Cargo.toml   # STARK prover/verifier + native walker
cargo test --release --manifest-path stark/Cargo.toml    # 8 completeness/forgery tests
pytest -q                                                # 31 tests, ~10 s

export ECCPOOL_PEPPER=$(openssl rand -hex 32) ECCPOOL_ADMIN_TOKEN=$(openssl rand -hex 32)
eccpool serve --port 8000 &

eccpool key issue --label alice             # prints {"api_key": "eccp_..."} once
eccpool job create --challenge challenges/ecc40.json --dp-bits 10

ECCPOOL_API_KEY=eccp_... eccpool worker --procs 4

# STARK-verified job on a Goldilocks curve: every share carries a proof
eccpool job create --challenge challenges/gl40.json --dp-bits 10 --verification stark
# or: prove a random 25% of shares' segments on demand
eccpool job create --challenge challenges/gl40.json --dp-bits 10 \
    --verification stark-challenge --challenge-rate 0.25
```

The worker detects the mode and uses the native Rust walker and prover.

Measured on a 4-core container, two workers each, live HTTP. None of these
discrete logs was known beforehand, and each answer checked as `k·G == Q`:

| Challenge | Verification | Time | Shares | Rejections / failed audits |
|---|---|---|---|---|
| `ecc40` | `audit` | ~10 s | ~1,600 | 0 |
| `gl40` | `stark` (every share proven) | 49.6 s | 1,314 | 0 |
| `gl40` | `stark-challenge` (25%) | 4.7 s | 490 (126 proofs) | 0 |

## Challenges

`challenges/eccNN.json` are prime-order curves of NN bits.
`challenges/glNN.json` are curves over the Goldilocks field
2^64 − 2^32 + 1 with an NN-bit prime-order subgroup; STARK modes need these.
In both, `G` and `Q` are hash-derived from the public seed in the file, so
nobody knows `log_G(Q)`. Make new ones with
`eccpool gen-curve --bits NN --seed ... [--field goldilocks]`.

| Challenge | Expected group ops (√(πn/2)) | Reference worker, 4 cores (≈1.5·10^5 ops/s measured) |
|---|---|---|
| ecc32 | 8·10^4 | < 1 s |
| ecc40 | 1.3·10^6 | ~10 s |
| ecc48 | 2.1·10^7 | ~2–3 min |
| ecc56 | 3.4·10^8 | ~40 min |
| ecc64 | 5.4·10^9 | ~10 h (a native worker: far less) |

## API

Worker endpoints (`Authorization: Bearer eccp_<id>_<secret>`):

| Method | Path | |
|---|---|---|
| GET | `/v1/jobs`, `/v1/jobs/{id}` | job specs, progress, solution once solved |
| POST | `/v1/shares` | `{"job_id", "shares": [{nonce, length, dp: [x, y], a, b, checkpoints: [[x, y], ...]}]}` (≤ 64) |
| GET | `/v1/me` | status, strikes, audits, credit |
| GET | `/v1/challenges` | pending proof challenges (`stark-challenge`) |
| POST | `/v1/challenges/{id}/proof` | `{"proof": "<base64>"}` |

In `stark` jobs a share carries `"proof"` (base64) instead of checkpoints.

Admin endpoints (`Authorization: Bearer $ECCPOOL_ADMIN_TOKEN`):
`POST/GET /admin/keys`, `GET /admin/keys/{id}`,
`POST /admin/keys/{id}/{revoke|suspend|reinstate|audit}`,
`PATCH /admin/keys/{id}/limits`, `POST /admin/jobs`,
`POST /admin/jobs/{id}/status`, `GET /admin/{audits|events|collisions}`.
The `eccpool` CLI wraps all of them.

## Writing a fast worker

Implement the walk in `eccpool/walk.py` (one page) and reproduce
`tests/vectors.json` exactly. The server doesn't care what produced a share,
only that it replays or proves. For STARK jobs, produce the proofs with
`eccstark` (`stark/src/main.rs` documents its JSON protocol). The native
walker there is a starting point for a faster one.

## Layout

```
eccpool/ec.py        curve arithmetic          eccpool/pool.py      verification, audits, sanctions
eccpool/walk.py      the walk spec             eccpool/server.py    HTTP API, auth, rate limits
eccpool/curvegen.py  curves + challenges       eccpool/keys.py      API keys
eccpool/worker.py    reference worker          eccpool/cli.py       eccpool command
eccpool/stark.py     bridge to eccstark        stark/src/lib.rs     AIR, prover, verifier, native walk
```
