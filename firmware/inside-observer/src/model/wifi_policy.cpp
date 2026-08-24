#include "model/wifi_policy.h"

namespace observer {

namespace {

// Every deadline in this firmware is compared through a signed difference
// rather than `a >= b`, because `millis()` wraps every 49.7 days and this
// display is meant to outlast that on a shelf. A plain comparison across the
// wrap would park the next attempt roughly seven weeks into the future, which
// is indistinguishable from the bug being fixed here.
bool reached(uint32_t nowMs, uint32_t deadlineMs) {
  return static_cast<int32_t>(nowMs - deadlineMs) >= 0;
}

}  // namespace

uint32_t WifiPolicy::backoffFor(uint32_t attempt) const {
  if (attempt <= 1) {
    return timing_.firstBackoffMs;
  }
  uint32_t backoff = timing_.firstBackoffMs;
  // Doubling, but bounded by the cap rather than by the shift: 32 doublings of
  // five seconds overflows, and an overflowed deadline is a display that waits
  // for a moment that has already passed.
  for (uint32_t n = 1; n < attempt; ++n) {
    if (backoff >= timing_.maxBackoffMs / 2) {
      return timing_.maxBackoffMs;
    }
    backoff *= 2;
  }
  return backoff > timing_.maxBackoffMs ? timing_.maxBackoffMs : backoff;
}

uint32_t WifiPolicy::msUntilNextAttempt(uint32_t nowMs) const {
  if (!down_ || reached(nowMs, nextAttemptMs_)) {
    return 0;
  }
  return nextAttemptMs_ - nowMs;
}

WifiAction WifiPolicy::evaluate(bool linkUp, uint32_t nowMs) {
  if (linkUp) {
    // Recovery is the only thing that clears the backoff. Deliberately not
    // "the attempt we just made returned true": on this stack an attempt
    // returns long before the association it started has finished, so success
    // can only be read off the link itself.
    down_ = false;
    attempts_ = 0;
    return WifiAction::kNothing;
  }

  if (!down_) {
    down_ = true;
    downSinceMs_ = nowMs;
    attempts_ = 0;
    nextAttemptMs_ = nowMs + timing_.graceMs;
    return WifiAction::kNothing;
  }

  if (!reached(nowMs, nextAttemptMs_)) {
    return WifiAction::kNothing;
  }

  ++attempts_;
  nextAttemptMs_ = nowMs + backoffFor(attempts_);
  return WifiAction::kAttemptReconnect;
}

}  // namespace observer
