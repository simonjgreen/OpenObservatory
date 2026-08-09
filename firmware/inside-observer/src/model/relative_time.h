// "4s ago". How the inside observer says when something happened.
//
// ADR-038 replaced clock times ("21:04") with elapsed times that tick every
// second. The reason is what the object is for: it sits on a shelf and is
// glanced at, and "4s ago" answers the question a glance is actually asking -
// is this happening now? - without the reader having to know what time it is.
//
// Pure. No Arduino, no clock, no timezone: this function converts a number of
// seconds into words and nothing else, so every boundary below is host-tested.
#pragma once

#include <cstdint>
#include <string>

namespace observer {

// Thresholds, stated once here rather than being implied by the code:
//
//   age < 1 s        "now"        - including a negative age (see below)
//   1..59 s          "Ns ago"
//   60 s..59 m       "Nm ago"
//   1 h..23 h        "Nh ago"
//   >= 24 h          "Nd ago", saturating at "99d+ ago"
//
// Rounding is **floor**, not nearest, at every step. "1m ago" therefore means
// "at least a minute, less than two", which is the weaker and safer claim, and
// it is what makes the one-second tick read as a count rather than as a value
// that jitters between neighbouring units.
//
// A **negative** age means the station's timestamp is ahead of the display's
// idea of now - a few hundred milliseconds of it is normal, because the epoch
// anchor is only re-taken on a heartbeat. It renders as "now" rather than as
// "-1s ago", because a display cannot honestly claim to know about the future
// and a minus sign on a counter top reads as a fault.
//
// Beyond a day the unit stops getting coarser. Weeks and months were considered
// and rejected: a feed row older than a day already means the garden has been
// silent for a day, which is a fault report, not an observation, and "3d ago"
// says that plainly where "last week" would soften it. The 99-day saturation
// exists so the string can never grow wide enough to disturb the layout.
std::string formatRelative(int64_t ageSeconds);

}  // namespace observer
