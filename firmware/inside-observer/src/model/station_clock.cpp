#include "model/station_clock.h"

namespace observer {

void StationClock::tick(uint32_t millisNow) {
  if (!started_) {
    started_ = true;
    lastMillis_ = millisNow;
    return;
  }
  // Unsigned subtraction is correct across the 32-bit wrap: at the wrap
  // `millisNow` is small and `lastMillis_` is huge, and the difference still
  // comes out as the real elapsed milliseconds. This is the whole reason the
  // uptime is accumulated from deltas rather than read from millis() directly.
  const uint32_t delta = millisNow - lastMillis_;
  lastMillis_ = millisNow;
  uptimeMs_ += delta;
}

void StationClock::anchor(int64_t serverEpochSeconds) {
  const int64_t candidate = serverEpochSeconds - uptimeSeconds();
  if (!anchored_) {
    offsetSeconds_ = candidate;
    anchored_ = true;
    return;
  }
  const int64_t drift = candidate - offsetSeconds_;
  if (drift >= kResyncThresholdSeconds || drift <= -kResyncThresholdSeconds) {
    offsetSeconds_ = candidate;
    ++resyncs_;
  }
}

}  // namespace observer
