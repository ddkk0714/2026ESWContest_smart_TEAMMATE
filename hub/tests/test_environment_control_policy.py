"""Synthetic fixtures only: these tests do not claim hardware calibration."""
import copy
import io
from dataclasses import replace

import pytest

from deskmate_hub.control import ControlDispatcher, load_control_config
from deskmate_hub.control.devices import Capability, Device, DeviceRegistry
from deskmate_hub.control.environment_policy import EnvironmentControlPolicy, load_environment_control_config
from deskmate_hub.inference import GateMode, State
from deskmate_hub.ingest import Sample, SensorCache
from deskmate_hub.live import LiveHub


def config(*names, automatic=True):
    cfg = load_environment_control_config()
    cfg.update(enabled=True, automatic_control=automatic)
    cfg['rules'] = {k: v for k, v in cfg['rules'].items() if k in (names or ('co2_high',))}
    for rule in cfg['rules'].values():
        rule.update(minimum_duration_sec=10, cooldown_sec=20)
    return cfg


def device(registry, role='outside_air', target='mock_vent', *, now=1000, state='off'):
    d = Device(target, 'mock', (role,), {'set_power': Capability('set_power', ('on', 'off'), verified=True)},
               online=True, state={'set_power': state}, observed_at=now)
    registry.register(d)
    return d


def sample(now, value=1200, **extra):
    return Sample(now, now, None, {'co2_ppm': value, 'co2_valid': True, **extra})


def ready(cfg=None):
    registry = DeviceRegistry()
    d = device(registry)
    p = EnvironmentControlPolicy(cfg or config(), registry)
    p.observe(sample(1000), 1000)
    p.observe(sample(1010), 1010)
    return p, registry, d


def dispatch(p, sent):
    cfg = load_control_config()
    cfg['cooldown_sec'] = 0  # Isolate per-rule cooldown in policy tests.
    return ControlDispatcher(cfg, publish_cmd=sent.append, plan_commands=p.plan,
                             dispatch_guard=p.can_dispatch, on_dispatched=p.dispatched)


def test_defaults_disabled_and_mock_only():
    cfg = load_environment_control_config()
    assert not cfg['enabled'] and not cfg['automatic_control']
    p = EnvironmentControlPolicy(cfg, DeviceRegistry())
    p.observe(sample(1000), 1000)
    assert p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 2000) == []
    with pytest.raises(ValueError, match='mock mode'):
        EnvironmentControlPolicy(dict(cfg, enabled=True, mode='live'), DeviceRegistry())


def test_duration_hysteresis_and_rule_cooldown():
    r = DeviceRegistry(); device(r)
    p = EnvironmentControlPolicy(config(), r)
    p.observe(sample(1000), 1000)
    assert p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1009) == []
    p.observe(sample(1010, 950), 1010)  # Inside the hysteresis band, still active.
    sent = []; ctrl = dispatch(p, sent)
    ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    assert len(sent) == 1 and 'context' not in sent[0]
    p.observe(sample(1020), 1020)
    assert p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1020) == []
    r.devices['mock_vent'].observed_at = 1030
    p.observe(sample(1030), 1030)
    assert len(p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1030)) == 1
    p.observe(sample(1031, 900), 1031)
    assert p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1031) == []


@pytest.mark.parametrize('value,extra', [(None, {}), (float('nan'), {}), (float('inf'), {}),
    (True, {}), (-1, {}), (1200, {'co2_valid': False}), (1200, {'co2_valid': None})])
def test_bad_or_invalid_sensor_resets_evidence(value, extra):
    p, _, _ = ready()
    p.observe(sample(1011, value, **extra), 1011)
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1011)


def test_stale_missing_future_gap_and_clock_rewind_reset_evidence():
    p, _, _ = ready()
    for s, now in ((sample(1000), 1020), (None, 1020), (sample(1030), 1020),
                   (sample(1040), 1040), (sample(1000), 1000)):
        p.observe(s, now)
        assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, now)


def test_aggregate_valid_does_not_replace_per_sensor_flag():
    p, _, _ = ready()
    p.observe(Sample(1011, 1011, 1, {'co2_ppm': 1200, 'valid': True}), 1011)
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1011)


@pytest.mark.parametrize('change', ['offline', 'stale', 'manual', 'unverified', 'already_on', 'wrong_role', 'live'])
def test_device_selection_is_conservative(change):
    p, _, d = ready()
    if change == 'offline': d.online = False
    elif change == 'stale': d.observed_at = 900
    elif change == 'manual': d.last_manual_at = 1005
    elif change == 'unverified': d.capabilities['set_power'] = Capability('set_power', ('on', 'off'))
    elif change == 'already_on': d.state['set_power'] = 'on'
    elif change == 'wrong_role': d.roles = ('air_cleaning',)
    else: d.mode = 'live'
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)


def test_co2_never_selects_air_purifier_or_unregistered_device():
    p, r, _ = ready(); r.devices.clear()
    device(r, role='air_cleaning', target='mock_purifier')
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    assert p.recommendations == ['co2_high']
    cfg = config(); cfg['rules']['co2_high']['role'] = 'air_cleaning'
    with pytest.raises(ValueError): EnvironmentControlPolicy(cfg, r)
    cfg['rules']['co2_high']['role'] = 'cooling'
    with pytest.raises(ValueError): EnvironmentControlPolicy(cfg, r)


def test_policy_gate_never_upgrades_none_or_auto_permission():
    p, _, _ = ready(config(automatic=False))
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    assert not p.plan('ACTION_ENV', 'environment', GateMode.NONE, 1010)
    assert p.plan('ACTION_ENV', 'environment', GateMode.SUGGEST, 1010)
    assert not p.plan('ACTION_POSTURE', 'environment', GateMode.SUGGEST, 1010)


def test_accept_reject_expire_and_sensor_recheck_use_existing_dispatcher():
    for verdict in ('accept', 'reject', 'expire', 'stale'):
        p, r, d = ready(); sent = []; ctrl = dispatch(p, sent)
        ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.SUGGEST, 1010)
        assert ep.awaiting_user and sent == []
        if verdict == 'expire': ctrl.tick(1200)
        elif verdict == 'stale':
            p.observe(None, 1011); ctrl.on_feedback('accept', 1011)
        else: ctrl.on_feedback(verdict, 1011)
        assert len(sent) == (1 if verdict == 'accept' else 0)
        if verdict == 'expire': assert ep.outcome == 'expired'
        if verdict == 'reject': assert ep.outcome == 'rejected'
        assert all(c.target_id != 'desk_lamp' and c.target_id != 'vent_fan' for c in ep.commands)


def test_no_legacy_fan_plus_lamp_fallback_even_with_empty_policy_plan():
    p, r, _ = ready(); r.devices.clear()
    sent = []; ctrl = dispatch(p, sent)
    ep = ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    assert ep.done and ep.outcome == 'skipped' and not sent


def test_undo_captures_observed_previous_value_and_respects_manual_change():
    for manual in (False, True):
        p, r, d = ready(); sent = []; ctrl = dispatch(p, sent)
        ctrl.on_enter_action('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
        r.observe(d.target_id, {'set_power': 'on'}, 1010, expected={'set_power': 'on'})
        ctrl.on_result({'command_id': sent[0]['command_id'], 'status': 'succeeded', 'actual_value': 'on'}, 1010)
        if manual: r.mark_manual(d.target_id, 1011)
        ctrl.on_feedback('reject', 1011)
        assert len(sent) == (1 if manual else 2)
        if not manual: assert sent[1]['gate'] == 'undo' and sent[1]['value'] == 'off'


def test_humidifier_dehumidifier_exclusion_and_unknown_opposing_state():
    cfg = config('humidity_low', 'humidity_high')
    r = DeviceRegistry(); humid = device(r, 'humidifying', 'mock_humid')
    dehumid = device(r, 'dehumidifying', 'mock_dehumid', state='on')
    p = EnvironmentControlPolicy(cfg, r)
    for now in (1000, 1010):
        p.observe(Sample(now, now, None, {'humidity_pct': 20, 'humidity_valid': True}), now)
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    dehumid.state['set_power'] = 'off'
    assert p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)[0]['target_id'] == humid.target_id
    dehumid.observed_at = None
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)


def test_livehub_uses_one_request_and_keeps_real_publisher_unused(tmp_path):
    cfg = config(); cfg['rules']['co2_high']['minimum_duration_sec'] = 0
    r = DeviceRegistry(); d = device(r)
    cache = SensorCache(); cache.put('env', sample(1000))
    published, requests = [], []
    control = load_control_config(); control['esm_log_path'] = str(tmp_path/'esm.jsonl')
    hub = LiveHub(cache, control_cfg=control, environment_control_cfg=cfg, device_registry=r,
                  publish_control=published.append, publish_request=requests.append, out=io.StringIO())
    class ControlledEngine:
        state = State.CAUSE_ANALYSIS
        def tick(self, frame):
            result = hub_delegate.tick(frame)
            self.state = State.ACTION_ENV
            return replace(result, state=State.ACTION_ENV, cause='environment', gate=GateMode.SUGGEST)
    hub_delegate = hub.engine; hub.engine = ControlledEngine()
    envelope = hub.tick_once(1000)
    assert envelope['data']['sensor_summary']['control']['execution_mode'] == 'mock'
    assert hub.control.episode.awaiting_user and len(requests) == 1 and not published
    request_id = requests[0]['data']['request_id']
    cache.put_feedback({'request_id': 'wrong-card', 'verdict': 'accept'})
    hub.tick_once(1001)
    assert hub.control.episode.awaiting_user
    cache.put_feedback({'request_id': request_id, 'verdict': 'accept'})
    hub.tick_once(1002)
    assert hub.control.episode.executed and d.state['set_power'] == 'on' and not published
    assert len(requests) == 1


def test_old_source_timestamp_cannot_be_freshened_by_network_receive_time():
    p, _, _ = ready()
    p.observe(Sample(1011, 900, 1, {'co2_ppm': 1200, 'co2_valid': True}), 1011)
    assert not p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1011)


def test_sustained_bad_humidity_rules_cannot_plan_both_directions():
    cfg = config('humidity_low', 'humidity_high')
    # Synthetic overlapping config tests arbitration, not a recommended policy.
    cfg['rules']['humidity_low']['threshold'] = 70
    cfg['rules']['humidity_high']['threshold'] = 30
    r = DeviceRegistry(); device(r, 'humidifying', 'mock_humid'); device(r, 'dehumidifying', 'mock_dehumid')
    p = EnvironmentControlPolicy(cfg, r)
    for now in (1000, 1010):
        p.observe(Sample(now, now, None, {'humidity_pct': 50, 'humidity_valid': True}), now)
    assert len(p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)) == 1


@pytest.mark.parametrize('name,field,flag,value,role,op,target_value,previous', [
    ('temperature_high', 'temp_c', 'temp_valid', 32, 'cooling', 'set_power', 'on', 'off'),
    ('humidity_high', 'humidity_pct', 'humidity_valid', 80, 'dehumidifying', 'set_power', 'on', 'off'),
    ('lux_low', 'lux', 'lux_valid', 100, 'lighting', 'set_brightness', 70, 25),
])
def test_rule_selects_only_matching_capability(name, field, flag, value, role, op, target_value, previous):
    cfg = config(name); r = DeviceRegistry()
    cap = Capability(op, values=('on', 'off'), verified=True) if op == 'set_power' else Capability(op, minimum=0, maximum=255, verified=True)
    r.register(Device('mock_target', 'mock', (role,), {op: cap}, online=True, state={op: previous}, observed_at=1000))
    p = EnvironmentControlPolicy(cfg, r)
    for now in (1000, 1010): p.observe(Sample(now, now, None, {field: value, flag: True}), now)
    specs = p.plan('ACTION_ENV', 'environment', GateMode.AUTO, 1010)
    assert len(specs) == 1 and specs[0]['value'] == target_value and specs[0]['undo_value'] == previous
