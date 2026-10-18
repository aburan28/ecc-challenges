#!/usr/bin/env python3
"""Bounded two-L4 fresh-point arrival/drain measurement for the private v2 gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.request import Request, urlopen

import boto3
import modal
from modal_worker import BOOT

ACCOUNT = '104186811093'
CAMPAIGN = 'eccp-79'
IMAGE = 'ghcr.io/aburan28/ecc-challenges-worker@sha256:d2a7354ee00f1bdddc0931895b19e1b60140812a1d315d223b897f28157a1f9f'
NAMESPACE = '4f59c5763dec32ca6faccf6ad4ecc285122cd5367ab9a48c8b1f444df27e8b5a'
BUCKET = 'ecc-challenges-data-104186811093'
DURATION = 600
INTERVAL = 30

def save(path, value):
    with Path(path).open('x') as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())

def status(key, run_id):
    request = Request('https://ecc-challenges.com/v2/runs/'+run_id,
                      headers={'Authorization':'Bearer '+key})
    with urlopen(request, timeout=20) as response:
        value=json.load(response)
    assert value['run_id']==run_id and value['namespace_id']==NAMESPACE
    return {name:value.get(name) for name in ('run_id','campaign','cursor','total_records','total_bytes','record_bytes','batches','pending_batch_id','closed_at')}

def checkpoint(sandbox):
    proc=sandbox.exec('cat','/data/ecc-state/generic-state.json',timeout=20)
    raw=proc.stdout.read()
    if proc.wait()!=0:return None
    value=json.loads(raw)
    assert value['namespace_id']==NAMESPACE and value['protocol']==2
    return {name:value[name] for name in ('run_id','cursor','pending','lanes','round_steps','next_trail')}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--key-file',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    dest=Path(args.out)
    assert not dest.exists()
    key_file=Path(args.key_file)
    assert key_file.stat().st_mode & 0o077 == 0
    key=key_file.read_text().strip()
    assert re.fullmatch(r'ecc1_[0-9a-f]{24}_[A-Za-z0-9_-]{43}',key)
    session=boto3.Session(profile_name='ecc-challenges-new',region_name='us-west-2')
    assert session.client('sts').get_caller_identity()['Account']==ACCOUNT
    cf=session.client('cloudformation')
    stack=cf.describe_stacks(StackName='ecc-challenges-service')['Stacks'][0]
    params={p['ParameterKey']:p['ParameterValue'] for p in stack['Parameters']}
    assert stack['StackStatus']=='UPDATE_COMPLETE' and params['MulticurveEnabled']=='false'
    assert key.split('_')[1] in params['MulticurveCanaryKey'].split(',')
    assert params['PublicWorkerImage']==IMAGE
    foundation=cf.describe_stacks(StackName='ecc-challenges-foundation')['Stacks'][0]
    outputs={o['OutputKey']:o['OutputValue'] for o in foundation['Outputs']}
    queue_url=outputs['AuditQueueUrl']
    sqs=session.client('sqs')
    ecs=session.client('ecs')
    request=Request('https://ecc-challenges.com/v2/config?campaign='+CAMPAIGN,
                    headers={'Authorization':'Bearer '+key})
    with urlopen(request,timeout=20) as response: config=json.load(response)
    assert config['namespace_id']==NAMESPACE and config['walk']['dp_bits']==16
    nonce=uuid.uuid4().hex[:12]
    app_name='ecc-capacity-'+nonce
    receipt={'format':'v2-fresh-capacity-gate-v1','ok':False,'source_commit':'5bd0a507869492de86a0d7a4f1bc569a0db0b417',
             'image':IMAGE,'campaign':CAMPAIGN,'namespace_id':NAMESPACE,'duration_seconds':DURATION,
             'intended_fleet_l4':1,'test_fleet_l4':2,'app_name':app_name,'samples':[],'workers':[],
             'started_at_unix':time.time(),'errors':[]}
    save(str(dest)+'.plan.json',{k:v for k,v in receipt.items() if k not in ('samples','workers','errors')})
    sandboxes=[]
    try:
        app=modal.App.lookup(app_name,create_if_missing=True)
        image=modal.Image.from_registry(IMAGE).dockerfile_commands('ENTRYPOINT []')
        secret=modal.Secret.from_dict({'ECC_API_KEY':key})
        for index in range(2):
            name='worker-'+str(index)
            volume_name=app_name+'-'+name+'-state'
            volume=modal.Volume.from_name(volume_name,create_if_missing=True)
            sandbox=modal.Sandbox.create('python3','-c',BOOT,name=name,app=app,image=image,
                secrets=[secret],env={'ECC_API_URL':'https://ecc-challenges.com','ECC_VOLUME_MOUNT':'/data',
                'ECC_CAMPAIGN':CAMPAIGN,'ECC_PROTOCOL':'2','ECC_BACKEND':'cuda'},
                volumes={'/data':volume},gpu='L4',cpu=2,memory=4096,timeout=1200)
            sandboxes.append((sandbox,volume,volume_name))
            receipt['workers'].append({'index':index,'sandbox_id':sandbox.object_id,'volume_name':volume_name})
            print('started',index,sandbox.object_id,flush=True)
        start=time.monotonic()
        deadline=start+DURATION
        next_sample=start
        while time.monotonic()<deadline:
            if time.monotonic()<next_sample:
                time.sleep(min(1,next_sample-time.monotonic()))
                continue
            row={'at_unix':time.time(),'elapsed_seconds':time.monotonic()-start,'workers':[]}
            for index,(sandbox,_,_) in enumerate(sandboxes):
                code=sandbox.poll()
                state=checkpoint(sandbox) if code is None else None
                item={'index':index,'sandbox_id':sandbox.object_id,'exit_code':code,'checkpoint':state}
                if state:item['run_status']=status(key,state['run_id'])
                row['workers'].append(item)
                if code is not None:raise RuntimeError('worker_exited_early_'+str(index))
            attrs=sqs.get_queue_attributes(QueueUrl=queue_url,AttributeNames=[
                'ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible','ApproximateNumberOfMessagesDelayed'])['Attributes']
            row['audit_queue']={k:int(v) for k,v in attrs.items()}
            service=ecs.describe_services(cluster='ecc-challenges',services=['ecc-challenges-audit'])['services'][0]
            row['audit_service']={k:service[k] for k in ('desiredCount','runningCount','pendingCount')}
            receipt['samples'].append(row)
            print('sample',len(receipt['samples']),'elapsed',round(row['elapsed_seconds']),
                  'records',[w.get('run_status',{}).get('total_records') for w in row['workers']],
                  'indexed',[w.get('run_status',{}).get('batches',{}).get('indexed',{}).get('records') for w in row['workers']],
                  'queue',row['audit_queue'],flush=True)
            next_sample+=INTERVAL
        receipt['measurement_elapsed_seconds']=time.monotonic()-start
    except Exception as error:
        receipt['errors'].append({'type':type(error).__name__,'message':str(error)[:200]})
    finally:
        for index,(sandbox,_,_) in enumerate(sandboxes):
            try:
                if sandbox.poll() is None:sandbox.terminate()
                sandbox.wait(raise_on_termination=False)
                receipt['workers'][index]['exit_code']=sandbox.poll()
                stdout=sandbox.stdout.read()
                stderr=sandbox.stderr.read()
                receipt['workers'][index]['stdout_sha256']=hashlib.sha256(stdout.encode()).hexdigest()
                receipt['workers'][index]['stderr_sha256']=hashlib.sha256(stderr.encode()).hexdigest()
                receipt['workers'][index]['stdout_bytes']=len(stdout.encode())
                receipt['workers'][index]['stderr_bytes']=len(stderr.encode())
                receipt['workers'][index]['upload_paused_events']=sum('"event": "upload-paused"' in line for line in stdout.splitlines())
            except Exception as error:receipt['errors'].append({'type':type(error).__name__,'stage':'stop','index':index})
        for index,(_,volume,_) in enumerate(sandboxes):
            for _ in range(30):
                try:
                    raw=b''.join(volume.read_file('ecc-state/generic-state.json'))
                    state=json.loads(raw)
                    receipt['workers'][index]['final_checkpoint']={k:state.get(k) for k in ('run_id','namespace_id','cursor','pending','lanes','round_steps','next_trail')}
                    receipt['workers'][index]['checkpoint_sha256']=hashlib.sha256(raw).hexdigest()
                    break
                except Exception:time.sleep(1)
            else:receipt['errors'].append({'type':'CheckpointNotCommitted','index':index})
        run_ids=[w.get('final_checkpoint',{}).get('run_id') for w in receipt['workers']]
        if len(run_ids)==2 and all(run_ids):
            for _ in range(30):
                rows=[status(key,run_id) for run_id in run_ids]
                receipt['final_run_statuses']=rows
                if all(r['batches'].get('indexed',{}).get('records',0)==r['total_records'] and not r['pending_batch_id'] for r in rows):
                    break
                time.sleep(10)
            for row in receipt.get('final_run_statuses',[]):
                if row['batches'].get('indexed',{}).get('records',0)!=row['total_records']:
                    receipt['errors'].append({'type':'NotFullyDrained','run_id':row['run_id']})
                elif not any(w['final_checkpoint']['run_id']==row['run_id'] and not w['final_checkpoint']['pending'] for w in receipt['workers']):
                    receipt['errors'].append({'type':'PendingCheckpoint','run_id':row['run_id']})
                else:
                    request=Request('https://ecc-challenges.com/v2/runs/'+row['run_id']+'/close',data=b'',method='POST',
                                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
                    with urlopen(request,timeout=20) as response:closed=json.load(response)
                    receipt.setdefault('closed_run_ids',[]).append(closed['run_id'])
        receipt['ended_at_unix']=time.time()
        if len(receipt['samples'])<18 or receipt.get('measurement_elapsed_seconds',0)<DURATION:
            receipt['errors'].append({'type':'InsufficientSustainedSamples'})
        if any(row['batches'].get('failed',{}).get('batches',0) for row in receipt.get('final_run_statuses',[])):
            receipt['errors'].append({'type':'VerifierFailedBatch'})
        if any(row['total_records']<50 for row in receipt.get('final_run_statuses',[])):
            receipt['errors'].append({'type':'InsufficientFreshPoints'})
        if any(sample['audit_service']['runningCount']<1 for sample in receipt['samples']):
            receipt['errors'].append({'type':'AuditServiceUnavailable'})
        receipt['ok']=not receipt['errors'] and len(receipt['workers'])==2 and len(receipt.get('closed_run_ids',[]))==2
        save(dest,receipt)
        print('complete',json.dumps({'ok':receipt['ok'],'errors':receipt['errors'],
              'records':[r['total_records'] for r in receipt.get('final_run_statuses',[])],
              'samples':len(receipt['samples'])}),flush=True)
    if not receipt['ok']:raise RuntimeError('capacity gate failed; receipt retained')

if __name__=='__main__':main()
