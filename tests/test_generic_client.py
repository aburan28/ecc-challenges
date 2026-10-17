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


def test_cpu_lane_default_follows_thread_count(monkeypatch):
    import generic.client as client
    monkeypatch.setenv('OMP_NUM_THREADS','6')
    assert client.default_lanes('cpu')==24
    assert client.default_lanes('cuda')==64
    monkeypatch.setenv('OMP_NUM_THREADS','5000')
    assert client.default_lanes('cpu')==4096


def test_combined_image_selects_cpu_engine_and_honors_override(tmp_path,monkeypatch):
    import generic.client as client
    cpu_engine=tmp_path/'generic-engine-cpu'
    monkeypatch.delenv('ECC_GENERIC_ENGINE',raising=False)
    assert client.engine_for_backend('cpu',cpu_engine)==Path('/opt/ecc/generic-engine')
    cpu_engine.touch()
    assert client.engine_for_backend('cpu',cpu_engine)==cpu_engine
    assert client.engine_for_backend('cuda',cpu_engine)==Path('/opt/ecc/generic-engine')
    monkeypatch.setenv('ECC_GENERIC_ENGINE',str(tmp_path/'custom-engine'))
    assert client.engine_for_backend('cpu',cpu_engine)==tmp_path/'custom-engine'


def test_resume_keeps_saved_lane_count_when_unset(tmp_path,monkeypatch):
    import base64
    import generic.client as client
    from generic.runtime import pack_state
    ns=namespace('eccp-79')
    run_id='18c3053b-96df-4879-a49d-18a52b8fdf8b'
    saved={'protocol':2,'namespace_id':ns.id,'run_id':run_id,'cursor':0,'pending':'',
           'lanes':5,'next_trail':5,'round_steps':256,
           'states':base64.b64encode(b''.join(pack_state(ns.start(run_id,i)) for i in range(5))).decode()}
    (tmp_path/'generic-state.json').write_text(json.dumps(saved))
    monkeypatch.setattr(client.API,'request',lambda self,method,path,body=None:
                        ns.public() if 'config' in path else {'closed_at':None})
    monkeypatch.delenv('ECC_LANES',raising=False)
    monkeypatch.setenv('OMP_NUM_THREADS','16')
    monkeypatch.setenv('ECC_BACKEND','cpu')
    class Stop(Exception):
        pass
    seen=[]
    def run_round(engine,backend,ns_,states,steps,temporary,timeout):
        seen.append(len(states))
        raise Stop
    monkeypatch.setattr(client,'run_round',run_round)
    try:
        client.main(tmp_path,'private-runtime-key','eccp-79')
    except Stop:
        pass
    assert seen==[5]
