#
# Generated file, do not edit.
#

list(APPEND FLUTTER_PLUGIN_LIST
  audioplayers_atlas
)

list(APPEND FLUTTER_FFI_PLUGIN_LIST
)

set(PLUGIN_BUNDLED_LIBRARIES)
set(PLATFORM_PLUGIN_BUNDLED_LIBRARIES)
set(APP_TYPE "third-party")  # Possible values: "system", "third-party"

  set(lib_glob_path "${CMAKE_SYSROOT}/${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}/libaudioplayers_atlas_plugin.so*")
  file(GLOB lib_files "${lib_glob_path}")
  message(STATUS "Found platforms plugin libraries: ${lib_files}")

  set(lib_path "${CMAKE_SYSROOT}/${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_LIBDIR}/libaudioplayers_atlas_plugin.so.1")
  set(include_path "${CMAKE_CURRENT_SOURCE_DIR}/flutter/ephemeral/.plugin_symlinks/audioplayers_atlas/atlas/include/audioplayers_atlas/audioplayers_atlas_plugin.h")
  if(EXISTS "${lib_path}" AND EXISTS "${include_path}")
    message(STATUS "Using existing sysroot library and header for plugin: audioplayers_atlas")
    target_include_directories(${BINARY_NAME} PRIVATE flutter/ephemeral/.plugin_symlinks/audioplayers_atlas/atlas/include)
    target_link_libraries(${BINARY_NAME} PRIVATE audioplayers_atlas_plugin)
    if(NOT APP_TYPE STREQUAL "system")
      list(APPEND PLATFORM_PLUGIN_BUNDLED_LIBRARIES ${lib_files})
    endif()
  else()
    message(STATUS "Building plugin from source: audioplayers_atlas")
    add_subdirectory("flutter/ephemeral/.plugin_symlinks/audioplayers_atlas/atlas" "plugins/audioplayers_atlas")
    target_link_libraries(${BINARY_NAME} PRIVATE audioplayers_atlas_plugin)
    list(APPEND PLUGIN_BUNDLED_LIBRARIES
      $<TARGET_FILE:audioplayers_atlas_plugin>
      $<TARGET_SONAME_FILE:audioplayers_atlas_plugin>
      $<TARGET_LINKER_FILE:audioplayers_atlas_plugin>
    )
  endif()
foreach(ffi_plugin ${FLUTTER_FFI_PLUGIN_LIST})
  add_subdirectory(flutter/ephemeral/.plugin_symlinks/${ffi_plugin}/atlas plugins/${ffi_plugin})
  list(APPEND PLUGIN_BUNDLED_LIBRARIES ${${ffi_plugin}_bundled_libraries})
endforeach()
