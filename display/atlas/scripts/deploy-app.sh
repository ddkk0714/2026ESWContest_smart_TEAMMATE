#!/usr/bin/env bash
# Atlas Flutter 앱을 ipk 로 빌드해 Pi 5 에 올린다. atlas-dev 컨테이너 안에서 실행한다.
#
#   bash /workspace/display/atlas/scripts/deploy-app.sh            # 테스트 + 빌드 + 설치 + 실행
#   bash /workspace/display/atlas/scripts/deploy-app.sh --install  # 설치까지만
#   bash /workspace/display/atlas/scripts/deploy-app.sh --build    # ipk 만 만든다
#   bash /workspace/display/atlas/scripts/deploy-app.sh --uninstall
#   bash /workspace/display/atlas/scripts/deploy-app.sh --mode debug --no-test
#   bash /workspace/display/atlas/scripts/deploy-app.sh --app-dir display/atlas/ui_env_app
#
# 대상 장치 · 앱 경로 · Hub URL 은 display/atlas/deploy/device.env 에서 읽는다.
set -euo pipefail

. "$(dirname "$0")/_device-env.sh"

action=run
mode=$ATLAS_BUILD_MODE
run_tests=1
force_clean=0
while [ $# -gt 0 ]; do
    case "$1" in
        --run)       action=run ;;
        --install)   action=install ;;
        --build)     action=build ;;
        --uninstall) action=uninstall ;;
        --mode)      shift; [ $# -gt 0 ] || die "--mode 뒤에 값이 없다"; mode=$1 ;;
        --mode=*)    mode=${1#--mode=} ;;
        --app-dir)   shift; [ $# -gt 0 ] || die "--app-dir 뒤에 값이 없다"; ATLAS_APP_DIR=$1 ;;
        --app-dir=*) ATLAS_APP_DIR=${1#--app-dir=} ;;
        --no-test)   run_tests=0 ;;
        --clean)     force_clean=1 ;;
        -h|--help)   sed -n '2,13p' "$0"; exit 0 ;;
        *)           die "알 수 없는 옵션: $1" ;;
    esac
    shift
done

case "$mode" in
    release|debug|profile) ;;
    *) die "빌드 모드는 release · debug · profile 중 하나여야 한다: $mode" ;;
esac

app_dir=$ATLAS_APP_DIR
case "$app_dir" in /*) ;; *) app_dir=$REPO_ROOT/$app_dir ;; esac
[ -d "$app_dir" ] || die "앱 폴더가 없다: $app_dir (device.env 의 ATLAS_APP_DIR 확인. Docker 는 레포 밖 경로를 보지 못한다)"
[ -f "$app_dir/pubspec.yaml" ] || die "Flutter 프로젝트가 아니다: $app_dir"

# ipk 는 atlas 플랫폼 폴더가 있어야 만들어진다. appinfo.json 의 id 가 설치 · 실행 대상이 된다.
if [ ! -f "$app_dir/atlas/meta/appinfo.json" ]; then
    die "atlas 플랫폼 폴더가 없다: $app_dir/atlas/meta/appinfo.json
      컨테이너 안에서 다음을 먼저 실행한다:
        cd $app_dir && flutter-atlas create --platforms atlas ."
fi

# Atlas NDK 환경. 설정 스크립트가 미정의 변수를 참조하므로 -u 를 잠시 푼다.
: "${ATLAS_FLUTTER_NDK_ENV:?ATLAS_FLUTTER_NDK_ENV 가 없다. atlas-dev 컨테이너 안에서 실행하는지 확인한다}"
set +u
# shellcheck disable=SC1090
. "$ATLAS_FLUTTER_NDK_ENV"
set -u

cd "$app_dir"

app_id=$(python3 -c 'import json; print(json.load(open("atlas/meta/appinfo.json", encoding="utf-8"))["id"])')

# Hub URL 은 빌드에 박히므로 값이 바뀌면 다시 빌드해야 한다. 비우면 내장 데모가 순환한다.
defines=()
if [ -n "$ATLAS_HUB_URL" ]; then
    defines+=("--dart-define=DESKMATE_HUB_URL=$ATLAS_HUB_URL")
fi

echo "앱      : $app_id ($app_dir)"
echo "장치    : $ATLAS_DEVICE_ID ($ATLAS_SSH_USER@$ATLAS_DEVICE_IP:$ATLAS_SSH_PORT)"
echo "모드    : $mode"
echo "Hub URL : ${ATLAS_HUB_URL:-(없음 - 내장 데모)}"
echo "동작    : $action"
echo

if [ "$action" = uninstall ]; then
    flutter-atlas install -d "$ATLAS_DEVICE_ID" --uninstall-only "--$mode"
    exit 0
fi

# 같은 작업 트리에서 모드를 바꿔 빌드하면 이전 모드의 bundle 산출물이 ipk 에 섞인다.
stamp=build/.deskmate-build-mode
if [ "$force_clean" -eq 1 ] || { [ -f "$stamp" ] && [ "$(cat "$stamp")" != "$mode" ]; }; then
    echo "이전 빌드 모드와 달라 clean 후 빌드한다."
    flutter-atlas clean
fi

flutter-atlas pub get
if [ "$run_tests" -eq 1 ]; then
    flutter-atlas test
fi

flutter-atlas build atlas --ipk "--$mode" ${defines[@]+"${defines[@]}"}
mkdir -p build && printf '%s\n' "$mode" > "$stamp"

ipk=build/atlas/arm64/$mode/ipk/$app_id.ipk
if [ -f "$ipk" ]; then
    echo "ipk: $app_dir/$ipk ($(du -h "$ipk" | cut -f1))"
else
    echo "warning: 예상 경로에 ipk 가 없다. 실제 산출물은 다음과 같다:" >&2
    find build -type f -name '*.ipk' -print >&2 || true
fi

case "$action" in
    build)   echo "빌드만 수행했다. 장치에 올리려면 --install 또는 --run 으로 다시 실행한다." ;;
    install) flutter-atlas install -d "$ATLAS_DEVICE_ID" "--$mode" ;;
    run)     flutter-atlas run -d "$ATLAS_DEVICE_ID" "--$mode" ${defines[@]+"${defines[@]}"} ;;
esac
