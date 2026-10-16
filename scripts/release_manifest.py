"""Record immutable digests from the bounded AWS Linux release build."""
import json
import os
import subprocess
from pathlib import Path


def aws(*args):
    result=subprocess.run(['aws',*args,'--output','json'],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)

commit=os.environ['RELEASE_COMMIT']
tag='allcurves-'+commit[:12]
account=os.environ['RELEASE_ACCOUNT']
region=os.environ['AWS_DEFAULT_REGION']
images={}
for component in ('web','audit','admin'):
    repo='ecc-challenges-'+component
    digest=aws('ecr','describe-images','--region',region,'--repository-name',repo,'--image-ids','imageTag='+tag)['imageDetails'][0]['imageDigest']
    images[component]=f'{account}.dkr.ecr.{region}.amazonaws.com/{repo}@{digest}'
digest=aws('ecr-public','describe-images','--region','us-east-1','--registry-id',account,'--repository-name','ecc-challenges-worker','--image-ids','imageTag='+tag)['imageDetails'][0]['imageDigest']
images['worker']=os.environ['PUBLIC_WORKER_REPOSITORY']+'@'+digest
value={'source_commit':commit,'tag':tag,'images':images,'build_id':os.environ.get('CODEBUILD_BUILD_ID')}
body=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
key=f'release-builds/{commit}/manifest.json'
Path('/tmp/ecc-release-manifest.json').write_bytes(body)
aws('s3api','put-object','--region',region,'--bucket',os.environ['RELEASE_BUCKET'],'--key',key,
    '--body','/tmp/ecc-release-manifest.json','--if-none-match','*',
    '--server-side-encryption','AES256','--content-type','application/json')
print(body.decode())
