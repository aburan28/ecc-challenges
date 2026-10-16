"""Record immutable digests from the bounded AWS Linux release build."""
import json
import os
import boto3

commit=os.environ['RELEASE_COMMIT']
tag='allcurves-'+commit[:12]
account=os.environ['RELEASE_ACCOUNT']
region=os.environ['AWS_DEFAULT_REGION']
ecr=boto3.client('ecr',region_name=region)
images={}
for component in ('web','audit','admin'):
    repo='ecc-challenges-'+component
    digest=ecr.describe_images(repositoryName=repo,imageIds=[{'imageTag':tag}])['imageDetails'][0]['imageDigest']
    images[component]=f'{account}.dkr.ecr.{region}.amazonaws.com/{repo}@{digest}'
public=boto3.client('ecr-public',region_name='us-east-1')
digest=public.describe_images(registryId=account,repositoryName='ecc-challenges-worker',imageIds=[{'imageTag':tag}])['imageDetails'][0]['imageDigest']
images['worker']=os.environ['PUBLIC_WORKER_REPOSITORY']+'@'+digest
value={'source_commit':commit,'tag':tag,'images':images,'build_id':os.environ.get('CODEBUILD_BUILD_ID')}
body=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
key=f'release-builds/{commit}/manifest.json'
boto3.client('s3',region_name=region).put_object(Bucket=os.environ['RELEASE_BUCKET'],Key=key,Body=body,
    IfNoneMatch='*',ServerSideEncryption='AES256',ContentType='application/json')
print(body.decode())
