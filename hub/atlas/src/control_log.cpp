#include "control_log.h"

#include <algorithm>

namespace deskmate {

std::string extractCommandId(const std::string& record)
{
    static const std::string key = "\"command_id\"";
    std::size_t at = record.find(key);
    if (at == std::string::npos) return {};
    at = record.find(':', at + key.size());
    if (at == std::string::npos) return {};
    at = record.find_first_not_of(" \t", at + 1);
    if (at == std::string::npos || record[at] != '"') return {};
    std::string value;
    for (std::size_t i = at + 1; i < record.size(); ++i) {
        const char c = record[i];
        if (c == '\\' && i + 1 < record.size()) {
            value += record[++i];
            continue;
        }
        if (c == '"') return value;
        value += c;
    }
    return {};
}

ControlLogBuffer::ControlLogBuffer(std::size_t capacity)
    : capacity_(capacity == 0 ? 1 : capacity)
{
}

bool ControlLogBuffer::add(const std::string& record)
{
    if (record.empty() || record.front() != '{' || record.back() != '}') return false;
    std::string id = extractCommandId(record);
    if (id.empty()) return false;
    std::lock_guard<std::mutex> lock(mutex_);
    records_.erase(std::remove_if(records_.begin(), records_.end(),
                                  [&id](const auto& entry) { return entry.first == id; }),
                   records_.end());
    records_.emplace_back(std::move(id), record);
    while (records_.size() > capacity_) records_.pop_front();
    return true;
}

std::string ControlLogBuffer::toJsonArray(std::size_t limit) const
{
    std::lock_guard<std::mutex> lock(mutex_);
    const std::size_t count = (limit == 0 || limit > records_.size()) ? records_.size() : limit;
    std::string out = "[";
    for (std::size_t i = records_.size() - count; i < records_.size(); ++i) {
        if (out.size() > 1) out += ',';
        out += records_[i].second;
    }
    out += ']';
    return out;
}

std::size_t ControlLogBuffer::size() const
{
    std::lock_guard<std::mutex> lock(mutex_);
    return records_.size();
}

}  // namespace deskmate
