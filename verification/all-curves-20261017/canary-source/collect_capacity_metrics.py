#!/usr/bin/env python3
"""Capture CloudWatch health for the exact bounded fresh-point load window."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import boto3


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--capacity-receipt',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():raise FileExistsError(args.out)
    source=json.loads(args.capacity_receipt.read_text())
    assert source['format']=='v2-fresh-capacity-gate-v1'
    session=boto3.Session(profile_name='ecc-challenges-new',region_name='us-west-2')
    assert session.client('sts').get_caller_identity()['Account']=='104186811093'
    client=session.client('cloudwatch')
    start=datetime.fromtimestamp(source['started_at_unix'],timezone.utc)-timedelta(minutes=1)
    end=datetime.fromtimestamp(source['ended_at_unix'],timezone.utc)+timedelta(minutes=2)
    specs=[
        ('AWS/RDS','CPUUtilization','DBInstanceIdentifier','ecc-challenges-db'),
        ('AWS/RDS','DatabaseConnections','DBInstanceIdentifier','ecc-challenges-db'),
        ('AWS/RDS','ReadIOPS','DBInstanceIdentifier','ecc-challenges-db'),
        ('AWS/RDS','WriteIOPS','DBInstanceIdentifier','ecc-challenges-db'),
        ('AWS/RDS','ReadLatency','DBInstanceIdentifier','ecc-challenges-db'),
        ('AWS/RDS','WriteLatency','DBInstanceIdentifier','ecc-challenges-db'),
        ('AWS/SQS','ApproximateAgeOfOldestMessage','QueueName','ecc-challenges-audit'),
        ('AWS/SQS','ApproximateNumberOfMessagesVisible','QueueName','ecc-challenges-audit'),
        ('AWS/SQS','NumberOfMessagesSent','QueueName','ecc-challenges-audit'),
        ('AWS/SQS','NumberOfMessagesReceived','QueueName','ecc-challenges-audit'),
    ]
    receipt={'format':'v2-fresh-capacity-cloudwatch-v1','source_receipt_sha256':hashlib.sha256(args.capacity_receipt.read_bytes()).hexdigest(),
             'start_utc':start.isoformat(),'end_utc':end.isoformat(),'period_seconds':60,'metrics':{}}
    for namespace,name,dimension,value in specs:
        response=client.get_metric_statistics(Namespace=namespace,MetricName=name,
            Dimensions=[{'Name':dimension,'Value':value}],StartTime=start,EndTime=end,
            Period=60,Statistics=['Average','Maximum','Sum'])
        rows=[]
        for item in sorted(response['Datapoints'],key=lambda row:row['Timestamp']):
            rows.append({k:(v.isoformat() if k=='Timestamp' else v) for k,v in item.items()})
        receipt['metrics'][namespace+'/'+name]=rows
    receipt['missing_metrics']=[k for k,v in receipt['metrics'].items() if not v]
    receipt['ok']=not receipt['missing_metrics']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x') as handle:
        json.dump(receipt,handle,sort_keys=True,indent=2)
        handle.write('\n');handle.flush();os.fsync(handle.fileno())
    print(json.dumps({'ok':receipt['ok'],'missing_metrics':receipt['missing_metrics'],
                      'counts':{k:len(v) for k,v in receipt['metrics'].items()}},sort_keys=True))

if __name__=='__main__':main()
