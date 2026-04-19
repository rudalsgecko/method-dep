#pragma once

#include "foo/IService.h"

namespace foo {

struct Config {
    int id = 0;
    std::string tag;
};

struct Input {
    int value;
};

class Bar {
public:
    Bar(IService& svc);
    bool doWork(Config const& cfg, Input* in);

private:
    IService& svc_;
};

}  // namespace foo
