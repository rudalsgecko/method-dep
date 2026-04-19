#pragma once

#include <string>

namespace net {

class IClient {
public:
    virtual ~IClient() = default;
    virtual bool connect(std::string const& host, int port) = 0;
    virtual int send(std::string const& data) = 0;
    virtual void disconnect() = 0;
};

}  // namespace net
