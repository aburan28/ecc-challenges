"""Contributor entry points work from packaged assets and give usable commands."""
from html.parser import HTMLParser
from pathlib import Path
import shlex
import subprocess

from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from ecc_challenges.app import TEMPLATES
from ecc_challenges.catalog import CHALLENGES
from test_catalog import application
from test_results import UnavailableDB


def test_public_skill_is_canonical_and_needs_no_database():
    source = Path(__file__).resolve().parents[1] / "skills/ecc-challenges-worker/SKILL.md"
    with TestClient(application(db=UnavailableDB()), base_url="https://ecc-challenges.com") as client:
        response = client.get("/skill.md")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.headers["cache-control"] == "public, max-age=300"
        assert response.content == source.read_bytes()
        assert client.get("/docs/agents").status_code == 200


class Commands(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.commands = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "pre" and self.current is not None:
            self.commands.append("".join(self.current))
            self.current = None


@pytest.mark.parametrize("campaign", ["ecc2k-130", "eccp-131"])
def test_dashboard_docker_commands_start_named_workers_and_follow_logs(campaign):
    request = Request({"type": "http", "method": "GET", "path": "/dashboard", "query_string": b"",
                       "headers": [], "session": {"github_id": 1}, "scheme": "https",
                       "server": ("ecc-challenges.com", 443)})
    html = TEMPLATES.get_template("dashboard.html").render(
        request=request, login="me", keys=[], csrf="x", issued=None, challenges=CHALLENGES,
        worker_image="example.invalid/worker@sha256:" + "a" * 64,
        cpu_worker_image="example.invalid/cpu@sha256:" + "b" * 64,
        activity=None, multicurve_enabled=True, campaign_status=None,
        names={c.id: c.public()["name"] for c in CHALLENGES}, selected_campaign=campaign,
        client_os="linux", selected_dp_weight=34, recommended_dp_weight=34,
        sigma_dp_weights=[34, 32, 30])
    commands = [text for text in Commands(html).commands if "docker run" in text]
    assert len(commands) == 2
    for command in commands:
        # Execute the copied shell recipe with inert Docker and mkdir commands.
        # This checks shell expansion and command sequencing without Docker,
        # credentials, filesystem mutation or contributor enrollment.
        completed = subprocess.run(
            ["sh", "-c", 'docker() { printf "%s\\n" "$*"; }; mkdir() { :; };\n' + command],
            capture_output=True, text=True, check=True)
        run, logs = [shlex.split(line) for line in completed.stdout.splitlines()]
        assert run[0] == "run" and "-d" in run
        assert run[run.index("--stop-timeout") + 1] == "120"
        name = run[run.index("--name") + 1]
        assert campaign in name
        assert logs == ["logs", "--tail", "100", "-f", name]
        assert f"ECC_CAMPAIGN={campaign}" in run
        assert any(item.endswith(":/data") for item in run)
        assert any(item.endswith(":/run/secrets/ecc_api_key:ro") for item in run)
