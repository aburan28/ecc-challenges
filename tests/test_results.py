"""Public result pages must resolve every solved link without a live database."""

from contextlib import contextmanager
import re

from fastapi.testclient import TestClient
import pytest

from ecc_challenges.catalog import CHALLENGES
from ecc_challenges.results import PUBLISHED_RESULTS, published_result
from test_catalog import application


SOLVED = {challenge.id for challenge in CHALLENGES if challenge.solved_in_2009}


class UnavailableDB:
    @contextmanager
    def connection(self):
        raise RuntimeError("database is offline")
        yield  # pragma: no cover


@pytest.mark.parametrize("campaign", sorted(SOLVED))
def test_published_result_is_public_and_complete_without_database(campaign):
    with TestClient(application(db=UnavailableDB()), base_url="https://ecc-challenges.com") as client:
        response = client.get(f"/results/{campaign}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"].startswith("private,")
    result = published_result(campaign)
    assert f"<h1>{result['challenge']['name']}</h1>" in response.text
    assert f'id="scalar-hex">{result["scalar_hex"]}</code>' in response.text
    assert f'id="scalar-decimal">{result["scalar_decimal"]}</code>' in response.text
    assert f'href="{result["source_url"]}"' in response.text
    assert f'datetime="{result["solved_on"]}"' in response.text
    assert 'href="/status"' in response.text
    for value in result["instance"]["Q"] + result["instance"]["G"]:
        assert f"<code>{value}</code>" in response.text


@pytest.mark.parametrize("offline", [False, True])
def test_only_solved_challenges_link_to_results_on_both_catalog_pages(offline):
    assert set(PUBLISHED_RESULTS) == SOLVED
    app = application(db=UnavailableDB() if offline else None)
    with TestClient(app, base_url="https://ecc-challenges.com") as client:
        for path in ("/", "/status"):
            response = client.get(path)
            assert response.status_code == 200
            links = re.findall(r'href="/results/([^"]+)"', response.text)
            assert len(links) == 2 * len(SOLVED)
            assert set(links) == SOLVED
            names = re.findall(r'class="name" href="/results/([^"]+)"', response.text)
            assert len(names) == len(SOLVED)
            assert set(names) == SOLVED
            for campaign in names:
                result = client.get(f"/results/{campaign}")
                assert result.status_code == 200
                assert published_result(campaign)["scalar_hex"] in result.text
                assert published_result(campaign)["scalar_decimal"] in result.text


@pytest.mark.parametrize("campaign", ["ecc2k-130", "eccp-359", "not-a-challenge"])
def test_missing_and_unsolved_results_use_existing_404_page(campaign):
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        response = client.get(f"/results/{campaign}")
    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "scalar-hex" not in response.text


def test_source_decimal_and_parameter_normalization_are_preserved():
    # Section 6.1.10 prints this scalar in decimal, unlike the other nine.
    assert published_result("eccp-109")["scalar_decimal"] == "281183840311601949668207954530684"
    with TestClient(application(), base_url="https://ecc-challenges.com") as client:
        assert "reduced modulo p" in client.get("/results/eccp-89").text
        assert "97-bit challenge" in client.get("/results/ecc2k-95").text


def test_solved_challenges_show_a_complete_search_instead_of_live_progress(monkeypatch):
    """Certicom's solved list closes a challenge: 100%, never this gateway's test-run estimate."""
    from itsdangerous import TimestampSigner
    import base64
    import json

    from ecc_challenges.service import Service

    monkeypatch.setattr(Service, "account_keys", lambda *args: [])
    monkeypatch.setattr(Service, "account_runs", lambda *args, **kwargs: None)
    session = TimestampSigner("s" * 48).sign(base64.b64encode(json.dumps(
        {"github_id": 1, "login": "test", "csrf": "test"}).encode())).decode()
    with TestClient(application(multicurve_enabled=True), base_url="https://ecc-challenges.com") as client:
        for path in ("/", "/status"):
            table = re.search(r'<table class="challenge-status">(.*?)</table>', client.get(path).text, re.S).group(1)
            rows = re.findall(r'<tr class="(solved|open|not-started)">(.*?)</tr>', table, re.S)
            solved = [html for state, html in rows if state == "solved"]
            assert len(solved) == len(SOLVED)
            for html in solved:
                assert '<span class="pct done">' in html and ">Done</span>" in html
                assert 'class="bar mini done"' in html and 'style="width:100%"' in html
                assert "operations</small>" not in html and "public uploads only" not in html
            assert any("Solved 16 December 1997" in html and "by Robert J. Harley" in html for html in solved)
            assert all('bar mini done' not in html for state, html in rows if state != "solved")
        page = client.get("/challenges/ecc2-79").text
        assert "<dt>Progress</dt><dd><span class=\"progress-primary\">100%</span>" in page
        assert "Already solved" in page and 'href="/results/ecc2-79"' in page
        for live in ("Collision odds", "Archived points", "batches awaiting checks", "Join the search",
                     "does not yet fold in"):
            assert live not in page, live
        open_page = client.get("/challenges/eccp-131").text
        assert "Already solved" not in open_page and "Collision odds" in open_page
        client.cookies.set("ecc_session", session)
        dashboard = client.get("/dashboard?campaign=ecc2-79").text
        progress = dashboard.split('aria-labelledby="challenge-progress"', 1)[1].split("</section>", 1)[0]
        assert '<span class="progress-primary">100%</span>' in progress
        assert "Solved 16 December 1997" in progress and 'href="/results/ecc2-79"' in progress
        assert "Already solved" in progress and "expected operations" not in progress
        assert '<optgroup label="Open challenges">' in dashboard
        assert '<optgroup label="Solved by 2004, test runs only">' in dashboard
