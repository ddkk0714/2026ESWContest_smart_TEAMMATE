"""devview --act-laptop 의 노트북 대역 기기(Windows 전용): 내장 화면 밝기와 마스터 볼륨, 짧은 알림음.

- 화면 밝기: WMI(root/WMI WmiMonitorBrightness·WmiMonitorBrightnessMethods)를 인라인 `powershell -Command` 로.
  스크립트 파일·실행 정책 변경 없음.
- 볼륨: Windows Core Audio(IMMDeviceEnumerator → 기본 출력 장치 → IAudioEndpointVolume)를 ctypes 로 직접(pycaw 없이).
- 알림음: 표준 라이브러리 winsound.

여기 함수는 실패하면 예외를 던진다. 호출하는 쪽(devview.LaptopActuator)이 이유를 화면에 띄우고 계속 간다.
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import POINTER, byref, c_float, c_uint, c_void_p, wintypes

_NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW — 창이 깜빡이지 않게


def _ps(script: str, timeout: float = 8.0) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       timeout=timeout, creationflags=_NO_WINDOW if sys.platform == "win32" else 0)
    if r.returncode != 0:
        raise OSError((r.stderr or r.stdout or "powershell 실패").strip()[:200])
    return r.stdout.strip()


def get_brightness() -> int:
    out = _ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness | Select-Object -First 1")
    return int(out.splitlines()[-1])


def set_brightness(percent: int) -> None:
    n = max(0, min(100, int(percent)))
    _ps("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
        f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=[uint32]1; Brightness=[byte]{n}}} | Out-Null")


# ---- Core Audio (ctypes) -------------------------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        import uuid
        u = uuid.UUID(text)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
        g.Data4[:] = list(u.bytes[8:])
        return g


_CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
_CLSCTX_ALL = 23
_eRender, _eMultimedia = 0, 1


def _method(obj: c_void_p, index: int, restype, *argtypes):
    """COM 객체의 vtable 에서 index 번째 메서드(앞 3 개는 IUnknown)."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtbl[index])


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} 실패 HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def _release(obj: c_void_p) -> None:
    if obj:
        _method(obj, 2, ctypes.c_ulong)(obj)


def _endpoint_volume() -> c_void_p:
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)   # 이미 초기화돼 있으면 S_FALSE/RPC_E_CHANGED_MODE — 무시
    enum = c_void_p()
    clsid, iid = _GUID.of(_CLSID_MMDeviceEnumerator), _GUID.of(_IID_IMMDeviceEnumerator)
    _check(ole32.CoCreateInstance(byref(clsid), None, _CLSCTX_ALL, byref(iid), byref(enum)), "CoCreateInstance")
    dev = c_void_p()
    try:
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable 4)
        _check(_method(enum, 4, ctypes.HRESULT, c_uint, c_uint, POINTER(c_void_p))(enum, _eRender, _eMultimedia, byref(dev)),
               "GetDefaultAudioEndpoint")
    finally:
        _release(enum)
    vol = c_void_p()
    try:
        iid_vol = _GUID.of(_IID_IAudioEndpointVolume)
        # IMMDevice::Activate (vtable 3)
        _check(_method(dev, 3, ctypes.HRESULT, POINTER(_GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(
            dev, byref(iid_vol), _CLSCTX_ALL, None, byref(vol)), "IMMDevice.Activate")
    finally:
        _release(dev)
    return vol


def get_volume() -> float:
    vol = _endpoint_volume()
    try:
        v = c_float()
        # IAudioEndpointVolume::GetMasterVolumeLevelScalar (vtable 9)
        _check(_method(vol, 9, ctypes.HRESULT, POINTER(c_float))(vol, byref(v)), "GetMasterVolumeLevelScalar")
        return round(float(v.value), 3)
    finally:
        _release(vol)


def set_volume(level: float) -> None:
    vol = _endpoint_volume()
    try:
        # IAudioEndpointVolume::SetMasterVolumeLevelScalar (vtable 7)
        _check(_method(vol, 7, ctypes.HRESULT, c_float, c_void_p)(vol, c_float(max(0.0, min(1.0, float(level)))), None),
               "SetMasterVolumeLevelScalar")
    finally:
        _release(vol)


def speak(text: str) -> str:
    """Windows 내장 TTS(System.Speech)로 말한다. 한국어 목소리가 있으면 그것, 없으면 OSError(부르는 쪽이 알림음으로).
    글자는 base64 로 넘겨 따옴표·특수문자가 명령이 되지 않게 한다. 실행 정책은 건드리지 않는다(인라인 -Command)."""
    import base64
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    out = _ps("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'ko*' } | Select-Object -First 1; "
              "if (-not $v) { [Console]::Error.WriteLine('no korean voice'); exit 2 }; $s.SelectVoice($v.VoiceInfo.Name); "
              f"$t = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')); $s.Speak($t); $v.VoiceInfo.Name",
              timeout=60)
    return out.splitlines()[-1] if out else "TTS"


def chime(kind: str = "alert") -> None:
    """짧은 소리: alert = 두 음, voice = 세 음(음성 안내 대신), music = 부드러운 네 음(음악 대신). 막지 않게 부르는 쪽이 스레드로."""
    import winsound
    notes = {"alert": [(880, 160), (1175, 220)], "voice": [(660, 140), (880, 140), (990, 260)],
             "music": [(523, 220), (659, 220), (784, 220), (1047, 360)],
             # hub chime pattern(ad76ff0): single · double · triple
             "single": [(880, 220)], "double": [(880, 150), (1175, 200)], "triple": [(988, 120), (988, 120), (988, 120)],
             "soft": [(660, 180), (880, 260)]}.get(kind, [(880, 200)])
    for f, ms in notes:
        winsound.Beep(f, ms)
