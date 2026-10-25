"""Launch review: public production pages and an isolated authenticated fixture.

Run with the project's test dependencies, Playwright, PostgreSQL binaries and
Chrome installed. No production login or write is performed. Local credentials
exist only in the disposable test database and are not written to evidence.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
import conftest
from test_key_activity import session_cookie
from test_pipeline import record
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from ecc_challenges.app import create_app
from ecc_challenges.catalog import CHALLENGES
from ecc_challenges.service import Service


class TempFactory:
    def __init__(self, root):
        self.root = root

    def mktemp(self, name):
        return Path(tempfile.mkdtemp(prefix=name, dir=self.root))


def main():
    if os.environ.get("TEST_POSTGRES_DSN"):
        raise RuntimeError("Unset TEST_POSTGRES_DSN: this check requires disposable local PostgreSQL")
    evidence = Path(__file__).parent
    report = {"as_of": datetime.now(timezone.utc).isoformat(), "public": [], "local": []}
    with ExitStack() as stack:
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="ecc-browser-"))
        dsn = stack.enter_context(contextmanager(conftest.postgres_dsn.__wrapped__)(TempFactory(temporary)))
        database = stack.enter_context(contextmanager(conftest.database.__wrapped__)(dsn))
        stack.callback(database.close)
        aws = stack.enter_context(contextmanager(conftest.aws_resources.__wrapped__)())
        settings = replace(conftest.settings.__wrapped__(dsn, aws), multicurve_enabled=True,
                           public_origin="https://ecc-review.invalid")
        service = Service(database, aws[0], settings)
        service.upsert_account(12, "review-fixture")
        key = service.authenticate(service.create_key(12, "fixture worker"))
        run = service.register(key, str(uuid4()))["run_id"]
        service.upload_batch(key, run, 0, record(run, 1))
        with database.connection() as conn:
            conn.execute("UPDATE ecc_public.batches SET audit_state='unverified',audit_finished_at=now()")
            conn.execute("UPDATE ecc_public.api_keys SET created_at=now()-interval '2 minutes'")
        client = stack.enter_context(TestClient(create_app(settings, db=database, storage=aws[0]),
                                                base_url=settings.public_origin))
        client.cookies.set("ecc_session", session_cookie(12, settings))
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True,
                    executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        stack.callback(browser.close)
        public = browser.new_context()
        page = public.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        paths = ["/", "/status", "/challenges/ecc2k-130", "/challenges/eccp-79",
                 "/results/eccp-79", "/pollard-rho", "/docs/running-a-worker"]
        for width in (320, 390, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            for path in paths:
                response = page.goto("https://ecc-challenges.com" + path, wait_until="networkidle")
                overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
                assert response.status == 200 and not overflow and not errors, (path, width, errors)
                report["public"].append({"path": path, "width": width, "status": response.status,
                                         "overflow": overflow})
                if path == "/status":
                    page.screenshot(path=str(evidence / f"public-status-{width}.png"), full_page=True)
        # Every catalog and published-result route must resolve in production.
        for challenge in CHALLENGES:
            response = public.request.get("https://ecc-challenges.com/challenges/" + challenge.id)
            assert response.status == 200, challenge.id
            if challenge.solved_in_2009:
                response = public.request.get("https://ecc-challenges.com/results/" + challenge.id)
                assert response.status == 200, challenge.id
        report["public_catalog_routes"] = 24
        report["public_result_routes"] = sum(c.solved_in_2009 for c in CHALLENGES)
        page.goto("https://ecc-challenges.com/status", wait_until="networkidle")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/status") as response:
            page.locator("[data-live-refresh]").click()
        assert response.value.status == 200
        report["public_manual_refresh"] = "passed"
        public.close()

        local = browser.new_context()
        requests = []
        fail_refresh = False

        def route_request(route):
            req = route.request
            path = urlsplit(req.url).path
            if fail_refresh and path == "/dashboard" and req.resource_type == "fetch":
                route.fulfill(status=503, body="temporarily unavailable")
                return
            requests.append((req.method, path))
            response = client.request(req.method, req.url, content=req.post_data_buffer,
                                      headers={k: v for k, v in req.headers.items()
                                               if k.lower() not in ("cookie", "host")})
            route.fulfill(status=response.status_code,
                          headers={k: v for k, v in response.headers.items()
                                   if k.lower() not in ("content-length", "content-encoding", "set-cookie")},
                          body=response.content)

        local.route("https://ecc-review.invalid/**", route_request)
        page = local.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        for width in (320, 390, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            for path in ("/dashboard?campaign=eccp-131", f"/dashboard/workers/v1/{run}"):
                response = page.goto(settings.public_origin + path, wait_until="networkidle")
                assert response.status == 200 and not errors
                assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
                report["local"].append({"path": path, "width": width, "status": response.status})
        assert "1 batch unresolved" in page.inner_text("body")
        assert page.locator(".stat", has_text="Waiting for processing").locator("dd").inner_text().startswith("0")
        page.goto(settings.public_origin + "/dashboard?campaign=eccp-131", wait_until="networkidle")
        page.locator("#label").fill("browser-created fixture")
        page.get_by_role("button", name="Create key", exact=True).click()
        page.wait_for_url("**/keys")
        secret = page.locator(".secret").inner_text()
        page.locator("#label").fill("preserved unsent input")
        with page.expect_response(lambda r: urlsplit(r.url).path == "/dashboard") as response:
            page.locator("[data-live-refresh]").click()
        assert response.value.status == 200
        expect(page.locator("[data-live-refresh]")).to_be_enabled()
        assert "campaign=eccp-131" in response.value.url
        assert page.locator(".secret").inner_text() == secret
        assert page.locator("#label").input_value() == "preserved unsent input"
        assert requests.count(("POST", "/keys")) == 1 and ("GET", "/keys") not in requests
        fail_refresh = True
        page.locator("[data-live-refresh]").click()
        expect(page.locator("[data-live-message]")).to_contain_text("Updates unavailable")
        fail_refresh = False
        page.locator("[data-live-refresh]").click()
        expect(page.locator("[data-live-refresh]")).to_be_enabled()
        expect(page.locator("[data-live-message]")).not_to_contain_text("Updates unavailable")
        assert page.locator(".secret").inner_text() == secret
        report["local_key_creation_refresh"] = "passed; selected campaign, secret and form input preserved"
        report["local_refresh_failure_recovery"] = "passed"
        report["javascript_errors"] = errors
        local.close()
    (evidence / "browser-results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
