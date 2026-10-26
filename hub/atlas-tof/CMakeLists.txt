cmake_minimum_required(VERSION 3.14)
project(deskmate_tofposture)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Threads REQUIRED)

# ATLAS 는 D-Bus activation 으로 서비스를 띄운다. hub/atlas 처럼 sdbus-c++ 가 있으면 이름을 가진다.
# 없어도(호스트 빌드) 서비스는 그대로 돈다 — camsvc 도 sdbus 없이 Pi 5 에서 동작했다.
find_package(sdbus-c++ 2.0 QUIET)

set(TOFPOSTURE_CORE src/tof_line.cpp src/device_posture.cpp src/placement.cpp src/baseline.cpp)

add_executable(${PROJECT_NAME} src/main.cpp src/line_source.cpp src/http_server.cpp ${TOFPOSTURE_CORE})
target_link_libraries(${PROJECT_NAME} PRIVATE Threads::Threads)
if(sdbus-c++_FOUND)
    target_compile_definitions(${PROJECT_NAME} PRIVATE TOFPOSTURE_HAVE_SDBUS=1)
    target_link_libraries(${PROJECT_NAME} PRIVATE SDBusCpp::sdbus-c++)
    message(STATUS "tofposture: sdbus-c++ found, D-Bus name ownership enabled")
else()
    message(STATUS "tofposture: sdbus-c++ not found, building without D-Bus (host build)")
endif()

# 호스트 테스트: 보드 없이 파서·모델·리플레이를 검사. 모델·골든 파일은 커밋하지 않으므로
# test/ 에 export 산출물(posture_net.bin, golden.txt, 선택: replay_*.jsonl)을 복사해 둔 뒤 ctest.
include(CTest)
if(BUILD_TESTING)
    add_executable(tofposture_host_test src/host_test.cpp src/http_server.cpp ${TOFPOSTURE_CORE})
    target_link_libraries(tofposture_host_test PRIVATE Threads::Threads)
    set(TOFPOSTURE_TEST_DIR ${CMAKE_CURRENT_SOURCE_DIR}/test)
    file(GLOB TOFPOSTURE_REPLAY ${TOFPOSTURE_TEST_DIR}/replay_*.jsonl)
    list(SORT TOFPOSTURE_REPLAY)
    # 실측 녹화 리플레이(record_board.py 의 rec_toflines.jsonl + 실제 시각 라벨) — 게이트 전/후 라벨 분포. 호스트 전용 도구
    add_executable(tofposture_real_replay src/real_replay.cpp ${TOFPOSTURE_CORE})
    target_link_libraries(tofposture_real_replay PRIVATE Threads::Threads)
    add_test(NAME tofposture_host_test
        COMMAND tofposture_host_test ${TOFPOSTURE_TEST_DIR}/posture_net.bin ${TOFPOSTURE_TEST_DIR}/golden.txt
                ${TOFPOSTURE_REPLAY})
endif()

set(SERVICE_ID com.deskmate.tofposture1)
set(SERVICE_INSTALL_DIR ${CMAKE_INSTALL_PREFIX}/services/${SERVICE_ID})

install(TARGETS ${PROJECT_NAME} DESTINATION ${SERVICE_INSTALL_DIR})
install(FILES files/serviceinfo.json files/.apptype DESTINATION ${SERVICE_INSTALL_DIR})
install(FILES files/dbus/${SERVICE_ID}.conf
    DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system.d)
install(FILES files/dbus/${SERVICE_ID}.service
    DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system-services)

# 모델(posture_net.bin)은 IPK 에 넣지 않는다 — camsvc 와 같은 이유(ARC 0.5 는 실행 파일 하나,
# 학습 모델은 커밋하지 않음). 설치 뒤 tools/push-model.sh 로 보드의 서비스 폴더 models/ 에 넣는다.
