# Modal workers for all Certicom curves

`scripts/modal_worker.py` reads the current pinned worker image and enabled
campaigns from `https://ecc-challenges.com/v1/campaigns`. It refuses a disabled
campaign or a campaign without protocol 2. It supports all 24 campaign IDs when
protocol 2 is enabled. A real L4 passed the
[all-curve arithmetic controls](../verification/all-curves-20261016/README.md);
the final image and contributor canaries are separate release checks.

Install Modal and authenticate with `modal token new`. Sign in on the website,
create an API key and save it as a local file readable only by you:

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
original compatible GPU configuration. New workers use protocol 2 by default.
`--workers` takes 1 to 8 and `--hours` takes 1 to 24.

Each `(campaign,protocol,worker index)` has its own named sandbox and volume.
The launcher keeps its state in `/data/ecc-state`, owned by UID and GID 10001,
with private directories and mode 0600 files. Modal starts the bootstrap as
root. The bootstrap resolves Modal's provider-created mount symlink, prepares
the child directory, then drops privileges before running the worker. The API
key is kept in an ephemeral mode 0600 file outside the persistent volume.

Volumes created by the older launcher are upgraded in place. Registration
nonces, checkpoints and pending distinguished point data move into the private
child directory before the worker starts. An atomic migration marker allows a bootstrap
interrupted between file moves to continue. If both layouts already contain
worker state, startup stops and preserves both copies for operator recovery.
Do not remove either checkpoint or create another run to bypass that error.

Keep that identity to resume, and never mount one worker volume into two
workers at once. Modal volumes commit asynchronously and provide no distributed
file locking. The worker pins round geometry, preserves the distinguished point
outbox with advanced states atomically, and can replay already admitted batches
using stable batch acknowledgements after an older checkpoint is restored.
Losing the checkpoint completely still requires operator recovery; the
server's distinguished point archives remain intact.

The launcher sends the key as a Modal Secret and writes a mode 0600 file inside
the sandbox. Keys and signed upload URLs are not logged. Revoke a key on the
website to stop its access. GPU time and persistent storage are billed to your
Modal account. `stop` keeps the volume and the server data. Start with one
worker and check upload and backlog status before adding more. The account cap
is eight active runs across both protocols.

The bounded, key-free `python3 scripts/check_modal_volume.py --out receipt.json`
check verifies migration, mode 0600 ownership, atomic checkpoint writes, two
fresh CPU sandboxes and external committed-volume hashes, using the exact
bootstrap. Its counter data is a filesystem control. A real GPU contribution is
a separate release check. [Modal documents its Docker USER behavior](https://modal.com/docs/guide/existing-images#user)
and [volume commit/locking behavior](https://modal.com/docs/guide/volumes).

RunPod and other Linux container hosts use the digest shown on the website,
a persistent `/data` volume and these settings:

| Setting | Value |
| --- | --- |
| `ECC_API_URL` | `https://ecc-challenges.com` |
| `ECC_CAMPAIGN` | Any enabled catalog ID, for example `ecc2-131` |
| `ECC_PROTOCOL` | `2` for a new generic worker |
| `ECC_BACKEND` | `auto`, `cuda`, or `cpu` |
| `ECC_API_KEY_FILE` | Mounted secret path, default `/run/secrets/ecc_api_key` |
| `ECC_API_KEY` | Provider environment secret instead of a mounted file |
| `ECC_STATE_DIR` | `/data`, backed by a distinct persistent volume per worker |

Use one key mechanism, not both. For CPU containers, omit Docker's `--gpus`
option. The worker reads and removes the environment key before starting native
code. A native arithmetic failure keeps the previous durable checkpoint and
reports an error; it does not silently change the requested CUDA computation.
