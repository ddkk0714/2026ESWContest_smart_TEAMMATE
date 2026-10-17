/// ToF 자세 화면. `pc/posture_view.html` 을 Flutter 로 옮긴 것이다.
///
/// 왼쪽: 큰 글씨 자세 · 확신 · 지속 시간 · 최근 36 초 이력 띠 · 센서 2개 상태 · 모델 요약.
/// 오른쪽: 9 라벨 순간 확률 막대(`instant.probs`). 0.3 초마다 `/api/state` 를 읽는다.
///
/// 이 위젯은 앱 골격(main)과 떼어 두었다. 통합 앱(ui_env_app)에 탭으로 넣을 때 그대로
/// 가져다 쓸 수 있게 — 소스·주소 저장소를 밖에서 넣는다.
library;

import 'dart:async';

import 'package:flutter/material.dart';

import 'address_store.dart';
import 'labels.dart';
import 'tof_client.dart';
import 'tof_state.dart';

typedef TofSourceFactory = TofSource Function(String baseUrl);

class TofPostureView extends StatefulWidget {
  const TofPostureView({
    super.key,
    required this.initialBaseUrl,
    this.sourceFactory = HttpTofSource.new,
    this.store,
    this.pollInterval = const Duration(milliseconds: 300),
    this.historyLength = 120,
    this.presets = kAddressPresets,
  });

  /// 처음 붙을 주소. 저장된 값 → 빌드 define → 기본값 순으로 main 이 정한다.
  final String initialBaseUrl;
  final TofSourceFactory sourceFactory;

  /// 주소를 바꿨을 때 남길 곳. null 이면 이번 실행에만 적용된다.
  final AddressStore? store;
  final Duration pollInterval;
  final int historyLength;
  final List<AddressPreset> presets;

  @override
  State<TofPostureView> createState() => _TofPostureViewState();
}

class _TofPostureViewState extends State<TofPostureView> {
  late String _baseUrl;
  late TofSource _source;
  Timer? _timer;
  bool _inFlight = false;
  TofPostureState? _state;
  int _fails = 0;
  String? _lastError;
  DateTime? _lastOkAt;
  final List<String> _history = [];
  String? _savedNote;

  @override
  void initState() {
    super.initState();
    _baseUrl = widget.initialBaseUrl;
    _source = widget.sourceFactory(_baseUrl);
    _timer = Timer.periodic(widget.pollInterval, (_) => _tick());
    _tick();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _source.close();
    super.dispose();
  }

  Future<void> _tick() async {
    // 응답이 폴링 주기보다 늦으면 요청을 겹치지 않는다(보드 TCP 소켓을 안 쌓이게).
    if (_inFlight) return;
    _inFlight = true;
    final source = _source;
    try {
      final s = await source.fetch();
      if (!mounted || !identical(source, _source)) return;
      setState(() {
        _state = s;
        _fails = 0;
        _lastError = null;
        _lastOkAt = DateTime.now();
        _history.add(s.historyLabel);
        while (_history.length > widget.historyLength) {
          _history.removeAt(0);
        }
      });
    } catch (e) {
      if (!mounted || !identical(source, _source)) return;
      setState(() {
        _fails++;
        _lastError = _shortError(e);
      });
    } finally {
      _inFlight = false;
    }
  }

  static String _shortError(Object e) {
    final text = e.toString();
    final nl = text.indexOf('\n');
    final line = nl < 0 ? text : text.substring(0, nl);
    return line.length > 90 ? '${line.substring(0, 90)}…' : line;
  }

  void _changeAddress(String url) {
    final old = _source;
    setState(() {
      _baseUrl = url;
      _source = widget.sourceFactory(url);
      _fails = 0;
      _lastError = null;
      _state = null;
      _history.clear();
      final path = widget.store?.save(url);
      _savedNote = widget.store == null
          ? null
          : (path == null ? '저장 실패 · 이번 실행에만 적용' : '저장됨 $path');
    });
    old.close();
    _tick();
  }

  Future<void> _openAddressDialog() async {
    final picked = await showAddressDialog(context,
        current: _baseUrl, presets: widget.presets);
    if (picked != null && picked != _baseUrl) _changeAddress(picked);
  }

  bool get _disconnected => _fails > 3 || (_state == null && _fails > 0);

  @override
  Widget build(BuildContext context) {
    final s = _state;
    return Container(
      color: kBg,
      padding: const EdgeInsets.all(14),
      child: DefaultTextStyle(
        style: const TextStyle(color: kFg, fontSize: 14, height: 1.25),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _Header(
              baseUrl: _baseUrl,
              status: _statusText(),
              statusColor: _lastError == null ? kDim : kBad,
              onTapAddress: _openAddressDialog,
            ),
            const SizedBox(height: 10),
            Expanded(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(
                    flex: 6,
                    child: _Card(
                      child: _MainPanel(
                        state: s,
                        disconnected: _disconnected,
                        history: _history,
                        historyLength: widget.historyLength,
                        savedNote: _savedNote,
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    flex: 5,
                    child: _Card(child: _ProbPanel(state: s)),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _statusText() {
    if (_lastError != null) {
      return '연결 실패 ($_fails): $_lastError';
    }
    final at = _lastOkAt;
    if (at == null) return '연결 중…';
    final hh = at.hour.toString().padLeft(2, '0');
    final mm = at.minute.toString().padLeft(2, '0');
    final ss = at.second.toString().padLeft(2, '0');
    return 'ok $hh:$mm:$ss';
  }
}

class _Header extends StatelessWidget {
  const _Header({
    required this.baseUrl,
    required this.status,
    required this.statusColor,
    required this.onTapAddress,
  });

  final String baseUrl;
  final String status;
  final Color statusColor;
  final VoidCallback onTapAddress;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const Text('DESKMATE ToF 자세',
            style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(width: 14),
        // 주소 칸. 누르면 바꿀 수 있다(키보드가 없는 보드라 키패드를 띄운다).
        Material(
          color: kCard,
          borderRadius: BorderRadius.circular(6),
          child: InkWell(
            key: const ValueKey('address-button'),
            onTap: onTapAddress,
            borderRadius: BorderRadius.circular(6),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
              decoration: BoxDecoration(
                border: Border.all(color: kLine),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(baseUrl, style: const TextStyle(fontSize: 14)),
                  const SizedBox(width: 8),
                  const Icon(Icons.edit, size: 14, color: kDim),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Text(status,
              key: const ValueKey('status-text'),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 13, color: statusColor)),
        ),
      ],
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: kCard,
        borderRadius: BorderRadius.circular(12),
      ),
      padding: const EdgeInsets.all(16),
      child: child,
    );
  }
}

class _MainPanel extends StatelessWidget {
  const _MainPanel({
    required this.state,
    required this.disconnected,
    required this.history,
    required this.historyLength,
    required this.savedNote,
  });

  final TofPostureState? state;
  final bool disconnected;
  final List<String> history;
  final int historyLength;
  final String? savedNote;

  @override
  Widget build(BuildContext context) {
    final s = state;
    final String big;
    final Color bigColor;
    if (disconnected && s == null) {
      big = '연결 없음';
      bigColor = kDim;
    } else if (s == null) {
      big = '모름';
      bigColor = kDim;
    } else if (!s.valid) {
      big = s.postureKo.isNotEmpty ? s.postureKo : labelKo(s.posture);
      bigColor = kDim;
    } else {
      big = s.postureKo.isNotEmpty ? s.postureKo : labelKo(s.posture);
      bigColor = labelColor(s.posture);
    }
    final String confLine;
    final String sinceLine;
    if (s == null) {
      confLine = '—';
      sinceLine = '';
    } else if (s.valid) {
      confLine = '확신 ${(s.conf * 100).round()} %  ·  present ${s.present}'
          '  ·  misplaced ${s.misplaced}';
      sinceLine = '이 자세 ${(s.sinceMs / 1000).toStringAsFixed(1)} s 째'
          ' · 센서 ${s.onlineSensors}개 · tick ${s.ticks}';
    } else {
      confLine = '센서 입력 없음';
      sinceLine = '센서 ${s.onlineSensors}개 · tick ${s.ticks}';
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Text(big,
              key: const ValueKey('posture-text'),
              style: TextStyle(
                  fontSize: 64,
                  fontWeight: FontWeight.w700,
                  height: 1.1,
                  color: bigColor)),
        ),
        const SizedBox(height: 4),
        Text(confLine,
            key: const ValueKey('conf-text'),
            style: const TextStyle(fontSize: 18, color: kDim)),
        const SizedBox(height: 6),
        Text(sinceLine, style: const TextStyle(fontSize: 14, color: kDim)),
        const SizedBox(height: 12),
        _HistoryStrip(history: history, length: historyLength),
        const SizedBox(height: 12),
        Expanded(
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _SensorGrid(sensors: s?.sensors ?? const []),
                const SizedBox(height: 8),
                if (s != null && s.modelFile.isNotEmpty)
                  Text(
                    'model ${s.modelFileName} · ${s.modelClasses} classes · '
                    '${s.rateHz} Hz · fusion ${s.fusion} · '
                    'flip_rows ${s.flipRows} flip_cols ${s.flipCols}',
                    style: const TextStyle(fontSize: 12, color: kDim),
                  ),
                if (savedNote != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(savedNote!,
                        style: const TextStyle(fontSize: 12, color: kDim)),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

/// 최근 판정 띠. 칸 하나가 폴링 한 번(0.3 s)이고, 무효 칸은 낮게 그린다.
class _HistoryStrip extends StatelessWidget {
  const _HistoryStrip({required this.history, required this.length});
  final List<String> history;
  final int length;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 28,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          for (var i = 0; i < length; i++)
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 1),
                child: _historyBar(
                    i < history.length ? history[i] : null),
              ),
            ),
        ],
      ),
    );
  }

  Widget _historyBar(String? label) {
    if (label == null) return const SizedBox.shrink();
    final unknown = label == 'unknown';
    return FractionallySizedBox(
      heightFactor: unknown ? 0.3 : 1,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: labelColor(label),
          borderRadius: BorderRadius.circular(2),
        ),
      ),
    );
  }
}

class _SensorGrid extends StatelessWidget {
  const _SensorGrid({required this.sensors});
  final List<SensorState> sensors;

  @override
  Widget build(BuildContext context) {
    if (sensors.isEmpty) {
      return const Text('센서 정보 없음', style: TextStyle(color: kDim));
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < sensors.length; i++) ...[
          if (i > 0) const SizedBox(width: 10),
          Expanded(child: _SensorTile(sensor: sensors[i])),
        ],
      ],
    );
  }
}

class _SensorTile extends StatelessWidget {
  const _SensorTile({required this.sensor});
  final SensorState sensor;

  @override
  Widget build(BuildContext context) {
    final x = sensor;
    final line1 = x.online
        ? '${labelKo(x.posture)} ${(x.conf * 100).round()} %'
        : '오프라인';
    return Container(
      decoration: BoxDecoration(
        color: kCardDeep,
        borderRadius: BorderRadius.circular(10),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: x.online ? kOk : kBad,
                ),
              ),
              const SizedBox(width: 6),
              Flexible(
                child: Text(x.name,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        fontSize: 15, fontWeight: FontWeight.w700)),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
              '$line1 · ${x.hz.toStringAsFixed(1)} Hz · frames ${x.frames}',
              style: const TextStyle(fontSize: 13)),
          const SizedBox(height: 2),
          Text(
            'gaps ${x.seqGaps} · dup ${x.duplicates} · resets ${x.resets}'
            ' · flip r${x.flipRows ? 1 : 0}/c${x.flipCols ? 1 : 0}'
            '${x.error.isNotEmpty ? ' · ${x.error}' : ''}',
            style: const TextStyle(fontSize: 12, color: kDim),
          ),
        ],
      ),
    );
  }
}

class _ProbPanel extends StatelessWidget {
  const _ProbPanel({required this.state});
  final TofPostureState? state;

  @override
  Widget build(BuildContext context) {
    final s = state;
    final labels = s?.labels ?? kLabelKo.keys.where((k) => k != 'unknown').toList();
    final probs = s?.barProbs ?? const <String, double>{};
    final top = s?.topBarLabel;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('순간 확률 (instant.probs)',
            style: TextStyle(fontSize: 13, color: kDim)),
        const SizedBox(height: 6),
        Expanded(
          child: LayoutBuilder(builder: (context, box) {
            // 9 줄이 카드 높이에 맞게 늘어나되, 좁은 화면에서는 스크롤로 떨어진다.
            final rowH = (box.maxHeight / labels.length).clamp(24.0, 48.0);
            return ListView(
              children: [
                for (final l in labels)
                  SizedBox(
                    height: rowH,
                    child: _ProbRow(
                      label: l,
                      value: probs[l] ?? 0,
                      top: l == top,
                    ),
                  ),
              ],
            );
          }),
        ),
      ],
    );
  }
}

class _ProbRow extends StatelessWidget {
  const _ProbRow({required this.label, required this.value, required this.top});
  final String label;
  final double value;
  final bool top;

  @override
  Widget build(BuildContext context) {
    final pct = (value * 100).clamp(0, 100).toDouble();
    return Row(
      children: [
        SizedBox(
          width: 110,
          child: Text(labelKo(label),
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  fontSize: 14,
                  fontWeight: top ? FontWeight.w700 : FontWeight.w400)),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(6),
            child: Container(
              height: 12,
              color: kBarTrack,
              alignment: Alignment.centerLeft,
              child: AnimatedFractionallySizedBox(
                duration: const Duration(milliseconds: 250),
                widthFactor: pct / 100,
                child: Container(color: top ? kOk : kBar),
              ),
            ),
          ),
        ),
        const SizedBox(width: 8),
        SizedBox(
          width: 56,
          child: Text('${pct.toStringAsFixed(1)} %',
              textAlign: TextAlign.right,
              style: const TextStyle(fontSize: 14)),
        ),
      ],
    );
  }
}

/// 주소 바꾸기. 프리셋 두 개(유선·핫스팟) + 글자 칸 + 숫자 키패드.
///
/// 보드엔 키보드가 없고 ATLAS Flutter 는 화면 키보드를 띄우지 않아 키패드를 같이 둔다.
/// 돌려주는 값은 [normalizeBaseUrl] 을 거친 주소이고, 취소면 null.
Future<String?> showAddressDialog(
  BuildContext context, {
  required String current,
  List<AddressPreset> presets = kAddressPresets,
}) {
  return showDialog<String>(
    context: context,
    builder: (context) => _AddressDialog(current: current, presets: presets),
  );
}

class _AddressDialog extends StatefulWidget {
  const _AddressDialog({required this.current, required this.presets});
  final String current;
  final List<AddressPreset> presets;

  @override
  State<_AddressDialog> createState() => _AddressDialogState();
}

class _AddressDialogState extends State<_AddressDialog> {
  late final TextEditingController _text =
      TextEditingController(text: widget.current);
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  void _type(String ch) {
    final v = _text.value;
    final sel = v.selection.isValid
        ? v.selection
        : TextSelection.collapsed(offset: v.text.length);
    final next = v.text.replaceRange(sel.start, sel.end, ch);
    _text.value = TextEditingValue(
      text: next,
      selection: TextSelection.collapsed(offset: sel.start + ch.length),
    );
  }

  void _backspace() {
    final v = _text.value;
    final sel = v.selection.isValid
        ? v.selection
        : TextSelection.collapsed(offset: v.text.length);
    if (!sel.isCollapsed) {
      _text.value = TextEditingValue(
        text: v.text.replaceRange(sel.start, sel.end, ''),
        selection: TextSelection.collapsed(offset: sel.start),
      );
      return;
    }
    if (sel.start == 0) return;
    _text.value = TextEditingValue(
      text: v.text.replaceRange(sel.start - 1, sel.start, ''),
      selection: TextSelection.collapsed(offset: sel.start - 1),
    );
  }

  void _apply() {
    final url = normalizeBaseUrl(_text.text, defaultPort: kTofPort);
    if (url == null) {
      setState(() => _error = '주소 모양이 아니다. 예) 172.20.10.3 또는 http://172.20.10.3:8771');
      return;
    }
    Navigator.of(context).pop(url);
  }

  @override
  Widget build(BuildContext context) {
    const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '.', '0', ':'];
    return AlertDialog(
      backgroundColor: kCard,
      titleTextStyle: const TextStyle(
          color: kFg, fontSize: 18, fontWeight: FontWeight.w700),
      contentTextStyle: const TextStyle(color: kFg, fontSize: 14),
      title: const Text('Pi 4 자세 서비스 주소'),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final p in widget.presets)
                  ActionChip(
                    backgroundColor: kCardDeep,
                    side: const BorderSide(color: kLine),
                    label: Text('${p.name}  ${p.url}',
                        style: const TextStyle(color: kFg, fontSize: 13)),
                    onPressed: () => setState(() {
                      _text.text = p.url;
                      _error = null;
                    }),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _text,
              autofocus: true,
              style: const TextStyle(color: kFg, fontSize: 16),
              keyboardType: TextInputType.url,
              decoration: InputDecoration(
                isDense: true,
                filled: true,
                fillColor: kCardDeep,
                border: const OutlineInputBorder(),
                errorText: _error,
                errorMaxLines: 2,
                helperText: 'IP 만 치면 http:// 와 :$kTofPort 을 붙인다',
                helperStyle: const TextStyle(color: kDim),
              ),
              onSubmitted: (_) => _apply(),
            ),
            const SizedBox(height: 10),
            GridView.count(
              crossAxisCount: 4,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              mainAxisSpacing: 6,
              crossAxisSpacing: 6,
              childAspectRatio: 2.2,
              children: [
                for (final k in keys) _key(k, () => _type(k)),
                _key('⌫', _backspace),
                _key('지우기', () => _text.clear()),
                _key('/api', () => _type('/api')),
                _key('8771', () => _type('8771')),
              ],
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('취소', style: TextStyle(color: kDim)),
        ),
        FilledButton(
          key: const ValueKey('address-apply'),
          onPressed: _apply,
          child: const Text('적용'),
        ),
      ],
    );
  }

  Widget _key(String label, VoidCallback onTap) {
    return Material(
      color: kCardDeep,
      borderRadius: BorderRadius.circular(6),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Center(
          child: Text(label,
              style: const TextStyle(color: kFg, fontSize: 16)),
        ),
      ),
    );
  }
}
