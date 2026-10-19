
import http.server,threading,os,json,subprocess,tarfile,time
from pathlib import Path
root=Path('/tmp/ecc-result');root.mkdir()
token=os.environ.pop('ECC_RESULT_TOKEN')
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path!='/'+token+'/result.tgz' or not (root/'result.tgz').exists():self.send_error(404);return
  data=(root/'result.tgz').read_bytes();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def log_message(self,*args):pass
threading.Thread(target=http.server.ThreadingHTTPServer(('0.0.0.0',8000),Handler).serve_forever,daemon=True).start()
try:
 with (root/'device.txt').open('w') as f:subprocess.run(['nvidia-smi','--query-gpu=name,uuid,driver_version','--format=csv,noheader'],stdout=f,stderr=subprocess.STDOUT,timeout=30)
 env=os.environ.copy();env.update(ECC_BACKEND='cuda',OMP_NUM_THREADS='2',ECC_CHECK_OUT=str(root/'controls'))
 with (root/'run.log').open('w') as f:r=subprocess.run(['python3','/opt/ecc2k130/client.py','--check'],env=env,stdout=f,stderr=subprocess.STDOUT,timeout=600)
 (root/'status.json').write_text(json.dumps({'exit_code':r.returncode,'uid':os.getuid()}))
except Exception as e:(root/'status.json').write_text(json.dumps({'exit_code':1,'error_type':type(e).__name__}))
with tarfile.open(root/'result.tgz','w:gz') as tar:
 for p in root.iterdir():
  if p.name!='result.tgz':tar.add(p,arcname=p.name)
time.sleep(1800)
