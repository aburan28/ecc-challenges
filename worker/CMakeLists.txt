# Cross-platform build of the generic engine.
#
# Linux, macOS and Windows on x86-64 and arm64. Every build has the CPU
# backend; macOS builds add Metal (Apple silicon and Intel Macs), and a CUDA
# engine is built where the CUDA toolkit is found.
#
#   cmake -S worker -B build
#   cmake --build build --config Release
#   ctest --test-dir build -C Release --output-on-failure
#
# Options: -DECC_ENABLE_METAL=OFF, -DECC_ENABLE_CUDA=ON|OFF|AUTO,
# -DECC_CUDA_ARCHITECTURES=86 and -DECC_ENABLE_OPENMP=ON|OFF. Without OpenMP
# (Apple Clang has none; off by default for MSVC) the CPU backend uses
# std::thread and still honors OMP_NUM_THREADS. On macOS, -DCMAKE_OSX_ARCHITECTURES="arm64;x86_64" builds
# a universal binary.
cmake_minimum_required(VERSION 3.21)
project(ecc_generic_engine LANGUAGES CXX)

# MSVC's OpenMP needs vcomp140.dll at run time; std::thread does not.
if(MSVC)
  set(_ecc_openmp_default OFF)
else()
  set(_ecc_openmp_default ON)
endif()
option(ECC_ENABLE_OPENMP "Use OpenMP for the CPU backend if available"
       ${_ecc_openmp_default})
option(ECC_ENABLE_METAL "Build the Metal backend (macOS only)" ${APPLE})
set(ECC_ENABLE_CUDA AUTO CACHE STRING "Build the CUDA engine: ON, OFF or AUTO")
if(CMAKE_VERSION VERSION_LESS 3.24)
  set(_ecc_cuda_default "75;80;86;89;90")
else()
  set(_ecc_cuda_default native)
endif()
set(ECC_CUDA_ARCHITECTURES ${_ecc_cuda_default} CACHE STRING
    "CUDA architectures for the CUDA engine")

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
# A static C++ runtime keeps the Windows engine a single copyable file.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

set(ECC_SOURCE ${CMAKE_CURRENT_SOURCE_DIR}/generic)
set(ECC_GENERATED ${CMAKE_CURRENT_BINARY_DIR}/generated)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
             ${ECC_SOURCE}/ec_core.h ${ECC_SOURCE}/fast131.h)

function(ecc_platform_settings target)
  if(MSVC)
    # The manifest makes argv and file paths UTF-8, so state directories
    # under non-ASCII user names work.
    target_sources(${target} PRIVATE ${ECC_SOURCE}/engine.manifest)
    target_compile_definitions(${target} PRIVATE _CRT_SECURE_NO_WARNINGS)
  endif()
endfunction()

if(APPLE AND ECC_ENABLE_METAL)
  # The kernel is compiled at run time from ec_core.h, embedded here.
  file(READ ${ECC_SOURCE}/ec_core.h _ecc_core)
  file(READ ${ECC_SOURCE}/fast131.h _ecc_fast)
  file(WRITE ${ECC_GENERATED}/metal_core_source.inc
       "static const char core_source[] = R\"ECCMSL(\n${_ecc_core})ECCMSL\";\n"
       "static const char fast_source[] = R\"ECCMSL(\n${_ecc_fast})ECCMSL\";\n")
  # Clang compiles .mm as Objective-C++; CMake treats it as a CXX source, so
  # the OpenMP flags below apply to it too.
  add_executable(generic-engine ${ECC_SOURCE}/engine_metal.mm)
  target_include_directories(generic-engine PRIVATE ${ECC_GENERATED})
  target_compile_options(generic-engine PRIVATE -fobjc-arc)
  target_link_libraries(generic-engine PRIVATE
                        "-framework Metal" "-framework Foundation")
else()
  file(WRITE ${ECC_GENERATED}/engine_cpu.cpp "#include \"engine.cu\"\n")
  add_executable(generic-engine ${ECC_GENERATED}/engine_cpu.cpp)
  target_include_directories(generic-engine PRIVATE ${ECC_SOURCE})
endif()
ecc_platform_settings(generic-engine)

# fast131.h selects carry-less multiplication at runtime. Keep the rest of
# the binary on the platform baseline so it also runs without that extension.

if(ECC_ENABLE_OPENMP)
  find_package(OpenMP COMPONENTS CXX)
  if(OpenMP_CXX_FOUND)
    target_link_libraries(generic-engine PRIVATE OpenMP::OpenMP_CXX)
  else()
    message(STATUS "OpenMP not found; the CPU backend uses std::thread")
  endif()
endif()
find_package(Threads REQUIRED)
target_link_libraries(generic-engine PRIVATE Threads::Threads)

set(_ecc_targets generic-engine)
if(NOT ECC_ENABLE_CUDA STREQUAL "OFF")
  include(CheckLanguage)
  check_language(CUDA)
  if(CMAKE_CUDA_COMPILER)
    enable_language(CUDA)
    add_executable(generic-engine-cuda ${ECC_SOURCE}/engine.cu)
    set_target_properties(generic-engine-cuda PROPERTIES
      CUDA_STANDARD 17 CUDA_STANDARD_REQUIRED ON
      CUDA_ARCHITECTURES "${ECC_CUDA_ARCHITECTURES}")
    target_link_libraries(generic-engine-cuda PRIVATE Threads::Threads)
    ecc_platform_settings(generic-engine-cuda)
    list(APPEND _ecc_targets generic-engine-cuda)
  elseif(ECC_ENABLE_CUDA STREQUAL "ON")
    message(FATAL_ERROR "ECC_ENABLE_CUDA=ON but no CUDA compiler was found")
  endif()
endif()

install(TARGETS ${_ecc_targets} RUNTIME DESTINATION bin)

enable_testing()
foreach(target IN LISTS _ecc_targets)
  add_test(NAME ${target}-self-test COMMAND ${target} --self-test)
  add_test(NAME ${target}-probe COMMAND ${target} --probe)
endforeach()
