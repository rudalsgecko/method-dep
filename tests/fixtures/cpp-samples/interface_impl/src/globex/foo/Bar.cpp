#include "foo/Bar.h"

namespace foo {

// @methoddep:expect
//   classes:
Bar::Bar(IService& svc) : svc_(svc) {}

// @methoddep:expect
//   classes: foo::IService
//   calls: foo::IService::commit
bool Bar::doWork(Config const& cfg, Input* in) {
    // globex variant: never fetches, always commits.
    if (!in) {
        return false;
    }
    svc_.commit(in->value * 2 + cfg.id);
    return true;
}

}  // namespace foo
