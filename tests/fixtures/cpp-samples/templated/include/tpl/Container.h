#pragma once

#include <cstddef>
#include <vector>

namespace tpl {

template <typename T>
class Container {
public:
    void add(T const& value) { data_.push_back(value); }
    std::size_t size() const noexcept { return data_.size(); }
    T const& at(std::size_t i) const { return data_.at(i); }

private:
    std::vector<T> data_;
};

template <typename T, typename U>
auto pair_sum(T a, U b) -> decltype(a + b) {
    return a + b;
}

}  // namespace tpl
