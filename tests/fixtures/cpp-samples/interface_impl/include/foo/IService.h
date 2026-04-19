#pragma once

#include <string>

namespace foo {

class IService {
public:
    virtual ~IService() = default;
    virtual bool fetch(std::string const& key) = 0;
    virtual void commit(int n) = 0;
};

}  // namespace foo
