#!/usr/bin/env python3
"""Check the published image itself on L4; upload no local source or secrets."""
import json
import os
from pathlib import Path
import re
import modal

app=modal.App('ecc-challenges-release-image-correctness')
BOOT=r'''
import hashlib,io,json,os,platform,subprocess,sys,tarfile,tempfile,time
from pathlib import Path,PurePosixPath
from urllib.request import urlopen
image=os.environ['ECC_RELEASE_IMAGE']
started=time.monotonic()
receipt={'format':'published-worker-cuda-correctness-v1','release_image':image,
         'python':sys.version,'platform':platform.platform(),'uid':os.getuid(),'gid':os.getgid(),
         'rows':[],'errors':[]}
engine=Path('/opt/ecc/generic-engine')
url='https://raw.githubusercontent.com/aburan28/ecc-challenges/ca45bd1a4555386a59f9b27d89b36569dd43f085/verification/all-curves-20261016/job03-controls.tar.gz'
expected='b0807a28e8757993103ffb20b117e9d8b155517beb738089d3ab8285160e6cdb'
def run(command,timeout=120):
    before=time.monotonic()
    result=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    return {'command':command,'returncode':result.returncode,'stdout':result.stdout,
            'stderr':result.stderr,'elapsed_seconds':time.monotonic()-before}
try:
    receipt['engine_binary_sha256']=hashlib.sha256(engine.read_bytes()).hexdigest()
    receipt['self_test']=run([str(engine),'--self-test'],30)
    receipt['device']=run(['nvidia-smi','--query-gpu=name,uuid,driver_version','--format=csv,noheader'],30)
    receipt['worker_import']=run(['python3','-c',"import sys;sys.path.insert(0,'/opt/ecc2k130');import generic.client,generic.runtime;from ecc_challenges.multicurve.instances import all_instances;assert len(all_instances())==24"],60)
    if any(receipt[k]['returncode'] for k in ('self_test','device','worker_import')):
        raise RuntimeError('published image prerequisite failed')
    with urlopen(url,timeout=60) as response:
        payload=response.read(4*1024*1024+1)
    if len(payload)>4*1024*1024 or hashlib.sha256(payload).hexdigest()!=expected:
        raise RuntimeError('control archive checksum/size mismatch')
    receipt.update(archive_url=url,archive_sha256=expected)
    with tempfile.TemporaryDirectory(prefix='ecc-release-') as temporary:
        root=Path(temporary); controls=root/'controls'; controls.mkdir()
        total=0; seen=set()
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
            members=archive.getmembers()
            if len(members)>4096: raise RuntimeError('too many archive members')
            for member in members:
                name=PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or str(name) in seen:
                    raise RuntimeError('unsafe or duplicate archive member')
                seen.add(str(name)); target=controls.joinpath(*name.parts)
                if member.isdir(): target.mkdir(parents=True,exist_ok=True); continue
                total+=member.size
                if not member.isfile() or not 0<=member.size<=1048576 or total>16*1048576:
                    raise RuntimeError('unsafe archive member type/size')
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.extractfile(member) as stream: data=stream.read(member.size+1)
                if len(data)!=member.size: raise RuntimeError('archive member size differs')
                with target.open('xb') as output: output.write(data)
        campaigns=sorted(p for p in controls.iterdir() if p.is_dir())
        if len(campaigns)!=24: raise RuntimeError('archive lacks all24 campaigns')
        expected_cases={'mixed-dp1-cpu','mixed-dp40-cpu','unit-one-step-cpu',
                        'bad-field-cpu','bad-infinity-cpu','bad-scalar-cpu','step-overflow-cpu'}
        for campaign in campaigns:
            cases=sorted(p for p in campaign.iterdir() if p.is_dir() and p.name.endswith('-cpu'))
            if {p.name for p in cases}!=expected_cases: raise RuntimeError('case coverage differs')
            for case in cases:
                row={'campaign':campaign.name,'case':case.name,'ok':False}; receipt['rows'].append(row)
                try:
                    if time.monotonic()-started>1080: raise RuntimeError('harness deadline')
                    job=(case/'job.bin').read_bytes(); incoming=(case/'in.bin').read_bytes()
                    if len(job)!=6568 or job[:8]!=b'ECCJOB03' or not incoming or len(incoming)%216:
                        raise RuntimeError('control framing mismatch')
                    row['input_sha256']=hashlib.sha256(job+incoming).hexdigest()
                    output,dps=root/'out.bin',root/'dp.bin'
                    output.unlink(missing_ok=True);dps.unlink(missing_ok=True)
                    row.update(run([str(engine),'--backend','cuda',str(case/'job.bin'),str(case/'in.bin'),str(output),str(dps)]))
                    success=(case/'out.bin').exists()
                    if success:
                        a,b=output.read_bytes(),dps.read_bytes()
                        row.update(state_sha256=hashlib.sha256(a).hexdigest(),dp_sha256=hashlib.sha256(b).hexdigest(),dp_bytes=len(b))
                        row['ok']=row['returncode']==0 and a==(case/'out.bin').read_bytes() and b==(case/'dp.bin').read_bytes()
                        if case.name=='mixed-dp1-cpu' and not b:row['ok']=False
                    else:row['ok']=row['returncode']!=0 and not output.exists() and not dps.exists()
                except Exception as error:row.update(error_type=type(error).__name__,error=str(error))
except Exception as error:receipt['errors'].append({'type':type(error).__name__,'message':str(error)})
receipt.update(campaigns=len({r['campaign'] for r in receipt['rows']}),checks=len(receipt['rows']),
               passed_checks=sum(r['ok'] for r in receipt['rows']),elapsed_seconds=time.monotonic()-started)
receipt['ok']=receipt['checks']==168 and receipt['passed_checks']==168 and receipt['campaigns']==24 and receipt['uid']==receipt['gid']==10001 and not receipt['errors']
print(json.dumps(receipt,sort_keys=True),flush=True)
raise SystemExit(0 if receipt['ok'] else 1)
'''

@app.local_entrypoint()
def main(out:str):
    uri=os.environ.get('ECC_RELEASE_IMAGE','')
    if not re.fullmatch(r'(?:public\.ecr\.aws/[A-Za-z0-9_-]+|ghcr\.io/aburan28)/ecc-challenges-worker@sha256:[0-9a-f]{64}',uri):
        raise ValueError('ECC_RELEASE_IMAGE must be an immutable public worker digest')
    destination=Path(out)
    if destination.exists():raise FileExistsError(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    registry_file=os.environ.get('ECC_REGISTRY_AUTH_FILE')
    secret=None
    if registry_file:
        auth_path=Path(registry_file)
        if auth_path.stat().st_mode & 0o077:
            raise ValueError('registry authentication file must have mode 0600')
        auth=json.loads(auth_path.read_text())
        if set(auth)!={'REGISTRY_USERNAME','REGISTRY_PASSWORD'} or auth['REGISTRY_USERNAME']!='AWS':
            raise ValueError('expected temporary pull-only ECR registry authentication')
        secret=modal.Secret.from_dict(auth)
    image=modal.Image.from_registry(uri,secret=secret,setup_dockerfile_commands=['ENTRYPOINT []','USER 10001:10001'])
    sandbox=None
    try:
        sandbox=modal.Sandbox.create('python3','-c',BOOT,app=app,image=image,gpu='L4',cpu=2,memory=4096,
                                    timeout=1200,workdir='/tmp',env={'ECC_RELEASE_IMAGE':uri})
        print('release check sandbox: '+sandbox.object_id,flush=True)
        sandbox.wait(raise_on_termination=False)
        stdout,stderr=sandbox.stdout.read(),sandbox.stderr.read()
        try:receipt=json.loads(stdout)
        except (ValueError,TypeError):receipt={'ok':False,'stdout':stdout}
        receipt.update(modal_app_id=app.app_id,modal_sandbox_id=sandbox.object_id,
                       sandbox_exit_code=sandbox.poll(),sandbox_stderr=stderr)
        if receipt['sandbox_exit_code']!=0:receipt['ok']=False
    except Exception as error:
        receipt={'ok':False,'error_type':type(error).__name__,'error':str(error),'release_image':uri,
                 'modal_sandbox_id':sandbox.object_id if sandbox else None}
        if sandbox:sandbox.terminate()
    with destination.open('x') as output:
        json.dump(receipt,output,sort_keys=True,indent=2);output.write('\n');output.flush();os.fsync(output.fileno())
    print(json.dumps({k:receipt.get(k) for k in ('ok','checks','campaigns','passed_checks','modal_sandbox_id')}))
    if not receipt.get('ok'):raise RuntimeError('release check failed; receipt retained at '+str(destination))
