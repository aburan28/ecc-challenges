import json
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from eccpool.ec import Curve
from eccpool.pool import PoolConfig
from eccpool.server import ServerConfig, create_app

ADMIN = "admin-" + "x" * 40

# The published 32-bit challenge curve; tests pick their own Q = k*G so the
# discrete log is known.
_CH = json.loads((Path(__file__).resolve().parent.parent / "challenges" / "ecc32.json").read_text())
SMALL = Curve.from_dict(_CH["curve"])



@pytest.fixture(scope="session")
def curve() -> Curve:
    return SMALL


@pytest.fixture
def secret_k(curve):
    return secrets.randbelow(curve.n - 1) + 1


@pytest.fixture
def make_app(tmp_path):
    apps = []

    def factory(**pool_kw) -> TestClient:
        cfg = ServerConfig(db_path=str(tmp_path / f"pool{len(apps)}.sqlite3"),
                           pepper=b"p" * 32, admin_token=ADMIN,
                           pool=PoolConfig(**pool_kw), run_auditor=False)
        app = create_app(cfg)
        client = TestClient(app)
        apps.append(client)
        return client

    yield factory
    for c in apps:
        c.close()


def admin_headers() -> dict:
    return {"Authorization": f"Bearer {ADMIN}"}


def issue_key(client: TestClient, label: str = "w", **limits) -> tuple[str, dict]:
    r = client.post("/admin/keys", json={"label": label, **limits}, headers=admin_headers())
    assert r.status_code == 201, r.text
    token = r.json()["api_key"]
    return token, {"Authorization": f"Bearer {token}"}


def create_job(client: TestClient, curve: Curve, k: int, dp_bits: int = 6, **kw) -> dict:
    Q = curve.mul(k, curve.G)
    r = client.post("/admin/jobs", headers=admin_headers(), json={
        "name": "test", "dp_bits": dp_bits, "curve": curve.to_dict(),
        "Q": [hex(Q[0]), hex(Q[1])], **kw})
    assert r.status_code == 201, r.text
    return r.json()
