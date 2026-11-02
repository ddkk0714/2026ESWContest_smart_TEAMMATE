"""--broker auto: 후보 순서·hub 확인·끊김 뒤 다시 찾기 (네트워크 없이 가짜 probe/verify 로)."""
from __future__ import annotations

import time

from collector.discover import AutoBroker, candidates, discover, load_cached, save_cached


def test_candidates_order_and_dedup():
    order = candidates("172.20.10.6", ["172.20.10.3", "172.16.34.199", "172.20.10.6"], ["172.20.10.2"])
    assert order[:3] == ["172.20.10.6", "172.20.10.3", "172.16.34.199"]     # 캐시 → 별칭
    assert "172.20.10.2" not in order                                           # 자기 자신 제외
    assert "172.20.10.254" in order and len(order) == len(set(order))         # /24 전체, 중복 없음


def test_discover_picks_first_verified_hub():
    open_ports = {"172.20.10.1", "172.20.10.6", "172.20.10.9"}                 # .1 = 핫스팟 공유기(MQTT 아님)
    hubs = {"172.20.10.6"}
    checked = []

    def verify(host, port):
        checked.append(host)
        return host in hubs

    host = discover(1883, ["172.20.10.3"], cached=None, local_ips=["172.20.10.2"],
                    probe=lambda h, p: h in open_ports, verify=verify)
    assert host == "172.20.10.6"
    assert "172.20.10.3" not in checked                                         # 포트가 닫힌 후보는 hub 확인을 안 한다


def test_discover_none_when_no_hub():
    assert discover(1883, [], local_ips=["10.0.0.5"], probe=lambda h, p: True, verify=lambda h, p: False) is None


def test_cache_roundtrip(tmp_path):
    path = str(tmp_path / "b.json")
    assert load_cached(path) is None
    save_cached("172.20.10.6", path)
    assert load_cached(path) == "172.20.10.6"


class FakePub:
    def __init__(self):
        self.connected = False
        self.targets = []

    def retarget(self, host):
        self.targets.append(host)
        self.connected = True


def _wait(auto):
    while auto._thread is not None and auto._thread.is_alive():
        time.sleep(0.01)


def test_auto_broker_finds_then_rediscovers_after_long_drop(tmp_path):
    hosts = ["172.20.10.3", "172.20.10.6"]
    found = []

    def find(port, targets, cached=None):
        found.append(cached)
        return hosts.pop(0)

    auto = AutoBroker(1883, [], rediscover_after=30, find=find, cache_file=str(tmp_path / "c.json"), log=lambda m: None)
    pub = FakePub()
    auto.tick(pub, 0.0)              # 처음: 바로 찾기 시작
    _wait(auto)
    auto.tick(pub, 1.0)              # 결과 반영 → retarget
    assert pub.targets == ["172.20.10.3"] and load_cached(str(tmp_path / "c.json")) == "172.20.10.3"

    pub.connected = False            # 끊김 — 30 s 전에는 다시 찾지 않는다(paho 가 같은 주소로 재연결 중)
    auto.tick(pub, 10.0)
    auto.tick(pub, 25.0)
    assert auto._thread is None
    auto.tick(pub, 40.5)             # 30 s 넘게 끊김 → 다시 찾기(핫스팟이 바뀌어 주소가 달라짐)
    _wait(auto)
    auto.tick(pub, 41.0)
    assert pub.targets == ["172.20.10.3", "172.20.10.6"]
    assert found == [None, "172.20.10.3"]                                       # 두 번째엔 캐시를 먼저 쓴다


def test_auto_broker_retries_later_when_not_found(tmp_path):
    calls = []
    auto = AutoBroker(1883, [], retry_every=15, find=lambda *a, **k: calls.append(1),
                      cache_file=str(tmp_path / "c.json"), log=lambda m: None)
    pub = FakePub()
    auto.tick(pub, 0.0)
    _wait(auto)
    auto.tick(pub, 1.0)              # 못 찾음 → 15 s 뒤
    auto.tick(pub, 10.0)
    assert len(calls) == 1 and pub.targets == []
    auto.tick(pub, 16.5)
    _wait(auto)
    assert len(calls) == 2
