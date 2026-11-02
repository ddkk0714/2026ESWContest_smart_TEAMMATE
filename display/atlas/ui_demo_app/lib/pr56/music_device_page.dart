import 'package:flutter/material.dart';
import 'device_widgets.dart';
import 'music_playback.dart';
import 'workspace_store.dart';

class MusicDevicePage extends StatefulWidget {
  const MusicDevicePage(
      {super.key,
      required this.nickname,
      required this.status,
      required this.playerBuilder,
      required this.favorites,
      required this.playlists,
      required this.favorite,
      required this.onFavorite,
      required this.onVolume,
      required this.onBack,
      required this.onRename,
      required this.onTrack,
      required this.onPlayQueue,
      required this.onSavePlaylist,
      required this.onDeletePlaylist});
  final String nickname;
  final String status;
  final Widget Function(VoidCallback onPlaylist) playerBuilder;
  final bool favorite;
  final VoidCallback onFavorite;
  final VoidCallback onVolume;
  final List<int> favorites;
  final List<DeskPlaylist> playlists;
  final VoidCallback onBack;
  final VoidCallback onRename;
  final ValueChanged<int> onTrack;
  final ValueChanged<List<int>> onPlayQueue;
  final ValueChanged<DeskPlaylist> onSavePlaylist;
  final ValueChanged<String> onDeletePlaylist;
  @override
  State<MusicDevicePage> createState() => _MusicDevicePageState();
}

class _MusicDevicePageState extends State<MusicDevicePage> {
  int _tab = 1;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        final compact = box.maxWidth < 450;
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(children: [
                IconButton(
                    tooltip: '기기 목록',
                    onPressed: widget.onBack,
                    icon: const Icon(Icons.arrow_back_rounded)),
                Expanded(
                    child: Text(widget.nickname,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 24, fontWeight: FontWeight.w600))),
                Text(widget.status, style: const TextStyle(fontSize: 11)),
                IconButton(
                    tooltip: '별명 변경',
                    onPressed: widget.onRename,
                    icon: const Icon(Icons.edit_outlined, size: 19)),
              ]),
              const SizedBox(height: 8),
              Expanded(
                  child: _tab == 0
                      ? _radio()
                      : _tab == 1
                          ? _library()
                          : _playlists()),
              const SizedBox(height: 10),
              SizedBox(
                  height: compact ? 190 : 196,
                  child: widget.playerBuilder(() => setState(() => _tab = 2))),
              const SizedBox(height: 7),
              NavigationBar(
                  height: 62,
                  selectedIndex: _tab,
                  onDestinationSelected: (i) => setState(() => _tab = i),
                  destinations: const [
                    NavigationDestination(
                        icon: Icon(Icons.radio_rounded), label: '라디오'),
                    NavigationDestination(
                        icon: Icon(Icons.library_music_outlined), label: '보관함'),
                    NavigationDestination(
                        icon: Icon(Icons.queue_music_rounded), label: '플레이리스트')
                  ]),
            ]);
      });

  Widget _library() =>
      ListView(key: const PageStorageKey('music-library-list'), children: [
        Row(children: [
          const Expanded(
              child: Text('보관함',
                  style: TextStyle(fontSize: 23, fontWeight: FontWeight.w600))),
          IconButton(
              tooltip: widget.favorite ? '즐겨찾기 해제' : '즐겨찾기',
              onPressed: widget.onFavorite,
              icon: Icon(widget.favorite
                  ? Icons.favorite_rounded
                  : Icons.favorite_border_rounded)),
          IconButton(
              tooltip: '볼륨',
              onPressed: widget.onVolume,
              icon: const Icon(Icons.volume_up_outlined)),
        ]),
        for (var i = 0; i < classicalTrackTitles.length; i++)
          ListTile(
              key: ValueKey('workspace-track-$i'),
              contentPadding: const EdgeInsets.symmetric(horizontal: 3),
              leading: const ApplianceArt(music: true, size: 44),
              title: Text(classicalTrackTitles[i],
                  style: const TextStyle(fontSize: 15)),
              trailing: const Icon(Icons.play_arrow_rounded),
              onTap: () => widget.onTrack(i)),
      ]);

  Widget _radio() =>
      ListView(key: const PageStorageKey('music-radio-list'), children: [
        const Text('라디오',
            style: TextStyle(fontSize: 23, fontWeight: FontWeight.w600)),
        const SizedBox(height: 5),
        Text('당신의 몰입을 위한 음악',
            style: TextStyle(
                fontSize: 12,
                color: Theme.of(context).colorScheme.onSurfaceVariant)),
        const SizedBox(height: 13),
        for (final station in [
          ('클래식 믹스', '여섯 곡을 이어서', [0, 1, 2, 3, 4, 5]),
          ('잔잔한 피아노', '달빛 · 녹턴 · 짐노페디', [0, 1, 2]),
          ('작업실 라디오', '모차르트와 바흐', [4, 5])
        ])
          Card(
              elevation: 0,
              color:
                  Theme.of(context).colorScheme.primary.withValues(alpha: .08),
              child: ListTile(
                  leading: const Icon(Icons.radio_rounded, size: 30),
                  title: Text(station.$1),
                  subtitle: Text(station.$2),
                  trailing: const Icon(Icons.play_circle_fill_rounded),
                  onTap: () => widget.onPlayQueue(station.$3))),
      ]);

  Widget _playlists() =>
      ListView(key: const PageStorageKey('music-playlists-list'), children: [
        Row(children: [
          const Expanded(
              child: Text('플레이리스트',
                  style: TextStyle(fontSize: 23, fontWeight: FontWeight.w600))),
          IconButton(
              tooltip: '플레이리스트 추가',
              onPressed: () => _editPlaylist(),
              icon: const Icon(Icons.add_circle_outline_rounded))
        ]),
        ListTile(
            leading: const Icon(Icons.music_note_rounded),
            title: const Text('모든 곡'),
            subtitle: Text('${classicalTrackTitles.length}곡'),
            trailing: const Icon(Icons.play_arrow_rounded),
            onTap: () => widget.onPlayQueue(
                List.generate(classicalTrackTitles.length, (i) => i))),
        ListTile(
            leading: const Icon(Icons.favorite_rounded),
            title: const Text('즐겨찾기'),
            subtitle: Text('${widget.favorites.length}곡'),
            enabled: widget.favorites.isNotEmpty,
            trailing: const Icon(Icons.play_arrow_rounded),
            onTap: () => widget.onPlayQueue(widget.favorites)),
        for (final playlist in widget.playlists)
          ListTile(
              leading: const Icon(Icons.queue_music_rounded),
              title: Text(playlist.name),
              subtitle: Text('${playlist.tracks.length}곡'),
              onTap: playlist.tracks.isEmpty
                  ? null
                  : () => widget.onPlayQueue(playlist.tracks),
              trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                IconButton(
                    tooltip: '플레이리스트 수정',
                    onPressed: () => _editPlaylist(playlist),
                    icon: const Icon(Icons.edit_outlined, size: 18)),
                IconButton(
                    tooltip: '플레이리스트 삭제',
                    onPressed: () => widget.onDeletePlaylist(playlist.id),
                    icon: const Icon(Icons.delete_outline_rounded, size: 18)),
              ])),
      ]);

  Future<void> _editPlaylist([DeskPlaylist? playlist]) async {
    final name = TextEditingController(text: playlist?.name ?? '');
    final tracks = {...?playlist?.tracks};
    var invalid = false;
    final result = await showDialog<DeskPlaylist>(
        context: context,
        builder: (context) => StatefulBuilder(
            builder: (context, refresh) => AlertDialog(
                  title: Text(playlist == null ? '플레이리스트 추가' : '플레이리스트 수정'),
                  content: SizedBox(
                      width: 400,
                      child: SingleChildScrollView(
                          child:
                              Column(mainAxisSize: MainAxisSize.min, children: [
                        TextField(
                            key: const ValueKey('playlist-name'),
                            controller: name,
                            maxLength: 30,
                            decoration: InputDecoration(
                                labelText: '이름',
                                errorText:
                                    invalid ? '이름과 한 곡 이상을 골라주세요.' : null)),
                        for (var i = 0; i < classicalTrackTitles.length; i++)
                          CheckboxListTile(
                              contentPadding: EdgeInsets.zero,
                              title: Text(classicalTrackTitles[i],
                                  style: const TextStyle(fontSize: 13)),
                              value: tracks.contains(i),
                              onChanged: (v) => refresh(() {
                                    if (v == true) {
                                      tracks.add(i);
                                    } else {
                                      tracks.remove(i);
                                    }
                                  })),
                      ]))),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context),
                        child: const Text('취소')),
                    FilledButton(
                        key: const ValueKey('playlist-save'),
                        onPressed: () {
                          if (name.text.trim().isEmpty || tracks.isEmpty) {
                            refresh(() => invalid = true);
                            return;
                          }
                          Navigator.pop(
                              context,
                              DeskPlaylist(
                                  id: playlist?.id ??
                                      '${DateTime.now().microsecondsSinceEpoch}',
                                  name: name.text.trim(),
                                  tracks: tracks.toList()..sort()));
                        },
                        child: const Text('저장'))
                  ],
                )));
    Future<void>.delayed(const Duration(milliseconds: 400), name.dispose);
    if (result != null && mounted) widget.onSavePlaylist(result);
  }
}
