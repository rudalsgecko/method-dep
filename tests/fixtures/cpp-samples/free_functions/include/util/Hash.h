#pragma once

#include <cstddef>
#include <string_view>

namespace util {

std::size_t hash(std::string_view s);
int clamp(int v, int lo, int hi);

}  // namespace util
