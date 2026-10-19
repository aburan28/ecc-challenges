"""Prevent lost or replaced run identities during the Modal layout upgrade."""
import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('modal_state',Path(__file__).resolve().parents[1]/'scripts/modal_state.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
prepare_state=module.prepare_state


def test_legacy_move_resumes_after_interrupted_rename(tmp_path,monkeypatch):
    import os
    checkpoint=b'{"run_id":"preserve-this-run","pending":"abcd"}'
    (tmp_path/'generic-state.json').write_bytes(checkpoint)
    (tmp_path/'generic-registration.json').write_text('durable registration nonce')
    (tmp_path/'.contributor.lock').touch()
    original=os.rename
    def interrupted(*args):
        original(*args)
        raise RuntimeError('interrupted bootstrap')
    monkeypatch.setattr(os,'rename',interrupted)
    with pytest.raises(RuntimeError,match='interrupted bootstrap'):
        prepare_state(str(tmp_path))
    monkeypatch.setattr(os,'rename',original)
    state=Path(prepare_state(str(tmp_path)))
    assert (state/'generic-state.json').read_bytes()==checkpoint
    assert (state/'generic-registration.json').read_text()=='durable registration nonce'
    assert (state/'.contributor.lock').is_file()
    assert not (tmp_path/'generic-state.json').exists()
    assert not (tmp_path/'.ecc-state-migration.json').exists()
    assert Path(prepare_state(str(tmp_path)))==state
    assert (state/'generic-state.json').read_bytes()==checkpoint


def test_conflicting_run_directories_remain_untouched(tmp_path):
    (tmp_path/'generic-state.json').write_text('old run')
    state=tmp_path/'ecc-state';state.mkdir()
    (state/'generic-state.json').write_text('other run')
    with pytest.raises(RuntimeError,match='both Modal root and child'):
        prepare_state(str(tmp_path))
    assert (tmp_path/'generic-state.json').read_text()=='old run'
    assert (state/'generic-state.json').read_text()=='other run'


def test_symlinks_cannot_redirect_bootstrap_writes(tmp_path):
    outside=tmp_path/'outside';outside.mkdir()
    mount=tmp_path/'volume';mount.mkdir()
    (mount/'ecc-state').symlink_to(outside,target_is_directory=True)
    with pytest.raises(RuntimeError,match='real directory'):
        prepare_state(str(mount))
    assert list(outside.iterdir())==[]


def test_migration_marker_cannot_escape_volume(tmp_path):
    (tmp_path/'.ecc-state-migration.json').write_text(json.dumps(['../outside']))
    with pytest.raises(RuntimeError,match='invalid Modal state migration marker'):
        prepare_state(str(tmp_path))


def test_provider_mount_symlink_resolves_to_owned_child(tmp_path):
    volume=tmp_path/'provider-volume';volume.mkdir()
    mount=tmp_path/'data';mount.symlink_to(volume,target_is_directory=True)
    assert Path(prepare_state(str(mount)))==volume/'ecc-state'


def test_interrupted_marker_write_leaves_original_checkpoint_recoverable(tmp_path,monkeypatch):
    (tmp_path/'generic-state.json').write_text('original checkpoint')
    original=json.dump
    def interrupted(value,output):
        output.write('[')
        raise RuntimeError('interrupted marker write')
    monkeypatch.setattr(json,'dump',interrupted)
    with pytest.raises(RuntimeError,match='interrupted marker write'):
        prepare_state(str(tmp_path))
    assert not (tmp_path/'.ecc-state-migration.json').exists()
    assert (tmp_path/'generic-state.json').read_text()=='original checkpoint'
    monkeypatch.setattr(json,'dump',original)
    state=Path(prepare_state(str(tmp_path)))
    assert (state/'generic-state.json').read_text()=='original checkpoint'
