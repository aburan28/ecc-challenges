# Operations

## Deploy

```sh
export ECCPOOL_PEPPER=$(openssl rand -hex 32)       # keep secret; rotating it invalidates every key
export ECCPOOL_ADMIN_TOKEN=$(openssl rand -hex 32)  # admin API token
export ECCPOOL_DB=/data/eccpool.sqlite3
eccpool serve --host 0.0.0.0 --port 8000
```

Or, from `pool/`, `docker build -t eccpool . && docker run -e ECCPOOL_PEPPER -e ECCPOOL_ADMIN_TOKEN -v pooldata:/data -p 8000:8000 eccpool`.

Run it behind a TLS-terminating reverse proxy (Caddy, nginx). API keys are
bearer tokens, so never serve them over plain HTTP. Behind a proxy set
`ECCPOOL_TRUST_PROXY=1` so per-IP limits use `X-Forwarded-For`, and make sure
the proxy overwrites that header rather than appending a client's value.

Run exactly one server process: the SQLite writer, the rate limiter and the
auditor thread live in it. Back up with `sqlite3 eccpool.sqlite3 ".backup b.sqlite3"`.

Tunables (environment):

| Variable | Default | Meaning |
|---|---|---|
| `ECCPOOL_AUDIT_RATE` | 0.05 | segment-audit probability per share |
| `ECCPOOL_FULL_AUDIT_RATE` | 0.01 | full-replay probability per share |
| `ECCPOOL_PROBATION_SHARES` | 50 | shares a new key is on probation for |
| `ECCPOOL_PROBATION_AUDIT_RATE` | 0.5 | audit probability during probation |
| `ECCPOOL_MAX_STRIKES` | 10 | invalid shares before suspension |
| `ECCPOOL_STARK_BIN` | `stark/target/release/eccstark` or `$PATH` | STARK prover/verifier |
| `ECCPOOL_STORE_PROOFS` | 1 | keep `stark` proofs (~50 KB/share) for re-verification |
| `ECCPOOL_PROOF_DEADLINE` | 3600 | seconds to answer a `stark-challenge` challenge |

## Keys

```sh
export ECCPOOL_URL=https://pool.example.org
eccpool key issue --label "alice@lab" --contact alice@example.org   # prints the key once
eccpool key list [--status active|suspended|revoked]
eccpool key show <key_id>                 # stats + last 50 events
eccpool key limits <key_id> --shares-per-minute 5000 --share-burst 1000
eccpool key suspend <key_id> --reason "investigating"
eccpool key reinstate <key_id> --reason "resolved"     # resets strikes
eccpool key revoke <key_id> --reason "abuse"           # permanent
eccpool key revoke <key_id> --reason "abuse" --forfeit # permanent + zero credit
eccpool key audit <key_id> --count 50 [--kind full]    # targeted audits
```

What happens automatically:

| Trigger | Action |
|---|---|
| a share fails a cheap check | strike (event `strike`) |
| `max_strikes` strikes | key **suspended** (`key_suspended`) |
| any audit fails | key **revoked**, all credit **forfeited** (`key_revoked`, `credit_forfeited`) |
| `stark`: a share's proof does not verify | strike (nothing was credited) |
| `stark-challenge`: a proof fails, or none arrives by the deadline | key **revoked**, all credit **forfeited** |
| too many requests or shares | `429` with `Retry-After`; no sanction |

Revoked and suspended keys get `403` with the reason. Repeated attempts with
them burn the per-IP auth-failure bucket and then get `429`.

## Paying out

Credit is in expected group operations (`2^dp_bits` per accepted share).
`GET /admin/keys` reports `earned`, `verified` (shares that passed an audit)
and `forfeited`. **Pay with a lag**: settle credit older than a holding period
(a day, or N further shares) so that an audit failure can still forfeit it.
Paying instantly turns an audit rate of p into "the cheater keeps 1/p shares
of pay before being caught". In `stark` jobs every credited share is
already proven, so there is nothing to wait for and you can pay at once.

## Jobs

```sh
eccpool gen-curve --bits 56 --seed "ECC-challenges/v1/ecc56" > challenges/ecc56.json
eccpool job create --challenge challenges/ecc56.json --dp-bits 14
eccpool job create --challenge challenges/gl56.json --dp-bits 14 --verification stark
eccpool job create --challenge challenges/gl56.json --dp-bits 14 --verification stark-challenge \
    --challenge-rate 0.25
eccpool job pause <job_id> ; eccpool job resume <job_id>
eccpool log collisions
```

Pick `dp_bits` so a DP takes a worker seconds to minutes: `2^dp_bits` steps
per share, about `sqrt(πn/2) / 2^dp_bits` shares to solve. The checkpoint
interval defaults to `2^dp_bits / 8`, so an audit replays about 1/8 of a
walk; lower it for cheaper audits at the cost of larger shares.
