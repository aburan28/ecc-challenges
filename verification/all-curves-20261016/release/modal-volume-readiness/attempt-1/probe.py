import hashlib,json,os,time,uuid
from pathlib import Path
import modal

image_uri="ghcr.io/aburan28/ecc-challenges-worker@sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee"
name="ecc-volume-readiness-"+uuid.uuid4().hex[:12]
app=modal.App("ecc-challenges-volume-readiness")
volume=modal.Volume.from_name(name,create_if_missing=True)
image=modal.Image.from_registry(image_uri,setup_dockerfile_commands=["ENTRYPOINT []"])
boot=r"""
import hashlib,json,os
from pathlib import Path
if os.getuid()==0:
 os.chown('/data',10001,10001,follow_symlinks=False)
 os.setgroups([]);os.setgid(10001);os.setuid(10001)
os.umask(0o077)
p=Path('/data/probe.json');expected=int(os.environ['EXPECTED_COUNTER'])
assert (json.loads(p.read_text())['counter'] if p.exists() else 0)==expected
raw=(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid()},sort_keys=True)+'\n').encode()
tmp=Path('/data/probe.tmp')
with tmp.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
os.replace(tmp,p)
fd=os.open('/data',os.O_RDONLY);os.fsync(fd);os.close(fd)
assert p.stat().st_mode&0o777==0o600
print(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid(),'sha256':hashlib.sha256(raw).hexdigest()}),flush=True)
"""
receipt={'format':'modal-persistent-volume-readiness-v1','ok':False,'image':image_uri,'volume_name':name,'cpu_only':True,'contributor_key_sent':False,'phases':[]}
sandbox=None
try:
 with modal.enable_output(),app.run():
  for expected in range(2):
   sandbox=modal.Sandbox.create('python3','-c',boot,app=app,image=image,cpu=1,memory=512,timeout=120,workdir='/tmp',volumes={'/data':volume},env={'EXPECTED_COUNTER':str(expected)})
   sandbox.wait(raise_on_termination=False)
   stdout,stderr=sandbox.stdout.read(),sandbox.stderr.read()
   value=json.loads(stdout.strip().splitlines()[-1]);assert sandbox.poll()==0 and value['uid']==value['gid']==10001
   deadline=time.monotonic()+30
   while True:
    try:
     raw=b''.join(volume.read_file('probe.json'))
     if hashlib.sha256(raw).hexdigest()==value['sha256']:break
    except modal.exception.NotFoundError:pass
    if time.monotonic()>deadline:raise RuntimeError('volume_commit_deadline')
    time.sleep(1)
   receipt['phases'].append({'sandbox_id':sandbox.object_id,'exit_code':sandbox.poll(),'probe':value,'persisted_checkpoint_sha256':hashlib.sha256(raw).hexdigest(),'stderr':stderr})
  receipt['ok']=len(receipt['phases'])==2
except Exception as error:
 receipt['error_type']=type(error).__name__
 if sandbox:
  try:sandbox.terminate()
  except Exception:pass
finally:
 receipt['modal_app_id']=app.app_id
 Path('.tmp/modal-volume-readiness.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps({'ok':receipt['ok'],'error_type':receipt.get('error_type'),'phases':len(receipt['phases'])}),flush=True)
raise SystemExit(0 if receipt['ok'] else 1)
