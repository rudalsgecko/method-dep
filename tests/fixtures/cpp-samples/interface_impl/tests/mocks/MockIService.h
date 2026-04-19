#pragma once

#include <gmock/gmock.h>

#include "foo/IService.h"

namespace test {

class MockIService : public foo::IService {
public:
    MOCK_METHOD(bool, fetch, (std::string const& key), (override));
    MOCK_METHOD(void, commit, (int n), (override));
};

}  // namespace test
