"""Status grouping must preserve campaign identity and combined progress."""

import re

from fastapi.testclient import TestClient
import pytest

from ecc_challenges.status import StatusReader
from test_catalog import application
from test_results import UnavailableDB


@pytest.mark.parametrize("mode", ["live", "reversed", "offline"])
def test_status_groups_numeric_sizes_without_moving_counts_or_results(monkeypatch, mode):
    monkeypatch.delenv("MULTICURVE_ENABLED", raising=False)
    app = application(db=UnavailableDB() if mode == "offline" else None)
    with TestClient(app, base_url="https://ecc-challenges.com") as client:
        if mode == "reversed":
            snapshot = client.get("/v1/status").json()
            snapshot["campaigns"].reverse()
            monkeypatch.setattr(StatusReader, "read", lambda self: snapshot)
        response = client.get("/status")
    assert response.status_code == 200
    table = re.search(r'<table class="challenge-status">(.*?)</table>', response.text, re.S).group(1)
    rows = re.findall(r'<tr class="(solved|open|not-started)">(.*?)</tr>', table, re.S)
    assert len(rows) == 24
    assert [state == "solved" for state, _ in rows] == [False] * 14 + [True] * 10
    sizes = [int(re.search(r'<td class="num">(\d+)</td>', html).group(1)) for _, html in rows]
    assert sizes[:14] == [131, 131, 131, 163, 163, 163, 191, 191, 239, 239, 239, 359, 359, 359]
    assert sizes[14:] == [79, 79, 89, 89, 97, 97, 97, 109, 109, 109]
    for state, html in rows:
        campaign = re.search(r'href="/challenges/([^"]+)"', html).group(1)
        if state == "solved":
            assert f'href="/results/{campaign}"' in html
        elif mode != "offline" and campaign == "ecc2k-130":
            assert state == "open"
            cells = re.findall(r'<td(?: [^>]*)?>(.*?)</td>', html, re.S)
            assert '<span class="mono">5.96%</span>' in cells[4]
            assert '&lt;0.01% public uploads only' in cells[4]
            assert cells[6:] == ["3", "2", "338,034,801", "4"]
        else:
            assert state == "not-started"
