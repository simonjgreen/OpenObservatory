// When the display may ask the WiFi stack to reconnect, and how often.
//
// The bug this file exists to stop coming back, observed 2026-08-24: after a
// WiFi drop the display never rejoined the network by itself, and only a power
// cycle brought it back.
//
// The cause was a cadence mismatch, not a missing retry. `loop()` asked for a
// reconnect from inside the block whose next-due time is
// `PushStationSource::serviceIntervalMs()`, which is **10 ms** -- it services a
// socket, it does not fetch anything. So while the link was down the firmware
// called `WiFi.reconnect()` a hundred times a second, and on the pinned core
// (framework-arduinoespressif32 3.20017, `WiFiSTA.cpp:329`) that call is
// `esp_wifi_disconnect()` followed by `esp_wifi_connect()`. Association, auth
// and DHCP need seconds. Every attempt was torn down by the next one roughly
// two hundred times before it could have finished. The radio was busy forever
// and connected never.
//
// A power cycle worked because boot takes a different path: `connectWifi()`
// calls `WiFi.begin()` exactly once and then waits patiently for 25 seconds.
//
// So the property under test is not "does it retry" -- it always retried. It is
// **"does it ever leave an attempt alone long enough to succeed"**.
#include <unity.h>

#include "model/wifi_policy.h"

using observer::WifiAction;
using observer::WifiPolicy;

namespace {

// The shortest time in which association, authentication and DHCP could
// plausibly complete on this board. No two reconnect attempts may ever be
// closer together than this, or the second one aborts the first.
constexpr uint32_t kAssociationNeedsMs = 3000;

void test_a_healthy_link_is_never_touched() {
  WifiPolicy policy;
  for (uint32_t t = 0; t < 600000; t += 10) {
    TEST_ASSERT_TRUE(policy.evaluate(true, t) == WifiAction::kNothing);
  }
  TEST_ASSERT_EQUAL_UINT32(0, policy.attempts());
  TEST_ASSERT_FALSE(policy.down());
}

// A link that flickers and returns must not cost a reconnect at all. Tearing
// the radio down to "fix" a link that was already coming back is how a blip
// becomes an outage.
void test_a_momentary_blip_is_ridden_out_rather_than_reconnected() {
  WifiPolicy policy;
  uint32_t t = 0;
  for (; t < 1000; t += 10) {
    TEST_ASSERT_TRUE(policy.evaluate(false, t) == WifiAction::kNothing);
  }
  TEST_ASSERT_TRUE(policy.evaluate(true, t) == WifiAction::kNothing);
  TEST_ASSERT_EQUAL_UINT32(0, policy.attempts());
}

// THE REGRESSION. Drive the policy exactly as `loop()` drives it -- every 10 ms,
// link down throughout -- and count what reaches the radio.
//
// Before the fix the answer was 6,000 reconnects in sixty seconds, each one
// destroying the last. After it, attempts are spaced by at least the time an
// association needs.
void test_reconnects_are_never_issued_faster_than_an_association_completes() {
  WifiPolicy policy;
  uint32_t attempts = 0;
  uint32_t lastAttemptAt = 0;
  bool haveAttempted = false;

  for (uint32_t t = 0; t <= 60000; t += 10) {
    if (policy.evaluate(false, t) == WifiAction::kAttemptReconnect) {
      if (haveAttempted) {
        TEST_ASSERT_GREATER_OR_EQUAL_UINT32(kAssociationNeedsMs,
                                            t - lastAttemptAt);
      }
      haveAttempted = true;
      lastAttemptAt = t;
      ++attempts;
    }
  }

  // It must try -- a policy that never reconnects would also pass the spacing
  // assertion above, and would be a worse bug than the one being fixed.
  TEST_ASSERT_GREATER_THAN_UINT32(0, attempts);
  // And it must not thrash. Sixty seconds of downtime is a handful of attempts,
  // not thousands.
  TEST_ASSERT_LESS_OR_EQUAL_UINT32(12, attempts);
}

// Backoff exists so a router that is genuinely off for an hour is not hammered,
// but it is capped: an outage that ends after two hours must be noticed within
// a minute, not slept through.
void test_backoff_grows_and_then_stops_growing() {
  WifiPolicy policy;
  uint32_t previousGap = 0;
  uint32_t lastAttemptAt = 0;
  bool haveAttempted = false;
  uint32_t largestGap = 0;

  for (uint32_t t = 0; t <= 3600000; t += 10) {
    if (policy.evaluate(false, t) == WifiAction::kAttemptReconnect) {
      if (haveAttempted) {
        const uint32_t gap = t - lastAttemptAt;
        TEST_ASSERT_GREATER_OR_EQUAL_UINT32(previousGap, gap);
        previousGap = gap;
        if (gap > largestGap) {
          largestGap = gap;
        }
      }
      haveAttempted = true;
      lastAttemptAt = t;
    }
  }
  TEST_ASSERT_EQUAL_UINT32(WifiPolicy::Timing{}.maxBackoffMs, largestGap);
}

// An hour of downtime must not exhaust the policy. There is no give-up state:
// the display is on a shelf and nobody is coming to press the button.
void test_it_never_gives_up() {
  WifiPolicy policy;
  uint32_t attemptsInFinalTenMinutes = 0;
  for (uint32_t t = 0; t <= 3600000; t += 10) {
    if (policy.evaluate(false, t) == WifiAction::kAttemptReconnect &&
        t > 3000000) {
      ++attemptsInFinalTenMinutes;
    }
  }
  TEST_ASSERT_GREATER_THAN_UINT32(0, attemptsInFinalTenMinutes);
}

// Coming back must clear the backoff. Otherwise the second outage of the day
// starts at a one-minute retry interval it did nothing to earn.
void test_recovery_resets_the_backoff() {
  WifiPolicy policy;
  uint32_t t = 0;
  for (; t <= 600000; t += 10) {
    policy.evaluate(false, t);
  }
  TEST_ASSERT_TRUE(policy.down());
  TEST_ASSERT_GREATER_THAN_UINT32(1, policy.attempts());

  policy.evaluate(true, t);
  TEST_ASSERT_FALSE(policy.down());
  TEST_ASSERT_EQUAL_UINT32(0, policy.attempts());

  // Second outage: the grace period applies again from scratch, and the first
  // attempt comes quickly rather than a minute later.
  const uint32_t downAt = t;
  bool attempted = false;
  for (; t <= downAt + 20000; t += 10) {
    if (policy.evaluate(false, t) == WifiAction::kAttemptReconnect) {
      attempted = true;
      break;
    }
  }
  TEST_ASSERT_TRUE(attempted);
  TEST_ASSERT_LESS_OR_EQUAL_UINT32(15000, t - downAt);
}

// `millis()` wraps every 49.7 days and this display is meant to sit on a shelf
// for longer than that. The rest of the firmware compares deadlines through a
// signed cast for exactly this reason; the policy must do the same, or an
// outage that straddles the wrap would wait 49 days for its next attempt.
void test_a_millis_wraparound_does_not_strand_the_policy() {
  WifiPolicy policy;
  const uint32_t nearTheEnd = 0xFFFFFF00u;
  uint32_t t = nearTheEnd;
  bool attemptedAfterWrap = false;

  for (uint32_t step = 0; step < 12000; ++step) {
    const WifiAction action = policy.evaluate(false, t);
    if (action == WifiAction::kAttemptReconnect && t < nearTheEnd) {
      attemptedAfterWrap = true;
    }
    t += 10;  // wraps through zero on its own
  }
  TEST_ASSERT_TRUE(attemptedAfterWrap);
}

// The display shows this number. It has to be the schedule the radio is
// actually on, counted down from the same state, or the screen says "3s" at a
// moment when nothing is going to happen for another minute.
void test_the_countdown_matches_the_schedule_it_is_counting_down_to() {
  WifiPolicy policy;

  // Link up: nothing to count down to.
  policy.evaluate(true, 0);
  TEST_ASSERT_EQUAL_UINT32(0, policy.msUntilNextAttempt(0));

  // First pass down starts the grace period.
  policy.evaluate(false, 1000);
  TEST_ASSERT_EQUAL_UINT32(WifiPolicy::Timing{}.graceMs,
                           policy.msUntilNextAttempt(1000));
  TEST_ASSERT_EQUAL_UINT32(1000, policy.msUntilNextAttempt(3000));

  // Walk to the attempt and confirm the countdown reaches zero exactly as the
  // action is issued, never showing a stale positive number afterwards.
  uint32_t t = 1000;
  bool issued = false;
  for (; t <= 20000; t += 10) {
    const uint32_t remaining = policy.msUntilNextAttempt(t);
    if (policy.evaluate(false, t) == WifiAction::kAttemptReconnect) {
      TEST_ASSERT_EQUAL_UINT32(0, remaining);
      issued = true;
      break;
    }
    TEST_ASSERT_GREATER_THAN_UINT32(0, remaining);
  }
  TEST_ASSERT_TRUE(issued);

  // And immediately after an attempt it counts the full backoff, not zero.
  TEST_ASSERT_EQUAL_UINT32(WifiPolicy::Timing{}.firstBackoffMs,
                           policy.msUntilNextAttempt(t));
}

// The countdown must survive the wrap for the same reason the schedule does.
void test_the_countdown_survives_a_millis_wraparound() {
  WifiPolicy policy;
  const uint32_t justBeforeWrap = 0xFFFFFFFFu - 1000;
  policy.evaluate(false, justBeforeWrap);
  // 0xFFFFFFFF is 2^32 - 1, so `justBeforeWrap + 3000` lands at 1999 rather
  // than the 2000 the arithmetic invites you to expect. Spelled out because
  // getting it wrong here is exactly how an off-by-one hides in a wrap test.
  TEST_ASSERT_EQUAL_UINT32(3000, policy.msUntilNextAttempt(justBeforeWrap));
  TEST_ASSERT_EQUAL_UINT32(999, policy.msUntilNextAttempt(1000));
  TEST_ASSERT_EQUAL_UINT32(0, policy.msUntilNextAttempt(2500));
}

}  // namespace

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_a_healthy_link_is_never_touched);
  RUN_TEST(test_a_momentary_blip_is_ridden_out_rather_than_reconnected);
  RUN_TEST(test_reconnects_are_never_issued_faster_than_an_association_completes);
  RUN_TEST(test_backoff_grows_and_then_stops_growing);
  RUN_TEST(test_it_never_gives_up);
  RUN_TEST(test_recovery_resets_the_backoff);
  RUN_TEST(test_a_millis_wraparound_does_not_strand_the_policy);
  RUN_TEST(test_the_countdown_matches_the_schedule_it_is_counting_down_to);
  RUN_TEST(test_the_countdown_survives_a_millis_wraparound);
  return UNITY_END();
}
