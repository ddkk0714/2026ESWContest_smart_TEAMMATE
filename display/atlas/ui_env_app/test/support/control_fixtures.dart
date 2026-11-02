// Synthetic control contracts only; no real device identities.
Map<String, dynamic> controlPlan(
        {String target = 'mock_ac',
        String status = 'accepted',
        bool adviceOnly = false,
        String mode = 'mock'}) =>
    {
      'active': true,
      'execution_mode': mode,
      'state': 'ACTION_ENV',
      'cause': 'environment',
      'gate': 'suggest',
      'awaiting_user': true,
      'outcome': 'pending',
      'commands': [
        if (!adviceOnly)
          <String, dynamic>{
            'command_id': 'mock-command-1',
            'target': target,
            'op': 'set_power',
            'value': 'on',
            'reason': 'temperature_high',
            'status': status,
            'state_confirmed': status == 'succeeded',
          }
      ],
      'advice': [
        if (adviceOnly)
          {
            'kind': 'manual_ventilation',
            'rule': 'co2_high',
            'sensor': 'co2_ppm',
            'role': 'outside_air',
            'reason': 'no_eligible_device',
          }
      ],
    };
Map<String, dynamic> controlEnvelope(Map<String, dynamic> plan,
        {DateTime? now}) =>
    {
      'schema_version': '1.0',
      'boot_id': 'mock-hub',
      'seq': 12,
      'ts': (now ?? DateTime.now()).millisecondsSinceEpoch / 1000,
      'data': {
        'fsm_state': 'ACTION_ENV',
        'phase': 'fatigue',
        'context': 'npc',
        'cause': 'environment',
        'gate': 'suggest',
        'sensor_summary': {
          'control': plan,
          'co2_ppm': 2000,
          'temp_c': 32,
          'env_flags': ['co2_high', 'too_hot'],
        }
      },
    };
Map<String, dynamic> requestEnvelope(Map<String, dynamic> plan,
        {String id = 'mock-request-1', DateTime? now}) =>
    {
      'schema_version': '1.0',
      'boot_id': 'mock-hub',
      'ts': (now ?? DateTime.now()).millisecondsSinceEpoch / 1000,
      'data': {
        'request_id': id,
        'kind': 'env_suggest',
        'expires_in_s': 60,
        'control_plan': plan
      },
    };
