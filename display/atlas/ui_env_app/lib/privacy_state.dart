class PrivacyState {
  const PrivacyState(
      {required this.available,
      required this.policyReady,
      required this.policyVersion,
      required this.consented,
      this.requestId,
      this.status,
      this.error,
      this.hubBootId,
      this.learning,
      this.registeredModelFiles = 0});
  final bool available, policyReady, consented;
  final String policyVersion;
  final String? requestId, status, error, hubBootId;
  final int registeredModelFiles;
  final LearningState? learning;

  static PrivacyState? fromSummary(dynamic summary) {
    final privacy = summary is Map ? summary['privacy'] : null;
    final learning = summary is Map ? summary['ondevice_learning'] : null;
    return privacy is Map<String, dynamic>
        ? PrivacyState.fromMap(privacy,
            learning: learning is Map<String, dynamic> ? learning : null)
        : null;
  }

  factory PrivacyState.fromMap(Map<String, dynamic> data,
          {Map<String, dynamic>? learning}) =>
      PrivacyState(
        available: data['available'] == true,
        policyReady: data['policy_ready'] == true,
        policyVersion: data['policy_version'] is String
            ? data['policy_version'] as String
            : 'pending',
        consented: data['consented'] == true,
        hubBootId: data['hub_boot_id'] as String?,
        requestId: data['request_id'] as String?,
        status: data['status'] as String?,
        error: data['error'] as String?,
        registeredModelFiles:
            (data['registered_model_files'] as num?)?.toInt() ?? 0,
        learning: learning == null ? null : LearningState.fromMap(learning),
      );
}

class LearningState {
  const LearningState(
      {required this.status,
      this.headSource = 'common',
      this.samples = 0,
      this.sessions = 0});
  final String status, headSource;
  final int samples, sessions;

  factory LearningState.fromMap(Map<String, dynamic> data) {
    int count(String key) {
      final value = data[key];
      return value is num && value.isFinite && value >= 0 ? value.toInt() : 0;
    }

    return LearningState(
      status: data['status'] is String ? data['status'] as String : 'unknown',
      headSource: data['head_source'] == 'personal' ? 'personal' : 'common',
      samples: count('samples'),
      sessions: count('sessions'),
    );
  }
}

abstract interface class PrivacySource {
  bool get isConnected;
  PrivacyState? get privacyState;
  Future<PrivacyState> changePrivacy(String action);
}
