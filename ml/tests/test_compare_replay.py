import copy
import json
import pytest
from ml.training.compare_replay import compare_session, intervals, run
from deskmate_hub.inference import load_config
from deskmate_hub.personalization import load_personalization_config
from deskmate_hub.personalization.contract import model_metadata

POLICY={"low_confidence":0.75,"max_interval_gap_sec":12}


class Fake:
    def __init__(self,path,metadata):
        pass
    def predict(self,window):
        return [0.6,0.2,0.1,0.1]


def configured(tmp_path):
    cfg=load_personalization_config()
    cfg.update(enabled=True,model_path=str(tmp_path/'model.tflite'),metadata_path=str(tmp_path/'meta.json'))
    (tmp_path/'model.tflite').write_bytes(b'fake')
    (tmp_path/'meta.json').write_text(json.dumps(model_metadata(6,10,'baseline')))
    return cfg


def frames():
    return [{"now":100+t*10,"present":False,"pc_ratio":0,
             "signals":{"keystroke":{"phi":0.8,"delta":0.1,"available":True}}} for t in range(10)]


def test_counts_intervals_and_observation_preserves_fsm(tmp_path):
    raw=frames()
    original=copy.deepcopy(raw)
    cfg=configured(tmp_path)
    result=compare_session(raw,load_config(),cfg,period=10,normalization='baseline',policy=POLICY,backend_factory=Fake)
    summary=result['summary']
    assert summary['predicted']==summary['comparable']==5
    assert summary['disagreements']==summary['low_confidence']==5
    assert summary['status_counts']=={'warming_up':5,'predicted':5}
    assert summary['agreement']==0 and sum(map(sum,summary['agreement_matrix']))==5
    assert summary['disagreement_intervals'][0]['ticks']==5
    assert result['fsm_and_report_unchanged'] and raw==original
    dropped=compare_session(raw,load_config(),cfg,period=10,normalization='baseline',policy=POLICY,backend_factory=Fake,drop_signal='keystroke')
    assert all('keystroke' in r['missing_signals'] for r in dropped['trace'])
    assert raw==original


def test_failure_and_timing_gap_do_not_inflate_denominator(tmp_path):
    cfg=configured(tmp_path)
    result=compare_session(frames(),load_config(),cfg,period=10,normalization='linear',policy=POLICY,backend_factory=Fake)
    assert result['summary']['agreement'] is None
    assert result['summary']['predicted']==0
    assert result['summary']['status_counts']=={'unavailable':10}
    raw=frames()
    for row in raw[6:]:
        row['now']+=100
    result=compare_session(raw,load_config(),cfg,period=10,normalization='baseline',policy=POLICY,backend_factory=Fake)
    assert result['summary']['predicted']==1
    assert result['summary']['status_counts']['warming_up']==9


def test_intervals_break_on_gap_and_nonmatching_tick():
    rows=[{"tick":t,"now":n,"flag":f} for t,n,f in ((0,0,True),(1,10,True),(2,20,False),(3,30,True),(4,100,True))]
    grouped=intervals(rows,lambda r:r['flag'],12)
    assert [r['ticks'] for r in grouped]==[2,1,1]


def test_artifacts_session_reset_and_fresh_output(tmp_path,monkeypatch):
    cfg=configured(tmp_path)
    bundle=tmp_path/'bundle'
    bundle.mkdir()
    (bundle/'selected.tflite').write_bytes(b'fake')
    (bundle/'selected.metadata.json').write_text(json.dumps(model_metadata(6,10,'baseline')))
    source=tmp_path/'frames.jsonl'
    source.write_text(''.join(json.dumps(f)+'\n' for f in frames()))
    monkeypatch.setattr('deskmate_hub.personalization.tflite_backend.TFLiteBackend',Fake)
    report=run([source,source],bundle,tmp_path/'report')
    assert len(report['sessions'])==2
    for session in report['sessions']:
        assert len(session['variants'])==6
        assert session['variants']['clean']['predicted']==5
        assert all(s['fsm_and_report_unchanged'] for s in session['variants'].values())
    trace=[json.loads(s) for s in (tmp_path/'report/trace.jsonl').read_text().splitlines()]
    assert len(trace)==120 and all('signals' not in r for r in trace)
    assert (tmp_path/'report/comparison.md').exists()
    with pytest.raises(ValueError,match='fresh'):
        run([source],bundle,tmp_path/'report')
