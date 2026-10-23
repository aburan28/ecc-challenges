import importlib.util
from pathlib import Path
import json
import pytest

pytest.importorskip("fcntl", reason="The legacy RunPod relay requires POSIX file locks")

spec = importlib.util.spec_from_file_location('legacy_relay', Path(__file__).parents[1] / 'legacy_relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

class Stop(BaseException):
    pass


def test_relay_retries_identical_bytes_after_lost_ack(tmp_path, monkeypatch):
    root = tmp_path / 'relay'
    root.mkdir()
    source = tmp_path / 'dp.bin'
    body = (249 << 48).to_bytes(8, 'little') + bytes(24)
    source.write_bytes(body)
    (root / 'api-key').write_text('test-key')
    monkeypatch.setattr(relay, 'ROOT', root)
    monkeypatch.setattr(relay, 'SOURCE', source)
    monkeypatch.setattr(relay.time, 'sleep', lambda _: None)
    posts = []
    def api(key, method, path, data=None):
        if method == 'GET':
            return {'cursor': 0}
        posts.append((path, data))
        if len(posts) == 1:
            raise RuntimeError('connection lost after remote commit')
        return {'cursor': 32, 'duplicate': True}
    monkeypatch.setattr(relay, 'request', api)
    save = relay.save
    def stop_after_ack(state):
        save(state)
        if state['cursor'] == 32:
            raise Stop()
    monkeypatch.setattr(relay, 'save', stop_after_ack)
    with pytest.raises(Stop):
        relay.main()
    assert len(posts) == 2 and posts[0] == posts[1]
    state = json.loads((root / 'relay-state.json').read_text())
    assert state['cursor'] == 32 and state['pending'] is None
    source.unlink()
    assert (root / state['generations'][0]['file']).read_bytes() == body


def test_relay_refuses_changed_run_and_bad_encoding():
    with pytest.raises(RuntimeError, match='run changed'):
        relay.check_records((250 << 48).to_bytes(8, 'little') + bytes(24))
    with pytest.raises(RuntimeError, match='encoding'):
        relay.check_records((249 << 48).to_bytes(8, 'little') + (1 << 132).to_bytes(24, 'little'))


def test_pending_retry_survives_source_unlink(tmp_path, monkeypatch):
    root = tmp_path / 'relay'
    root.mkdir()
    source = tmp_path / 'dp.bin'
    body = (249 << 48).to_bytes(8, 'little') + bytes(24)
    source.write_bytes(body)
    (root / 'api-key').write_text('test-key')
    monkeypatch.setattr(relay, 'ROOT', root)
    monkeypatch.setattr(relay, 'SOURCE', source)
    posts = []
    sleeps = []
    def sleep(_):
        sleeps.append(1)
        if len(sleeps) > 4:
            raise Stop()
    monkeypatch.setattr(relay.time, 'sleep', sleep)
    def api(key, method, path, data=None):
        if method == 'GET':
            return {'cursor': 0}
        posts.append((path, data))
        if len(posts) == 1:
            source.unlink()
            raise ConnectionError('lost ack during source rotation')
        return {'cursor': 32, 'duplicate': True}
    monkeypatch.setattr(relay, 'request', api)
    save = relay.save
    def save_and_stop(state):
        save(state)
        if state['cursor'] == 32:
            raise Stop()
    monkeypatch.setattr(relay, 'save', save_and_stop)
    with pytest.raises(Stop):
        relay.main()
    assert len(posts) == 2 and posts[0] == posts[1]
    assert json.loads((root / 'relay-state.json').read_text())['cursor'] == 32


def test_failed_pending_journal_write_stops_before_post(tmp_path, monkeypatch):
    root = tmp_path / 'relay'
    root.mkdir()
    source = tmp_path / 'dp.bin'
    source.write_bytes((249 << 48).to_bytes(8, 'little') + bytes(24))
    (root / 'api-key').write_text('test-key')
    monkeypatch.setattr(relay, 'ROOT', root)
    monkeypatch.setattr(relay, 'SOURCE', source)
    sleeps = []
    def sleep(_):
        sleeps.append(1)
        if len(sleeps) > 4:
            raise Stop()
    monkeypatch.setattr(relay.time, 'sleep', sleep)
    posts = []
    def api(key, method, path, data=None):
        if method == 'GET':
            return {'cursor': 0}
        posts.append(data)
        return {'cursor': 32}
    monkeypatch.setattr(relay, 'request', api)
    replace = relay.os.replace
    def fail_pending(src, dst):
        if dst.name == 'relay-state.json' and json.loads(src.read_text())['pending']:
            raise OSError('disk write failed')
        replace(src, dst)
    monkeypatch.setattr(relay.os, 'replace', fail_pending)
    with pytest.raises(OSError):
        relay.main()
    assert posts == []
    assert (root / 'pending.bin').read_bytes() == source.read_bytes()
