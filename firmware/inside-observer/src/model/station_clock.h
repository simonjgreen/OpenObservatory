// The monotonic base that "4s ago" is counted from.
//
// This board has no RTC and does not run NTP. Before ADR-038 that did not
// matter: the display asked the station what time an event happened and printed
// it, so it never had to know what time it was itself. An elapsed time does have
// to know, every second, and it has to keep knowing while the feed is down.
//
// The trap this class exists to avoid: computing "seconds ago" by subtracting an
// event's timestamp from a wall clock that can jump. Anchor the wall clock to
// the station's epoch and an NTP step, a reconnect, or a heartbeat arriving late
// would move every row on the screen at once - rows would age backwards, or leap
// forward by however far the clock moved. So the count comes from `millis()`,
// which only ever moves forward at one second per second, and the station's
// epoch is used *only* to establish the offset between the two.
//
// Pure. Host-tested, including the 32-bit `millis()` wrap at 49.7 days, which an
// ambient object left on a shelf will actually reach.
#pragma once

#include <cstdint>

namespace observer {

class StationClock {
 public:
  // Feed the device's own millis(). Safe to call as often as the loop runs; the
  // accumulated uptime survives the 32-bit wrap because only the *delta* between
  // successive calls is used, and that delta is correct across a wrap in
  // unsigned arithmetic.
  void tick(uint32_t millisNow);

  int64_t uptimeSeconds() const { return static_cast<int64_t>(uptimeMs_ / 1000); }

  // Take the station's idea of now, from a hello or heartbeat frame.
  //
  // The offset is only *moved* when it is out by `kResyncThresholdSeconds` or
  // more. Below that the existing anchor is kept, because re-anchoring on every
  // heartbeat would let sub-second network jitter push a displayed age back and
  // forth across a whole-second boundary - a row flickering between "7s ago" and
  // "6s ago" is worse than being half a second wrong.
  void anchor(int64_t serverEpochSeconds);

  bool anchored() const { return anchored_; }

  // How many times the anchor had to be moved after the first one. A rising
  // count means the station's clock and this board's crystal disagree, which is
  // worth seeing in the log rather than inferring from odd-looking rows.
  uint32_t resyncs() const { return resyncs_; }

  // Seconds since the Unix epoch, as best this device can tell. Meaningless
  // before the first anchor; callers check `anchored()`.
  int64_t nowEpoch() const { return uptimeSeconds() + offsetSeconds_; }

  // Elapsed seconds since `atEpoch`. Negative when the station's timestamp is
  // ahead of ours, which formatRelative renders as "now".
  int64_t ageOf(int64_t atEpoch) const { return nowEpoch() - atEpoch; }

  static constexpr int64_t kResyncThresholdSeconds = 2;

 private:
  uint64_t uptimeMs_ = 0;
  uint32_t lastMillis_ = 0;
  bool started_ = false;
  int64_t offsetSeconds_ = 0;
  bool anchored_ = false;
  uint32_t resyncs_ = 0;
};

}  // namespace observer
