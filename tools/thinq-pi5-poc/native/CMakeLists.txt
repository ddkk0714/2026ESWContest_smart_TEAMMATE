cmake_minimum_required(VERSION 3.16)
project(deskmate_thinq_poc LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
find_package(sdbus-c++ 2.0 REQUIRED)
find_package(CURL REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(Threads REQUIRED)
find_package(Python3 COMPONENTS Interpreter REQUIRED)
set(POC_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/..)
add_custom_command(OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/embedded.h
  COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/embed.py ${CMAKE_CURRENT_BINARY_DIR}/embedded.h
  DEPENDS embed.py ${POC_ROOT}/poc/session.py ${POC_ROOT}/poc/worker.py ${POC_ROOT}/poc/fixture.json
    ${POC_ROOT}/tools/thinq_connect/probe.py ${POC_ROOT}/tools/thinq_connect/manual_trial.py
    ${POC_ROOT}/tools/thinq_connect/air_quality.py)
add_custom_target(embed_worker DEPENDS ${CMAKE_CURRENT_BINARY_DIR}/embedded.h)
add_executable(${PROJECT_NAME} src/main.cpp src/https.cpp)
add_dependencies(${PROJECT_NAME} embed_worker)
target_include_directories(${PROJECT_NAME} PRIVATE ${CMAKE_CURRENT_BINARY_DIR} src)
target_compile_options(${PROJECT_NAME} PRIVATE -Wall -Wextra -Werror)
target_link_libraries(${PROJECT_NAME} PRIVATE SDBusCpp::sdbus-c++ CURL::libcurl OpenSSL::Crypto Threads::Threads)
install(TARGETS ${PROJECT_NAME} DESTINATION ${CMAKE_INSTALL_PREFIX}/services/com.deskmate.thinqpoc1)
install(FILES files/serviceinfo.json files/.apptype DESTINATION ${CMAKE_INSTALL_PREFIX}/services/com.deskmate.thinqpoc1)
install(FILES files/dbus/com.deskmate.thinqpoc1.service DESTINATION ${CMAKE_INSTALL_PREFIX}/share/dbus-1/system-services)
# No guessed UID, wildcard own policy, privileged borrowed groups or AppArmor file.
