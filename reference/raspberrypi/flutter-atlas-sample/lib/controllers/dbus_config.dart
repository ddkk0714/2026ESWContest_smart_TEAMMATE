class DBusConfig {
  // Bluetooth
  static const String bluetoothBusName = 'com.atlas.Bluetooth1';
  static const String bluetoothObjectPath = '/com/atlas/Bluetooth1';
  static const String adapterInterface = 'com.atlas.Bluetooth1.Adapter';
  static const String a2dpInterface = 'com.atlas.Bluetooth1.A2dp';
  static const String avrcpInterface = 'com.atlas.Bluetooth1.Avrcp';
  static const String deviceInterface = 'com.atlas.Bluetooth1.Device';
  static const String gattInterface = 'com.atlas.Bluetooth1.Gatt';
  static const String leInterface = 'com.atlas.Bluetooth1.Le';

  // MediaController1 — session hub & AVRCP routing
  static const String mediaControllerBusName = 'com.atlas.MediaController1';
  static const String mediaControllerObjectPath = '/com/atlas/MediaController1';
  static const String mediaControllerInterface = 'com.atlas.MediaController1';
  static const String mediaControllerSessionInterface = 'com.atlas.MediaController1.Session';
  // Session object path pattern: /com/atlas/MediaController1/Session/{session_id}
  static String mediaControllerSessionPath(String sessionId) =>
      '/com/atlas/MediaController1/Session/$sessionId';

  // MediaPlayer1 — actual playback engine
  static const String mediaPlayerBusName = 'com.atlas.MediaPlayer1';
  static const String mediaPlayerObjectPath = '/com/atlas/MediaPlayer1';
  static const String mediaPlayerInterface = 'com.atlas.MediaPlayer1';

  // MediaPlayerPipeline1 — dedicated pipeline process (not used directly by app)
  static const String mediaPlayerPipelineInterface = 'com.atlas.MediaPlayerPipeline1';

  // MediaResourceManager1 — hardware resource allocation (VDEC/ADEC/VENC)
  static const String mediaResourceManagerBusName = 'com.atlas.MediaResourceManager1';
  static const String mediaResourceManagerObjectPath = '/com/atlas/MediaResourceManager1';
  static const String mediaResourceManagerInterface = 'com.atlas.MediaResourceManager1';

  // AppManager1 — application lifecycle and status
  static const String appManagerBusName = 'com.atlas.AppManager1';
  static const String appManagerObjectPath = '/com/atlas/AppManager1';
  static const String appManagerInterface = 'com.atlas.AppManager1';
  static const String appId = 'com.atlas.app.smart_kitchen_app';

  // PermissionAgent1 — user_consent permission management (SQLite DB)
  static const String permissionAgentBusName = 'com.atlas.PermissionAgent1';
  static const String permissionAgentObjectPath = '/com/atlas/PermissionAgent1';
  static const String permissionAgentInterface = 'com.atlas.PermissionAgent1';

  // Test media sources
  static const String testLocalDir = 'file:///tmp';
  // Direct MP3 file (no ICY/Icecast — works without icydemux plugin)
  static const String testHttpStream = 'https://samplelib.com/lib/preview/mp3/sample-12s.mp3';
}
