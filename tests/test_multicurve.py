"""Public v2 trust boundaries, durable admission and exact collision controls."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
from urllib.parse import parse_qs,urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import pytest

from ecc_challenges.multicurve.archive import Archive
from ecc_challenges.multicurve.codec import Record, canonical, namespace
from ecc_challenges.multicurve.curves import Instance
from ecc_challenges.multicurve.index import PointIndex
from ecc_challenges.multicurve.instances import all_instances,registry_metadata
from ecc_challenges.multicurve.pipeline import Pipeline
from ecc_challenges.multicurve.service import Service as MultiService
from ecc_challenges.service import Service,Rejected


def dp(ns, trail=1):
    record = ns.start('04e5b6c1-eafc-4c08-8cb6-0eb1e9c647f9',trail)
    point,a,b=record.point,record.a,record.b
    for steps in range(1,1000):
        delta,da,db=ns.table()[ns.partition(point)]
        point=ns.instance.add(point,delta)
        a,b=(a+da)%ns.instance.n,(b+db)%ns.instance.n
        if ns.distinguished(point):
            return Record(trail,steps,point,a,b)
    raise AssertionError('small-predicate correctness control did not terminate')


def test_registry_exact_scope_and_published_controls():
    instances=all_instances()
    assert len(instances)==24
    assert sum(c.field_kind=='prime' for c in instances)==9
    assert sum(c.field_kind=='binary' for c in instances)==15
    for c in instances:
        assert all(c.validate().values())
        assert c.mul2(7,11)==c.add(c.mul(7,c.G),c.mul(11,c.Q))
        with pytest.raises(ValueError,match='immutable'):
            replace(c,G=list(c.G))
    for identity,vector in registry_metadata()['reference_vectors'].items():
        c=next(c for c in instances if c.id==identity)
        assert c.mul(int(vector['scalar'],16),c.G)==c.Q


def test_namespace_isolation_full_scalar_width_and_canonicality():
    assert namespace('ECCp-359').id==namespace('eccp-359').id
    for identity in ('ecc2-353','ecc2k-358','eccp-359'):
        ns=namespace(identity,1)
        assert any(a.bit_length()>256 for a,b in ns.coefficients)
        start=ns.start(str(uuid4()),1)
        assert max(start.a.bit_length(),start.b.bit_length())>256
        record=dp(ns)
        assert ns.decode(ns.encode(record))==[record]
        ns.verify(record)
        with pytest.raises(ValueError):
            ns.verify(replace(record,a=(record.a+1)%ns.instance.n))
        with pytest.raises(ValueError):
            ns.decode(ns.encode(record)[:-1])
        with pytest.raises(ValueError):
            ns.encode(replace(record,a=ns.instance.n))
        assert namespace(identity,2).id!=ns.id


@pytest.fixture
def platform(database,aws_resources,settings,monkeypatch):
    storage,sqs,*_=aws_resources
    # Production uses explicit SigV4 and bucket versioning; reproduce both.
    storage.s3=boto3.client('s3',region_name='us-west-2',config=Config(signature_version='s3v4'))
    storage.s3.put_bucket_versioning(Bucket=storage.bucket,VersioningConfiguration={'Status':'Enabled'})
    table=boto3.client('dynamodb',region_name='us-west-2')
    table.create_table(TableName='points',BillingMode='PAY_PER_REQUEST',
                       KeySchema=[{'AttributeName':'pk','KeyType':'HASH'}],
                       AttributeDefinitions=[{'AttributeName':'pk','AttributeType':'S'}])
    ns=namespace('eccp-79',1)
    import ecc_challenges.multicurve.service as service_module
    import ecc_challenges.multicurve.pipeline as pipeline_module
    monkeypatch.setattr(service_module,'namespace',lambda campaign: namespace(campaign,1))
    monkeypatch.setattr(pipeline_module,'namespace',lambda campaign: namespace(campaign,1))
    legacy=Service(database,storage,settings)
    legacy.upsert_account(123,'test-contributor')
    issued=legacy.create_key(123,'correctness')
    key=legacy.authenticate(issued)
    archive=Archive(storage)
    service=MultiService(legacy,database,archive,settings)
    run=service.register(key,ns.campaign,str(uuid4()))
    index=PointIndex(table,'points')
    pipeline=Pipeline(legacy,database,archive,settings,index)
    return service,key,run,ns,archive,pipeline,table


def admit(platform,records,offset=0,*,commit=True):
    service,key,run,ns,archive,*_=platform
    raw=b''.join(ns.encode(record) for record in records)
    digest=hashlib.sha256(raw).hexdigest()
    intent=service.intent(key,run['run_id'],offset,len(raw),len(records),digest)
    if intent['upload']:
        with service.db.connection() as conn:
            row=conn.execute('SELECT object_key FROM ecc_public.dp_batches WHERE id=%s',(intent['batch_id'],)).fetchone()
        try:
            archive.s3.put_object(Bucket=archive.bucket,Key=row['object_key'],Body=raw,
                ChecksumSHA256=base64.b64encode(hashlib.sha256(raw).digest()).decode(),
                ChecksumAlgorithm='SHA256',ServerSideEncryption='AES256',IfNoneMatch='*')
        except ClientError as exc:
            if exc.response['Error']['Code'] != 'PreconditionFailed':
                raise
    result=service.commit(key,run['run_id'],intent['batch_id']) if commit else None
    return intent,result


def test_signed_upload_every_control_is_bound(platform):
    service,key,run,ns,archive,*_=platform
    record=dp(ns)
    raw=ns.encode(record)
    intent=service.intent(key,run['run_id'],0,len(raw),1,hashlib.sha256(raw).hexdigest())
    upload=intent['upload']
    query=parse_qs(urlsplit(upload['url']).query)
    signed=set(query['X-Amz-SignedHeaders'][0].split(';'))
    assert {'content-length','content-type','if-none-match','x-amz-checksum-sha256',
            'x-amz-server-side-encryption'}<=signed
    assert upload['headers']['Content-Length']==str(ns.record_bytes)
    assert upload['headers']['If-None-Match']=='*'
    assert upload['headers']['x-amz-checksum-sha256']==base64.b64encode(hashlib.sha256(raw).digest()).decode()
    with pytest.raises(Rejected) as error:
        service.commit(key,run['run_id'],intent['batch_id'])
    assert error.value.status==409
    # Another payload at the same cursor cannot obtain an alternative capability.
    with pytest.raises(Rejected) as error:
        service.intent(key,run['run_id'],0,len(raw),1,'0'*64)
    assert error.value.status==409


def test_admission_retry_verification_and_collision(platform):
    service,key,run,ns,archive,pipeline,table=platform
    first=dp(ns)
    intent,result=admit(platform,[first])
    assert result['cursor']==ns.record_bytes
    assert service.commit(key,run['run_id'],intent['batch_id'])['duplicate']
    assert pipeline.process(intent['batch_id'])=='indexed'
    assert pipeline.process(intent['batch_id'])=='indexed'
    scalar=int(registry_metadata()['reference_vectors'][ns.campaign]['scalar'],16)
    second=replace(first,trail_id=2,a=(first.a-scalar)%ns.instance.n,b=(first.b+1)%ns.instance.n)
    ns.verify(second)
    next_intent,_=admit(platform,[second],ns.record_bytes)
    assert pipeline.process(next_intent['batch_id'])=='indexed'
    with service.db.connection() as conn:
        lead=conn.execute('SELECT * FROM ecc_public.dp_collision_leads').fetchone()
        pressure=conn.execute('SELECT sum(pending_batches) AS n FROM ecc_public.dp_pressure').fetchone()
    assert lead['state']=='verified'
    assert pressure['n']==0
    receipt=json.loads(archive.storage.get(lead['result_key']))
    assert receipt['scalar']==hex(scalar)
    assert table.scan(TableName='points')['Count']==1


def test_invalid_relation_never_reaches_index(platform):
    service,key,run,ns,archive,pipeline,table=platform
    record=dp(ns)
    invalid=replace(record,a=(record.a+1)%ns.instance.n)
    intent,_=admit(platform,[record,invalid])
    assert pipeline.process(intent['batch_id'])=='failed'
    assert table.scan(TableName='points')['Count']==0
    with service.db.connection() as conn:
        row=conn.execute('SELECT state,pressure_released_at FROM ecc_public.dp_batches WHERE id=%s',(intent['batch_id'],)).fetchone()
    assert row['pressure_released_at'] is not None
    assert pipeline.process(intent['batch_id'])=='failed'


def test_crash_after_conditional_insert_preserves_collision(platform,monkeypatch):
    service,key,run,ns,archive,pipeline,table=platform
    record=dp(ns)
    intent,_=admit(platform,[record])
    original=pipeline._index
    def fail_after_put(batch,namespace,token,records):
        pipeline.point_index.put(namespace,records[0],batch['id'],0,batch['verified_key'])
        raise OSError('constructed interruption after DynamoDB write')
    monkeypatch.setattr(pipeline,'_index',fail_after_put)
    with pytest.raises(OSError):
        pipeline.process(intent['batch_id'])
    monkeypatch.setattr(pipeline,'_index',original)
    assert pipeline.process(intent['batch_id'])=='indexed'
    with service.db.connection() as conn:
        row=conn.execute('SELECT new_points,deduplicated_points FROM ecc_public.dp_batches WHERE id=%s',(intent['batch_id'],)).fetchone()
    assert row=={'new_points':1,'deduplicated_points':0}


def test_expired_intent_releases_pressure_without_deleting_raw(platform):
    service,key,run,ns,archive,*_=platform
    intent,_=admit(platform,[dp(ns)],commit=False)
    with service.db.connection() as conn:
        row=conn.execute("UPDATE ecc_public.dp_batches SET expires_at=now()-interval '1 second' WHERE id=%s RETURNING object_key",(intent['batch_id'],)).fetchone()
    assert service.expire_intents()==1
    assert archive.storage.get(row['object_key'])
    with pytest.raises(Rejected) as error:
        service.commit(key,run['run_id'],intent['batch_id'])
    assert error.value.status==410
    replacement,_=admit(platform,[dp(ns)])
    assert replacement['batch_id']!=intent['batch_id']


def test_concurrent_exact_point_insert_preserves_first_witness(platform):
    service,key,run,ns,archive,pipeline,table=platform
    record=dp(ns)
    intent,_=admit(platform,[record])
    assert pipeline.process(intent['batch_id'])=='indexed'
    with service.db.connection() as conn:
        row=conn.execute('SELECT verified_key FROM ecc_public.dp_batches WHERE id=%s',(intent['batch_id'],)).fetchone()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:pipeline.point_index.put(ns,record,intent['batch_id'],0,row['verified_key']),range(8)))
    assert all(r['witness']==ns.witness(record) for r in results)
    assert table.scan(TableName='points')['Count']==1


def test_collision_retry_after_prior_return_keeps_lead(platform,monkeypatch):
    service,key,run,ns,archive,pipeline,table=platform
    first=dp(ns)
    first_intent,_=admit(platform,[first])
    assert pipeline.process(first_intent['batch_id'])=='indexed'
    d=int(registry_metadata()['reference_vectors'][ns.campaign]['scalar'],16)
    second=replace(first,trail_id=2,a=(first.a-d)%ns.instance.n,b=(first.b+1)%ns.instance.n)
    second_intent,_=admit(platform,[second],ns.record_bytes)
    original=pipeline._lead
    monkeypatch.setattr(pipeline,'_lead',lambda *args: (_ for _ in ()).throw(OSError('constructed interruption before durable lead')))
    with pytest.raises(OSError):
        pipeline.process(second_intent['batch_id'])
    monkeypatch.setattr(pipeline,'_lead',original)
    assert pipeline.process(second_intent['batch_id'])=='indexed'
    with service.db.connection() as conn:
        leads=conn.execute('SELECT state FROM ecc_public.dp_collision_leads').fetchall()
    assert leads==[{'state':'verified'}]


def test_repeated_witness_costs_one_conditional_index_write(platform,monkeypatch):
    service,key,run,ns,archive,pipeline,table=platform
    intent,_=admit(platform,[dp(ns)]*100)
    original=pipeline.point_index.put
    calls=[]
    def track(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(pipeline.point_index,'put',track)
    assert pipeline.process(intent['batch_id'])=='indexed'
    assert len(calls)==1
    with service.db.connection() as conn:
        row=conn.execute('SELECT new_points,deduplicated_points FROM ecc_public.dp_batches WHERE id=%s',(intent['batch_id'],)).fetchone()
    assert row=={'new_points':1,'deduplicated_points':99}


def test_revoked_capability_cannot_commit_and_expires_once(platform):
    service,key,run,ns,archive,pipeline,table=platform
    intent,_=admit(platform,[dp(ns)],commit=False)
    service.legacy.revoke_key(key['account_id'],key['id'])
    with pytest.raises(Rejected) as error:
        service.commit(key,run['run_id'],intent['batch_id'])
    assert error.value.status==401
    with service.db.connection() as conn:
        row=conn.execute("UPDATE ecc_public.dp_batches SET expires_at=now()-interval '1 second' WHERE id=%s RETURNING object_key",(intent['batch_id'],)).fetchone()
    assert service.expire_intents()==1
    assert service.expire_intents()==0
    assert archive.storage.get(row['object_key'])


def test_segment_roundtrip_and_archive_rebuild(platform):
    from ecc_challenges.multicurve.segments import compact,parse_segment
    from ecc_challenges.multicurve.rebuild import rebuild
    service,key,run,ns,archive,pipeline,table=platform
    first=dp(ns)
    d=int(registry_metadata()['reference_vectors'][ns.campaign]['scalar'],16)
    second=replace(first,trail_id=2,a=(first.a-d)%ns.instance.n,b=(first.b+1)%ns.instance.n)
    intents=[admit(platform,[first])[0],admit(platform,[second],ns.record_bytes)[0]]
    for item in intents:
        assert pipeline.process(item['batch_id'])=='indexed'
    with service.db.connection() as conn:
        keys=[r['verified_key'] for r in conn.execute('SELECT verified_key FROM ecc_public.dp_batches ORDER BY id').fetchall()]
    result=compact(archive,ns,keys)
    assert result==compact(archive,ns,list(reversed(keys)))
    body=archive.storage.get(result['segment_key'])
    assert parse_segment(ns,body,archive.prefix,result['segment_sha256'])['records']==2
    for corrupt in (b'badmagic'+body[8:],body[:-1],body[:-21]+bytes([body[-21]^1])+body[-20:]):
        with pytest.raises(ValueError):
            parse_segment(ns,corrupt,archive.prefix)
    with pytest.raises(ValueError,match='budget'):
        compact(archive,ns,keys,max_input_bytes=ns.record_bytes)
    table.create_table(TableName='rebuilt',BillingMode='PAY_PER_REQUEST',
                       KeySchema=[{'AttributeName':'pk','KeyType':'HASH'}],
                       AttributeDefinitions=[{'AttributeName':'pk','AttributeType':'S'}])
    leads=[]
    def persist(candidate):
        archive.put_receipt(ns.id,'collisions',candidate['lead_id'],candidate)
        leads.append(candidate)
        return True
    events=list(rebuild(archive,ns,keys,PointIndex(table,'rebuilt'),durable_lead_handler=persist))
    assert events[-1]['kind']=='rebuild-complete'
    assert events[-1]['records']==2
    assert len(leads)==1
    assert table.scan(TableName='rebuilt')['Count']==1
    with pytest.raises(RuntimeError,match='durable'):
        list(rebuild(archive,ns,keys,PointIndex(table,'rebuilt'),durable_lead_handler=lambda _:False))


def test_namespace_stats_count_each_batch_once_across_retry_and_reverification(
    platform, monkeypatch
):
    service, key, run, ns, archive, pipeline, table = platform

    def totals():
        with service.db.connection() as conn:
            return conn.execute("""
                SELECT
                  coalesce(sum(admitted_records),0) AS admitted,
                  coalesce(sum(admitted_bytes),0) AS bytes,
                  coalesce(sum(verified_records),0) AS verified,
                  coalesce(sum(indexed_records),0) AS indexed,
                  coalesce(sum(unique_points),0) AS unique_points,
                  coalesce(sum(deduplicated_points),0) AS duplicates
                FROM ecc_public.dp_namespace_stats WHERE namespace_id=%s
            """, (ns.id,)).fetchone()

    record = dp(ns)
    intent, _ = admit(platform, [record])
    service.commit(key, run["run_id"], intent["batch_id"])
    assert totals() == {
        "admitted": 1, "bytes": ns.record_bytes, "verified": 0,
        "indexed": 0, "unique_points": 0, "duplicates": 0,
    }

    original = pipeline._index

    def interrupted_after_verification(*args):
        raise OSError("constructed interruption after complete verification")

    monkeypatch.setattr(pipeline, "_index", interrupted_after_verification)
    with pytest.raises(OSError):
        pipeline.process(intent["batch_id"])
    assert totals()["verified"] == 1
    assert totals()["indexed"] == 0

    # A source revision forces a complete recheck. It must not count the same
    # batch as a second contribution.
    with service.db.connection() as conn:
        conn.execute("""
            UPDATE ecc_public.dp_batches SET verifier_sha256=%s WHERE id=%s
        """, ("f" * 64, intent["batch_id"]))
    monkeypatch.setattr(pipeline, "_index", original)
    assert pipeline.process(intent["batch_id"]) == "indexed"
    assert pipeline.process(intent["batch_id"]) == "indexed"
    assert totals() == {
        "admitted": 1, "bytes": ns.record_bytes, "verified": 1,
        "indexed": 1, "unique_points": 1, "duplicates": 0,
    }

    # Identical valid bytes in a subsequent admitted batch are real uploads but
    # produce a duplicate exact point.
    duplicate, _ = admit(platform, [record], ns.record_bytes)
    assert pipeline.process(duplicate["batch_id"]) == "indexed"
    assert totals() == {
        "admitted": 2, "bytes": 2 * ns.record_bytes, "verified": 2,
        "indexed": 2, "unique_points": 1, "duplicates": 1,
    }

    # An uploaded intent that expires before admission contributes no totals.
    pending, _ = admit(
        platform, [record], 2 * ns.record_bytes, commit=False
    )
    before_expiry = totals()
    with service.db.connection() as conn:
        conn.execute("""
            UPDATE ecc_public.dp_batches
            SET expires_at=now()-interval '1 second' WHERE id=%s
        """, (pending["batch_id"],))
    assert service.expire_intents() == 1
    assert service.expire_intents() == 0
    assert totals() == before_expiry


def test_all24_public_catalog_and_authenticated_v2_routes(platform):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from ecc_challenges.app import create_app
    service,key,run,ns,archive,pipeline,table=platform
    service.legacy.upsert_account(456,'api-route-control')
    credential=service.legacy.create_key(456,'route')
    config=replace(service.settings,multicurve_enabled=True)
    with TestClient(create_app(config,db=service.db,storage=archive.storage),base_url='https://ecc-challenges.com') as client:
        catalog=client.get('/v1/campaigns').json()
        assert catalog['multicurve_enabled']
        assert len(catalog['campaigns'])==24
        assert all(row['contribution']!='not_integrated' for row in catalog['campaigns'])
        assert client.get('/v2/config?campaign=eccp-79').status_code==401
        headers={'Authorization':'Bearer '+credential}
        assigned=client.post('/v2/register',headers=headers,json={'campaign':'ecc2-131','registration_id':str(uuid4())})
        assert assigned.status_code==201
        assert assigned.json()['campaign']=='ecc2-131'
        assert assigned.json()['record_bytes']==84
        unknown=client.post('/v2/register',headers=headers,json={'campaign':'not-a-curve','registration_id':str(uuid4())})
        assert unknown.status_code==404
        status=client.get('/v1/status').json()
        assert status['compute_ready']==24
        assert all(row['received_points'] is not None for row in status['campaigns'])
        assert client.get('/status').status_code==200


def test_hash_predicate_emits_for_binary_control_with_fixed_low_coordinate_bit():
    ns=namespace('ecc2-79',1)
    point=None
    points=[]
    for _ in range(32):
        point=ns.instance.add(point,ns.instance.G)
        points.append(point)
    # This finite subgroup control defeats the former raw-x-low-bit predicate.
    assert all(point[0]&1 for point in points)
    assert any(ns.distinguished(point) for point in points)
    assert any(not ns.distinguished(point) for point in points)
    for index,point in enumerate(points,1):
        if ns.distinguished(point):
            ns.verify(Record(index,1,point,index,0))


def test_recreated_index_generation_is_fenced(platform,monkeypatch):
    service,key,run,ns,archive,pipeline,table=platform
    original=table.describe_table
    identity=str(uuid4())
    def describe(**params):
        result=original(**params)
        result['Table']['TableId']=identity
        return result
    # Moto omits TableId; only this fixture supplies an explicit generation UUID.
    monkeypatch.setattr(table,'describe_table',describe)
    assert pipeline.point_index.bind_generation(service.db)['table_id']==identity
    assert pipeline.point_index.bind_generation(service.db)['table_id']==identity
    identity=str(uuid4())
    with pytest.raises(RuntimeError,match='generation changed'):
        pipeline.point_index.bind_generation(service.db)
    monkeypatch.setattr(table,'describe_table',original)
    with pytest.raises(RuntimeError,match='immutable TableId'):
        pipeline.point_index.bind_generation(service.db)


def test_canary_key_can_validate_before_public_enablement(platform):
    from fastapi.testclient import TestClient
    from ecc_challenges.app import create_app
    service,key,run,ns,archive,pipeline,table=platform
    service.legacy.upsert_account(789,'preview-route-control')
    credential=service.legacy.create_key(789,'preview')
    canary=service.legacy.authenticate(credential)
    service.legacy.upsert_account(790,'normal-route-control')
    normal=service.legacy.create_key(790,'normal')
    config=replace(service.settings,multicurve_enabled=False,multicurve_canary_keys=(canary['id'],))
    with TestClient(create_app(config,db=service.db,storage=archive.storage),base_url='https://ecc-challenges.com') as client:
        assert not client.get('/v1/campaigns').json()['multicurve_enabled']
        assert client.get('/v2/config?campaign=eccp-79',headers={'Authorization':'Bearer '+normal}).status_code==503
        assert client.get('/v2/config?campaign=eccp-79',headers={'Authorization':'Bearer '+credential}).status_code==200
        assert client.get('/v1/status').json()['compute_ready']==1
