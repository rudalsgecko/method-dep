// methoddep: generated-tests harness entry point.
// All real tests live under gen/*.cpp and are discovered via CMake GLOB_RECURSE.
#include <gtest/gtest.h>
#include <gmock/gmock.h>

int main(int argc, char** argv) {
    ::testing::InitGoogleMock(&argc, argv);
    return RUN_ALL_TESTS();
}
