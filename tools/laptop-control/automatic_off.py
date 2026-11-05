"""One consent, one OFF attempt. Reuses the existing policy/manual executor."""
import copy


class AutomaticOff:
    def __init__(self, host):
        self.host = host
        self.consent = None
        self.busy = False
        self.inflight = False
        self.result = None
        self.absence_seen = False

    def enabled(self):
        c = self.host.session.ctx
        return (c.get('automatic_off') is True and self.host.policy_link
                and c.get('policy_live_post') is True and (c.get('policy_stage') == 'POWER_OFF'
                    or c.get('combined_manual') is True and c.get('policy_stage') is None)
                and self.host.backend in ('fixture', 'live_manual'))

    def valid(self):
        h, c = self.host, self.consent
        phase = h.current_policy_phase() or {}
        env = phase.get('envelope') or {}
        return bool(c and self.enabled() and h.active() and h.phase_current()
                    and phase.get('mqtt_connected') is True
                    and phase.get('mqtt_epoch') == c['epoch'] and env.get('boot_id') == c['boot']
                    and h.policy.session == c['session'] and h.policy.active_session
                    and h.session.ctx.get('private_target') == c['target']
                    and not h.session.ctx.get('uncertain') and not h.policy.clock_invalid
                    and h.clock() >= h.policy.manual_until
                    and (not self.inflight or h.policy.present is False))

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
            self.consent = None
        finally:
            conn.close()

    def view(self):
        return {'enabled': self.enabled(), 'armed': self.consent is not None,
                'inflight': self.inflight, 'one_off_only': True,
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
            if action == 'arm':
                if self.consent or self.result is not None:
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
                    ('status', 'api_accepted', 'state_match', 'physical_confirmed')}
                self.result['post_attempted'] = 'POWER_OFF' in h.session.ctx['attempted']
            except Exception:
                # Never expose SDK exception text, retry, or restore power.
                self.result = {'status': 'stopped',
                    'post_attempted': 'POWER_OFF' in h.session.ctx['attempted']}
            finally:
                self.consent = None
                self.inflight = False
                h.session.ctx.pop('challenge', None)
            return self.view()
        finally:
            self.busy = False
