// Time parsing and presentation for the inside observer.
//
// Deliberately free of Arduino and of the C library's timezone database: the
// ESP32 has neither an IANA tz database nor (necessarily) a synchronised
// clock, and the station already hands us unambiguous UTC instants. We convert
// UTC -> local by applying a fixed offset that the operator configures, and we
// do the calendar arithmetic ourselves so the result does not depend on the
// host's TZ environment variable. That also makes it testable.
//
// UTC internally, local only for presentation - see CLAUDE.md.
#pragma once

#include <cstdint>
#include <string>

namespace observer {

// Sentinel returned by parseIso8601Utc for anything it cannot parse.
constexpr int64_t kInvalidTime = INT64_MIN;

// Parses the subset of ISO 8601 the station emits, e.g.
//   "2026-08-08T13:46:39.755370Z"  or  "2026-08-08T13:46:39Z"
// Fractional seconds are accepted and truncated (we present whole minutes).
// A trailing "Z" is required: the API documents UTC and we refuse to guess.
// Returns seconds since the Unix epoch, or kInvalidTime.
int64_t parseIso8601Utc(const char* iso);

// Whole days since the Unix epoch for a proleptic Gregorian y/m/d.
// Exposed because "is this the same local day" needs it too.
int64_t daysFromCivil(int year, unsigned month, unsigned day);

// Wall-clock time of `utcSeconds` in a zone `offsetSeconds` east of UTC.
// use24h=true  -> "14:46"
// use24h=false -> "2:46 pm"  (12:00 -> "12:00 pm", 00:30 -> "12:30 am")
std::string formatClock(int64_t utcSeconds, int32_t offsetSeconds, bool use24h);

// Local calendar day index (days since epoch) for an instant. Used to decide
// whether a detection belongs to "today" from the display's point of view.
int64_t localDayIndex(int64_t utcSeconds, int32_t offsetSeconds);

// Seconds east of UTC, derived from an instant that is known to be local
// midnight. The station tells us its own local midnight in the `today` window
// of `GET /api/v1/history` (e.g. "2026-08-07T23:00:00Z" for Europe/London in
// summer), so the display gets the station's real UTC offset - DST included -
// without shipping an IANA database or trusting the ESP32's clock. Result is
// normalised to (-12h, +14h].
int32_t offsetFromLocalMidnight(int64_t midnightUtcSeconds);

// "36.2 kHz". One decimal is all the precision an ambient display can honestly
// carry, and more would imply the peak-frequency estimate is tighter than it
// is. Returns an empty string for a non-positive frequency.
std::string formatPeakFrequency(double hz);

}  // namespace observer
