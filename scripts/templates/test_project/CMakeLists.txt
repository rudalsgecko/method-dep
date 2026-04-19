cmake_minimum_required(VERSION 3.20)
project(methoddep_generated_tests CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---- Configurable knobs ----
# -DMETHODDEP_SOURCE_ROOT=...  path to the real project source root (e.g. D:/proj/PowerToys)
#                              generated tests may #include project headers relative to this
# -DMETHODDEP_EXTRA_INCLUDES=a;b;c extra include dirs (semicolon-separated)
# -DMETHODDEP_COVERAGE=ON/OFF  enable coverage build flags (clang gcov / msvc-friendly)
# -DMETHODDEP_COMPILE_SOURCES=... semi-colon-separated list of project .cpp files to compile in
#                                 (optional; lets generated tests call into real code without
#                                  setting up a full build of the target project).

set(METHODDEP_SOURCE_ROOT "" CACHE PATH "Project source root the generated tests reference")
set(METHODDEP_EXTRA_INCLUDES "" CACHE STRING "Extra ';'-separated include directories")
set(METHODDEP_COMPILE_SOURCES "" CACHE STRING "Extra ';'-separated project source files")
option(METHODDEP_COVERAGE "Enable coverage instrumentation" OFF)

# ---- GoogleTest / GoogleMock via FetchContent (self-contained build) ----
include(FetchContent)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG        v1.14.0
)
FetchContent_MakeAvailable(googletest)

# ---- Collect generated test sources ----
file(GLOB_RECURSE METHODDEP_GENERATED_SOURCES
    CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/gen/*.cpp"
)

if(NOT METHODDEP_GENERATED_SOURCES)
    message(WARNING "methoddep: no generated tests found under gen/. A placeholder will be used.")
    set(METHODDEP_GENERATED_SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/gen_placeholder.cpp")
    # Placeholder ensures CMake can still configure before the very first generation.
    file(WRITE "${METHODDEP_GENERATED_SOURCES}"
        "// Auto-generated placeholder; overwritten on first real generation.\n"
        "#include <gtest/gtest.h>\n"
        "TEST(MethoddepPlaceholder, Loads) { SUCCEED(); }\n"
    )
endif()

add_executable(test_runner
    ${METHODDEP_GENERATED_SOURCES}
    main.cpp
)

# Extra project sources (the code under test) if caller opted in
if(METHODDEP_COMPILE_SOURCES)
    target_sources(test_runner PRIVATE ${METHODDEP_COMPILE_SOURCES})
endif()

target_link_libraries(test_runner PRIVATE gtest gmock)

if(METHODDEP_SOURCE_ROOT)
    target_include_directories(test_runner PRIVATE "${METHODDEP_SOURCE_ROOT}")
endif()

if(METHODDEP_EXTRA_INCLUDES)
    foreach(inc IN LISTS METHODDEP_EXTRA_INCLUDES)
        if(inc)
            target_include_directories(test_runner PRIVATE "${inc}")
        endif()
    endforeach()
endif()

# ---- Coverage flags ----
if(METHODDEP_COVERAGE)
    if(CMAKE_CXX_COMPILER_ID MATCHES "Clang")
        target_compile_options(test_runner PRIVATE
            -fprofile-instr-generate -fcoverage-mapping -O0 -g)
        target_link_options(test_runner PRIVATE
            -fprofile-instr-generate -fcoverage-mapping)
        message(STATUS "methoddep: Clang source-based coverage enabled (llvm-cov / llvm-profdata).")
    elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
        target_compile_options(test_runner PRIVATE --coverage -O0 -g)
        target_link_options(test_runner PRIVATE --coverage)
        message(STATUS "methoddep: GCC gcov coverage enabled.")
    elseif(MSVC)
        # MSVC has no native coverage flag; rely on OpenCppCoverage at runtime.
        target_compile_options(test_runner PRIVATE /Od /Zi)
        message(STATUS "methoddep: MSVC build; run through OpenCppCoverage for coverage data.")
    endif()
endif()

enable_testing()
add_test(NAME methoddep_generated COMMAND test_runner)
