cmake_minimum_required(VERSION 3.15)
project(cycflow_py LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# -----------------------------------------------------------------------------
# pybind11
# -----------------------------------------------------------------------------
set(PYBIND11_FINDPYTHON NEW)

include(FetchContent)
FetchContent_Declare(
  pybind11
  GIT_REPOSITORY https://github.com/pybind/pybind11.git
  GIT_TAG        v3.0.4
)
FetchContent_MakeAvailable(pybind11)

# -----------------------------------------------------------------------------
# CycFlow (develop branch) — provides CycFlow and asio::asio.
# BUILD_TESTING=OFF suppresses Qt-dependent test targets.
# -----------------------------------------------------------------------------
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
  cycflow
  GIT_REPOSITORY https://github.com/griglapidus/CycFlow.git
  GIT_TAG        v0.3.0
)
FetchContent_MakeAvailable(cycflow)

# -----------------------------------------------------------------------------
# Python module
# -----------------------------------------------------------------------------
pybind11_add_module(_cycflow
  src/module.cpp
  src/bind_core.cpp
  src/bind_buffer.cpp
  src/bind_writer.cpp
  src/bind_consumer.cpp
  src/bind_tcp.cpp
  src/bind_server.cpp
  src/bind_cbf.cpp
  src/numpy_bridge.cpp
)

target_include_directories(_cycflow PRIVATE
  ${cycflow_SOURCE_DIR}/CycLib
  src
)

target_link_libraries(_cycflow PRIVATE CycLib)

install(TARGETS _cycflow LIBRARY DESTINATION cycflow)
