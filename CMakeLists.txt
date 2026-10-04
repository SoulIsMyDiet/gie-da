cmake_minimum_required(VERSION 4.3)
project(gieda)

set(CMAKE_CXX_STANDARD 20)

add_executable(gieda WIN32 main.cpp AudioSignal.cpp)
set_property(TARGET gieda PROPERTY CXX_SCAN_FOR_MODULES OFF)
target_link_libraries(gieda PRIVATE winmm)
