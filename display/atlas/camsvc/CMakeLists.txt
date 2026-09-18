cmake_minimum_required(VERSION 3.14)
project(deskmate_camsvc)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Threads REQUIRED)

# 보드 이미지에 libtensorflowlite.so 가 있고 SDK sysroot 에 헤더가 있다.
# pkg-config 로 찾는 것이 ecoai 예제 방식이지만 ARC 빌드 환경에는
# PKG_CONFIG_PATH 가 안 깔려 있다. 툴체인이 이미 --sysroot 로 컴파일하므로
# pkg-config 가 있으면 쓰고, 없으면 이름만 걸어도 sysroot 에서 찾는다.
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(TFLITE QUIET tensorflowlite)
    if(NOT TFLITE_FOUND)
        pkg_check_modules(TFLITE QUIET tensorflow2-lite)
    endif()
endif()
if(NOT TFLITE_LIBRARIES)
    set(TFLITE_LIBRARIES tensorflowlite)
endif()

# 호스트(x86)에는 TFLite 가 없다. 그래도 `cmake -DBUILD_TESTING=ON && ctest` 로
# 모델을 안 쓰는 부분은 검사할 수 있어야 하므로, 헤더가 없으면 서비스 본체만 건너뛴다.
find_file(CAMSVC_TFLITE_HEADER tensorflow/lite/interpreter.h
    HINTS ${TFLITE_INCLUDE_DIRS})
if(CAMSVC_TFLITE_HEADER)
    set(CAMSVC_BUILD_SERVICE ON)
else()
    set(CAMSVC_BUILD_SERVICE OFF)
    message(WARNING "TensorFlow Lite 헤더가 없어 서비스는 빌드하지 않고 테스트만 만듭니다")
endif()

set(CAMSVC_SOURCES
    src/main.cpp
    src/pose_geometry.cpp
    src/pose_model.cpp
    src/posture_pose.cpp
    src/service_api.cpp
    src/vision_link.cpp)

if(CAMSVC_BUILD_SERVICE)
    add_executable(${PROJECT_NAME} ${CAMSVC_SOURCES})
    target_include_directories(${PROJECT_NAME} PRIVATE ${TFLITE_INCLUDE_DIRS})
    target_link_directories(${PROJECT_NAME} PRIVATE ${TFLITE_LIBRARY_DIRS})
    target_link_libraries(${PROJECT_NAME} PRIVATE ${TFLITE_LIBRARIES} Threads::Threads)
endif()

# 호스트 테스트는 TFLite 없이 도는 부분만 본다. 모델을 태우는 부분은 보드에서
# `--replay` 로 MediaPipe 정답지와 맞춰 본다.
include(CTest)
if(BUILD_TESTING)
    add_executable(camsvc_posture_pose_host_test
        src/posture_pose.cpp src/posture_pose_host_test.cpp)
    add_test(NAME camsvc_posture_pose_host_test
        COMMAND camsvc_posture_pose_host_test
            ${CMAKE_CURRENT_SOURCE_DIR}/test/pose_golden.txt)

    add_executable(camsvc_vision_link_host_test
        src/vision_link.cpp src/vision_link_host_test.cpp)
    add_test(NAME camsvc_vision_link_host_test COMMAND camsvc_vision_link_host_test)

    add_executable(camsvc_pose_geometry_host_test
        src/pose_geometry.cpp src/pose_geometry_host_test.cpp)
    add_test(NAME camsvc_pose_geometry_host_test COMMAND camsvc_pose_geometry_host_test)
endif()

if(CAMSVC_BUILD_SERVICE)
    set(SERVICE_ID com.deskmate.camsvc1)
    set(SERVICE_INSTALL_DIR ${CMAKE_INSTALL_PREFIX}/services/${SERVICE_ID})

    install(TARGETS ${PROJECT_NAME} DESTINATION ${SERVICE_INSTALL_DIR})
    install(FILES files/serviceinfo.json files/.apptype DESTINATION ${SERVICE_INSTALL_DIR})
    install(FILES files/dbus/${SERVICE_ID}.conf
        DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system.d)
    install(FILES files/dbus/${SERVICE_ID}.service
        DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system-services)
endif()

# 모델 두 개는 IPK 에 안 들어간다 - ARC 0.5 는 서비스 실행 파일 하나만 담고,
# 팀 규약상 학습 모델은 커밋하지 않는다. 설치 뒤 `tools/push-models.sh` 로
# 보드의 서비스 폴더에 따로 넣는다.
