#pragma once

// NOTE: This header intentionally relies on <string>/<vector> already
// being available via the precompiled header. Non-PCH builds must add
// them back via -include pch.h for libclang.

namespace pchp {

class Service {
public:
    std::string greet(std::string const& name) const;
    std::vector<int> counts() const;
};

}  // namespace pchp
