#include "model/time_utils.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace observer {
namespace {

// Reads exactly `digits` ASCII digits into `out`. Returns false on anything
// else, which is how we reject a malformed timestamp rather than half-read it.
bool readFixed(const char*& p, int digits, int& out) {
  int value = 0;
  for (int i = 0; i < digits; ++i) {
    if (*p < '0' || *p > '9') {
      return false;
    }
    value = value * 10 + (*p - '0');
    ++p;
  }
  out = value;
  return true;
}

bool expect(const char*& p, char c) {
  if (*p != c) {
    return false;
  }
  ++p;
  return true;
}

// Floor division, because C++ integer division truncates towards zero and
// negative epoch days (pre-1970, and any negative UTC offset near the epoch)
// would otherwise land on the wrong day.
int64_t floorDiv(int64_t a, int64_t b) {
  int64_t q = a / b;
  if ((a % b != 0) && ((a < 0) != (b < 0))) {
    --q;
  }
  return q;
}

int64_t floorMod(int64_t a, int64_t b) { return a - floorDiv(a, b) * b; }

}  // namespace

// Howard Hinnant's days_from_civil. Valid for the whole proleptic Gregorian
// calendar and free of the 2038 and leap-year traps a hand-rolled version
// usually carries.
int64_t daysFromCivil(int year, unsigned month, unsigned day) {
  int64_t y = year;
  y -= month <= 2;
  const int64_t era = floorDiv(y, 400);
  const int64_t yoe = y - era * 400;                                  // [0, 399]
  const int64_t doy =
      (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;       // [0, 365]
  const int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;          // [0, 146096]
  return era * 146097 + doe - 719468;
}

int64_t parseIso8601Utc(const char* iso) {
  if (iso == nullptr) {
    return kInvalidTime;
  }
  const char* p = iso;
  int year = 0, month = 0, day = 0, hour = 0, minute = 0, second = 0;
  if (!readFixed(p, 4, year) || !expect(p, '-') || !readFixed(p, 2, month) ||
      !expect(p, '-') || !readFixed(p, 2, day)) {
    return kInvalidTime;
  }
  if (!expect(p, 'T') && !expect(p, ' ')) {
    return kInvalidTime;
  }
  if (!readFixed(p, 2, hour) || !expect(p, ':') || !readFixed(p, 2, minute) ||
      !expect(p, ':') || !readFixed(p, 2, second)) {
    return kInvalidTime;
  }
  if (*p == '.') {
    ++p;
    while (*p >= '0' && *p <= '9') {
      ++p;  // fractional seconds, deliberately discarded
    }
  }
  // The station documents UTC on every timestamp. Anything else is either a
  // contract change or a bug, and silently assuming an offset would put wrong
  // times on the counter top.
  if (*p != 'Z' && *p != 'z') {
    return kInvalidTime;
  }
  if (month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 ||
      minute > 59 || second > 60) {
    return kInvalidTime;
  }

  const int64_t days = daysFromCivil(year, static_cast<unsigned>(month),
                                     static_cast<unsigned>(day));
  return days * 86400 + hour * 3600 + minute * 60 + second;
}

std::string formatClock(int64_t utcSeconds, int32_t offsetSeconds,
                        bool use24h) {
  if (utcSeconds == kInvalidTime) {
    return "--:--";
  }
  const int64_t local = utcSeconds + offsetSeconds;
  const int64_t secondOfDay = floorMod(local, 86400);
  const int hour = static_cast<int>(secondOfDay / 3600);
  const int minute = static_cast<int>((secondOfDay % 3600) / 60);

  char buf[16];
  if (use24h) {
    std::snprintf(buf, sizeof(buf), "%02d:%02d", hour, minute);
  } else {
    const int display = (hour % 12 == 0) ? 12 : hour % 12;
    std::snprintf(buf, sizeof(buf), "%d:%02d %s", display, minute,
                  hour < 12 ? "am" : "pm");
  }
  return std::string(buf);
}

int64_t localDayIndex(int64_t utcSeconds, int32_t offsetSeconds) {
  if (utcSeconds == kInvalidTime) {
    return INT64_MIN;
  }
  return floorDiv(utcSeconds + offsetSeconds, 86400);
}

int32_t offsetFromLocalMidnight(int64_t midnightUtcSeconds) {
  if (midnightUtcSeconds == kInvalidTime) {
    return 0;
  }
  const int64_t secondOfDay = floorMod(midnightUtcSeconds, 86400);
  int64_t offset = (secondOfDay == 0) ? 0 : (86400 - secondOfDay);
  if (offset > 14 * 3600) {
    offset -= 86400;  // western hemisphere
  }
  return static_cast<int32_t>(offset);
}

std::string formatPeakFrequency(double hz) {
  if (!(hz > 0.0)) {
    return std::string();
  }
  char buf[24];
  std::snprintf(buf, sizeof(buf), "%.1f kHz", hz / 1000.0);
  return std::string(buf);
}

}  // namespace observer
