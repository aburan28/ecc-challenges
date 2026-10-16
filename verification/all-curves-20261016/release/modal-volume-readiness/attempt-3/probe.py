#!/usr/bin/env python3
"""Two fresh CPU sandboxes check the launcher's exact Modal state setup; no keys."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import uuid

import modal
from modal_state import STATE_SETUP

IMAGE = 'ghcr.io/aburan28/ecc-challenges-worker@sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee'
BOOT = """
import json,os
from pathlib import Path
if int(os.environ['EXPECTED_COUNTER']) == 0:
    # Public filesystem counter, not a real contributor checkpoint or key.
    with Path('/data/generic-state.json').open('x') as output:
        os.fchmod(output.fileno(),0o600)
        output.write('{"counter":0}')
        output.flush();os.fsync(output.fileno())
""" + STATE_SETUP + """
if os.getuid()==0:
    os.setgroups([]);os.setgid(10001);os.setuid(10001)
import hashlib
os.umask(0o077)
p=Path(os.environ['ECC_STATE_DIR'])/'generic-state.json'
expected=int(os.environ['EXPECTED_COUNTER'])
assert json.loads(p.read_text())['counter']==expected
raw=(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid()},sort_keys=True)+'\\n').encode()
tmp=p.with_suffix('.tmp')
with tmp.open('xb') as output:
    output.write(raw);output.flush();os.fsync(output.fileno())
os.replace(tmp,p)
fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
assert p.read_bytes()==raw
info=p.stat()
assert info.st_uid==info.st_gid==10001 and info.st_mode&0o777==0o600
print(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid(),
    'file_uid':info.st_uid,'file_gid':info.st_gid,'file_mode':oct(info.st_mode&0o777),
    'state_directory':str(p.parent),'legacy_file_remains':Path('/data/generic-state.json').exists(),
    'sha256':hashlib.sha256(raw).hexdigest()}),flush=True)
"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    if args.out.exists():raise FileExistsError(args.out)
    name='ecc-volume-bootstrap-'+uuid.uuid4().hex[:12]
    app=modal.App('ecc-challenges-volume-bootstrap-readiness')
    volume=modal.Volume.from_name(name,create_if_missing=True)
    image=modal.Image.from_registry(IMAGE,setup_dockerfile_commands=['ENTRYPOINT []'])
    receipt={'format':'modal-launcher-volume-readiness-v1','ok':False,'image':IMAGE,
             'volume_name':name,'cpu_only':True,'contributor_key_sent':False,'phases':[],
             'boot_sha256':hashlib.sha256(BOOT.encode()).hexdigest(),
             'state_setup_sha256':hashlib.sha256(STATE_SETUP.encode()).hexdigest(),
             'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    sandbox=None
    try:
        with modal.enable_output(),app.run():
            for expected in range(2):
                sandbox=modal.Sandbox.create('python3','-c',BOOT,app=app,image=image,
                    cpu=1,memory=512,timeout=120,workdir='/tmp',volumes={'/data':volume},
                    env={'EXPECTED_COUNTER':str(expected)})
                sandbox.wait(raise_on_termination=False)
                phase={'sandbox_id':sandbox.object_id,'exit_code':sandbox.poll(),
                       'stdout':sandbox.stdout.read(),'stderr':sandbox.stderr.read()}
                receipt['phases'].append(phase)
                if phase['exit_code']!=0:raise RuntimeError('remote_volume_probe_failed')
                value=json.loads(phase['stdout'].strip().splitlines()[-1])
                phase['probe']=value
                assert value['uid']==value['gid']==10001 and value['counter']==expected+1
                assert not value['legacy_file_remains']
                deadline=time.monotonic()+30
                while True:
                    try:
                        raw=b''.join(volume.read_file('ecc-state/generic-state.json'))
                        if hashlib.sha256(raw).hexdigest()==value['sha256']:break
                    except (modal.exception.NotFoundError,FileNotFoundError):pass
                    if time.monotonic()>deadline:raise RuntimeError('volume_commit_deadline')
                    time.sleep(1)
                phase['persisted_checkpoint_sha256']=hashlib.sha256(raw).hexdigest()
            receipt['ok']=len(receipt['phases'])==2
    except Exception as error:
        receipt.update(error_type=type(error).__name__,error=str(error))
        if sandbox:
            try:sandbox.terminate()
            except Exception:pass
    finally:
        receipt['modal_app_id']=app.app_id
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('x') as output:
            json.dump(receipt,output,indent=2,sort_keys=True);output.write('\n')
    print(json.dumps({'ok':receipt['ok'],'error_type':receipt.get('error_type'),
                      'phases':len(receipt['phases'])}),flush=True)
    raise SystemExit(0 if receipt['ok'] else 1)


if __name__=='__main__':main()
