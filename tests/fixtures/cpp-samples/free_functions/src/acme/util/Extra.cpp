#include "util/Hash.h"

namespace util {

// @methoddep:expect
//   classes:
int double_it(int v) {
    return v + v;
}

// @methoddep:expect
//   classes:
//   cc_max: 3
int sign(int v) {
    if (v > 0) return 1;
    return v < 0 ? -1 : 0;
}

// @methoddep:expect
//   classes:
bool is_empty(std::string_view s) {
    return s.empty();
}

// @methoddep:expect
//   calls: util::sign
int absolute(int v) {
    return sign(v) * v;
}

}  // namespace util
