import json

from deskmate_hub.ingest import SensorCache, SessionTracker, build_frame, load_ingest_config, sensor_summary
from deskmate_hub.ingest.protocol import parse_sensor_message
from deskmate_hub.inference import State


def test_user_disabled_sample_remains_explicit_and_unavailable_to_keyboard_inference():
    data = {'node':'pc-collector','window_s':60,'valid':False,
            'keyboard_capture_enabled':False,'capture_status':'user_disabled',
            'mouse_active':True,'mouse_event_rate':5.0,'input_active':True}
    kind, sample = parse_sensor_message('deskmate/sensor/keystroke',
        json.dumps({'schema_version':'1.0','ts':100,'seq':1,'data':data}).encode(),100)
    cache=SensorCache();cache.put(kind,sample)
    cfg=load_ingest_config()
    frame=build_frame(cache.snapshot(),100,cfg,SessionTracker(),fsm_state=State.FOCUS_NPC)
    assert frame.signals['keystroke'].available is False
    summary=sensor_summary(cache.snapshot(),100,cfg)['keystroke']
    assert summary['capture_status']=='user_disabled'
    assert summary['keyboard_capture_enabled'] is False
    assert summary['mouse_active'] is True
    assert 'typing_active' not in summary and 'idle_ratio' not in summary
