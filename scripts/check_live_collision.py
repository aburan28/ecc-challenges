#!/usr/bin/env python3
"""One constructed equivalent witness for the published ECCp-79 scalar.

This validates deployed collision plumbing; the answer is supplied by Certicom's
published correctness vector. It is not an unknown-scalar computation.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import boto3

from ecc_challenges.multicurve.codec import canonical,namespace
from ecc_challenges.multicurve.index import _prior
from ecc_challenges.multicurve.instances import registry_metadata
from ecc_challenges.multicurve.pipeline import collision_lead_id

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from generic.client import API


def write_new(path,value):
    with Path(path).open('x') as f:json.dump(value,f,sort_keys=True,indent=2);f.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--key-file',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False)
    if a.key_file.stat().st_mode&0o077:raise ValueError('private key file required')
    s=boto3.Session(profile_name='ecc-challenges-new',region_name='us-west-2')
    assert s.client('sts').get_caller_identity()['Account']=='104186811093'
    source=json.loads((ROOT/'verification/all-curves-20261016/release/live-cpu-archive-binding.json').read_text())['rows'][0]
    s3=s.client('s3');bucket='ecc-challenges-data-104186811093'
    raw=s3.get_object(Bucket=bucket,Key=source['object_key'],VersionId=source['version_id'])['Body'].read(57)
    assert len(raw)==56 and hashlib.sha256(raw).hexdigest()==source['sha256']
    ns=namespace('eccp-79');first=ns.decode(raw)[0];ns.verify(first)
    scalar=int(registry_metadata()['reference_vectors'][ns.campaign]['scalar'],16)
    assert ns.instance.mul(scalar,ns.instance.G)==ns.instance.Q
    constructed=replace(first,a=(first.a-scalar)%ns.instance.n,b=(first.b+1)%ns.instance.n)
    ns.verify(constructed);payload=ns.encode(constructed)
    point=ns.point_key(first)
    item=s.client('dynamodb').get_item(TableName='ecc-challenges-points-v2',
        Key={'pk':{'S':ns.id+'#'+point.hex()}},ConsistentRead=True)['Item']
    prior=_prior(ns,point,item)
    assert prior['witness']==ns.witness(first)
    lead=collision_lead_id(ns,constructed,prior)
    assert lead is not None
    prefix='public-gateway/ecc-challenges-v1/v2/collisions/'+ns.id+'/'+lead+'/'
    existing=s3.list_objects_v2(Bucket=bucket,Prefix=prefix,MaxKeys=4)
    assert existing.get('KeyCount',0)==0,'control lead already exists; retain its earlier attempt'
    plan={'format':'live-published-scalar-collision-control-plan-v1','scope':'constructed equivalent coefficient witness for an already published scalar; one56-byte record',
          'namespace_id':ns.id,'source':source,'point_key':point.hex(),'lead_id':lead,
          'published_scalar':hex(scalar),'constructed_sha256':hashlib.sha256(payload).hexdigest(),
          'first_witness':ns.witness(first).hex(),'constructed_witness':ns.witness(constructed).hex(),
          'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          'acceptance':'new record indexed; immutable lead and result bind both source receipts and return exactly the published scalar; run closes'}
    write_new(a.out/'plan.json',plan)
    with (a.out/'constructed-record.bin').open('xb') as f:f.write(payload)
    if not a.apply:print(json.dumps(plan,sort_keys=True));return
    api=API('https://ecc-challenges.com',a.key_file.read_text().strip())
    receipt={'format':'live-published-scalar-collision-control-result-v1','ok':False,
             'plan_sha256':hashlib.sha256((a.out/'plan.json').read_bytes()).hexdigest()}
    run=None
    try:
        assigned=api.request('POST','/v2/register',{'campaign':ns.campaign,'registration_id':str(uuid4())})
        assert assigned['namespace_id']==ns.id
        run=assigned['run_id'];receipt['run_id']=run
        intent=api.request('POST',f'/v2/runs/{run}/batches',{'offset':0,'byte_length':len(payload),
            'records':1,'sha256':hashlib.sha256(payload).hexdigest()})
        assert intent['run_id']==run and intent['namespace_id']==ns.id and intent['offset']==0
        api.upload(intent['upload'],payload)
        committed=api.request('POST',f'/v2/runs/{run}/batches/{intent["batch_id"]}/commit')
        receipt['commit']=committed
        assert committed['cursor']==56 and committed['batch_end_cursor']==56
        until=time.monotonic()+180
        while time.monotonic()<until:
            state=api.request('GET',f'/v2/runs/{run}')
            if state['batches']=={'indexed':{'batches':1,'records':1}}:break
            time.sleep(3)
        else:raise RuntimeError('control_index_deadline')
        receipt['indexed_status']={k:state[k] for k in ('run_id','namespace_id','cursor','batches','total_records')}
        listing=s3.list_objects_v2(Bucket=bucket,Prefix=prefix,MaxKeys=4)
        assert not listing.get('IsTruncated') and listing['KeyCount']==2
        values=[]
        for row in listing['Contents']:
            body=s3.get_object(Bucket=bucket,Key=row['Key'])['Body'].read(65537)
            value=json.loads(body)
            assert len(body)<=65536 and canonical(value)==body
            assert row['Key'].rsplit('/',1)[1]==hashlib.sha256(body).hexdigest()+'.json'
            assert value['namespace_id']==ns.id and value['lead_id']==lead and value['point_key']==point.hex()
            values.append({'object_key':row['Key'],'sha256':hashlib.sha256(body).hexdigest(),'receipt':value})
        first_receipt=next(row for row in values if row['receipt']['kind']=='collision-lead')
        result=next(row for row in values if row['receipt']['kind']=='collision-result')
        result_body=result['receipt']
        assert result_body['state']=='verified' and result_body['scalar']==hex(scalar)
        assert result_body['lead_receipt_key']==first_receipt['object_key']
        assert result_body['first']['verified_key']==prior['verified_key']
        assert result_body['first']['witness']==ns.witness(first).hex()
        assert result_body['second']['batch_id']==intent['batch_id']
        assert result_body['second']['witness']==ns.witness(constructed).hex()
        receipt['collision_receipts']=values
        receipt['published_scalar_matches']=True
        receipt['ok']=True
    except Exception as error:
        receipt['error_type']=type(error).__name__
    finally:
        if run:
            try:
                receipt['closure']=api.request('POST',f'/v2/runs/{run}/close')
                if not receipt['closure'].get('closed_at'):receipt['ok']=False
            except Exception as error:
                receipt['closure_error_type']=type(error).__name__;receipt['ok']=False
        write_new(a.out/'result.json',receipt)
    print(json.dumps({'ok':receipt['ok'],'run_id':run,'published_scalar_matches':receipt.get('published_scalar_matches'),
                      'error_type':receipt.get('error_type')},sort_keys=True))
    if not receipt['ok']:raise SystemExit(1)


if __name__=='__main__':main()
