import hashlib

from botocore.exceptions import ClientError
import pytest

from ecc_challenges.multicurve import pointindex
from ecc_challenges.multicurve.instances import registry_metadata
from ecc_challenges.multicurve.pipeline import Pipeline
from ecc_challenges.multicurve.pointindex import (ENTRY_BYTES, CompactPointIndex, ConcurrentMerge,
                                                  Merger, locator, part_of, split_locator)
from ecc_challenges.settings import Settings

from test_multicurve import admit, colliding, dp, platform  # noqa: F401  (fixture)


@pytest.fixture
def compact(platform):  # noqa: F811
    service, key, run, ns, archive, pipeline, _ = platform
    index = CompactPointIndex(archive)
    pipe = Pipeline(pipeline.legacy, pipeline.db, archive, pipeline.settings, index)
    return service, key, run, ns, archive, pipe, Merger(pipe, index)


def scalar(ns):
    return int(registry_metadata()['reference_vectors'][ns.campaign]['scalar'], 16)


def upload(compact, records, offset):
    service, key, run, ns, archive, pipe, _ = compact
    intent, _ = admit(compact, records, offset)
    assert pipe.process(intent['batch_id']) == 'indexed'
    return intent['batch_id'], offset + len(records) * ns.record_bytes


def stats(compact):
    service, *_ = compact
    with service.db.connection() as conn:
        return conn.execute("""
            SELECT sum(indexed_records) AS indexed,sum(unique_points) AS unique_points,
                   sum(deduplicated_points) AS deduplicated FROM ecc_public.dp_namespace_stats
        """).fetchone()


def batches(compact):
    service, *_ = compact
    with service.db.connection() as conn:
        return conn.execute("""
            SELECT id,index_seq,index_generation,index_duplicates,new_points,deduplicated_points
            FROM ecc_public.dp_batches WHERE state='indexed' ORDER BY index_seq
        """).fetchall()


def leads(compact):
    service, *_ = compact
    with service.db.connection() as conn:
        return conn.execute('SELECT id,state FROM ecc_public.dp_collision_leads ORDER BY id').fetchall()


def test_entry_layout():
    entry = bytes.fromhex('abcdef0123456789') + locator(5, 7)
    assert len(entry) == ENTRY_BYTES and split_locator(entry) == (5, 7) and part_of(entry) == 'abc'
    for bad in ((0, 0), (1 << 40, 0), (1, 1 << 24)):
        with pytest.raises(ValueError):
            locator(*bad)


def test_settings_validate_index_mode(settings):
    import dataclasses
    assert settings.point_index_mode == 'dynamodb'
    for env, value in (('POINT_INDEX_MODE', 'redis'), ('POINT_INDEX_MERGE_SECONDS', '1')):
        with pytest.MonkeyPatch.context() as mp:
            for name, item in (('DATABASE_URL', settings.database_url), ('ECC_BUCKET', 'b'),
                               ('AUDIT_QUEUE_URL', 'a'), ('INDEX_QUEUE_URL', 'i'), (env, value)):
                mp.setenv(name, item)
            with pytest.raises(ValueError):
                Settings.from_env(web=False)
    assert dataclasses.replace(settings, point_index_mode='compact').point_index_mode == 'compact'


def test_collision_is_found_at_merge_not_at_indexing(compact):
    service, key, run, ns, archive, pipe, merger = compact
    first = dp(ns, 1)
    second = colliding(ns, first, scalar(ns))
    offset = 0
    for record in (first, second):
        _, offset = upload(compact, [record], offset)
    assert leads(compact) == []
    assert [(b['index_seq'] is not None, b['index_generation']) for b in batches(compact)] == [(True, None)] * 2

    [result] = merger.merge_all()
    assert result['generation'] == 1 and result['entries'] == 2
    assert result['matches']['false_positive_groups'] == 0
    [lead] = leads(compact)
    assert lead['state'] == 'verified' and result['matches']['collision_leads'] == [lead['id']]
    assert [b['index_generation'] for b in batches(compact)] == [1, 1]
    assert merger.merge_all() == []

    # Every part is sorted, checksummed and holds whole entries.
    seen = []
    for part, info in result['parts'].items():
        body = merger.index.read_part(ns.id, part, info)
        entries = [body[i:i + ENTRY_BYTES] for i in range(0, len(body), ENTRY_BYTES)]
        assert entries == sorted(entries) and {part_of(e) for e in entries} == {part}
        seen.extend(entries)
    assert len(seen) == 2
    assert merger.status(ns.id)['generation'] == 1


def test_duplicates_move_counters_once_and_rebuild_is_idempotent(compact):
    service, key, run, ns, archive, pipe, merger = compact
    first = dp(ns, 1)
    offset = 0
    _, offset = upload(compact, [first], offset)
    _, offset = upload(compact, [first, first], offset)
    rows = batches(compact)
    assert (rows[1]['new_points'], rows[1]['deduplicated_points']) == (1, 1)

    merger.merge_all()
    expected = {'indexed': 3, 'unique_points': 1, 'deduplicated': 2}
    assert dict(stats(compact)) == expected
    rows = batches(compact)
    assert [(r['new_points'], r['deduplicated_points'], r['index_duplicates']) for r in rows] == [
        (1, 0, 0), (0, 2, 1)]
    assert leads(compact) == []

    assert merger.rebuild(ns.id) == 2
    assert merger.status(ns.id)['generation'] == 0
    [result] = merger.merge_all()
    assert result['generation'] == 1 and result['entries'] == 1
    assert dict(stats(compact)) == expected
    assert [r['index_duplicates'] for r in batches(compact)] == [0, 1]


def test_false_positive_group_still_finds_the_real_collision(compact, monkeypatch):
    service, key, run, ns, archive, pipe, merger = compact
    # Every point shares one fingerprint, so each lookup must compare full points.
    monkeypatch.setattr(pointindex, 'fingerprint', lambda point_key: b'\0' * 8)
    a, b = dp(ns, 1), dp(ns, 3)
    assert ns.point_key(a) != ns.point_key(b)
    c = colliding(ns, b, scalar(ns))
    offset = 0
    _, offset = upload(compact, [a], offset)
    _, offset = upload(compact, [b], offset)
    [first] = merger.merge_all()
    assert first['matches']['false_positive_groups'] == 1 and leads(compact) == []

    _, offset = upload(compact, [c], offset)
    [second] = merger.merge_all()
    assert second['generation'] == 2 and second['entries'] == 3
    [lead] = leads(compact)
    assert lead['state'] == 'verified' and second['matches']['collision_leads'] == [lead['id']]


def test_superseded_parts_are_deleted(compact, monkeypatch):
    service, key, run, ns, archive, pipe, merger = compact
    monkeypatch.setattr(pointindex, 'fingerprint',
                        lambda point_key: b'\0\0' + hashlib.sha256(point_key).digest()[2:8])
    offset = 0
    _, offset = upload(compact, [dp(ns, 1)], offset)
    [first] = merger.merge_all()
    old_key = first['parts']['000']['key']
    _, offset = upload(compact, [dp(ns, 3)], offset)
    [second] = merger.merge_all()
    assert list(second['parts']) == ['000'] and second['parts']['000']['key'] != old_key
    with pytest.raises(ClientError):
        archive.s3.head_object(Bucket=archive.bucket, Key=old_key)
    archive.s3.head_object(Bucket=archive.bucket, Key=second['parts']['000']['key'])


def test_commit_is_compare_and_swap(compact, monkeypatch):
    service, key, run, ns, archive, pipe, merger = compact
    upload(compact, [dp(ns, 1)], 0)
    commit = merger._commit

    def racing(ns_, pending, generation, key_, total, duplicates):
        with service.db.connection() as conn:
            conn.execute("""
                INSERT INTO ecc_public.dp_point_index(namespace_id,generation,manifest_key,entries)
                VALUES (%s,7,'elsewhere',0)
            """, (ns_.id,))
        return commit(ns_, pending, generation, key_, total, duplicates)

    monkeypatch.setattr(merger, '_commit', racing)
    with pytest.raises(ConcurrentMerge):
        merger.merge(ns.id)
    assert [b['index_generation'] for b in batches(compact)] == [None]
