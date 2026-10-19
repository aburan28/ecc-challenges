#!/usr/bin/env python3
"""Freeze and run all24 real-container canaries, at most four concurrently."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from ecc_challenges.multicurve.instances import all_instances

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--key-secret-arn',required=True)
p.add_argument('--image',required=True,help='immutable public release image to validate')
p.add_argument('--account-id',required=True,help='expected destination AWS account')
p.add_argument('--profile',help='optional AWS profile; otherwise use the configured identity')
p.add_argument('--out',type=Path,required=True)
p.add_argument('--apply',action='store_true')
a=p.parse_args()
a.out.mkdir(parents=True,exist_ok=False)
image=a.image
rows=[c.id for c in all_instances()]
plan={'format':'all24-live-contributor-plan-v1','campaigns':rows,'concurrency':4,'per_task_operator_deadline_seconds':1200,
      'image':image,'backend':'cpu','lanes':4,'round_steps':256,
      'boot_sha256':hashlib.sha256(Path('scripts/contributor_canary_boot.py').read_bytes()).hexdigest(),
      'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
      'acceptance':'each campaign generates a production-predicate DP, reaches indexed state, resumes the same checkpoint, and closes; no fleet-rate claim'}
(a.out/'plan.json').write_text(json.dumps(plan,sort_keys=True,indent=2)+'\n')
print(json.dumps(plan),flush=True)
if not a.apply:raise SystemExit(0)
def one(campaign):
    command=[sys.executable,'scripts/run_contributor_canary.py','--account-id',a.account_id,
             '--image',image,'--key-secret-arn',a.key_secret_arn,'--campaign',campaign,'--out',str(a.out/(campaign+'.json')),'--apply']
    if a.profile:command.extend(['--profile',a.profile])
    result=subprocess.run(command,capture_output=True,text=True)
    (a.out/(campaign+'.stdout.txt')).write_text(result.stdout)
    (a.out/(campaign+'.stderr.txt')).write_text(result.stderr)
    return {'campaign':campaign,'returncode':result.returncode,'receipt':campaign+'.json'}
results=[]
with ThreadPoolExecutor(max_workers=4) as executor:
    futures=[executor.submit(one,campaign) for campaign in rows]
    for future in as_completed(futures):
        row=future.result();results.append(row);print(json.dumps(row),flush=True)
summary={'format':'all24-live-contributor-summary-v1','rows':sorted(results,key=lambda r:r['campaign']),
         'ok':len(results)==24 and all(r['returncode']==0 for r in results)}
(a.out/'summary.json').write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n')
if not summary['ok']:raise SystemExit(1)
