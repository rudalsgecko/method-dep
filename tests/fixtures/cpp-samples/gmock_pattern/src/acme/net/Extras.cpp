#include "net/IClient.h"

#include <string>

namespace net {

// @methoddep:expect
//   classes: net::IClient
//   calls: net::IClient::connect
bool try_once(IClient& c, std::string const& host) {
    return c.connect(host, 80);
}

// @methoddep:expect
//   classes: net::IClient
//   calls: net::IClient::connect; net::IClient::send; net::IClient::disconnect
int send_and_close(IClient& c, std::string const& host, std::string const& payload) {
    if (!c.connect(host, 443)) {
        return -1;
    }
    int written = c.send(payload);
    c.disconnect();
    return written;
}

}  // namespace net
