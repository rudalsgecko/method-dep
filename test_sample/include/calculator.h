#pragma once
#include <stdexcept>
#include <string>

namespace math {

enum class Operation {
    ADD,
    SUBTRACT,
    MULTIPLY,
    DIVIDE
};

struct Result {
    double value;
    std::string description;
    bool success;
};

class ILogger {
public:
    virtual ~ILogger() = default;
    virtual void log(const std::string& message) = 0;
    virtual int getLogCount() const = 0;
};

class Calculator {
public:
    Calculator(ILogger* logger = nullptr);

    Result compute(Operation op, double a, double b);
    double accumulate(const double* values, int count);
    std::string formatResult(const Result& result);

private:
    ILogger* logger_;
    int operation_count_;
};

} // namespace math
