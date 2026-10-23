cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico2_w CACHE STRING "Board type")
include(pico_sdk_import.cmake)

project(deskmate_ble_fan C CXX ASM)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(deskmate_ble_fan
    src/main.c
    src/fan_core.c
)
target_include_directories(deskmate_ble_fan PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src) # btstack_config.h
target_compile_options(deskmate_ble_fan PRIVATE -Wall -Wextra)
target_compile_definitions(deskmate_ble_fan PRIVATE CYW43_LWIP=0) # BLE only, no Wi-Fi stack
target_link_libraries(deskmate_ble_fan
    pico_stdlib
    hardware_pwm
    hardware_watchdog
    pico_btstack_ble
    pico_btstack_cyw43
    pico_cyw43_arch_poll
)
pico_btstack_make_gatt_header(deskmate_ble_fan PRIVATE "${CMAKE_CURRENT_LIST_DIR}/src/deskmate_fan.gatt")

pico_set_program_name(deskmate_ble_fan "DESKMATE BLE fan")
pico_enable_stdio_usb(deskmate_ble_fan 1)
pico_enable_stdio_uart(deskmate_ble_fan 0)
pico_add_extra_outputs(deskmate_ble_fan)
