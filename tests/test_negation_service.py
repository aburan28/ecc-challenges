"""Admission, verification and collision solving for the negation-class walk.

The walk is disabled by default behind MULTICURVE_NEGATION_ENABLED (or a
per-key canary list); these tests cover the closed gate, the open gate, a
negation-walk DP through the whole pipeline, and the bad cases being rejected.
"""
from dataclasses import replace
from uuid import UUID, uuid4
import json

import boto3
from botocore.config import Config
import pytest

from ecc_challenges.multicurve import negation_walk as nw
from ecc_challenges.multicurve.archive import Archive
from ecc_challenges.multicurve.codec import WALK_NEGATION, WALK_V2, Record, namespace
from ecc_challenges.multicurve.index import PointIndex
from ecc_challenges.multicurve.instances import registry_metadata
from ecc_challenges.multicurve.pipeline import Pipeline
from ecc_challenges.multicurve.service import Service as MultiService
from ecc_challenges.service import Rejected, Service
from test_multicurve import TEST_RUN, admit


def neg_dp(ns, trail=1):
    """A distinguished point of the negation-class walk on a real campaign."""
    record, active = nw.walk_lane(ns, ns.start(TEST_RUN, trail), 1, 4000)
    assert not active, "small-predicate control did not terminate"
    return record


def make_platform(database, aws_resources, settings, monkeypatch, *, negation_enabled):
    storage, sqs, *_ = aws_resources
    storage.s3 = boto3.client('s3', region_name='us-west-2', config=Config(signature_version='s3v4'))
    storage.s3.put_bucket_versioning(Bucket=storage.bucket, VersioningConfiguration={'Status': 'Enabled'})
    table = boto3.client('dynamodb', region_name='us-west-2')
    table.create_table(TableName='points', BillingMode='PAY_PER_REQUEST',
                       KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'}],
                       AttributeDefinitions=[{'AttributeName': 'pk', 'AttributeType': 'S'}])
    import ecc_challenges.multicurve.service as service_module
    monkeypatch.setattr(service_module, 'namespace',
                        lambda campaign, dp_bits=None, walk=WALK_V2: namespace(campaign, 1, walk))
    settings = replace(settings, multicurve_negation_enabled=negation_enabled)
    legacy = Service(database, storage, settings)
    legacy.upsert_account(123, 'test-contributor')
    key = legacy.authenticate(legacy.create_key(123, 'negation'))
    archive = Archive(storage)
    service = MultiService(legacy, database, archive, settings)
    ids = iter([UUID(TEST_RUN)])
    monkeypatch.setattr(service_module, 'uuid4', lambda: next(ids, None) or uuid4())
    pipeline = Pipeline(legacy, database, archive, settings, PointIndex(table, 'points'))
    return service, key, archive, pipeline, table


def test_negation_walk_is_disabled_by_default(database, aws_resources, settings, monkeypatch):
    service, key, *_ = make_platform(database, aws_resources, settings, monkeypatch, negation_enabled=False)
    with pytest.raises(Rejected) as error:
        service.config('eccp-79', WALK_NEGATION, key)
    assert error.value.status == 503
    with pytest.raises(Rejected) as error:
        service.register(key, 'eccp-79', str(uuid4()), WALK_NEGATION)
    assert error.value.status == 503
    with pytest.raises(Rejected) as error:
        service.config('eccp-79', 'made-up-walk', key)
    assert error.value.status == 400
    # v2 is unaffected: no walk, or the v2 walk named explicitly, is the v2 namespace.
    assert service.config('eccp-79')['walk']['id'] == WALK_V2
    assert service.config('eccp-79', WALK_V2, key) == service.config('eccp-79')
    run = service.register(key, 'eccp-79', str(uuid4()))
    assert run['config']['walk']['id'] == WALK_V2 and run['namespace_id'] == namespace('eccp-79', 1).id


def test_canary_key_opens_the_gate_for_that_key_only(database, aws_resources, settings, monkeypatch):
    service, key, *_ = make_platform(database, aws_resources, settings, monkeypatch, negation_enabled=False)
    service.settings = replace(service.settings, multicurve_negation_canary_keys=(key['id'],))
    assert service.config('eccp-79', WALK_NEGATION, key)['walk']['id'] == WALK_NEGATION
    with pytest.raises(Rejected):
        service.config('eccp-79', WALK_NEGATION, {**key, 'id': '0' * 24})


def test_negation_dp_goes_through_the_whole_pipeline_and_collides(database, aws_resources, settings, monkeypatch):
    platform = make_platform(database, aws_resources, settings, monkeypatch, negation_enabled=True)
    service, key, archive, pipeline, table = platform
    run = service.register(key, 'eccp-79', str(uuid4()), WALK_NEGATION)
    assert run['run_id'] == TEST_RUN and run['config']['walk']['id'] == WALK_NEGATION
    ns = namespace('eccp-79', 1, WALK_NEGATION)
    assert run['namespace_id'] == ns.id
    first = neg_dp(ns)
    ns.verify(first)
    ns.check_trail(first, TEST_RUN)
    harness = (service, key, run, ns, archive, pipeline, table)
    intent, result = admit(harness, [first])
    assert result['cursor'] == ns.record_bytes
    assert pipeline.process(intent['batch_id']) == 'indexed'
    # A second trail reaching the same class carries its own b (or -b).
    scalar = int(registry_metadata()['reference_vectors'][ns.campaign]['scalar'], 16)
    b2 = ns.start(TEST_RUN, 2).b
    second = replace(first, trail_id=2, b=b2,
                     a=(first.a + (first.b - b2) * scalar) % ns.instance.n)
    ns.verify(second)
    ns.check_trail(second, TEST_RUN)
    intent2, _ = admit(harness, [second], ns.record_bytes)
    assert pipeline.process(intent2['batch_id']) == 'indexed'
    with service.db.connection() as conn:
        lead = conn.execute('SELECT * FROM ecc_public.dp_collision_leads').fetchone()
    assert lead['state'] == 'verified'
    receipt = json.loads(archive.storage.get(lead['result_key']))
    assert receipt['scalar'] == hex(scalar)
    assert receipt['sign'] == 1
    assert table.scan(TableName='points')['Count'] == 1


def test_bad_negation_records_are_rejected(database, aws_resources, settings, monkeypatch):
    platform = make_platform(database, aws_resources, settings, monkeypatch, negation_enabled=True)
    service, key, archive, pipeline, table = platform
    run = service.register(key, 'eccp-79', str(uuid4()), WALK_NEGATION)
    ns = namespace('eccp-79', 1, WALK_NEGATION)
    harness = (service, key, run, ns, archive, pipeline, table)
    good = neg_dp(ns)
    n = ns.instance.n
    negative = ns.instance.neg(good.point)
    non_canonical = replace(good, point=negative, a=(-good.a) % n, b=(-good.b) % n)
    assert ns.instance.mul2(non_canonical.a, non_canonical.b) == negative       # a valid relation ...
    with pytest.raises(ValueError, match='canonical'):                          # ... but not the class form
        ns.encode(non_canonical)
    # A batch holding it is failed by the verifier and never reaches the index.
    raw_bad = ns.encode(good)[:ns.record_bytes - 2 * ns.scalar_bytes] + \
        ((-good.a) % n).to_bytes(ns.scalar_bytes, 'big') + ((-good.b) % n).to_bytes(ns.scalar_bytes, 'big')
    assert len(raw_bad) == ns.record_bytes
    # Decoding the forged bytes fails (relation a,b no longer matches the stored point).
    decoded = ns.decode(raw_bad)[0]
    with pytest.raises(ValueError):
        ns.verify(decoded)
    # A b that is neither the trail's start b nor its negative is refused by the trail check.
    with pytest.raises(ValueError):
        ns.check_trail(replace(good, b=(2 * good.b) % n), TEST_RUN)
    # Wrong relation: failed batch, nothing indexed.
    invalid = replace(good, a=(good.a + 1) % n)
    intent, _ = admit(harness, [good, invalid])
    assert pipeline.process(intent['batch_id']) == 'failed'
    assert table.scan(TableName='points')['Count'] == 0


@pytest.fixture
def conn(postgres_dsn, database):
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(postgres_dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


def test_status_counts_a_negation_namespace_against_its_own_expectation(database, conn):
    import math
    from psycopg.types.json import Jsonb
    from ecc_challenges.status import StatusReader

    def completion(walk):
        ns = namespace("eccp-131", 20, walk)
        conn.execute("DELETE FROM ecc_public.dp_namespace_stats")
        conn.execute("DELETE FROM ecc_public.dp_namespaces")
        conn.execute("INSERT INTO ecc_public.dp_namespaces(id,campaign_id,manifest,record_bytes) VALUES (%s,%s,%s,%s)",
                     (ns.id, "eccp-131", Jsonb(ns.public()), ns.record_bytes))
        conn.execute("""INSERT INTO ecc_public.dp_namespace_stats(namespace_id,shard,admitted_records,verified_records)
                        VALUES (%s,0,%s,%s)""", (ns.id, 7, 7))
        row = next(c for c in StatusReader(database, ttl_seconds=0, multicurve_enabled=True).read()["campaigns"]
                   if c["id"] == "eccp-131")
        assert row["estimated_work_log2"] == round(math.log2(7 * 2**20), 2)
        return row["estimated_completion_probability_percent"]

    # The same work is a larger share of sqrt(pi n/4) than of sqrt(pi n/2): the
    # birthday exponent (k/E)^2 doubles.
    assert completion(WALK_NEGATION) == pytest.approx(2 * completion(WALK_V2), rel=1e-6)
