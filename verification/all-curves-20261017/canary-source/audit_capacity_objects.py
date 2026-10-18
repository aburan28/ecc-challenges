#!/usr/bin/env python3
"""Verify every frozen fresh-point batch for the bounded two-L4 capacity run."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

import boto3
from ecc_challenges.multicurve.codec import namespace

ACCOUNT='104186811093'
BUCKET='ecc-challenges-data-104186811093'
PREFIX='public-gateway/ecc-challenges-v1/v2/raw/'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--capacity-receipt',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():raise FileExistsError(args.out)
    source=json.loads(args.capacity_receipt.read_text())
    assert source['ok'] and source['format']=='v2-fresh-capacity-gate-v1'
    ns=namespace(source['campaign'])
    assert ns.id==source['namespace_id']
    run_ids={w['final_checkpoint']['run_id'] for w in source['workers']}
    assert len(run_ids)==2
    session=boto3.Session(profile_name='ecc-challenges-new',region_name='us-west-2')
    assert session.client('sts').get_caller_identity()['Account']==ACCOUNT
    s3=session.client('s3')
    receipt={'format':'v2-fresh-capacity-object-audit-v1','ok':False,'source_receipt_sha256':hashlib.sha256(args.capacity_receipt.read_bytes()).hexdigest(),
             'namespace_id':ns.id,'run_ids':sorted(run_ids),'objects':[],'record_counts':{r:0 for r in run_ids},'unique_points':0,'duplicate_points':0}
    points=set()
    paginator=s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=BUCKET,Prefix=PREFIX+ns.id+'/'):
        for obj in page.get('Contents',[]):
            key=obj['Key']
            run_id=next((r for r in run_ids if '/runs/'+r+'/' in key),None)
            if not run_id:continue
            digest=key.rsplit('-',1)[-1].removesuffix('.bin')
            assert re.fullmatch(r'[0-9a-f]{64}',digest)
            value=s3.get_object(Bucket=BUCKET,Key=key)
            body=value['Body'].read(1048577)
            assert len(body)<=1048576 and hashlib.sha256(body).hexdigest()==digest
            records=ns.decode(body)
            for record in records:
                ns.verify(record)
                point=ns.point_key(record)
                if point in points:receipt['duplicate_points']+=1
                else:points.add(point)
                receipt['record_counts'][run_id]+=1
            receipt['objects'].append({'key':key,'version_id':value.get('VersionId'),'sha256':digest,
                                       'bytes':len(body),'records':len(records)})
    receipt['unique_points']=len(points)
    receipt['object_count']=len(receipt['objects'])
    receipt['point_set_sha256']=hashlib.sha256(b''.join(sorted(points))).hexdigest()
    expected={r['run_id']:r['total_records'] for r in source['final_run_statuses']}
    receipt['expected_records']=expected
    receipt['ok']=receipt['record_counts']==expected and receipt['duplicate_points']==0 and receipt['unique_points']==sum(expected.values())
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as handle:
        json.dump(receipt,handle,sort_keys=True,indent=2)
        handle.write('\n');handle.flush();os.fsync(handle.fileno())
    print(json.dumps({k:receipt[k] for k in ('ok','object_count','record_counts','unique_points','duplicate_points','point_set_sha256')},sort_keys=True))
    if not receipt['ok']:raise SystemExit(1)

if __name__=='__main__':main()
