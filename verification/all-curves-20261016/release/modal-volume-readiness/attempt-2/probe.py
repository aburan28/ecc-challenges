import hashlib,json,subprocess,time,uuid
from pathlib import Path
import modal

image_uri='ghcr.io/aburan28/ecc-challenges-worker@sha256:f6333f0cf90ff080b7d49d701a2745ad821cc10abeb2fa5c43c623eb1afaf2ee'
name='ecc-volume-child-'+uuid.uuid4().hex[:12]
app=modal.App('ecc-challenges-volume-child-readiness')
volume=modal.Volume.from_name(name,create_if_missing=True)
image=modal.Image.from_registry(image_uri,setup_dockerfile_commands=['ENTRYPOINT []'])
probe=r'''
import hashlib,json,os
from pathlib import Path
os.umask(0o077)
p=Path('/data/ecc-state/probe.json');expected=int(os.environ['EXPECTED_COUNTER'])
assert (json.loads(p.read_text())['counter'] if p.exists() else 0)==expected
raw=(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid()},sort_keys=True)+'\n').encode()
tmp=p.with_suffix('.tmp')
with tmp.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
os.replace(tmp,p)
fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
assert p.read_bytes()==raw
s=p.stat()
assert s.st_mode&0o777==0o600
assert s.st_uid==s.st_gid==10001
print(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid(),'sha256':hashlib.sha256(raw).hexdigest(),'file_uid':s.st_uid,'file_gid':s.st_gid,'file_mode':oct(s.st_mode&0o777)}),flush=True)
'''
boot=r'''
import json,os,subprocess,sys
from pathlib import Path
def meta(p):
 s=p.stat();return {'uid':s.st_uid,'gid':s.st_gid,'mode':oct(s.st_mode&0o777)}
data=Path('/data');child=data/'ecc-state'
result={'before':meta(data)}
child.mkdir(mode=0o700,exist_ok=True)
os.chmod(data,0o755)
os.chown(child,10001,10001,follow_symlinks=False);os.chmod(child,0o700)
result.update(mount=meta(data),child=meta(child),mountinfo=[x for x in Path('/proc/self/mountinfo').read_text().splitlines() if ' /data ' in x])
attempt=subprocess.run([sys.executable,'-c',os.environ['PROBE_SOURCE']],user=10001,group=10001,extra_groups=[],capture_output=True,text=True,timeout=30,cwd='/tmp')
result.update(exit_code=attempt.returncode,stdout=attempt.stdout,stderr=attempt.stderr)
print(json.dumps(result,sort_keys=True),flush=True)
raise SystemExit(attempt.returncode)
'''
out=Path('.tmp/modal-volume-child-attempt-2.json')
if out.exists():raise FileExistsError(out)
receipt={'format':'modal-persistent-volume-child-diagnostic-v1','ok':False,'image':image_uri,'volume_name':name,'cpu_only':True,'contributor_key_sent':False,'phases':[],'boot_sha256':hashlib.sha256(boot.encode()).hexdigest(),'probe_sha256':hashlib.sha256(probe.encode()).hexdigest()}
sandbox=None
try:
 with modal.enable_output(),app.run():
  for expected in range(2):
   sandbox=modal.Sandbox.create('python3','-c',boot,app=app,image=image,cpu=1,memory=512,timeout=120,workdir='/tmp',volumes={'/data':volume},env={'EXPECTED_COUNTER':str(expected),'PROBE_SOURCE':probe})
   sandbox.wait(raise_on_termination=False)
   phase={'sandbox_id':sandbox.object_id,'exit_code':sandbox.poll(),'stdout':sandbox.stdout.read(),'stderr':sandbox.stderr.read()}
   receipt['phases'].append(phase)
   if phase['stdout'].strip():phase['diagnostic']=json.loads(phase['stdout'].strip().splitlines()[-1])
   if phase['exit_code']!=0:raise RuntimeError('remote_volume_probe_failed')
   value=json.loads(phase['diagnostic']['stdout'].strip().splitlines()[-1])
   deadline=time.monotonic()+30
   while True:
    try:
     raw=b''.join(volume.read_file('ecc-state/probe.json'))
     if hashlib.sha256(raw).hexdigest()==value['sha256']:break
    except (modal.exception.NotFoundError,FileNotFoundError):pass
    if time.monotonic()>deadline:raise RuntimeError('volume_commit_deadline')
    time.sleep(1)
   phase.update(probe=value,persisted_checkpoint_sha256=hashlib.sha256(raw).hexdigest())
  receipt['ok']=len(receipt['phases'])==2
except Exception as error:
 receipt.update(error_type=type(error).__name__,error=str(error))
 if sandbox:
  try:sandbox.terminate()
  except Exception:pass
finally:
 receipt['modal_app_id']=app.app_id
 out.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps({'ok':receipt['ok'],'error_type':receipt.get('error_type'),'phases':len(receipt['phases'])}),flush=True)
raise SystemExit(0 if receipt['ok'] else 1)
