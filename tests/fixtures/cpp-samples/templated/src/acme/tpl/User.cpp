#include "tpl/Container.h"

#include <string>

namespace tpl {

// @methoddep:expect
//   classes:
int use_container_int() {
    Container<int> c;
    c.add(1);
    c.add(2);
    return static_cast<int>(c.size());
}

// @methoddep:expect
//   classes:
int use_pair_sum() {
    return pair_sum(2, 3);
}

// @methoddep:expect
//   classes:
//   cc_max: 2
std::size_t multiply_limited(std::size_t a, std::size_t b) {
    if (a == 0 || b == 0) return 0;
    return a * b;
}

}  // namespace tpl
