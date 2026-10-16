#!/usr/bin/env python3
"""Read pressure counters using the existing admin image and worker SQL role."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import boto3

BOOT = r'''
import json,time,boto3,psycopg
receipt={'format':'live-pressure-snapshot-v1','ok':False,'read_only_transaction':True}
try:
    s=boto3.Session(region_name='us-west-2')
    password=s.client('secretsmanager').get_secret_value(SecretId='ecc-challenges/worker-db-password')['SecretString']
    with psycopg.connect(host='ecc-challenges-db.cn266os0m3am.us-west-2.rds.amazonaws.com',dbname='rho_dp',
        user='ecc_site_worker',password=password,sslmode='verify-full',sslrootcert='/app/infra/rds-ca.pem',connect_timeout=15) as c:
        c.execute('SET TRANSACTION READ ONLY')
        c.execute("SET LOCAL statement_timeout='15s'")
        assert c.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        for label,table in [('v2','dp_pressure'),('legacy','pressure_shards')]:
            batches,size=c.execute('SELECT coalesce(sum(pending_batches),0),coalesce(sum(pending_bytes),0) FROM ecc_public.'+table).fetchone()
            receipt[label+'_pending_batches']=int(batches)
            receipt[label+'_pending_bytes']=int(size)
        receipt['v2_batch_states']={state:int(count) for state,count in c.execute('SELECT state,count(*) FROM ecc_public.dp_batches GROUP BY state')}
        receipt['v2_active_processing_leases']=c.execute("SELECT count(*) FROM ecc_public.dp_batches WHERE state IN ('admitted','verified') AND verification_lease_until>clock_timestamp()").fetchone()[0]
        receipt['ok']=True
except Exception as error:
    receipt['error_type']=type(error).__name__
receipt['created_at_unix']=time.time()
print('ECC_PRESSURE '+json.dumps(receipt,sort_keys=True),flush=True)
raise SystemExit(0 if receipt['ok'] else 1)
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    s=boto3.Session(profile_name='ecc-challenges-new',region_name='us-west-2')
    assert s.client('sts').get_caller_identity()['Account']=='104186811093'
    cfn,ecs,logs,sqs=(s.client(name) for name in ('cloudformation','ecs','logs','sqs'))
    def outputs(name):
        return {x['OutputKey']:x['OutputValue'] for x in cfn.describe_stacks(StackName=name)['Stacks'][0]['Outputs']}
    data,foundation=outputs('ecc-challenges-data'),outputs('ecc-challenges-foundation')
    td=ecs.describe_task_definition(taskDefinition=data['AdminTaskArn'])['taskDefinition']
    receipt={'format':'live-pressure-execution-v1','ok':False,'account':'104186811093',
             'command_sha256':hashlib.sha256(BOOT.encode()).hexdigest(),
             'image':td['containerDefinitions'][0]['image'],'task_definition':td['taskDefinitionArn']}
    task=None
    try:
        response=ecs.run_task(cluster=data['AdminClusterName'],taskDefinition=data['AdminTaskArn'],launchType='FARGATE',
            count=1,networkConfiguration={'awsvpcConfiguration':{
                'subnets':['subnet-09dd10a77ae5c6152','subnet-0b451e0104033f9bd'],
                'securityGroups':[data['AdminSecurityGroupId']],'assignPublicIp':'ENABLED'}},
            overrides={'containerOverrides':[{'name':'admin','command':['python','-c',BOOT]}]})
        if response.get('failures'):raise RuntimeError('admin_task_refused')
        task=response['tasks'][0]['taskArn'];receipt['task_arn']=task
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            row=ecs.describe_tasks(cluster=data['AdminClusterName'],tasks=[task])['tasks'][0]
            if row['lastStatus']=='STOPPED':break
            time.sleep(3)
        else:raise RuntimeError('admin_task_deadline')
        receipt['exit_code']=row['containers'][0].get('exitCode')
        stream='admin/admin/'+task.rsplit('/',1)[1]
        for _ in range(5):
            events=logs.get_log_events(logGroupName='/ecc-challenges/admin',logStreamName=stream,startFromHead=True)['events']
            matches=[json.loads(e['message'].removeprefix('ECC_PRESSURE ')) for e in events if e['message'].startswith('ECC_PRESSURE ')]
            if matches:break
            time.sleep(2)
        if len(matches)!=1:raise RuntimeError('pressure_receipt_missing_or_duplicate')
        receipt['snapshot']=matches[0]
        attrs=sqs.get_queue_attributes(QueueUrl=foundation['AuditQueueUrl'],AttributeNames=[
            'ApproximateNumberOfMessages','ApproximateNumberOfMessagesNotVisible','ApproximateNumberOfMessagesDelayed'])['Attributes']
        receipt['audit_queue']={k:int(v) for k,v in attrs.items()}
        receipt['ok']=bool(receipt['exit_code']==0 and receipt['snapshot']['ok'])
    except Exception as error:
        receipt['error_type']=type(error).__name__
        if task:
            ecs.stop_task(cluster=data['AdminClusterName'],task=task,reason='bounded pressure snapshot cleanup')
    finally:
        a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open('x') as f:json.dump(receipt,f,sort_keys=True,indent=2);f.write('\n')
    print(json.dumps(receipt,sort_keys=True))
    if not receipt['ok']:raise SystemExit(1)


if __name__=='__main__':main()
