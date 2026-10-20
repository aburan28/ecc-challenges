"""Best-effort liveness, independent of compute rounds and upload retries."""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HEARTBEAT_SECONDS = 60
REQUEST_TIMEOUT = 10


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("heartbeat redirects are not allowed")


class Heartbeat:
    def __init__(self, origin: str, key: str):
        parsed = urlsplit(origin)
        local = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (not parsed.hostname or (parsed.scheme != "https" and not local)
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("heartbeat requires an HTTPS gateway")
        self.request = Request(origin.rstrip("/") + "/v1/heartbeat", method="POST", data=b"",
                               headers={"Authorization": "Bearer " + key,
                                        "User-Agent": "ecc-challenges-worker/heartbeat"})
        self.opener = build_opener(NoRedirect)
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ecc-heartbeat", daemon=True)

    def _run(self):
        while not self.stopped.is_set():
            started = time.monotonic()
            try:
                with self.opener.open(self.request, timeout=REQUEST_TIMEOUT) as response:
                    if response.status != 204:
                        raise RuntimeError("unexpected heartbeat status")
            except Exception as exc:
                # Only the exception type: URLs, headers and response bodies can contain secrets.
                print(json.dumps({"event": "heartbeat-failed", "error": type(exc).__name__}), flush=True)
            self.stopped.wait(max(0, HEARTBEAT_SECONDS - (time.monotonic() - started)))

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        # No offline message: another worker may be using the same key.
        self.stopped.set()
        self.thread.join(timeout=1)
