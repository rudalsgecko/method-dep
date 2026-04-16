#include "calculator.h"
#include <sstream>
#include <cmath>

namespace math {

Calculator::Calculator(ILogger* logger)
    : logger_(logger), operation_count_(0) {}

Result Calculator::compute(Operation op, double a, double b) {
    Result result;
    result.success = true;

    switch (op) {
        case Operation::ADD:
            result.value = a + b;
            result.description = "addition";
            break;
        case Operation::SUBTRACT:
            result.value = a - b;
            result.description = "subtraction";
            break;
        case Operation::MULTIPLY:
            result.value = a * b;
            result.description = "multiplication";
            break;
        case Operation::DIVIDE:
            if (std::abs(b) < 1e-10) {
                result.value = 0;
                result.description = "division by zero";
                result.success = false;
                return result;
            }
            result.value = a / b;
            result.description = "division";
            break;
    }

    operation_count_++;
    if (logger_) {
        logger_->log("Operation: " + result.description);
    }

    return result;
}

double Calculator::accumulate(const double* values, int count) {
    if (values == nullptr || count <= 0) {
        return 0.0;
    }

    double sum = 0.0;
    for (int i = 0; i < count; i++) {
        sum += values[i];
    }

    if (logger_) {
        std::ostringstream oss;
        oss << "Accumulated " << count << " values, sum = " << sum;
        logger_->log(oss.str());
    }

    return sum;
}

std::string Calculator::formatResult(const Result& result) {
    std::ostringstream oss;
    if (result.success) {
        oss << "Result of " << result.description << ": " << result.value;
    } else {
        oss << "Error: " << result.description;
    }
    return oss.str();
}

} // namespace math
