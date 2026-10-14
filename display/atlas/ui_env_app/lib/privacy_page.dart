import 'package:flutter/material.dart';
import 'privacy_state.dart';

class PrivacyPage extends StatefulWidget {
  const PrivacyPage({super.key, required this.source});
  final PrivacySource? source;
  @override
  State<PrivacyPage> createState() => _PrivacyPageState();
}

class _PrivacyPageState extends State<PrivacyPage> {
  bool _busy = false;
  String? _message;
  Future<void> _run(String action) async {
    final source = widget.source;
    if (source == null || _busy) return;
    final title = action == 'grant' ? '개인화 사용 동의' : '개인화 동의 취소 및 삭제';
    final confirmed = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: Text(title),
            content: Text(action == 'grant'
                ? '기기에 설정된 개인 기준선 저장과 개인화 모델 사용에 동의할까요? 학습 기능이 준비된 기기는 작업 특징과 직접 정정한 상태를 기기 안에서 모아, 충분한 기록이 쌓이면 쉬는 동안 개인 모델을 학습합니다. 개발 PC로 옮기는 과정은 필요하지 않습니다.'
                : '개인화 사용을 중지하고 메모리 기준선·모델을 초기화합니다. 등록된 로컬 기준선·개인 모델 파일을 삭제합니다. 공통 모델과 ESM·실험 로그, PC 학습 데이터는 별도 대상입니다.'),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(context, false),
                  child: const Text('취소')),
              FilledButton(
                  onPressed: () => Navigator.pop(context, true),
                  child: Text(action == 'grant' ? '동의' : '취소하고 삭제')),
            ],
          ),
        ) ??
        false;
    if (!confirmed || !mounted) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final result = await source.changePrivacy(action);
      if (!mounted) return;
      setState(() {
        _message = result.status == 'succeeded'
            ? (action == 'grant'
                ? '동의 상태를 저장했습니다.'
                : '등록된 개인화 데이터를 삭제하고 사용을 중지했습니다.')
            : '처리를 완료하지 못했습니다. 기기 설정과 저장소 권한을 확인해 주세요.';
      });
    } catch (_) {
      if (mounted) {
        setState(() => _message = '기기의 처리 확인을 받지 못했습니다. 연결을 확인한 뒤 다시 시도해 주세요.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final source = widget.source;
    final state = source?.privacyState;
    final available =
        !_busy && source?.isConnected == true && state?.available == true;
    return SingleChildScrollView(
        child: Center(
            child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 680),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('개인화 관리',
            style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
        const SizedBox(height: 16),
        Text(state?.consented == true
            ? '개인화 사용에 동의한 상태입니다.'
            : '개인화 사용 동의가 없습니다.'),
        const SizedBox(height: 12),
        if (state?.policyReady != true)
          const Text('동의 정책이 아직 승인되지 않았습니다. 지금은 동의를 활성화할 수 없습니다.'),
        if (state?.available != true)
          const Text('기기에 개인화 관리가 준비되지 않았거나 연결 상태를 확인 중입니다.'),
        const SizedBox(height: 12),
        const Text('저장은 기기에 설정된 범위에서만 이루어집니다. 키 내용·영상·음성은 수집하지 않습니다.'),
        const SizedBox(height: 12),
        _LearningCard(
            connected: source?.isConnected == true,
            consented: state?.consented == true,
            learning: state?.learning),
        const SizedBox(height: 12),
        Text('등록된 개인 모델 파일: ${state?.registeredModelFiles ?? 0}개'),
        const Text(
            '삭제 범위: 로컬 기준선과 등록된 개인 모델. ESM·실험 로그와 PC 학습 자료는 별도 관리 대상입니다.'),
        const SizedBox(height: 20),
        Wrap(spacing: 12, runSpacing: 12, children: [
          FilledButton(
              key: const ValueKey('privacy-grant'),
              onPressed: available &&
                      state?.policyReady == true &&
                      state?.consented != true
                  ? () => _run('grant')
                  : null,
              child: const Text('개인화 사용에 동의')),
          OutlinedButton(
              key: const ValueKey('privacy-revoke'),
              onPressed: available ? () => _run('revoke') : null,
              child: const Text('동의 취소 및 삭제')),
          OutlinedButton(
              key: const ValueKey('privacy-delete'),
              onPressed: available ? () => _run('delete') : null,
              child: const Text('개인화 데이터 초기화')),
        ]),
        if (_busy)
          const Padding(
              padding: EdgeInsets.only(top: 16),
              child: LinearProgressIndicator()),
        if (_message != null)
          Padding(
              padding: const EdgeInsets.only(top: 16), child: Text(_message!)),
      ]),
    )));
  }
}

class _LearningCard extends StatelessWidget {
  const _LearningCard(
      {required this.connected, required this.consented, this.learning});
  final bool connected, consented;
  final LearningState? learning;

  @override
  Widget build(BuildContext context) {
    final state = learning;
    final (title, detail) = !connected
        ? ('학습 상태 확인 대기', '기기 연결이 끊겼습니다. 다시 연결되면 최신 상태를 확인합니다.')
        : !consented
            ? ('개인 학습 사용 중지', '동의 후 준비된 기기에서만 개인 학습을 사용할 수 있습니다.')
            : switch (state?.status) {
                'collecting' => (
                    '정정 기록 수집 중',
                    '직접 정정한 기록이 충분히 모이면 쉬는 동안 학습합니다. 학습에 필요한 상태별 기록이 부족하면 기존 모델을 유지합니다.'
                  ),
                'training' => (
                    '기기에서 개인 모델 학습 중',
                    '사용 중인 모델은 유지하며 새 모델을 준비합니다.'
                  ),
                'paused' => (
                    '개인 학습 잠시 멈춤',
                    '작업을 다시 시작해 학습을 멈췄습니다. 쉬는 동안 이어서 진행합니다.'
                  ),
                'accepted' => (
                    '개인 모델 준비 완료',
                    '성능 비교를 통과한 모델을 기기에 저장했습니다. 현재는 예측을 비교하는 단계입니다.'
                  ),
                'rejected' => (
                    '기존 모델 유지',
                    '새 후보의 개선을 확인하지 못해 기존 모델을 계속 사용합니다.'
                  ),
                'storage_error' => (
                    '개인 학습 처리 실패',
                    '학습 또는 저장을 완료하지 못했습니다. 기존 모델을 유지합니다. 기기 저장소와 설정을 확인해 주세요.'
                  ),
                'head_load_failed' => (
                    '개인 모델 복원 실패',
                    '저장된 개인 모델을 사용할 수 없어 공통 모델로 동작합니다. 기기 설정을 확인해 주세요.'
                  ),
                'awaiting_consent' => ('개인 학습 사용 중지', '기기의 동의 상태를 확인해 주세요.'),
                'disabled' || 'unavailable' || 'awaiting_portable_model' => (
                    '개인 학습 준비 필요',
                    '기기에 학습 기능이나 공통 모델이 준비되지 않았습니다. 기존 방식으로 동작합니다.'
                  ),
                _ => ('학습 상태 확인 중', '기기에서 개인 학습 상태를 보내면 여기에 표시합니다.'),
              };
    final showCounts = connected && consented && state != null;
    final showModel = showCounts &&
        ![
          'disabled',
          'unavailable',
          'awaiting_consent',
          'awaiting_portable_model',
          'unknown'
        ].contains(state.status);
    return Card(
      key: const ValueKey('personal-learning-card'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title,
              key: const ValueKey('personal-learning-status'),
              style:
                  const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text(detail),
          if (showCounts) ...[
            const SizedBox(height: 8),
            Text('정정 기록 ${state.samples}개 · 세션 ${state.sessions}개'),
          ],
          if (showModel)
            Text(state.headSource == 'personal'
                ? '비교 중인 모델: 저장된 개인 모델'
                : '비교 중인 모델: 공통 모델'),
          const SizedBox(height: 8),
          const Text('현재 개인 모델은 예측 비교용이며, 실제 상태 판단과 제어는 기존 방식으로 유지합니다.'),
        ]),
      ),
    );
  }
}
