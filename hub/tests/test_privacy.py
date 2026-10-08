import io
import json
from pathlib import Path
import pytest
from deskmate_hub.personalization.privacy import PersonalizationPrivacy
from deskmate_hub.ingest import SensorCache, load_ingest_config
from deskmate_hub.ingest.mqtt_lines import route_mqtt_message
from deskmate_hub.live import LiveHub


def config(tmp_path, approved=True):
    return {'enabled':True, 'policy_approved':approved, 'policy_version':'test-v1' if approved else 'pending',
            'data_root':str(tmp_path), 'consent_file':str(tmp_path/'consent.json'),
            'personal_model_files':[str(tmp_path/'personal.tflite'), str(tmp_path/'personal.metadata.json')]}


def command(action, rid='one', version='test-v1'):
    return {'kind':'personalization', 'request_id':rid, 'action':action,
            'policy_version':version, 'confirmed':True}


def test_policy_pending_and_version_mismatch_cannot_grant(tmp_path):
    for cfg, request in ((config(tmp_path, False), command('grant')), (config(tmp_path), command('grant', version='stale'))):
        privacy = PersonalizationPrivacy(cfg)
        privacy.handle(request)
        assert not privacy.consented and privacy.snapshot()['error'] == 'policy_not_approved'
        assert not (tmp_path/'consent.json').exists()


def test_grant_survives_restart_and_revoke_deletes_only_registered_files(tmp_path):
    cfg = config(tmp_path)
    baseline = tmp_path/'baseline.json'
    paths = [baseline, *(Path(p) for p in cfg['personal_model_files'])]
    for path in paths:
        path.write_text('personal data')
    common = tmp_path/'common.tflite'
    common.write_text('shared model')
    esm = tmp_path/'esm.jsonl'
    esm.write_text('test label')
    resets = []
    privacy = PersonalizationPrivacy(cfg, baseline_file=str(baseline), on_reset=lambda: resets.append(True))
    privacy.handle(command('grant'))
    assert privacy.consented and privacy.snapshot()['status'] == 'succeeded'
    assert PersonalizationPrivacy(cfg).consented
    privacy.handle(command('revoke', 'two'))
    assert resets == [True] and not privacy.consented
    assert all(not path.exists() for path in paths)
    assert common.read_text() == 'shared model' and esm.exists()
    assert not PersonalizationPrivacy(cfg).consented
    privacy.handle(command('revoke', 'two'))
    assert resets == [True]
    privacy.handle(command('delete', 'three'))
    assert privacy.snapshot()['status'] == 'succeeded'


@pytest.mark.parametrize('fault', ['path_payload','no_confirmation','unknown_action','bad_id'])
def test_invalid_remote_request_cannot_mutate_disk(tmp_path, fault):
    privacy = PersonalizationPrivacy(config(tmp_path))
    request = command('delete')
    if fault == 'path_payload': request['path'] = str(tmp_path/'victim')
    elif fault == 'no_confirmation': request['confirmed'] = False
    elif fault == 'unknown_action': request['action'] = 'reset-all'
    else: request['request_id'] = 1
    privacy.handle(request)
    assert not (tmp_path/'consent.json').exists() and privacy.last == {}


def test_partial_delete_reports_failure_but_revocation_is_persisted(tmp_path, monkeypatch):
    import deskmate_hub.personalization.privacy as module
    cfg = config(tmp_path)
    model = Path(cfg['personal_model_files'][0])
    model.write_text('model')
    privacy = PersonalizationPrivacy(cfg)
    privacy.handle(command('grant'))
    original = module.os.unlink
    def refuse(path):
        if str(path) == str(model): raise PermissionError('busy file')
        return original(path)
    monkeypatch.setattr(module.os, 'unlink', refuse)
    privacy.handle(command('delete', 'two'))
    assert not privacy.consented and privacy.snapshot()['status'] == 'failed'
    assert model.exists() and not PersonalizationPrivacy(cfg).consented


def test_outside_root_registration_is_refused(tmp_path):
    cfg = config(tmp_path)
    cfg['personal_model_files'] = [str(tmp_path.parent/'shared-model')]
    privacy = PersonalizationPrivacy(cfg)
    privacy.handle(command('delete'))
    assert not privacy.snapshot()['available']
    assert privacy.snapshot()['error'] == 'unavailable'


def test_policy_revision_requires_new_consent(tmp_path):
    cfg = config(tmp_path)
    privacy = PersonalizationPrivacy(cfg)
    privacy.handle(command('grant'))
    assert not PersonalizationPrivacy(dict(cfg, policy_version='test-v2')).consented


def test_corrupt_consent_is_not_granted(tmp_path):
    (tmp_path/'consent.json').write_text('broken')
    assert not PersonalizationPrivacy(config(tmp_path)).consented


def test_mqtt_consent_and_delete_reset_hub_without_recording_esm(tmp_path):
    cfg = config(tmp_path)
    ingest = load_ingest_config()
    baseline_path = tmp_path/'baseline.json'
    ingest['baseline']['persist'] = {'enabled':True, 'path':str(baseline_path)}
    hub = LiveHub(SensorCache(), privacy_cfg=cfg, ingest_cfg=ingest,
                  personalization_cfg={'enabled':False}, control_cfg={'enabled':False}, out=io.StringIO())
    assert hub.tracker.baseline.persist_path is None
    route_mqtt_message(hub.cache, 'deskmate/feedback/user', json.dumps({'data':dict(command('grant'), hub_boot_id=hub.boot_id)}).encode(), 100)
    hub.tick_once(100)
    assert hub.privacy.consented and hub.tracker.baseline.persist_path == str(baseline_path)
    hub.tracker.baseline.begin_calibration()
    for _ in range(25): hub.tracker.baseline.observe('idle_ratio', .2)
    hub.tracker.baseline.end_calibration(100)
    assert baseline_path.exists()
    route_mqtt_message(hub.cache, 'deskmate/feedback/user', json.dumps(dict(command('delete','two'), hub_boot_id=hub.boot_id)).encode(), 110)
    state = hub.tick_once(110)
    assert not baseline_path.exists() and not hub.tracker.baseline.snapshot()['buckets']
    assert hub.tracker.baseline.persist_path is None and hub.personalization.backend is None
    assert state['data']['sensor_summary']['privacy']['status'] == 'succeeded'
    assert hub.recorder.r.esm_labels == []


def test_symlink_appearing_after_initialization_is_not_followed(tmp_path):
    cfg = config(tmp_path)
    privacy = PersonalizationPrivacy(cfg)
    outside = tmp_path.parent/(tmp_path.name+'-outside')
    outside.write_text('must remain')
    model = Path(cfg['personal_model_files'][0])
    try:
        model.symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation unavailable')
    privacy.handle(command('delete'))
    assert outside.read_text() == 'must remain' and model.is_symlink()
    assert privacy.snapshot()['status'] == 'failed'


def test_replayed_old_revoke_cannot_undo_a_new_grant(tmp_path):
    privacy = PersonalizationPrivacy(config(tmp_path))
    privacy.handle(command('revoke', 'first'))
    privacy.handle(command('grant', 'second'))
    privacy.handle(command('revoke', 'first'))
    assert privacy.consented and privacy.snapshot()['error'] == 'stale_request'


def test_request_bound_to_previous_hub_run_is_refused(tmp_path):
    privacy = PersonalizationPrivacy(config(tmp_path))
    privacy.boot_id = 'new-run'
    privacy.handle(dict(command('grant'), hub_boot_id='old-run'))
    assert not privacy.consented and privacy.snapshot()['error'] == 'stale_request'


def test_model_use_is_gated_and_revoke_drops_window_without_deleting_common_model(tmp_path):
    from deskmate_hub.personalization import load_personalization_config
    from deskmate_hub.personalization.contract import model_metadata
    cfg = config(tmp_path)
    common_model = tmp_path / 'common.tflite'
    common_model.write_bytes(b'model fixture')
    metadata = tmp_path / 'common.metadata.json'
    metadata.write_text(json.dumps(model_metadata(6, 10, 'baseline')))
    personalization = dict(load_personalization_config(), enabled=True,
                           model_path=str(common_model), metadata_path=str(metadata))
    loaded = []
    class Backend:
        def __init__(self, *_): loaded.append(True)
    ingest = load_ingest_config()
    ingest['baseline']['persist'] = {'enabled':False, 'path':str(tmp_path / 'baseline.json')}
    hub = LiveHub(SensorCache(), privacy_cfg=cfg, ingest_cfg=ingest, personalization_cfg=personalization,
                  personalization_backend_factory=Backend, control_cfg={'enabled':False}, out=io.StringIO())
    assert loaded == [] and hub.personalization.backend is None
    hub.cache.put_feedback(dict(command('grant'), hub_boot_id=hub.boot_id))
    hub.tick_once(100)
    assert loaded == [True] and hub.personalization.backend is not None
    hub.personalization.rows.append([0] * 17)
    hub.cache.put_feedback(dict(command('revoke','two'), hub_boot_id=hub.boot_id))
    hub.tick_once(110)
    assert hub.personalization.backend is None and not hub.personalization.rows
    assert common_model.read_bytes() == b'model fixture' and metadata.exists()


@pytest.mark.parametrize('patch', [{'recent_request_limit':0}, {'personal_model_files':1}, {'data_root':''}])
def test_bad_local_configuration_is_unavailable_not_a_hub_exception(tmp_path, patch):
    privacy = PersonalizationPrivacy(dict(config(tmp_path), **patch))
    privacy.handle(command('grant'))
    assert not privacy.snapshot()['available'] and not privacy.consented


def test_consent_write_failure_disables_memory_and_is_not_success(tmp_path, monkeypatch):
    privacy = PersonalizationPrivacy(config(tmp_path))
    resets = []
    privacy.on_reset = lambda: resets.append(True)
    def fail(): raise PermissionError('read-only')
    monkeypatch.setattr(privacy, '_save', fail)
    privacy.handle(command('grant'))
    assert not privacy.consented and resets == [True]
    assert privacy.snapshot()['status'] == 'failed'
