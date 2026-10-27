"""Threshold selection must not silently change an existing or pending walk."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest


@pytest.mark.parametrize("status,method", [(301, "GET"), (302, "GET"), (303, "POST"),
                                          (307, "GET"), (308, "GET")])
def test_gateway_redirect_does_not_forward_key(sigma_client, status, method):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            if self.path == "/start":
                self.send_response(status)
                # A different hostname models a redirect outside the configured origin.
                self.send_header("Location", f"http://localhost:{self.server.server_port}/sink")
            else:
                self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            api = sigma_client.API(f"http://127.0.0.1:{server.server_port}", "local-test-credential")
            with pytest.raises(RuntimeError, match="redirect"):
                api.request(method, "/start", b"" if method == "POST" else None)
            assert received == [("/start", "Bearer local-test-credential")]
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def sigma_client(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "worker"))
    monkeypatch.delenv("ECC_DP_WEIGHT", raising=False)
    import client
    return client


class Gateway:
    campaign = "ecc2k-130"

    def __init__(self, walk_id):
        self.config = {"campaign": self.campaign, "walk_id": walk_id, "record_bytes": 32,
                       "dp_weight": 32, "recommended_dp_weight": 34, "supported_dp_weights": [30, 32, 34]}
        self.registered_weight = None
        self.registrations = []
        self.lose_response = False

    def request(self, method, path, data=None):
        if path.startswith("/v1/config"):
            return self.config
        if path == "/v1/register":
            self.registrations.append((self.registration_id, self.dp_weight))
            if self.registered_weight is not None:
                assert self.registered_weight == self.dp_weight
            self.registered_weight = self.dp_weight
            if self.lose_response:
                raise ConnectionError("registration response lost")
        return {"campaign": self.campaign, "walk_id": self.config["walk_id"],
                "run_id": 50000, "dp_weight": self.registered_weight, "checkpoint_at": None, "cursor": 0}


@pytest.mark.parametrize("choice, expected", [(None, 34), (30, 30), (32, 32), (34, 34)])
def test_new_run_uses_recommendation_or_explicit_choice(sigma_client, monkeypatch, tmp_path, choice, expected):
    api = Gateway(sigma_client.WALK_ID)
    if choice is not None:
        monkeypatch.setenv("ECC_DP_WEIGHT", str(choice))
    state = sigma_client.ensure_identity(api, tmp_path)
    assert state["dp_weight"] == api.registered_weight == expected
    assert json.loads((tmp_path / "registration.json").read_text())["dp_weight"] == expected


def test_older_gateway_keeps_original_default(sigma_client, tmp_path):
    api = Gateway(sigma_client.WALK_ID)
    del api.config["recommended_dp_weight"]
    del api.config["supported_dp_weights"]
    assert sigma_client.ensure_identity(api, tmp_path)["dp_weight"] == 32


@pytest.mark.parametrize("saved_weight", [None, 30, 32, 34])
def test_resume_preserves_original_threshold(sigma_client, tmp_path, saved_weight):
    api = Gateway(sigma_client.WALK_ID)
    api.registered_weight = saved_weight or 32
    state = {"protocol": "ecc-challenges-v1", "campaign": api.campaign, "run_id": 50000,
             "remote_cursor": 0, "uploaded_local": 0}
    if saved_weight is not None:
        state["dp_weight"] = saved_weight
    (tmp_path / "state.json").write_text(json.dumps(state))
    sigma_client.ensure_identity(api, tmp_path)
    assert api.dp_weight == (saved_weight or 32)
    assert api.registrations == []


def test_lost_registration_response_keeps_threshold_across_recommendation_change(sigma_client, tmp_path):
    api = Gateway(sigma_client.WALK_ID)
    api.lose_response = True
    with pytest.raises(ConnectionError):
        sigma_client.ensure_identity(api, tmp_path)
    api.config["recommended_dp_weight"] = 30
    api.lose_response = False
    assert sigma_client.ensure_identity(api, tmp_path)["dp_weight"] == 34
    assert len(api.registrations) == 2 and api.registrations[0] == api.registrations[1]


def test_pending_registration_rejects_threshold_change(sigma_client, monkeypatch, tmp_path):
    api = Gateway(sigma_client.WALK_ID)
    api.lose_response = True
    with pytest.raises(ConnectionError):
        sigma_client.ensure_identity(api, tmp_path)
    monkeypatch.setenv("ECC_DP_WEIGHT", "30")
    with pytest.raises(RuntimeError, match="pending registration"):
        sigma_client.ensure_identity(api, tmp_path)
    assert len(api.registrations) == 1


def test_old_pending_registration_without_threshold_keeps_dp32(sigma_client, tmp_path):
    api = Gateway(sigma_client.WALK_ID)
    (tmp_path / "registration.json").write_text(json.dumps({"registration_id": "old-registration"}))
    assert sigma_client.ensure_identity(api, tmp_path)["dp_weight"] == 32


def test_unsupported_threshold_does_not_register(sigma_client, monkeypatch, tmp_path):
    api = Gateway(sigma_client.WALK_ID)
    monkeypatch.setenv("ECC_DP_WEIGHT", "36")
    with pytest.raises(RuntimeError, match="does not support"):
        sigma_client.ensure_identity(api, tmp_path)
    assert not api.registrations and not (tmp_path / "registration.json").exists()
