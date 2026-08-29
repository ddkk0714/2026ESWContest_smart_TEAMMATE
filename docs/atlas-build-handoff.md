# Atlas IPK 빌드 재시작 인계

> 작성: 2026-08-29, Windows 재부팅 직전
> 재시작 문구: `docs/atlas-build-handoff.md와 AGENTS.md를 읽고 Atlas IPK 빌드를 이어서 진행해줘.`

## 완료된 작업

- 실제 개발 흐름을 `개발 PC Docker → arm64 .ipk → SSH → Pi 5 AI Native OS 설치·실행 → 로그 기반 수정`으로 문서화했다.
- 문서 커밋 `af23144`를 `origin/feat/atlas-display-env`에 푸시했다.
- LG 공식 Atlas SDK 자산을 Git 제외 경로 `display/atlas/vendor/`에 import하고 검증했다.
- Windows 기능 `Microsoft-Windows-Subsystem-Linux`, `VirtualMachinePlatform`을 활성화했다.
- Docker Desktop 공식 설치 파일을 내려받고 Docker Inc Authenticode 서명이 `Valid`임을 확인했다.
- Docker Desktop 프로그램을 `D:\SW임베디드경진대회_LG\Toolchains\DockerDesktop`에 설치했다.
- Docker WSL 데이터 경로는 `D:\SW임베디드경진대회_LG\Toolchains\DockerData`로 지정했다.

## 현재 중단 지점

- Windows 기능 적용을 위한 **재부팅 직전**이다.
- `.ipk`는 아직 생성하지 않았다.
- Pi 5 SSH 장치 등록과 실기 로그 확인도 아직 하지 않았다.
- 설치 파일 SHA-256:
  `89FE3D80A326A2AD521DE09B5A89EF04D10C60593604B344F11F433CA7F1F6F0`

## 재부팅 직후 할 일

1. Docker Desktop을 한 번 실행하고 WSL2 backend 초기화를 완료한다.
2. Docker CLI가 PATH에 없다면 아래 세션 경로를 임시로 추가한다.

   ```powershell
   $env:PATH = 'D:\SW임베디드경진대회_LG\Toolchains\DockerDesktop\resources\bin;' + $env:PATH
   ```

3. 설치 상태를 확인한다.

   ```powershell
   wsl --status
   docker version
   docker compose version
   PowerShell -ExecutionPolicy Bypass -File .\display\atlas\scripts\verify-atlas-vendor.ps1
   ```

4. Atlas 개발 이미지를 빌드한다.

   ```powershell
   docker compose -f display/atlas/compose.yaml build atlas-dev
   docker compose -f display/atlas/compose.yaml up -d
   docker compose -f display/atlas/compose.yaml exec atlas-dev bash
   ```

5. 컨테이너에서 우선 Pi 4 URL 없이 내장 데모 release `.ipk`를 만든다.

   ```bash
   source "$ATLAS_FLUTTER_NDK_ENV"
   cd /workspace/display/atlas/app
   flutter pub get
   flutter test
   flutter-atlas build atlas --ipk --release
   find build -type f -name '*.ipk' -print
   ```

6. Pi 4 IP가 정해지면 `DESKMATE_HUB_URL`을 넣어 다시 빌드한다.
7. Pi 5의 IP·SSH 포트·인증 방식이 준비되면 `flutter-atlas custom-devices add` 후 `flutter-atlas run`으로 설치·실행한다.

## 사용자에게 받을 정보

Pi 5 실기 배포 직전 다음 정보가 필요하다. 자격증명이나 개인키 내용은 채팅·Git에 넣지 않는다.

- Pi 5 IP 주소
- SSH 포트(기본 22인지)
- SSH 사용자명 및 비밀번호/개인키 중 인증 방식
- Pi 4 IP 주소 또는 우선 내장 데모만 시험할지 여부
