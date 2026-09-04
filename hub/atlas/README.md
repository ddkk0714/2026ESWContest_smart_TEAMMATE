# Pi 4 Atlas Hub service

Pi 4 Headless 프로파일에서 기존 Python FSM Hub를 실행하는 native-service IPK다.
SSH 셸의 기본 AppArmor 프로파일은 `/restricted/python3` 실행을 거부하므로,
시스템 정책을 변경하지 않고 Atlas 서비스 샌드박스 안에서 제한 Python을 실행한다.

## 빌드

저장소 루트가 `/workspace`로 마운트된 Atlas 개발 컨테이너에서 실행한다.

```bash
cd /workspace
arc build hub/atlas
```

ARC 0.5가 서비스 실행 파일 하나만 패키징하므로, 빌드 시 Hub를 실행 파일 뒤쪽의
zip payload로 포함한다. Atlas 제한 Python에는 HTTP/JSON 실행에 필요한 순수 표준
모듈도 다수 빠져 있어, 빌드 컨테이너의 동일한 Python 3.12 순수 표준 라이브러리를
함께 넣는다. 네이티브 확장과 개발·테스트 패키지는 포함하지 않는다. PyYAML이
요구하는 일부 모듈도 없으므로 `fsm.yaml`은 빌드 시 JSON으로 변환한다. 이 JSON은
빌드 산출물이며, FSM 임계값과 가중치의 원본은 계속
`hub/deskmate_hub/config/fsm.yaml` 하나뿐이다.

## 설치 및 실행 확인

```bash
arc devices add -d deskmate_pi4 --ip=<PI4_IP> --user=root
arc install <생성된-ipk> -d deskmate_pi4
ssh rpi4 'busctl --system status com.deskmate.hub1'
curl http://<PI4_IP>:8765/health
```

`busctl` 접근은 D-Bus activation으로 서비스를 시작한다. 8765 HTTP API는 Pi 5
화면과 보드 간 연동을 확인하는 개발용 어댑터이며 최종 통신 방식은 확정하지 않는다.
