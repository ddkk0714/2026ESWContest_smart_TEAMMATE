# Pi 5 Atlas Docker 개발 환경

이 환경은 Docker 설정과 작업 소스를 모두 이 레포 안에 둔다. 공급사 Atlas SDK·엔진·플러그인·toolchain은 용량과 배포 조건상 Git에 저장하지 않으며, 각 개발자가 제공받은 공식 Atlas 패키지에서 레포 내부 `display/atlas/vendor/`로 준비한다. Docker는 외부 경로를 참조하지 않는다.

## 최초 1회 준비

공식 Atlas 패키지를 확보한 뒤 아래 명령을 저장소 루트에서 실행한다.

```powershell
.\display\atlas\scripts\import-atlas-vendor.ps1 -SourcePath '공식 Atlas 패키지를 푼 폴더'
.\display\atlas\scripts\verify-atlas-vendor.ps1
```

`vendor/`는 `.gitignore` 대상이다. 다른 컴퓨터에서도 동일한 공식 패키지로 위 준비 과정을 한 번 실행하면 같은 Docker 빌드 입력을 사용한다.

## 빌드와 접속

```powershell
docker compose -f display/atlas/compose.yaml build atlas-dev
docker compose -f display/atlas/compose.yaml up -d
docker compose -f display/atlas/compose.yaml exec atlas-dev bash
```

컨테이너의 `/workspace`는 이 레포 루트이며 기본 작업 위치는 `/workspace/display`다. Atlas SDK 환경을 사용하기 전 다음을 실행한다.

```bash
source "$ATLAS_FLUTTER_NDK_ENV"
```

작업을 끝내면 다음으로 컨테이너를 정리한다.

```powershell
docker compose -f display/atlas/compose.yaml down
```

Docker Desktop이 없는 현재 PC에서는 Compose 검증·이미지 빌드를 실행할 수 없다. Windows Docker Desktop에서 host network 제약이 있으므로 MQTT·실장치 통신 검증은 Raspberry Pi 또는 WSL/Linux에서 수행한다.
