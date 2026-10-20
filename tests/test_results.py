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
            assert len(links) == len(SOLVED)
            assert set(links) == SOLVED


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
