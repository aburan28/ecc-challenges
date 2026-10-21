"""Exercise the background sender against a local HTTP gateway, without secrets."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import json
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))
from generic import heartbeat


@pytest.fixture
def gateway():
    calls = []
    ready = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.payloads.append(json.loads(raw) if raw else None)
            calls.append((self.path, self.headers.get("Authorization"), time.monotonic()))
            status = self.server.responses.pop(0) if self.server.responses else 204
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            if len(calls) >= 2:
                ready.set()
        def do_GET(self):
            calls.append((self.path, self.headers.get("Authorization"), time.monotonic()))
            self.send_response(204)
            self.end_headers()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.responses = [503, 204]
    server.payloads = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, calls, ready
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_ping_without_points_retries_and_stops(gateway, monkeypatch, capsys):
    server, calls, ready = gateway
    assert heartbeat.HEARTBEAT_SECONDS == 60
    monkeypatch.setattr(heartbeat, "HEARTBEAT_SECONDS", 0.05)
    started = time.monotonic()
    with heartbeat.Heartbeat(f"http://127.0.0.1:{server.server_port}", "test-only-key") as sender:
        assert ready.wait(2)
    assert calls[0][2] - started < 1  # immediate first ping
    assert calls[1][2] - calls[0][2] >= 0.03
    assert all(path == "/v1/heartbeat" and auth == "Bearer test-only-key" for path, auth, _ in calls)
    count = len(calls)
    time.sleep(0.1)
    assert len(calls) == count and not sender.thread.is_alive()
    logs = capsys.readouterr().out
    assert 'heartbeat-failed' in logs and 'test-only-key' not in logs


def test_redirect_does_not_forward_credential(gateway, monkeypatch):
    server, calls, ready = gateway
    server.responses = [302, 204]
    monkeypatch.setattr(heartbeat, "HEARTBEAT_SECONDS", 0.02)
    with heartbeat.Heartbeat(f"http://127.0.0.1:{server.server_port}", "test-only-key"):
        assert ready.wait(2)
    assert all(path == "/v1/heartbeat" for path, *_ in calls)


@pytest.mark.parametrize("origin", ["http://example.com", "https://user:pass@example.com",
                                    "https://example.com?key=secret", "https://example.com#key"])
def test_heartbeat_rejects_invalid_gateway(origin):
    with pytest.raises(ValueError):
        heartbeat.Heartbeat(origin, "test-only-key")


def test_run_identity_and_health_are_sent_without_point_activity(gateway, monkeypatch):
    server, calls, ready = gateway
    monkeypatch.setattr(heartbeat, "HEARTBEAT_SECONDS", 0.03)
    with heartbeat.Heartbeat(f"http://127.0.0.1:{server.server_port}", "test-only-key",
                             protocol="v1", run_id=50000, backend="cuda") as sender:
        sender.update("upload_retry")
        assert ready.wait(2)
    report = server.payloads[-1]
    assert report["run_id"] == "50000" and report["protocol"] == "v1"
    assert report["state"] == "upload_retry" and report["backend"] == "cuda"
    assert "test-only-key" not in json.dumps(report)
