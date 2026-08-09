// Host tests for the push transport's pure logic (ADR-038).
//
//   pio test -e native
//
// The frames asserted below are byte-for-byte what
// `src/open_observatory/display_channel.py` emits - taken from
// `scripts/measure_display_wire.py`, not invented here - so a change on either
// side that breaks the other fails here rather than on a shelf.

#include <string>
#include <vector>

#include <unity.h>

#include "model/detection_feed.h"
#include "model/push_frame.h"
#include "model/relative_time.h"
#include "model/station_clock.h"

using namespace observer;

// ---------------------------------------------------------------------------
// Relative times
// ---------------------------------------------------------------------------

void test_zero_and_negative_ages_read_as_now(void) {
  // A negative age happens routinely: the epoch anchor is only re-taken on a
  // heartbeat, so a detection can arrive fractionally "before" our idea of now.
  TEST_ASSERT_EQUAL_STRING("now", formatRelative(0).c_str());
  TEST_ASSERT_EQUAL_STRING("now", formatRelative(-1).c_str());
  TEST_ASSERT_EQUAL_STRING("now", formatRelative(-3600).c_str());
}

void test_seconds_tick_one_at_a_time(void) {
  TEST_ASSERT_EQUAL_STRING("1s ago", formatRelative(1).c_str());
  TEST_ASSERT_EQUAL_STRING("4s ago", formatRelative(4).c_str());
  TEST_ASSERT_EQUAL_STRING("59s ago", formatRelative(59).c_str());
}

void test_the_minute_boundary(void) {
  TEST_ASSERT_EQUAL_STRING("1m ago", formatRelative(60).c_str());
  // Floor, not nearest: 119 s is "at least one minute", not "two".
  TEST_ASSERT_EQUAL_STRING("1m ago", formatRelative(119).c_str());
  TEST_ASSERT_EQUAL_STRING("2m ago", formatRelative(120).c_str());
  TEST_ASSERT_EQUAL_STRING("59m ago", formatRelative(3599).c_str());
}

void test_the_hour_boundary(void) {
  TEST_ASSERT_EQUAL_STRING("1h ago", formatRelative(3600).c_str());
  TEST_ASSERT_EQUAL_STRING("1h ago", formatRelative(7199).c_str());
  TEST_ASSERT_EQUAL_STRING("2h ago", formatRelative(7200).c_str());
  TEST_ASSERT_EQUAL_STRING("23h ago", formatRelative(86399).c_str());
}

void test_the_day_boundary_and_its_saturation(void) {
  TEST_ASSERT_EQUAL_STRING("1d ago", formatRelative(86400).c_str());
  TEST_ASSERT_EQUAL_STRING("3d ago", formatRelative(3 * 86400 + 55).c_str());
  TEST_ASSERT_EQUAL_STRING("99d ago", formatRelative(99 * 86400).c_str());
  // Saturates rather than growing wide enough to disturb the layout.
  TEST_ASSERT_EQUAL_STRING("99d+ ago", formatRelative(400 * 86400).c_str());
}

void test_every_string_is_short_enough_for_the_reserved_column(void) {
  // The display reserves a fixed-width zone for this text so the rest of the
  // second line does not shuffle sideways once a second. Nothing may exceed it.
  const int64_t probes[] = {0, 1, 59, 60, 3599, 3600, 86399, 86400,
                            99 * 86400, 4000LL * 86400};
  for (int64_t age : probes) {
    TEST_ASSERT_TRUE(formatRelative(age).size() <= 8);
  }
}

// ---------------------------------------------------------------------------
// The monotonic clock
// ---------------------------------------------------------------------------

void test_uptime_accumulates_from_millis(void) {
  StationClock clock;
  clock.tick(1000);
  clock.tick(4500);
  TEST_ASSERT_EQUAL_INT64(3, clock.uptimeSeconds());
}

void test_uptime_survives_the_32_bit_millis_wrap(void) {
  // 49.7 days. An object left on a shelf reaches this; a row whose age went
  // backwards by seven weeks at that moment would be a very confusing bug.
  StationClock clock;
  clock.tick(0xFFFFF000u);
  clock.tick(0x00000FA0u);  // wrapped: 8096 ms later
  TEST_ASSERT_EQUAL_INT64(8, clock.uptimeSeconds());
}

void test_the_first_anchor_establishes_the_epoch(void) {
  StationClock clock;
  clock.tick(0);
  clock.tick(10000);
  TEST_ASSERT_FALSE(clock.anchored());
  clock.anchor(1786196799);
  TEST_ASSERT_TRUE(clock.anchored());
  TEST_ASSERT_EQUAL_INT64(1786196799, clock.nowEpoch());
}

void test_ages_advance_with_millis_and_not_with_the_feed(void) {
  StationClock clock;
  clock.tick(0);
  clock.anchor(1786196799);
  clock.tick(4000);
  // Four seconds of uptime, no new frame from the station: the row must still
  // read "4s ago". This is the property that makes the tick self-sustaining.
  TEST_ASSERT_EQUAL_INT64(4, clock.ageOf(1786196799));
  TEST_ASSERT_EQUAL_STRING("4s ago", formatRelative(clock.ageOf(1786196799)).c_str());
}

void test_small_drift_does_not_move_the_anchor(void) {
  StationClock clock;
  clock.tick(0);
  clock.anchor(1786196799);
  clock.tick(10000);
  clock.anchor(1786196810);  // one second out: network jitter, not a real step
  TEST_ASSERT_EQUAL_UINT32(0, clock.resyncs());
  TEST_ASSERT_EQUAL_INT64(1786196809, clock.nowEpoch());
}

void test_a_real_clock_step_does_move_the_anchor(void) {
  StationClock clock;
  clock.tick(0);
  clock.anchor(1786196799);
  clock.tick(10000);
  clock.anchor(1786196900);  // the station's clock genuinely stepped
  TEST_ASSERT_EQUAL_UINT32(1, clock.resyncs());
  TEST_ASSERT_EQUAL_INT64(1786196900, clock.nowEpoch());
}

// ---------------------------------------------------------------------------
// Wire format
// ---------------------------------------------------------------------------

void test_a_bird_detection_frame(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"d\",\"n\":\"Common Woodpigeon\",\"at\":1786196799}", frame));
  TEST_ASSERT_TRUE(frame.type == PushFrameType::kDetection);
  TEST_ASSERT_EQUAL_size_t(1, frame.items.size());
  TEST_ASSERT_EQUAL_STRING("Common Woodpigeon", frame.items[0].title.c_str());
  TEST_ASSERT_EQUAL_INT64(1786196799, frame.items[0].startUtc);
  TEST_ASSERT_FALSE(frame.items[0].isBat());
  TEST_ASSERT_EQUAL_INT(1, frame.items[0].repeats);
  TEST_ASSERT_EQUAL_INT(-1, frame.speciesToday);
}

void test_a_detection_frame_can_carry_a_moved_species_count(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"d\",\"n\":\"Common Woodpigeon\",\"at\":1786196799,\"sp\":15}",
      frame));
  TEST_ASSERT_EQUAL_INT(15, frame.speciesToday);
}

void test_a_bat_pass_is_never_named_and_carries_its_frequency(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"d\",\"b\":1,\"at\":1786226651,\"k\":36.2}", frame));
  TEST_ASSERT_EQUAL_STRING("Bat pass", frame.items[0].title.c_str());
  TEST_ASSERT_EQUAL_STRING("36.2 kHz", frame.items[0].detail.c_str());
  TEST_ASSERT_TRUE(frame.items[0].isBat());
}

void test_a_pass_that_arrived_with_a_name_is_still_not_given_one(void) {
  // Defence in depth. If some future station change ever put a candidate
  // species on a pass, the glass refuses it.
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"d\",\"b\":1,\"n\":\"Common pipistrelle\",\"at\":1786226651,"
      "\"k\":45.0}",
      frame));
  TEST_ASSERT_EQUAL_STRING("Bat pass", frame.items[0].title.c_str());
}

void test_no_score_field_exists_on_this_wire(void) {
  // A frame that somehow carried a score must still produce a row with nothing
  // numeric on it beyond a bat's measured frequency.
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"d\",\"n\":\"Common Woodpigeon\",\"at\":1786196799,"
      "\"score\":0.91}",
      frame));
  TEST_ASSERT_EQUAL_STRING("", frame.items[0].detail.c_str());
}

void test_the_connect_snapshot(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"h\",\"v\":1,\"now\":1786196799,\"hb\":10,\"st\":\"L\","
      "\"sp\":14,\"f\":[{\"n\":\"Common Woodpigeon\",\"at\":1786196799,"
      "\"r\":3},{\"b\":1,\"at\":1786226651,\"k\":36.2}]}",
      frame));
  TEST_ASSERT_TRUE(frame.type == PushFrameType::kHello);
  TEST_ASSERT_EQUAL_INT(1, frame.version);
  TEST_ASSERT_EQUAL_INT(10, frame.heartbeatSeconds);
  TEST_ASSERT_TRUE(frame.hasServerNow);
  TEST_ASSERT_EQUAL_INT64(1786196799, frame.serverNow);
  TEST_ASSERT_EQUAL_INT(14, frame.speciesToday);
  TEST_ASSERT_EQUAL_size_t(2, frame.items.size());
  TEST_ASSERT_EQUAL_INT(3, frame.items[0].repeats);
  TEST_ASSERT_TRUE(frame.items[1].isBat());
  TEST_ASSERT_TRUE(frame.hasState);
  TEST_ASSERT_TRUE(frame.state == StationState::kListening);
  TEST_ASSERT_EQUAL_STRING("", frame.detail.c_str());
}

void test_a_degraded_hello_carries_the_stations_own_words(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"h\",\"v\":1,\"now\":1,\"hb\":10,\"st\":\"D\","
      "\"d\":\"NO MICROPHONE - SYNTHETIC SOURCE\",\"sp\":0,\"f\":[]}",
      frame));
  TEST_ASSERT_TRUE(frame.state == StationState::kDegraded);
  TEST_ASSERT_EQUAL_STRING("NO MICROPHONE - SYNTHETIC SOURCE",
                           frame.detail.c_str());
}

void test_a_degraded_frame_without_words_still_says_something(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(
      parsePushFrame("{\"t\":\"s\",\"now\":1,\"st\":\"D\",\"sp\":2}", frame));
  TEST_ASSERT_EQUAL_STRING("STATION REPORTS A PROBLEM", frame.detail.c_str());
}

void test_a_heartbeat(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"s\",\"now\":1786196799,\"st\":\"L\",\"sp\":14}", frame));
  TEST_ASSERT_TRUE(frame.type == PushFrameType::kHeartbeat);
  TEST_ASSERT_EQUAL_INT64(1786196799, frame.serverNow);
  TEST_ASSERT_EQUAL_INT(14, frame.speciesToday);
  TEST_ASSERT_EQUAL_size_t(0, frame.items.size());
}

void test_a_future_wire_version_is_refused_rather_than_half_parsed(void) {
  PushFrame frame;
  TEST_ASSERT_FALSE(parsePushFrame(
      "{\"t\":\"h\",\"v\":9,\"now\":1,\"hb\":10,\"st\":\"L\",\"sp\":0,"
      "\"f\":[{\"n\":\"Common Woodpigeon\",\"at\":1786196799}]}",
      frame));
}

void test_rubbish_is_refused(void) {
  PushFrame frame;
  TEST_ASSERT_FALSE(parsePushFrame("", frame));
  TEST_ASSERT_FALSE(parsePushFrame("not json", frame));
  TEST_ASSERT_FALSE(parsePushFrame("{\"t\":\"x\"}", frame));
  // A detection with no timestamp cannot be placed on a timeline.
  TEST_ASSERT_FALSE(parsePushFrame("{\"t\":\"d\",\"n\":\"Robin\"}", frame));
  // A row that is neither named nor a pass has nothing to draw.
  TEST_ASSERT_FALSE(parsePushFrame("{\"t\":\"d\",\"at\":1786196799}", frame));
}

// ---------------------------------------------------------------------------
// Feed maintenance from a stream of deltas
// ---------------------------------------------------------------------------

void test_deltas_accumulate_into_the_same_feed_the_poll_produced(void) {
  // buildFeed is unchanged from the polled transport: the display keeps a
  // bounded candidate list and re-collapses it, so a species calling twice in a
  // row still occupies one row and reports the most recent time.
  FeedFilter filter;
  filter.maxItems = 6;
  std::vector<FeedItem> candidates;
  const char* deltas[] = {
      "{\"t\":\"d\",\"n\":\"Common Woodpigeon\",\"at\":1786196700}",
      "{\"t\":\"d\",\"n\":\"Common Woodpigeon\",\"at\":1786196750}",
      "{\"t\":\"d\",\"b\":1,\"at\":1786196800,\"k\":36.2}",
  };
  for (const char* raw : deltas) {
    PushFrame frame;
    TEST_ASSERT_TRUE(parsePushFrame(raw, frame));
    candidates.insert(candidates.begin(), frame.items.begin(),
                      frame.items.end());
  }
  std::vector<FeedItem> feed = buildFeed(candidates, filter);
  TEST_ASSERT_EQUAL_size_t(2, feed.size());
  TEST_ASSERT_EQUAL_STRING("Bat pass", feed[0].title.c_str());
  TEST_ASSERT_EQUAL_STRING("Common Woodpigeon", feed[1].title.c_str());
  TEST_ASSERT_EQUAL_INT(2, feed[1].repeats);
  TEST_ASSERT_EQUAL_INT64(1786196750, feed[1].startUtc);
}

void test_a_snapshots_repeat_counts_survive_into_the_feed(void) {
  PushFrame frame;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"h\",\"v\":1,\"now\":1786196799,\"hb\":10,\"st\":\"L\","
      "\"sp\":14,\"f\":[{\"n\":\"Common Woodpigeon\",\"at\":1786196799,"
      "\"r\":194}]}",
      frame));
  FeedFilter filter;
  std::vector<FeedItem> feed = buildFeed(frame.items, filter);
  // The count on the glass is the station's, not one invented from however many
  // rows happened to be sent.
  TEST_ASSERT_EQUAL_INT(194, feed[0].repeats);
}

int main(int, char**) {
  UNITY_BEGIN();

  RUN_TEST(test_zero_and_negative_ages_read_as_now);
  RUN_TEST(test_seconds_tick_one_at_a_time);
  RUN_TEST(test_the_minute_boundary);
  RUN_TEST(test_the_hour_boundary);
  RUN_TEST(test_the_day_boundary_and_its_saturation);
  RUN_TEST(test_every_string_is_short_enough_for_the_reserved_column);

  RUN_TEST(test_uptime_accumulates_from_millis);
  RUN_TEST(test_uptime_survives_the_32_bit_millis_wrap);
  RUN_TEST(test_the_first_anchor_establishes_the_epoch);
  RUN_TEST(test_ages_advance_with_millis_and_not_with_the_feed);
  RUN_TEST(test_small_drift_does_not_move_the_anchor);
  RUN_TEST(test_a_real_clock_step_does_move_the_anchor);

  RUN_TEST(test_a_bird_detection_frame);
  RUN_TEST(test_a_detection_frame_can_carry_a_moved_species_count);
  RUN_TEST(test_a_bat_pass_is_never_named_and_carries_its_frequency);
  RUN_TEST(test_a_pass_that_arrived_with_a_name_is_still_not_given_one);
  RUN_TEST(test_no_score_field_exists_on_this_wire);
  RUN_TEST(test_the_connect_snapshot);
  RUN_TEST(test_a_degraded_hello_carries_the_stations_own_words);
  RUN_TEST(test_a_degraded_frame_without_words_still_says_something);
  RUN_TEST(test_a_heartbeat);
  RUN_TEST(test_a_future_wire_version_is_refused_rather_than_half_parsed);
  RUN_TEST(test_rubbish_is_refused);

  RUN_TEST(test_deltas_accumulate_into_the_same_feed_the_poll_produced);
  RUN_TEST(test_a_snapshots_repeat_counts_survive_into_the_feed);

  return UNITY_END();
}
