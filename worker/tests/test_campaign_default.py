"""Which challenge a worker starts on when ECC_CAMPAIGN is not set."""
from __future__ import annotations

import pytest

import client as entry
import generic.client as generic


@pytest.mark.parametrize("name", entry.STATE_FILES)
def test_existing_state_keeps_the_original_default(tmp_path, name):
    (tmp_path / name).write_text("{}")
    assert entry.default_campaign(tmp_path) == "ecc2k-130"


def test_fresh_directory_starts_on_the_installer_default(tmp_path):
    assert entry.default_campaign(tmp_path) == "eccp-131"


@pytest.mark.parametrize("env, files, expected", [
    (None, (), "eccp-131"),
    (None, ("generic-state.json",), "ecc2k-130"),
    ("ECC2-131", ("generic-state.json",), "ecc2-131"),
])
def test_entrypoint_passes_the_chosen_campaign(monkeypatch, tmp_path, env, files, expected):
    for name in files:
        (tmp_path / name).write_text("{}")
    monkeypatch.setenv("ECC_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("ECC_PROTOCOL", raising=False)
    if env is None:
        monkeypatch.delenv("ECC_CAMPAIGN", raising=False)
    else:
        monkeypatch.setenv("ECC_CAMPAIGN", env)
    monkeypatch.setattr(entry, "read_api_key", lambda: "ecc1_test")
    seen = {}
    monkeypatch.setattr(generic, "main", lambda root, key, campaign: seen.setdefault("campaign", campaign) and 0)
    entry.main()
    assert seen["campaign"] == expected


def test_protocol_1_on_a_fresh_directory_stays_on_ecc2k_130(monkeypatch, tmp_path):
    monkeypatch.setenv("ECC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ECC_PROTOCOL", "1")
    monkeypatch.delenv("ECC_CAMPAIGN", raising=False)
    monkeypatch.setattr(entry, "read_api_key", lambda: "ecc1_test")
    monkeypatch.setattr(generic, "main", lambda *args: pytest.fail("protocol 1 took the generic path"))
    seen = {}

    class Stop(Exception):
        pass

    def api(url, key, campaign):
        seen["campaign"] = campaign
        raise Stop

    monkeypatch.setenv("ECC_API_URL", "https://example.invalid")
    monkeypatch.setattr(entry, "API", api)
    with pytest.raises(Stop):
        entry.main()
    assert seen["campaign"] == "ecc2k-130"
