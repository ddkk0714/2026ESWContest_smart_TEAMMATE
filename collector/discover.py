"""Pi 4 브로커 자동 찾기 — `--broker auto`.

노트북과 같은 Wi-Fi(같은 /24)에서 MQTT 포트가 열린 기기를 찾고, hub 가 retain 으로 남기는
`deskmate/health/hub`(또는 `deskmate/state/phase`)가 실제로 오는 브로커만 DESKMATE hub 로 본다.
핫스팟·장소가 바뀌어 Pi 4 주소가 달라져도 주소를 손으로 넣지 않게 하려는 것(11-02).

찾는 순서: 지난번에 찾은 주소(캐시) → config.TARGETS 별칭 → 노트북 IPv4 마다 같은 /24 전체.
포트 확인은 병렬, hub 확인은 위 순서대로 하나씩. 키 값과는 무관 — 네트워크 주소만 다룬다.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

HUB_TOPICS = ("deskmate/health/hub", "deskmate/state/phase")


def cache_path() -> str:
    """찾은 주소를 기억해 두는 파일(다음 시작 때 먼저 시도)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DESKMATE", "collector_broker.json")


def load_cached(path: str | None = None) -> str | None:
    try:
        with open(path or cache_path(), encoding="utf-8") as fh:
            host = json.load(fh).get("host")
        return host if isinstance(host, str) and host else None
    except (OSError, ValueError):
        return None


def save_cached(host: str, path: str | None = None) -> None:
    path = path or cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"host": host, "ts": round(time.time(), 3)}, fh)
    except OSError:
        pass


def local_ipv4() -> list[str]:
    """이 PC 의 사설 IPv4 주소들(루프백·링크로컬 제외)."""
    found: list[str] = []
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        ip = info[4][0]
        addr = ipaddress.ip_address(ip)
        if addr.is_private and not addr.is_loopback and not addr.is_link_local and ip not in found:
            found.append(ip)
    return found


def candidates(cached: str | None, targets: Iterable[str], local_ips: Iterable[str]) -> list[str]:
    """시도 순서대로 중복 없이: 캐시 → 별칭 → 각 로컬 IP 의 /24(자기 자신 제외)."""
    out: list[str] = []
    mine = set(local_ips)

    def add(host: str | None) -> None:
        if host and host not in out and host not in mine:
            out.append(host)

    add(cached)
    for host in targets:
        add(host)
    for ip in mine:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        for host in net.hosts():
            add(str(host))
    return out


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_deskmate_hub(host: str, port: int, timeout: float = 3.0) -> bool:
    """브로커에 붙어 hub 의 retain 토픽이 오면 True."""
    import paho.mqtt.client as mqtt

    got = {"hub": False}

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            for topic in HUB_TOPICS:
                client.subscribe(topic, qos=0)

    def on_message(client, userdata, msg):
        got["hub"] = True

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"collector-probe-{int(time.time() * 1000)}")
    c.on_connect = on_connect
    c.on_message = on_message
    try:
        c.connect(host, port, keepalive=10)
    except (OSError, ValueError):
        return False
    c.loop_start()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not got["hub"]:
            time.sleep(0.05)
    finally:
        c.loop_stop()
        try:
            c.disconnect()
        except Exception:
            pass
    return got["hub"]


class AutoBroker:
    """`--broker auto` 운영: 처음엔 찾을 때까지, 그 뒤엔 연결이 [rediscover_after] 초 넘게 끊기면 다시 찾는다.

    찾기는 뒤 스레드에서 하고(수집·로컬 기록은 멈추지 않음), 메인 루프가 매 주기 [tick] 을 부른다.
    찾으면 캐시에 남기고 발행기를 그 주소로 옮긴다."""

    def __init__(self, port: int, targets: Iterable[str], *, rediscover_after: float = 30.0,
                 retry_every: float = 15.0, find: Callable[..., str | None] | None = None,
                 cache_file: str | None = None, log: Callable[[str], None] = print):
        import threading

        self.port = port
        self.targets = list(targets)
        self.rediscover_after = rediscover_after
        self.retry_every = retry_every
        self._find = find or discover
        self._cache_file = cache_file
        self._log = log
        self._threading = threading
        self._thread = None
        self._result: list[str | None] = []
        self.host: str | None = None
        self._down_since: float | None = None
        self._next_try = 0.0

    def _run(self) -> None:
        self._result.append(self._find(self.port, self.targets, cached=load_cached(self._cache_file)))

    def tick(self, pub, now: float) -> None:
        if self._thread is not None:
            if self._thread.is_alive():
                return
            self._thread = None
            host = self._result.pop() if self._result else None
            if host:
                self._log(f"[auto] DESKMATE hub 브로커 찾음 → {host}:{self.port}")
                save_cached(host, self._cache_file)
                self._down_since = None
                if host != self.host or not pub.connected:
                    self.host = host
                    pub.retarget(host)
                return
            self._log(f"[auto] hub 브로커를 못 찾음 — {self.retry_every:.0f}s 뒤 다시 찾는다(로컬 기록은 계속)")
            self._next_try = now + self.retry_every
            return
        if pub.connected:
            self._down_since = None
            return
        if self._down_since is None:
            self._down_since = now
        waited = now - self._down_since
        if (self.host is None or waited >= self.rediscover_after) and now >= self._next_try:
            self._log("[auto] 같은 네트워크에서 DESKMATE hub 브로커를 찾는 중…")
            self._thread = self._threading.Thread(target=self._run, daemon=True)
            self._thread.start()


def discover(port: int, targets: Iterable[str], *,
             cached: str | None = None,
             local_ips: Iterable[str] | None = None,
             probe: Callable[[str, int], bool] = port_open,
             verify: Callable[[str, int], bool] = is_deskmate_hub,
             workers: int = 64) -> str | None:
    """DESKMATE hub 브로커 주소. 못 찾으면 None."""
    order = candidates(cached, targets, local_ipv4() if local_ips is None else local_ips)
    if not order:
        return None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        open_flags = list(pool.map(lambda h: probe(h, port), order))
    for host, is_open in zip(order, open_flags):
        if is_open and verify(host, port):
            return host
    return None
