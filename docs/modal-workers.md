# Modal GPU workers

The public Docker image is pinned to the digest in `scripts/modal_worker.py`.
This image passed a bounded CUDA health check on an RTX PRO 6000 Blackwell
Server Edition GPU. A complete contributor run still requires website key
creation, registration, upload, replay, and indexing checks.

Install Modal in your own account and authenticate with `modal token new`.
Sign in at <https://ecc-challenges.com>, create a contributor key, and save it
to a local file that only you can read:

```sh
chmod 600 /path/to/ecc_api_key
python3 scripts/modal_worker.py start --key-file /path/to/ecc_api_key --workers 1 --hours 1
python3 scripts/modal_worker.py status --workers 1
```

After the first run has registered, uploaded distinguished points, and been
checked in the dashboard, raise `--workers` to at most 8. Each named worker
keeps its own Modal Volume and run identity across restarts. Reuse the same app
name and worker index to resume its state. A sandbox ends after the requested
hours; run `start` again to resume. `stop` ends a sandbox but retains its volume:

```sh
python3 scripts/modal_worker.py stop --workers 1
```

The launch script passes the key as a Modal Secret and writes a mode-0600 file
inside the sandbox. It never logs the key. The key remains visible to the owner
of the Modal account and should be revoked on the website if that account is
compromised. One key can own several workers, subject to the website's run and
rate limits. Modal GPU time and persistent volume storage are billed to the
Modal account. Monitor usage and the site's upload backlog before increasing
worker count. The script does not delete volumes or archived server data.
