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
