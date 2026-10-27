#!/usr/bin/env python3
"""Bounded GPU contributor check using the released image and a persistent volume."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid
import modal
from modal_state import STATE_SETUP

app=modal.App('ecc-challenges-live-release-canary')

@app.local_entrypoint()
def main(key_file:str,out:str,image:str,campaign:str,
         registry_auth_file:str='',backend:str='cuda',lanes:int=256,
         require_dp:bool=True):
    destination=Path(out)
    if destination.exists():raise FileExistsError(destination)
    if not re.fullmatch(r'ecc(?:2k?|p)-[0-9]+',campaign):
        raise ValueError('invalid campaign ID')
    if not re.fullmatch(r'(?:public\.ecr\.aws/o1a3z1g6|ghcr\.io/(?:aburan28|isogeny-labs))/ecc-challenges-worker@sha256:[0-9a-f]{64}',image):
        raise ValueError('worker image must be an approved immutable public digest')
    if backend not in ('cpu','cuda') or not 1<=lanes<=4096:
        raise ValueError('invalid backend or lane count')
    key_path=Path(key_file)
    if key_path.stat().st_mode&0o077:
        raise ValueError('runtime secret files must have mode 0600')
    key=key_path.read_text().strip()
    if not re.fullmatch(r'ecc1_[0-9a-f]{24}_[A-Za-z0-9_-]{43}',key):raise ValueError('invalid contributor key')
    registry_secret=None
    image_uri=image
    if registry_auth_file:
        if not image_uri.startswith('public.ecr.aws/'):
            raise ValueError('registry authentication is only for ECR Public')
        auth_path=Path(registry_auth_file)
        if auth_path.stat().st_mode&0o077:raise ValueError('runtime secret files must have mode 0600')
        auth=json.loads(auth_path.read_text())
        if set(auth)!={'REGISTRY_USERNAME','REGISTRY_PASSWORD'} or auth['REGISTRY_USERNAME']!='AWS':
            raise ValueError('expected approved temporary public pull-only registry authentication')
        registry_secret=modal.Secret.from_dict(auth)
    source=Path(__file__).with_name('contributor_canary_boot.py').read_text()
    old="root=Path('/tmp/ecc-canary')"
    assert source.count(old)==1
    source=source.replace(old,"root=Path(os.environ['ECC_CANARY_ROOT'])")
    old="key_path=root/'api-key'\nwith key_path.open('x') as output:"
    assert source.count(old)==1
    # The frozen CPU harness used ephemeral /tmp for its entire state. This
    # provider check persists checkpoints, so keep its key outside that volume.
    source=source.replace(old,"""key_fd,key_name=tempfile.mkstemp(prefix='ecc-canary-api-key-',dir='/tmp')
key_path=Path(key_name)
with os.fdopen(key_fd,'w') as output:""")
    old="print('ECC_CONTRIBUTOR_CANARY '+json.dumps(receipt,sort_keys=True),flush=True)"
    assert source.count(old)==1
    source=source.replace(old,"""payload=json.dumps(receipt,sort_keys=True).encode()
    chunks=[payload[i:i+7000] for i in range(0,len(payload),7000)]
    for i,chunk in enumerate(chunks):
        print('ECC_RECEIPT_CHUNK '+json.dumps({'index':i,'data':base64.b64encode(chunk).decode()}),flush=True)
    print('ECC_RECEIPT_END '+json.dumps({'chunks':len(chunks),'sha256':hashlib.sha256(payload).hexdigest()}),flush=True)""")
    prefix=STATE_SETUP+"""import os,json,subprocess
print('ECC_DEVICE '+json.dumps(subprocess.run(['nvidia-smi','--query-gpu=name,uuid,driver_version','--format=csv,noheader'],capture_output=True,text=True,check=True,timeout=30).stdout.strip()),flush=True)
if os.getuid()==0:
    os.setgroups([]);os.setgid(10001);os.setuid(10001)
"""
    boot=prefix+source
    compile(boot,'modal-contributor-canary','exec')
    nonce=uuid.uuid4().hex[:12]
    volume_name='ecc-release-canary-'+nonce
    root_name='case-'+nonce
    volume=modal.Volume.from_name(volume_name,create_if_missing=True)
    image=modal.Image.from_registry(image_uri,secret=registry_secret,setup_dockerfile_commands=['ENTRYPOINT []'])
    sandbox=None
    receipt={'format':'modal-gpu-contributor-canary-execution-v2','image':image_uri,'ok':False,
             'campaign':campaign,'backend':backend,'lanes':lanes,'requires_production_dp':require_dp,
             'boot_sha256':hashlib.sha256(boot.encode()).hexdigest(),'volume_name':volume_name,'root_name':root_name}
    try:
        sandbox=modal.Sandbox.create('python3','-c',boot,app=app,image=image,gpu='L4',cpu=2,memory=4096,
            timeout=1200,workdir='/tmp',volumes={'/data':volume},
            secrets=[modal.Secret.from_dict({'ECC_API_KEY':key})],
            env={'ECC_RELEASE_IMAGE':image_uri,'ECC_CANARY_ROOT':'/data/ecc-state/'+root_name,
                 'ECC_CANARY_CAMPAIGN':campaign,'ECC_CANARY_BACKEND':backend,
                 'ECC_CANARY_LANES':str(lanes),
                 'ECC_CANARY_REQUIRE_DP':'true' if require_dp else 'false'})
        print('contributor sandbox: '+sandbox.object_id,flush=True)
        sandbox.wait(raise_on_termination=False)
        stdout,stderr=sandbox.stdout.read(),sandbox.stderr.read()
        # Keep the remote failure even when no complete receipt was printed.
        receipt.update(sandbox_exit_code=sandbox.poll(),sandbox_stderr=stderr.replace(key,'[redacted]'))
        chunks={};end=None
        for line in stdout.splitlines():
            if line.startswith('ECC_RECEIPT_CHUNK '):
                item=json.loads(line[len('ECC_RECEIPT_CHUNK '):]);index=item['index']
                if index in chunks:raise ValueError('duplicate receipt chunk')
                chunks[index]=base64.b64decode(item['data'],validate=True)
            elif line.startswith('ECC_RECEIPT_END '):end=json.loads(line[len('ECC_RECEIPT_END '):])
            elif line.startswith('ECC_DEVICE '):receipt['device']=json.loads(line[len('ECC_DEVICE '):])
        if end is None or set(chunks)!=set(range(end['chunks'])):
            receipt['stdout']=stdout.replace(key,'[redacted]')
            raise ValueError('incomplete contributor receipt')
        body=b''.join(chunks[i] for i in range(end['chunks']))
        if hashlib.sha256(body).hexdigest()!=end['sha256']:raise ValueError('contributor receipt checksum differs')
        result=json.loads(body)
        receipt.update(result=result,sandbox_exit_code=sandbox.poll(),
                       sandbox_stderr=stderr.replace(key,'[redacted]'))
        closure=result.get('closure') or {}
        receipt['closure_verified']=(closure.get('run_id')==result.get('run_id') and
                                     closure.get('namespace_id')==result.get('namespace_id') and
                                     bool(closure.get('closed_at')))
        deadline=time.monotonic()+30
        while True:
            try:
                persisted=b''.join(volume.read_file('ecc-state/'+root_name+'/generic-state.json'))
                receipt['last_observed_checkpoint_sha256']=hashlib.sha256(persisted).hexdigest()
                if json.loads(persisted)==result['final_checkpoint']:break
            except (modal.exception.NotFoundError,FileNotFoundError,json.JSONDecodeError):pass
            if time.monotonic()>deadline:raise ValueError('persistent volume commit deadline')
            time.sleep(1)
        receipt.update(persisted_checkpoint_sha256=hashlib.sha256(persisted).hexdigest(),persisted_checkpoint_matches=True,
                       ok=bool(result.get('ok') and receipt['closure_verified'] and sandbox.poll()==0 and
                               result.get('uid')==result.get('gid')==10001))
    except Exception as error:
        receipt.update(ok=False,error_type=type(error).__name__)
        if sandbox:
            try:sandbox.terminate()
            except Exception:pass
    finally:
        receipt.update(modal_app_id=app.app_id,modal_sandbox_id=sandbox.object_id if sandbox else None)
        destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('x') as output:json.dump(receipt,output,sort_keys=True,indent=2);output.write('\n')
    print(json.dumps({k:receipt.get(k) for k in ('ok','modal_sandbox_id','persisted_checkpoint_matches','error_type')}),flush=True)
    if not receipt['ok']:raise RuntimeError('GPU contributor check failed; receipt retained at '+str(destination))
