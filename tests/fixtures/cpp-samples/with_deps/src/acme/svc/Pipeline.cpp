#include "svc/Pipeline.h"

namespace svc {

int g_processed = 0;

// @methoddep:expect
//   classes:
Pipeline::Pipeline(Cache& c, Reporter& r) : cache_(c), reporter_(r) {}

// @methoddep:expect
//   classes: svc::Cache; svc::Reporter
//   calls: svc::Cache::has; svc::Cache::put; svc::Reporter::log
//   globals_read: svc::g_processed
//   static_locals: seen
Status Pipeline::process(std::vector<Packet> const& batch) {
    static std::map<int, int> seen;
    if (batch.empty()) {
        reporter_.log("empty batch");
        return Status::OK;
    }
    for (auto const& p : batch) {
        if (cache_.has(p.id)) {
            seen[p.id] += 1;
            continue;
        }
        cache_.put(p.id, p.payload);
        ++g_processed;
    }
    if (g_processed > 1000) {
        reporter_.log("high water mark");
        return Status::Retry;
    }
    return Status::OK;
}

}  // namespace svc
