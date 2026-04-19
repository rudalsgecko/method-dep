#include "pch.h"  // MSVC would consume this via /Yu
#include "pch/Service.h"

namespace pchp {

std::string Service::greet(std::string const& name) const {
    return std::string("hi, ") + name;
}

std::vector<int> Service::counts() const {
    return {1, 2, 3};
}

}  // namespace pchp
