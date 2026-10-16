"""Provider checkpoint rollback and credential separation controls."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from generic.client import flush
from ecc_challenges.multicurve.codec import namespace


def test_restored_checkpoint_accepts_stable_batch_ack(tmp_path):
    ns=namespace('eccp-79',1)
    data=bytes(ns.record_bytes)
    state={'run_id':'old-run','namespace_id':ns.id,'cursor':0,'pending':data.hex()}
    class API:
        def request(self,method,path,body=None):
            if path.endswith('/batches'):
                return {**body,'batch_id':'one','run_id':'old-run','namespace_id':ns.id,'upload':None}
            return {'batch_id':'one','run_id':'old-run','namespace_id':ns.id,
                    'batch_end_cursor':len(data),'cursor':3*len(data),'admission_key':'durable-receipt'}
        def upload(self,*args):
            raise AssertionError('an existing immutable batch is not uploaded again')
    path=tmp_path/'state.json'
    flush(API(),ns,state,path)
    restored=json.loads(path.read_text())
    assert restored['cursor']==len(data)
    assert restored['pending']==''


def test_explicit_close_preserves_checkpoint_and_never_starts_engine(tmp_path,monkeypatch):
    import generic.client as client
    ns=namespace('eccp-79')
    saved={'protocol':2,'namespace_id':ns.id,'run_id':'saved-run','pending':''}
    path=tmp_path/'generic-state.json'
    path.write_text(json.dumps(saved))
    calls=[]
    def request(self,method,path,body=None):
        calls.append((method,path))
        return ns.public() if method=='GET' else {'run_id':'saved-run','closed_at':'now'}
    monkeypatch.setattr(client.API,'request',request)
    monkeypatch.setenv('ECC_ACTION','close')
    monkeypatch.setattr(client,'run_round',lambda *a,**k: (_ for _ in ()).throw(AssertionError('engine must not run')))
    assert client.main(tmp_path,'private-runtime-key','eccp-79')==0
    assert calls==[('GET','/v2/config?campaign=eccp-79'),('POST','/v2/runs/saved-run/close')]
    assert json.loads(path.read_text())==saved
