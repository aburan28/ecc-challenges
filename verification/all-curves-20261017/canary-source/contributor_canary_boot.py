"""Bounded entrypoint, production-predicate upload, index and restart canary.

Executed inside the frozen release image. The key is injected at runtime and
never included in the receipt. Full public checkpoint bytes survive in logs.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, '/opt/ecc2k130')
from generic.client import API

os.umask(0o077)
root=Path('/tmp/ecc-canary')
root.mkdir(mode=0o700)
state_path=root/'generic-state.json'
key=os.environ.pop('ECC_API_KEY')
key_path=root/'api-key'
with key_path.open('x') as output:output.write(key+'\n')
key_path.chmod(0o600)
api=API('https://ecc-challenges.com',key)
campaign=os.environ.get('ECC_CANARY_CAMPAIGN','eccp-79')
backend=os.environ.get('ECC_CANARY_BACKEND','cpu')
lanes=int(os.environ.get('ECC_CANARY_LANES','4'))
started=time.monotonic()
deadline=started+1100
receipt={'format':'live-contributor-entrypoint-canary-v1','campaign':campaign,'backend':backend,
         'lanes':lanes,'round_steps':256,'uid':os.getuid(),'gid':os.getgid(),
         'release_image':os.environ['ECC_RELEASE_IMAGE'],'phases':[],'ok':False}
child=None
handle=None
finished=threading.Event()

def watchdog():
    if finished.wait(max(0,deadline-time.monotonic())):return
    if child is not None:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    print(json.dumps({'format':receipt['format'],'ok':False,'error':'hard_deadline','run_id':receipt.get('run_id')}),flush=True)
    os._exit(124)
threading.Thread(target=watchdog,daemon=True).start()

def saved():
    return json.loads(state_path.read_text()) if state_path.exists() else None

def start(phase):
    global child,handle
    env=os.environ.copy()
    env.update(ECC_API_KEY_FILE=str(key_path),ECC_API_URL='https://ecc-challenges.com',
               ECC_STATE_DIR=str(root),ECC_CAMPAIGN=campaign,ECC_PROTOCOL='2',ECC_BACKEND=backend,
               ECC_LANES=str(lanes),ECC_STEPS_PER_ROUND='256',ECC_ACTION='work',PYTHONUNBUFFERED='1')
    stdout=tempfile.TemporaryFile(dir=root);stderr=tempfile.TemporaryFile(dir=root)
    child=subprocess.Popen([sys.executable,'/opt/ecc2k130/client.py'],env=env,stdout=stdout,stderr=stderr,start_new_session=True)
    handle=(phase,stdout,stderr)

def stop():
    global child,handle
    if child is None:return
    forced=False
    if child.poll() is None:
        child.send_signal(signal.SIGTERM)
        try:child.wait(timeout=min(90,max(1,deadline-time.monotonic()-5)))
        except subprocess.TimeoutExpired:
            forced=True;os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
    phase,stdout,stderr=handle
    stdout.seek(0);stderr.seek(0)
    a,b=stdout.read(),stderr.read();stdout.close();stderr.close()
    result={'phase':phase,'exit_code':child.returncode,'forced_stop':forced,
            'stdout_sha256':hashlib.sha256(a).hexdigest(),'stderr_sha256':hashlib.sha256(b).hexdigest(),
            'stdout_bytes':len(a),'stderr_bytes':len(b)}
    # Both streams are retained as hashes; credentials and arbitrary exceptions
    # cannot escape into the public receipt.
    if result['exit_code'] != 0:
        result['stderr_excerpt_redacted'] = b[:2048].decode(errors='replace').replace(key, '[redacted]')
    receipt['phases'].append(result)
    child=None;handle=None
    if forced or result['exit_code']!=0:raise RuntimeError(phase+'_entrypoint_exit')

def indexed(checkpoint,seconds):
    until=min(time.monotonic()+seconds,deadline-30)
    last=None
    while time.monotonic()<until:
        last=api.request('GET','/v2/runs/'+checkpoint['run_id'])
        if last['namespace_id']!=receipt['namespace_id'] or last['campaign']!=campaign:
            raise RuntimeError('status_binding')
        if last['batches'].get('failed',{}).get('batches',0):raise RuntimeError('pipeline_rejected_batch')
        count=last['batches'].get('indexed',{}).get('records',0)
        if count==last['total_records']>0 and last['cursor']==checkpoint['cursor']==count*last['record_bytes'] and not last['pending_batch_id']:
            return {k:last.get(k) for k in ('run_id','namespace_id','campaign','cursor','total_records','total_bytes','record_bytes','batches','pending_batch_id','closed_at')}
        time.sleep(5)
    receipt['last_status']={k:last.get(k) for k in ('run_id','cursor','total_records','batches')} if last else None
    raise RuntimeError('index_deadline')

try:
    config=api.request('GET','/v2/config?campaign='+campaign)
    receipt['namespace_id']=config['namespace_id']
    receipt['engine_binary_sha256']=hashlib.sha256(Path('/opt/ecc/generic-engine').read_bytes()).hexdigest()
    start('first_run')
    until=started+600
    while time.monotonic()<until:
        state=saved()
        if state:
            receipt['run_id']=state['run_id']
            if state['cursor']>0:break
        if child.poll() is not None:break
        time.sleep(1)
    stop()
    first=saved();receipt['first_checkpoint']=first
    if not first or first['cursor']<=0:raise RuntimeError('no_production_dp_within_600_seconds')
    if first['pending']:raise RuntimeError('pending_after_stop')
    receipt['first_indexed_status']=indexed(first,120)
    start('restart_same_checkpoint')
    until=time.monotonic()+30
    advanced=False
    while time.monotonic()<until:
        state=saved()
        if not state or state['run_id']!=first['run_id'] or state['cursor']<first['cursor']:
            raise RuntimeError('restart_identity_or_cursor')
        if state['states']!=first['states']:advanced=True;break
        if child.poll() is not None:break
        time.sleep(1)
    stop()
    second=saved();receipt['restart_checkpoint']=second
    if not advanced or second['pending']:raise RuntimeError('restart_did_not_advance_and_flush')
    receipt['final_indexed_status']=indexed(second,90)
    receipt.update(restart_same_run_id=True,restart_cursor_monotonic=True,restart_checkpoint_advanced=True,ok=True)
except Exception as error:
    # API errors expose only HTTP status; other exception messages are omitted.
    receipt['error_type']=type(error).__name__
    if type(error) is RuntimeError:receipt['error']=str(error)
    receipt['ok']=False
finally:
    try:stop()
    except Exception as error:receipt.update(cleanup_error_type=type(error).__name__,ok=False)
    state=saved();receipt['final_checkpoint']=state
    if state:
        receipt['run_id']=state['run_id']
        if not state['pending'] and time.monotonic()<deadline-20:
            try:receipt['closure']=api.request('POST','/v2/runs/'+state['run_id']+'/close')
            except Exception as error:receipt.update(closure_error_type=type(error).__name__,ok=False)
    receipt['elapsed_seconds']=time.monotonic()-started
    finished.set();key_path.unlink(missing_ok=True)
    print('ECC_CONTRIBUTOR_CANARY '+json.dumps(receipt,sort_keys=True),flush=True)
raise SystemExit(0 if receipt['ok'] else 1)
