"""One consent, one OFF attempt. Reuses the existing policy/manual executor.

Unified console (intent_control) = demo auto mode (11-06: no per-proposal question during the demo): one consent
stands for the rest of the auth session and every V1.1 proposal runs exactly like its console button (prepare -> one
approved POST -> GET readback): absence -> OFF, sitting 30 s -> ON, persistent BAD on LOW -> AUTO. Still never a
resend, a restore or a POST on stale/unknown data; right before POST, OFF re-checks live absence and ON live presence.
The trial mode (--policy-stage POWER_OFF) keeps one consent = one OFF."""
import copy


class AutomaticOff:
    def __init__(self, host):
        self.host = host
        self.consent = None
        self.busy = False
        self.inflight = False
        self.result = None
        self.absence_seen = False
        self.last_refresh = float('-inf')   # last GET made for an automatic decision
        self.pending_value = None            # value being executed (the presence re-check depends on it)
        self.runs = 0                        # POSTs made by the demo auto mode

    def enabled(self):
        c = self.host.session.ctx
        return (c.get('automatic_off') is True and self.host.policy_link
                and c.get('policy_live_post') is True and (c.get('policy_stage') == 'POWER_OFF'
                    or c.get('combined_manual') is True and c.get('policy_stage') is None)
                and self.host.backend in ('fixture', 'live_manual'))

    def invalid_reason(self):
        """First failing condition as a fixed code (shown to the user), or None when the consent is still valid."""
        h, c = self.host, self.consent
        phase = h.current_policy_phase() or {}
        env = phase.get('envelope') or {}
        checks = (
            ('no_consent', lambda: bool(c)),
            ('not_enabled', self.enabled),
            ('auth_expired', h.active),
            ('hub_phase_stale', h.phase_current),
            ('mqtt_disconnected', lambda: phase.get('mqtt_connected') is True),
            ('viewer_reconnected', lambda: self.standing() or phase.get('mqtt_epoch') == c['epoch']),
            ('hub_rebooted', lambda: self.standing() or env.get('boot_id') == c['boot']),
            ('fsm_session_changed', lambda: self.standing() or h.policy.session == c['session']),
            # Standing mode: leaving the seat ends the FSM session (IDLE, absent_timeout) and that IS the OFF case;
            # each proposal carries its own FSM/presence conditions.
            ('fsm_session_inactive', lambda: self.standing() or h.policy.active_session),
            ('device_changed', lambda: h.session.ctx.get('private_target') == c['target']),
            ('outcome_uncertain', lambda: not h.session.ctx.get('uncertain')),
            ('clock_regressed', lambda: not h.policy.clock_invalid),
            ('manual_override', lambda: h.clock() >= h.policy.manual_until),
            ('person_back', lambda: not self.inflight or self.pending_value not in (None, 'POWER_OFF')
                                    or h.policy.present is False),
            ('person_left', lambda: not self.inflight or self.pending_value != 'POWER_ON'
                                    or h.policy.present is True),
        )
        for code, ok in checks:
            if not ok():
                return code
        return None

    def valid(self):
        return self.invalid_reason() is None

    def standing(self):
        """Demo auto mode: the consent outlives one FSM session / viewer reconnect (unified console only)."""
        return self.host.session.ctx.get('intent_control') is True

    def refresh_source(self):
        # Re-read the trusted local sensor viewer immediately before live POST.
        # No redirects, proxies, Cloud requests or MQTT writes.
        if self.host.backend != 'live_manual':
            return
        import http.client
        import json
        conn = http.client.HTTPConnection('127.0.0.1', 18890, timeout=2)
        try:
            conn.request('GET', '/data')
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError('sensor_unavailable')
            data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('sensor_response_too_large')
            phase = json.loads(data).get('thinq_phase')
            self.host.set_policy_phase(phase)
            self.host.policy.observe(phase, self.host.snapshot.view())
        except Exception:
            # Standing mode keeps the consent: the old phase then fails hub_phase_stale and this POST is blocked.
            if not self.standing():
                self.consent = None
        finally:
            conn.close()

    def view(self):
        return {'enabled': self.enabled(), 'armed': self.consent is not None,
                'inflight': self.inflight, 'one_off_only': not self.standing(), 'runs': self.runs,
                'result': copy.deepcopy(self.result)}

    async def call(self, request):
        action = request.get('action')
        if action == 'disarm':
            self.consent = None
            return self.view()
        if action not in ('arm', 'tick') or not self.enabled():
            raise ValueError('automatic_off_not_enabled')
        if self.busy:
            return self.view()
        self.busy = True
        h = self.host
        try:
            await h.call(h.key, {'op': 'policy', 'phase': request.get('phase')})
            if self.standing():
                return await self._demo_auto(action, request)
            if action == 'arm':
                if self.consent or self.inflight or self.result is not None:
                    raise ValueError('automatic_off_session_already_used')
                if not (request.get('approved') is True
                        and request.get('physical_online_confirmed') is True
                        and request.get('no_external_change') is True):
                    raise ValueError('automatic_off_consent_required')
                # A fresh manual GET and a real present baseline are mandatory.
                view = h.policy.view()
                if (view['reason'] in ('thinq_missing_or_stale', 'manual_override', 'power_unknown')
                        or h.policy._field('power') != 'POWER_ON' or h.policy.present is not True
                        or not view['session_active'] or not h.session.ctx.get('private_target')):
                    raise ValueError('automatic_off_fresh_on_presence_required')
                phase = h.current_policy_phase() or {}
                self.absence_seen = False
                self.consent = {'epoch': phase.get('mqtt_epoch'),
                                'boot': (phase.get('envelope') or {}).get('boot_id'),
                                'session': h.policy.session,
                                'target': h.session.ctx['private_target']}
                if not self.valid():
                    self.consent = None
                    raise ValueError('automatic_off_identity_or_link_unverified')
                return self.view()
            if not self.consent:
                return self.view()
            if h.policy.present is False:
                self.absence_seen = True
            if not self.valid() or self.absence_seen and h.policy.present is True:
                self.consent = None
                self.result = {'status': 'cancelled', 'reason': 'session_or_sensor_changed'}
                return self.view()
            proposal = h.policy.view().get('proposal')
            if not proposal or (proposal['operation'], proposal['value']) != ('set_power', 'off'):
                return self.view()
            # Consume the single automatic trial before any awaited GET/POST.
            self.inflight = True
            self.result = {'status': 'checking', 'post_attempted': False}
            sent_before = len(h.session.ctx['attempted'])   # earlier intents may already include POWER_OFF
            try:
                handoff = await h.call(h.key, {'op': 'policy_respond', 'decision': 'accept',
                    'proposal_id': proposal['proposal_id'], 'phase': request.get('phase')})
                challenge = handoff.get('manual_approval') or {}
                if challenge.get('value') != 'POWER_OFF' or not self.valid():
                    raise ValueError('automatic_off_recheck_failed')
                h.session.ctx['challenge']['automatic_off'] = True
                reply = await h.call(h.key, {'op': 'execute', 'nonce': challenge['nonce'],
                    'approved': True, 'physical_online_confirmed': True, 'no_external_change': True,
                    'phase': request.get('phase')})
                self.result = {k: reply.get(k) for k in
                    ('status', 'reason', 'detail', 'api_accepted', 'state_match', 'physical_confirmed')}
                self.result['post_attempted'] = len(h.session.ctx['attempted']) > sent_before
            except Exception:
                # Never expose SDK exception text, retry, or restore power.
                self.result = {'status': 'stopped',
                    'post_attempted': len(h.session.ctx['attempted']) > sent_before}
            finally:
                self.consent = None
                self.inflight = False
                h.session.ctx.pop('challenge', None)
            return self.view()
        finally:
            self.busy = False

    VALUES = {('set_power', 'off'): 'POWER_OFF', ('set_power', 'on'): 'POWER_ON', ('set_mode', 'auto'): 'AUTO'}

    async def _demo_auto(self, action, request):
        """Unified console: one consent stands for the auth session; every V1.1 proposal runs like its button."""
        h = self.host
        if action == 'arm':
            if self.consent or self.inflight:
                return self.view()
            if not (request.get('approved') is True and request.get('physical_online_confirmed') is True
                    and request.get('no_external_change') is True):
                raise ValueError('automatic_off_consent_required')
            if not h.session.ctx.get('private_target') or not h.policy.view()['session_active']:
                raise ValueError('automatic_off_fresh_on_presence_required')
            phase = h.current_policy_phase() or {}
            self.consent = {'epoch': phase.get('mqtt_epoch'), 'boot': (phase.get('envelope') or {}).get('boot_id'),
                            'session': h.policy.session, 'target': h.session.ctx['private_target']}
            code = self.invalid_reason()
            if code is not None:
                self.consent = None
                raise ValueError('automatic_off_identity_or_link_unverified:' + code)
            self.result = None
            return self.view()
        if not self.consent:
            return self.view()
        # Proposals need a recent ThinQ GET: read when stale, at most every 20 s. GET only.
        if h.policy.view().get('reason') == 'thinq_missing_or_stale' and h.clock() - self.last_refresh >= 20:
            self.last_refresh = h.clock()
            await h.call(h.key, {'op': 'read'})
            await h.call(h.key, {'op': 'policy', 'phase': request.get('phase')})
        code = self.invalid_reason()
        if code in ('auth_expired', 'device_changed', 'not_enabled'):
            self.consent = None
            self.result = {'status': 'cancelled', 'reason': code}
            return self.view()
        if code is not None:
            return self.view()          # wait (stale link, uncertain outcome, manual hold); the consent stands
        proposal = h.policy.view().get('proposal') or {}
        value = self.VALUES.get((proposal.get('operation'), proposal.get('value')))
        if value is None:
            return self.view()
        self.inflight, self.pending_value = True, value
        self.result = {'status': 'checking', 'value': value}
        sent_before = len(h.session.ctx['attempted'])
        try:
            challenge = await h.call(h.key, {'op': 'prepare', 'value': value})
            if challenge.get('status') != 'approval_required':
                self.result = {'status': challenge.get('status'), 'reason': challenge.get('reason'), 'value': value}
                return self.view()
            h.session.ctx['challenge']['automatic_off'] = True
            reply = await h.call(h.key, {'op': 'execute', 'nonce': challenge['nonce'], 'approved': True,
                                         'physical_online_confirmed': True, 'no_external_change': True,
                                         'phase': request.get('phase')})
            self.result = {k: reply.get(k) for k in
                           ('status', 'reason', 'detail', 'api_accepted', 'state_match', 'physical_confirmed')}
            self.result['value'] = value
        except Exception:
            # Never expose SDK exception text, retry, or restore power.
            self.result = {'status': 'stopped', 'value': value}
        finally:
            self.result['post_attempted'] = len(h.session.ctx['attempted']) > sent_before
            if self.result['post_attempted']:
                self.runs += 1
            self.inflight, self.pending_value = False, None
            h.session.ctx.pop('challenge', None)
        return self.view()
