#include "net/IClient.h"

#include <string>

namespace net {

class Session {
public:
    Session(IClient& c) : client_(c) {}

    // @methoddep:expect
    //   classes: net::IClient
    //   calls: net::IClient::connect; net::IClient::disconnect
    bool run(std::string const& host, int port) {
        if (!client_.connect(host, port)) {
            return false;
        }
        client_.disconnect();
        return true;
    }

    // @methoddep:expect
    //   classes: net::IClient
    //   calls: net::IClient::send
    int send_payload(std::string const& data) {
        return client_.send(data);
    }

private:
    IClient& client_;
};

// @methoddep:expect
//   classes:
int compute_timeout(int base, int multiplier) {
    return base * multiplier;
}

}  // namespace net
