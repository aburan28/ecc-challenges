# Modal workers for all Certicom curves

The launcher reads the current pinned worker image and enabled campaigns from
`https://ecc-challenges.com/v1/campaigns`. It refuses a disabled campaign or v2
release. It supports all24 campaign IDs when v2 is enabled. A real L4 passed the
[all-curve arithmetic controls](../verification/all-curves-20261016/README.md);
final image and contributor canaries are separate release checks.

Install Modal and authenticate with `modal token new`. Sign in on the website,
create a key and save it as a local file readable only by you:

```sh
chmod 600 /path/to/ecc_api_key
python3 scripts/modal_worker.py start --key-file /path/to/ecc_api_key \
  --campaign eccp-131 --gpu L4 --workers 1 --hours 1
python3 scripts/modal_worker.py status --campaign eccp-131 --workers 1
python3 scripts/modal_worker.py stop --campaign eccp-131 --workers 1
```

Replace the campaign with any enabled ID in the catalog. CPU workers use
`--gpu none --backend cpu`. CUDA requires a compatible NVIDIA driver and a
supported GPU; the generic binary includes Turing through Blackwell targets.
The preserved ECC2K-130 sigma binary is an explicit `--protocol 1` path for its
original compatible GPU configuration. New workers use protocol2 by default.

Each `(campaign,protocol,worker index)` has its own named sandbox and volume.
Keep that identity to resume, and never mount one worker volume into simultaneous
workers. Modal volumes commit asynchronously and do not provide distributed file
locking. The worker pins round geometry, preserves the DP outbox with advanced
states atomically, and can replay already admitted batches using stable batch
acknowledgements after an older checkpoint is restored. Losing the checkpoint
completely still requires operator recovery; server DP archives remain intact.

The launcher sends the key as a Modal Secret and writes a mode0600 file inside
the sandbox. Keys and signed upload URLs are not logged. Revoke a key on the
website when access should stop. GPU time and persistent storage are billed to
the contributor's provider account. `stop` preserves its volume and server data.
Start with one worker and inspect upload and backlog status before increasing
worker count. The account cap is eight active runs across both protocols.

RunPod and other Linux container hosts use the same digest shown on the
website, a persistent `/data` volume, and these runtime settings:

| Setting | Value |
| --- | --- |
| `ECC_API_URL` | `https://ecc-challenges.com` |
| `ECC_CAMPAIGN` | Any enabled catalog ID, for example `ecc2-131` |
| `ECC_PROTOCOL` | `2` for a new generic worker |
| `ECC_BACKEND` | `auto`, `cuda`, or `cpu` |
| `ECC_API_KEY_FILE` | Mounted secret path, default `/run/secrets/ecc_api_key` |
| `ECC_API_KEY` | Provider environment secret instead of a mounted file |
| `ECC_STATE_DIR` | `/data`, backed by a distinct persistent volume per worker |

Use either key mechanism. For CPU containers, omit Docker's `--gpus` option.
The worker consumes and removes the environment key before starting native code.
Native arithmetic failures preserve the previous durable checkpoint and report
an error; they do not silently change the requested CUDA computation.
