#include "model/relative_time.h"

#include <cstdio>

namespace observer {
namespace {

std::string unit(int64_t value, char suffix) {
  char buf[16];
  snprintf(buf, sizeof(buf), "%lld%c ago", static_cast<long long>(value), suffix);
  return std::string(buf);
}

constexpr int64_t kMinute = 60;
constexpr int64_t kHour = 60 * kMinute;
constexpr int64_t kDay = 24 * kHour;
constexpr int64_t kMaxDays = 99;

}  // namespace

std::string formatRelative(int64_t ageSeconds) {
  if (ageSeconds < 1) {
    return "now";
  }
  if (ageSeconds < kMinute) {
    return unit(ageSeconds, 's');
  }
  if (ageSeconds < kHour) {
    return unit(ageSeconds / kMinute, 'm');
  }
  if (ageSeconds < kDay) {
    return unit(ageSeconds / kHour, 'h');
  }
  const int64_t days = ageSeconds / kDay;
  if (days > kMaxDays) {
    return "99d+ ago";
  }
  return unit(days, 'd');
}

}  // namespace observer
