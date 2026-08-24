// When the display may ask the WiFi stack to reconnect, and how often.
//
// PURE. No Arduino, no WiFi. Everything here is a decision; `main.cpp` is the
// machinery that acts on one. Same split as `model/ota_policy.h`, and for the
// same reason: this display lives on a shelf in someone's house, so the rules
// that decide "try again now" have to be tested on a laptop rather than
// discovered by finding the screen dead three days later.
//
// The rule this class exists to enforce is a single sentence:
//
//   **an attempt must be left alone long enough to succeed.**
//
// On the pinned core (framework-arduinoespressif32 3.20017) `WiFi.reconnect()`
// is `esp_wifi_disconnect()` followed by `esp_wifi_connect()`
// (`libraries/WiFi/src/WiFiSTA.cpp:329`). Association, authentication and DHCP
// need seconds. Calling it again before that has finished does not "retry
// harder" -- it destroys the attempt already in flight. Retrying at the speed
// of the main loop therefore reconnects strictly less often than retrying
// slowly, which is the failure mode of 2026-08-24: a display that asked to
// reconnect a hundred times a second and never once managed it.
//
// Why the firmware has to do this at all, given `WiFi.setAutoReconnect(true)`:
// the stack's own recovery is skipped outright for `WIFI_REASON_ASSOC_LEAVE`
// (reason 8) -- `WiFiGeneric.cpp:1077`, "Voluntarily disconnected. Don't
// reconnect!" -- whatever autoReconnect is set to. A router that reboots or
// drops a client deauthenticates it with exactly that reason. So on the most
// likely disconnect of all, this policy is the only thing that will act.
#pragma once

#include <cstdint>

namespace observer {

enum class WifiAction {
  kNothing,
  // Ask the radio to associate again. Costs whatever is in flight, which is
  // why it is rationed.
  kAttemptReconnect,
};

class WifiPolicy {
 public:
  struct Timing {
    // How long a link may be down before the firmware does anything about it.
    // Covers a genuine blip, and covers the stack's own `first_connect` retry,
    // which fires immediately and needs room to work before being interrupted.
    uint32_t graceMs = 3000;
    // Spacing of the first attempt after the grace period, doubling from there.
    // Comfortably longer than an association takes on this board.
    uint32_t firstBackoffMs = 5000;
    // Ceiling. An outage that ends after two hours must be noticed within a
    // minute of ending, so the backoff stops doubling here rather than growing
    // without bound.
    uint32_t maxBackoffMs = 60000;
  };

  WifiPolicy() = default;
  explicit WifiPolicy(const Timing& timing) : timing_(timing) {}

  // Call unconditionally, as often as you like. `linkUp` is
  // `WiFi.status() == WL_CONNECTED`; `nowMs` is `millis()`.
  WifiAction evaluate(bool linkUp, uint32_t nowMs);

  bool down() const { return down_; }
  // Attempts issued during the current outage. Zero while the link is up, and
  // reset by recovery -- the second outage of the day does not inherit the
  // first one's backoff.
  uint32_t attempts() const { return attempts_; }
  // Milliseconds the link has been down. Meaningless unless `down()`.
  uint32_t downForMs(uint32_t nowMs) const { return nowMs - downSinceMs_; }
  // The gap that will be left after the next attempt.
  uint32_t currentBackoffMs() const { return backoffFor(attempts_ + 1); }

 private:
  uint32_t backoffFor(uint32_t attempt) const;

  Timing timing_{};
  bool down_ = false;
  uint32_t downSinceMs_ = 0;
  uint32_t nextAttemptMs_ = 0;
  uint32_t attempts_ = 0;
};

}  // namespace observer
