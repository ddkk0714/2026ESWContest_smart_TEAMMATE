cmake_minimum_required(VERSION 3.14)
project(deskmate_hub_service)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(sdbus-c++ 2.0 REQUIRED)
find_package(Threads REQUIRED)

add_executable(${PROJECT_NAME} src/main.cpp)
target_link_libraries(${PROJECT_NAME} PRIVATE SDBusCpp::sdbus-c++ Threads::Threads)

set(SERVICE_ID com.deskmate.hub1)
set(SERVICE_INSTALL_DIR ${CMAKE_INSTALL_PREFIX}/services/${SERVICE_ID})
set(HUB_SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/../deskmate_hub)

# The target image provides a deliberately small Python in /restricted/python3.
# Convert fsm.yaml to JSON at build time and bundle it with the Hub as a zip
# payload appended to the ELF executable. ARC 0.5 packages only one service
# executable, and Python can import the appended zip without extracting files.
execute_process(
    COMMAND /usr/bin/python3 -c "import yaml"
    RESULT_VARIABLE HOST_PYYAML_RESULT
)
if(NOT HOST_PYYAML_RESULT EQUAL 0)
    message(FATAL_ERROR "PyYAML is required in the Atlas build container")
endif()
execute_process(
    COMMAND /usr/bin/python3 -c "import sysconfig; print(sysconfig.get_path('stdlib'))"
    OUTPUT_VARIABLE HOST_STDLIB_DIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
    RESULT_VARIABLE HOST_STDLIB_RESULT
)
if(NOT HOST_STDLIB_RESULT EQUAL 0 OR NOT IS_DIRECTORY "${HOST_STDLIB_DIR}")
    message(FATAL_ERROR "Python standard library was not found in the Atlas build container")
endif()

file(GLOB_RECURSE HUB_PAYLOAD_INPUTS CONFIGURE_DEPENDS
    ${HUB_SOURCE_DIR}/*.py
    ${HUB_SOURCE_DIR}/*.yaml)
set_property(TARGET ${PROJECT_NAME} PROPERTY LINK_DEPENDS "${HUB_PAYLOAD_INPUTS}")
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND /usr/bin/python3 ${CMAKE_CURRENT_SOURCE_DIR}/tools/build_payload.py
        --executable $<TARGET_FILE:${PROJECT_NAME}>
        --hub ${HUB_SOURCE_DIR}
        --config ${HUB_SOURCE_DIR}/config/fsm.yaml
        --stdlib ${HOST_STDLIB_DIR}
    COMMENT "Appending the DESKMATE Python Hub payload")

install(TARGETS ${PROJECT_NAME} DESTINATION ${SERVICE_INSTALL_DIR})
install(FILES files/serviceinfo.json files/.apptype DESTINATION ${SERVICE_INSTALL_DIR})
install(FILES files/dbus/${SERVICE_ID}.conf
    DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system.d)
install(FILES files/dbus/${SERVICE_ID}.service
    DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system-services)
