"""Bounded compaction and reconstruction from immutable verified S3 batches.

python -m ecc_challenges.multicurve.archive_tool --help
A rebuild writes a separate table and archived collision results; it never
switches the production service to that table or changes account metadata.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config

from ..storage import Storage
from .archive import Archive
from .codec import Record,canonical,namespace
from .index import PointIndex
from .rebuild import rebuild
from .segments import compact


def resolve_candidate(archive,ns,candidate):
    width=ns.scalar_bytes
    point_bytes=bytes.fromhex(candidate['point_key'])
    point=(int.from_bytes(point_bytes[:ns.field_bytes],'big'),
           int.from_bytes(point_bytes[ns.field_bytes:],'big'))
    coefficients=[]
    for name in ('first','second'):
        data=bytes.fromhex(candidate[name]['witness'])
        if len(data)!=2*width:
            raise ValueError('collision witness width mismatch')
        pair=(int.from_bytes(data[:width],'big'),int.from_bytes(data[width:],'big'))
        ns.verify(Record(0,1,point,*pair))
        coefficients.append(pair)
    lead_key=archive.put_receipt(ns.id,'collisions',candidate['lead_id'],candidate)
    scalar=None
    state,_,value,_=ns.solve_collision(point,coefficients)
    if state=='rejected':
        raise ValueError('archive collision scalar fails dG=Q')
    if value is not None:
        scalar,state=hex(value),'verified'
    archive.put_receipt(ns.id,'collisions',candidate['lead_id'],{
        'protocol':2,'kind':'archive-collision-result','namespace_id':ns.id,
        'lead_id':candidate['lead_id'],'lead_receipt_key':lead_key,
        'state':state,'scalar':scalar})
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('compact','rebuild'))
    parser.add_argument('--profile')
    parser.add_argument('--expected-account',required=True)
    parser.add_argument('--region',default='us-west-2')
    parser.add_argument('--bucket',required=True)
    parser.add_argument('--stream',default='ecc-challenges-v1')
    parser.add_argument('--campaign',required=True)
    parser.add_argument('--verified-keys',type=Path,required=True,help='JSON list of exact verified receipt keys')
    parser.add_argument('--target-table',help='separate table named ecc-challenges-rebuild-*')
    parser.add_argument('--resume',action='store_true',help='replay into a partially rebuilt table')
    parser.add_argument('--max-input-mib',type=int,default=128)
    args=parser.parse_args()
    if not 1<=args.max_input_mib<=256:
        parser.error('--max-input-mib must be 1..256')
    session=boto3.Session(profile_name=args.profile,region_name=args.region)
    account=session.client('sts').get_caller_identity()['Account']
    if account!=args.expected_account:
        raise RuntimeError('destination AWS account differs from expected account')
    s3=session.client('s3',config=Config(signature_version='s3v4'))
    archive=Archive(Storage(s3,None,args.bucket,args.stream))
    ns=namespace(args.campaign)
    keys=json.loads(args.verified_keys.read_text())
    limit=args.max_input_mib*1024*1024
    if args.action=='compact':
        result=compact(archive,ns,keys,max_input_bytes=limit)
    else:
        if not args.target_table or not args.target_table.startswith('ecc-challenges-rebuild-'):
            parser.error('rebuild requires a separate ecc-challenges-rebuild-* table')
        client=session.client('dynamodb',config=Config(connect_timeout=5,read_timeout=20,
            max_pool_connections=32,retries={'total_max_attempts':1}))
        table=client.describe_table(TableName=args.target_table)['Table']
        if table['TableArn'].split(':')[4]!=account or table['TableStatus']!='ACTIVE':
            raise RuntimeError('rebuild table account or state differs')
        if not args.resume and client.scan(TableName=args.target_table,Limit=1,ConsistentRead=True).get('Items'):
            raise RuntimeError('rebuild target is not empty; use --resume for an explicit retry')
        result=None
        for event in rebuild(archive,ns,keys,PointIndex(client,args.target_table),
            durable_lead_handler=lambda candidate:resolve_candidate(archive,ns,candidate),max_input_bytes=limit):
            result=event
        if not result or result['kind']!='rebuild-complete':
            raise RuntimeError('archive replay did not complete')
        result={**result,'target_table_arn':table['TableArn'],
                'verified_keys_sha256':hashlib.sha256(canonical({'keys':sorted(keys)})).hexdigest()}
        result['receipt_key']=archive.put_receipt(ns.id,'checkpoints',str(uuid4()),result)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    main()
