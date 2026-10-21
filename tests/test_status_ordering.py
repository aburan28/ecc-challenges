"""Both challenge tables preserve identity and progress when grouped by status."""

import re

from fastapi.testclient import TestClient
import pytest

from ecc_challenges.status import StatusReader
from test_catalog import application
from test_results import UnavailableDB


@pytest.mark.parametrize("mode", ["live", "reversed", "offline"])
@pytest.mark.parametrize("path", ["/", "/status"])
def test_tables_group_numeric_sizes_without_moving_counts_or_results(monkeypatch, mode, path):
    monkeypatch.delenv("MULTICURVE_ENABLED", raising=False)
    app = application(db=UnavailableDB() if mode == "offline" else None)
    with TestClient(app, base_url="https://ecc-challenges.com") as client:
        if mode == "reversed":
            snapshot = client.get("/v1/status").json()
            snapshot["campaigns"].reverse()
            monkeypatch.setattr(StatusReader, "read", lambda self: snapshot)
        response = client.get(path)
    assert response.status_code == 200
    table = re.search(r'<table class="challenge-status">(.*?)</table>', response.text, re.S).group(1)
    headings = re.findall(r'<th scope="rowgroup"[^>]*>(.*?)</th>', table)
    assert headings == [
        "Unsolved challenges · smallest to largest bit size",
        "Solved 1997–2004 · smallest to largest bit size",
    ]
    rows = re.findall(r'<tr class="(solved|open|not-started)">(.*?)</tr>', table, re.S)
    assert len(rows) == 24
    assert [state == "solved" for state, _ in rows] == [False] * 14 + [True] * 10
    sizes = [int(re.search(r'<td class="num">(\d+)</td>', html).group(1)) for _, html in rows]
    assert sizes[:14] == [131, 131, 131, 163, 163, 163, 191, 191, 239, 239, 239, 359, 359, 359]
    assert sizes[14:] == [79, 79, 89, 89, 97, 97, 97, 109, 109, 109]
    for state, html in rows:
        destination, campaign = re.search(r'class="name" href="/(results|challenges)/([^"]+)"', html).groups()
        cells = re.findall(r'<td(?: [^>]*)?>(.*?)</td>', html, re.S)
        if state == "solved":
            assert destination == "results"
        elif mode != "offline" and campaign == "ecc2k-130":
            assert destination == "challenges"
            assert state == "open"
            if path == "/status":
                assert '<span class="mono">5.96%</span>' in cells[4]
                assert '&lt;0.01% public uploads only' in cells[4]
                assert cells[6:] == ["3", "2", "338,034,801", "4"]
            else:
                assert '<span class="pct progress-primary">5.96%</span>' in cells[5]
                assert '&lt;0.01% public uploads only' in cells[5]
        else:
            assert destination == "challenges"
            assert state == "not-started"
            if path == "/":
                assert cells[5].strip() == "—"
