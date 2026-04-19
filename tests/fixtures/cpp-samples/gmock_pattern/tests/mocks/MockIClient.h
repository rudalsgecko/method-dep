#pragma once

#include <gmock/gmock.h>

#include "net/IClient.h"

namespace test {

// Real mock inheriting from the interface — should be resolved.
class MockIClient : public net::IClient {
public:
    MOCK_METHOD(bool, connect, (std::string const& host, int port), (override));
    MOCK_METHOD(int, send, (std::string const& data), (override));
    MOCK_METHOD(void, disconnect, (), (override));
};

// Decoy: name matches the pattern but does NOT inherit — must be
// rejected by the resolver (false-positive guard).
class MockIClientV2 {
public:
    bool connect(std::string const& host, int port);
};

}  // namespace test
