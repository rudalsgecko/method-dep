#include "foo/Bar.h"

namespace foo {

// @methoddep:expect
//   classes:
Bar::Bar(IService& svc) : svc_(svc) {}

// @methoddep:expect
//   classes: foo::IService
//   calls: foo::IService::fetch; foo::IService::commit
bool Bar::doWork(Config const& cfg, Input* in) {
    if (!in) {
        return false;
    }
    if (!svc_.fetch(cfg.tag)) {
        return false;
    }
    svc_.commit(in->value + cfg.id);
    return true;
}

}  // namespace foo
