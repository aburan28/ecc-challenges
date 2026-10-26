#!/usr/bin/env python3
"""Run one bounded Fargate contributor without runtime AWS permissions."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import boto3
from ecc_challenges.multicurve.instances import all_instances


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',help='optional AWS profile; otherwise use the configured identity')
    p.add_argument('--account-id',required=True)
    p.add_argument('--image',required=True)
    p.add_argument('--key-secret-arn',required=True)
    p.add_argument('--campaign',default='eccp-79',choices=sorted(c.id for c in all_instances()))
    p.add_argument('--checkpoint-only',action='store_true',help='check native progress and restart without requiring a sparse production DP')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    args=p.parse_args()
    if args.out.exists():raise FileExistsError(args.out)
    s=boto3.Session(profile_name=args.profile,region_name='us-west-2')
    assert s.client('sts').get_caller_identity()['Account']==args.account_id
    assert args.key_secret_arn.startswith('arn:aws:secretsmanager:us-west-2:'+args.account_id+':secret:ecc-challenges/contributor-canary-')
    assert re.fullmatch(r'(?:public\.ecr\.aws/o1a3z1g6|ghcr\.io/(?:aburan28|isogeny-labs))/ecc-challenges-worker(?:-cpu)?@sha256:[0-9a-f]{64}',args.image)
    digest=args.image.rsplit('@sha256:',1)[1]
    assert len(digest)==64 and all(c in '0123456789abcdef' for c in digest)
    boot=Path(__file__).with_name('contributor_canary_boot.py').read_text()
    cfn=s.client('cloudformation')
    def outputs(name):
        row=cfn.describe_stacks(StackName=name)['Stacks'][0]
        assert row['StackStatus'] in ('CREATE_COMPLETE','UPDATE_COMPLETE')
        return {r['OutputKey']:r['OutputValue'] for r in row['Outputs']}
    data=outputs('ecc-challenges-data'); infra=outputs('ecc-challenges-contributor-canary')
    receipt={'format':'fargate-contributor-canary-execution-v1','image':args.image,
             'campaign':args.campaign,
             'requires_production_dp':not args.checkpoint_only,
             'boot_sha256':hashlib.sha256(boot.encode()).hexdigest(),'account_id':args.account_id,
             'runtime_aws_role':None,'operator_deadline_seconds':1200,'mode':'apply' if args.apply else 'plan'}
    print(json.dumps(receipt),flush=True)
    if not args.apply:return
    ecs=s.client('ecs');logs=s.client('logs')
    definition=ecs.register_task_definition(family='ecc-challenges-contributor-canary',networkMode='awsvpc',
        requiresCompatibilities=['FARGATE'],cpu='2048',memory='4096',ephemeralStorage={'sizeInGiB':64},
        executionRoleArn=infra['ExecutionRoleArn'],runtimePlatform={'cpuArchitecture':'X86_64','operatingSystemFamily':'LINUX'},
        containerDefinitions=[{'name':'contributor','image':args.image,'essential':True,'user':'10001:10001',
            'entryPoint':['python3','-c'],'command':[boot],
            'environment':[{'name':'ECC_RELEASE_IMAGE','value':args.image},{'name':'ECC_CANARY_CAMPAIGN','value':args.campaign},
                           {'name':'ECC_CANARY_REQUIRE_DP','value':'false' if args.checkpoint_only else 'true'},
                           {'name':'ECC_CANARY_SECONDS','value':'180' if args.checkpoint_only else '1100'},
                           {'name':'AWS_EC2_METADATA_DISABLED','value':'true'}],
            'secrets':[{'name':'ECC_API_KEY','valueFrom':args.key_secret_arn}],
            'stopTimeout':120,'logConfiguration':{'logDriver':'awslogs','options':{
                'awslogs-group':'/ecc-challenges/contributor-canary','awslogs-region':'us-west-2','awslogs-stream-prefix':'contributor'}}}])['taskDefinition']
    assert not definition.get('taskRoleArn')
    receipt['task_definition']=definition['taskDefinitionArn']
    task=None
    try:
        result=ecs.run_task(cluster='ecc-challenges',taskDefinition=definition['taskDefinitionArn'],launchType='FARGATE',
            startedBy='ecc-contributor-canary',networkConfiguration={'awsvpcConfiguration':{
                'subnets':[data['PublicSubnetA'],data['PublicSubnetB']],'securityGroups':[infra['SecurityGroupId']],
                'assignPublicIp':'ENABLED'}})
        if result.get('failures') or len(result['tasks'])!=1:raise RuntimeError('canary task refused')
        task=result['tasks'][0]['taskArn'];receipt['task_arn']=task
        print('canary task: '+task,flush=True)
        until=time.monotonic()+1200
        while time.monotonic()<until:
            row=ecs.describe_tasks(cluster='ecc-challenges',tasks=[task])['tasks'][0]
            if row['lastStatus']=='STOPPED':break
            time.sleep(10)
        else:
            receipt['deadline_exceeded']=True
            ecs.stop_task(cluster='ecc-challenges',task=task,reason='bounded contributor release check deadline')
            ecs.get_waiter('tasks_stopped').wait(cluster='ecc-challenges',tasks=[task],WaiterConfig={'Delay':10,'MaxAttempts':18})
            row=ecs.describe_tasks(cluster='ecc-challenges',tasks=[task])['tasks'][0]
        receipt.update(last_status=row['lastStatus'],stop_code=row.get('stopCode'),stopped_reason=row.get('stoppedReason'),
                       containers=[{k:c.get(k) for k in ('name','exitCode','reason','imageDigest')} for c in row['containers']])
        stream='contributor/contributor/'+task.rsplit('/',1)[1]
        for _ in range(6):
            try:events=logs.get_log_events(logGroupName='/ecc-challenges/contributor-canary',logStreamName=stream,startFromHead=True)['events']
            except logs.exceptions.ResourceNotFoundException:events=[]
            messages=[r['message'] for r in events]
            matches=[m[len('ECC_CONTRIBUTOR_CANARY '):] for m in messages if m.startswith('ECC_CONTRIBUTOR_CANARY ')]
            if matches:break
            time.sleep(3)
        receipt['log_messages']=messages
        receipt['result']=json.loads(matches[-1]) if matches else None
        receipt['ok']=bool(receipt['result'] and receipt['result'].get('ok') and all(c.get('exitCode')==0 for c in row['containers']) and not receipt.get('deadline_exceeded'))
    except Exception as error:
        receipt.update(ok=False,error_type=type(error).__name__)
        if task:
            try:ecs.stop_task(cluster='ecc-challenges',task=task,reason='contributor check controller failure')
            except Exception:pass
    finally:
        try:ecs.deregister_task_definition(taskDefinition=definition['taskDefinitionArn'])
        except Exception as error:receipt['cleanup_error_type']=type(error).__name__
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('x') as output:json.dump(receipt,output,sort_keys=True,indent=2);output.write('\n')
    print(json.dumps({'ok':receipt.get('ok'),'task':task,'receipt':str(args.out)}),flush=True)
    if not receipt.get('ok'):raise SystemExit(1)

if __name__=='__main__':main()
