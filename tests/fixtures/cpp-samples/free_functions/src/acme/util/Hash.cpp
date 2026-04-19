#include "util/Hash.h"

namespace util {

// @methoddep:expect
//   classes:
std::size_t hash(std::string_view s) {
    std::size_t h = 1469598103934665603ULL;
    for (char c : s) {
        h ^= static_cast<unsigned char>(c);
        h *= 1099511628211ULL;
    }
    return h;
}

// @methoddep:expect
//   classes:
//   cc_max: 3
int clamp(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

}  // namespace util
