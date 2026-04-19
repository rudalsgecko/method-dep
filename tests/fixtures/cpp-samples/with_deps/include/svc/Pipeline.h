#pragma once

#include <map>
#include <string>
#include <vector>

namespace svc {

enum class Status { OK, Retry, Failed };

struct Packet {
    int id;
    std::string payload;
};

class Cache {
public:
    virtual ~Cache() = default;
    virtual bool has(int id) const = 0;
    virtual void put(int id, std::string const& v) = 0;
};

class Reporter {
public:
    virtual ~Reporter() = default;
    virtual void log(std::string const&) = 0;
};

extern int g_processed;

class Pipeline {
public:
    Pipeline(Cache& c, Reporter& r);
    Status process(std::vector<Packet> const& batch);

private:
    Cache& cache_;
    Reporter& reporter_;
};

}  // namespace svc
