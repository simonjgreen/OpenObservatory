// Host-side unit tests for the inside observer's pure logic.
//
//   pio test -e native
//
// No hardware, no WiFi, no display. Everything here is a rule that would
// otherwise only be checkable by flashing the board and squinting at it.
//
// The JSON fixtures are trimmed copies of real responses from the development
// station's API, captured 2026-08-08.

#include <unity.h>

#include <string>
#include <vector>

#include "model/detection_feed.h"
#include "model/settings.h"
#include "model/station_health.h"
#include "model/time_utils.h"

using namespace observer;

namespace {

// A real BirdNET detection, with the media array trimmed to keep the fixture
// readable (the firmware filters it out during the parse anyway).
constexpr const char* kDetectionsJson = R"JSON({
  "detections": [
    {"event_start_utc":"2026-08-08T13:46:39.755370Z","common_name":"Common Woodpigeon",
     "scientific_name":"Columba palumbus","taxonomic_group":"bird","score":0.996509,
     "peak_frequency_hz":null,"detector":{"plugin_id":"birdnet-v2.4"}},
    {"event_start_utc":"2026-08-08T13:46:36.755370Z","common_name":"Common Woodpigeon",
     "scientific_name":"Columba palumbus","taxonomic_group":"bird","score":0.981,
     "peak_frequency_hz":null,"detector":{"plugin_id":"birdnet-v2.4"}},
    {"event_start_utc":"2026-08-08T13:40:31.557287Z","common_name":"Collared Dove",
     "scientific_name":"Streptopelia decaocto","taxonomic_group":"bird","score":0.9115,
     "peak_frequency_hz":null,"detector":{"plugin_id":"birdnet-v2.4"}},
    {"event_start_utc":"2026-08-08T13:34:33.057287Z","common_name":"Engine",
     "scientific_name":"Engine","taxonomic_group":"bird","score":0.9833,
     "peak_frequency_hz":null,"detector":{"plugin_id":"birdnet-v2.4"}},
    {"event_start_utc":"2026-08-08T12:36:46.361375Z","common_name":"Barn Swallow",
     "scientific_name":"Hirundo rustica","taxonomic_group":"bird","score":0.6352,
     "peak_frequency_hz":null,"detector":{"plugin_id":"birdnet-v2.4"}},
    {"event_start_utc":"2026-08-08T11:52:04.210329Z","common_name":null,
     "scientific_name":null,"taxonomic_group":"acoustic_event","score":0.447689,
     "peak_frequency_hz":3140.625,"detector":{"plugin_id":"activity-v1"}}
  ],
  "excluded_synthetic_count": 0
})JSON";

constexpr const char* kBatJson = R"JSON({
  "detections": [
    {"event_start_utc":"2026-08-07T04:25:19.325200Z","common_name":null,
     "scientific_name":null,"taxonomic_group":"bat","score":0.108567,
     "peak_frequency_hz":36241.0,"detector":{"plugin_id":"ultrasonic-pass-v1"}}
  ]
})JSON";

constexpr const char* kHistoryJson = R"JSON({
  "range": {"start_utc":"2026-08-07T23:00:00Z","label":"today"},
  "species": [
    {"common_name":"Common Woodpigeon","scientific_name":"Columba palumbus","best_score":0.9986},
    {"common_name":"Collared Dove","scientific_name":"Streptopelia decaocto","best_score":0.9115},
    {"common_name":"Rook","scientific_name":"Corvus frugilegus","best_score":0.9776},
    {"common_name":"Engine","scientific_name":"Engine","best_score":0.9833},
    {"common_name":"Barn Swallow","scientific_name":"Hirundo rustica","best_score":0.6352}
  ]
})JSON";

constexpr const char* kHealthOkJson = R"JSON({
  "status":"ok","problems":[],
  "capture":{"state":"capturing","source_kind":"alsa","is_live_hardware":true,
             "device_label":"384kHz AudioMoth USB Microphone"}
})JSON";

constexpr const char* kHealthSyntheticJson = R"JSON({
  "status":"degraded","problems":["capture: microphone not present"],
  "capture":{"state":"capturing","source_kind":"synthetic","is_live_hardware":false,
             "device_label":"synthetic"}
})JSON";

// Parses a body through the same ArduinoJson filter the firmware uses, so a
// filter that accidentally drops a field the model needs fails here.
JsonDocument parseFiltered(const char* json, void (*makeFilter)(JsonDocument&)) {
  JsonDocument filter;
  makeFilter(filter);
  JsonDocument doc;
  const DeserializationError err =
      deserializeJson(doc, json, DeserializationOption::Filter(filter));
  TEST_ASSERT_EQUAL_STRING("Ok", err.c_str());
  return doc;
}

std::vector<FeedItem> feedFrom(const char* json, const FeedFilter& f) {
  JsonDocument doc = parseFiltered(json, buildDetectionsFilter);
  std::vector<FeedItem> candidates;
  collectDetections(doc.as<JsonObjectConst>(), f, candidates);
  return buildFeed(std::move(candidates), f);
}

}  // namespace

// ---------------------------------------------------------------------------
// Timestamp parsing
// ---------------------------------------------------------------------------

void test_parses_iso8601_with_fractional_seconds() {
  // 2026-08-08T13:46:39Z
  const int64_t expected =
      daysFromCivil(2026, 8, 8) * 86400 + 13 * 3600 + 46 * 60 + 39;
  TEST_ASSERT_EQUAL_INT64(expected,
                          parseIso8601Utc("2026-08-08T13:46:39.755370Z"));
  TEST_ASSERT_EQUAL_INT64(expected, parseIso8601Utc("2026-08-08T13:46:39Z"));
}

void test_known_epoch_anchors() {
  TEST_ASSERT_EQUAL_INT64(0, parseIso8601Utc("1970-01-01T00:00:00Z"));
  TEST_ASSERT_EQUAL_INT64(951782400, parseIso8601Utc("2000-02-29T00:00:00Z"));
  // Past the 32-bit time_t rollover, which is exactly why this is int64_t.
  TEST_ASSERT_EQUAL_INT64(2147483648LL, parseIso8601Utc("2038-01-19T03:14:08Z"));
}

void test_rejects_timestamps_without_a_zulu_marker() {
  // A naive local timestamp would be silently an hour wrong in British summer
  // time. Refuse it rather than guess.
  TEST_ASSERT_EQUAL_INT64(kInvalidTime, parseIso8601Utc("2026-08-08T13:46:39"));
  TEST_ASSERT_EQUAL_INT64(kInvalidTime,
                          parseIso8601Utc("2026-08-08T13:46:39+01:00"));
  TEST_ASSERT_EQUAL_INT64(kInvalidTime, parseIso8601Utc("not a time"));
  TEST_ASSERT_EQUAL_INT64(kInvalidTime, parseIso8601Utc(nullptr));
  TEST_ASSERT_EQUAL_INT64(kInvalidTime, parseIso8601Utc(""));
}

// ---------------------------------------------------------------------------
// Clock presentation
// ---------------------------------------------------------------------------

void test_formats_24h_and_12h_clocks() {
  const int64_t t = parseIso8601Utc("2026-08-08T13:46:39Z");
  TEST_ASSERT_EQUAL_STRING("13:46", formatClock(t, 0, true).c_str());
  TEST_ASSERT_EQUAL_STRING("14:46", formatClock(t, 3600, true).c_str());
  TEST_ASSERT_EQUAL_STRING("2:46 pm", formatClock(t, 3600, false).c_str());
}

void test_twelve_hour_clock_handles_noon_and_midnight() {
  const int64_t midnight = parseIso8601Utc("2026-08-08T00:15:00Z");
  const int64_t noon = parseIso8601Utc("2026-08-08T12:15:00Z");
  TEST_ASSERT_EQUAL_STRING("12:15 am", formatClock(midnight, 0, false).c_str());
  TEST_ASSERT_EQUAL_STRING("12:15 pm", formatClock(noon, 0, false).c_str());
  TEST_ASSERT_EQUAL_STRING("00:15", formatClock(midnight, 0, true).c_str());
}

void test_clock_wraps_backwards_over_midnight() {
  // 00:30 UTC in a zone one hour behind is 23:30 the previous day, not -00:30.
  const int64_t t = parseIso8601Utc("2026-08-08T00:30:00Z");
  TEST_ASSERT_EQUAL_STRING("23:30", formatClock(t, -3600, true).c_str());
  TEST_ASSERT_EQUAL_INT64(localDayIndex(t, 0) - 1, localDayIndex(t, -3600));
}

void test_unparseable_time_renders_as_dashes_not_1970() {
  TEST_ASSERT_EQUAL_STRING("--:--", formatClock(kInvalidTime, 0, true).c_str());
}

void test_derives_station_utc_offset_from_local_midnight() {
  // Europe/London in August: local midnight is 23:00Z the day before.
  TEST_ASSERT_EQUAL_INT32(
      3600, offsetFromLocalMidnight(parseIso8601Utc("2026-08-07T23:00:00Z")));
  // Europe/London in January: local midnight is 00:00Z.
  TEST_ASSERT_EQUAL_INT32(
      0, offsetFromLocalMidnight(parseIso8601Utc("2026-01-07T00:00:00Z")));
  // A western zone: local midnight at 05:00Z is UTC-5.
  TEST_ASSERT_EQUAL_INT32(
      -5 * 3600, offsetFromLocalMidnight(parseIso8601Utc("2026-01-07T05:00:00Z")));
}

// ---------------------------------------------------------------------------
// Frequency presentation
// ---------------------------------------------------------------------------

void test_formats_peak_frequency_in_kilohertz() {
  TEST_ASSERT_EQUAL_STRING("36.2 kHz", formatPeakFrequency(36241.0).c_str());
  TEST_ASSERT_EQUAL_STRING("20.0 kHz",
                           formatPeakFrequency(20027.10857196692).c_str());
  TEST_ASSERT_EQUAL_STRING("", formatPeakFrequency(0.0).c_str());
  TEST_ASSERT_EQUAL_STRING("", formatPeakFrequency(-1.0).c_str());
}

// ---------------------------------------------------------------------------
// What counts as a species
// ---------------------------------------------------------------------------

void test_birdnet_non_taxonomic_classes_are_not_species() {
  TEST_ASSERT_TRUE(isTaxonomicName("Columba palumbus", "Common Woodpigeon"));
  TEST_ASSERT_FALSE(isTaxonomicName("Engine", "Engine"));
  TEST_ASSERT_FALSE(isTaxonomicName("Human vocal", "Human vocal"));
  TEST_ASSERT_FALSE(isTaxonomicName("Siren", "Siren"));
  TEST_ASSERT_FALSE(isTaxonomicName(nullptr, "Something"));
  TEST_ASSERT_FALSE(isTaxonomicName("", ""));
}

// ---------------------------------------------------------------------------
// Threshold filtering
// ---------------------------------------------------------------------------

void test_threshold_excludes_low_scoring_birds() {
  FeedFilter f;
  f.minScore = 0.75;
  f.maxItems = 10;
  const std::vector<FeedItem> feed = feedFrom(kDetectionsJson, f);

  // Woodpigeon (0.9965, collapsed x2) and Collared Dove (0.9115) survive.
  // Barn Swallow (0.6352) is below the threshold. "Engine" is not a species.
  // The unidentified acoustic event has no name at all.
  TEST_ASSERT_EQUAL_size_t(2, feed.size());
  TEST_ASSERT_EQUAL_STRING("Common Woodpigeon", feed[0].title.c_str());
  TEST_ASSERT_EQUAL_STRING("Collared Dove", feed[1].title.c_str());
}

void test_lowering_the_threshold_admits_more_species() {
  FeedFilter f;
  f.minScore = 0.5;
  f.maxItems = 10;
  const std::vector<FeedItem> feed = feedFrom(kDetectionsJson, f);
  TEST_ASSERT_EQUAL_size_t(3, feed.size());
  TEST_ASSERT_EQUAL_STRING("Barn Swallow", feed[2].title.c_str());
}

void test_no_feed_item_ever_carries_a_score() {
  // The regression guard for the whole no-numbers rule: a score must not be
  // reachable from a rendered row, and neither the title nor the detail may
  // contain a digit-bearing confidence figure for a bird.
  FeedFilter f;
  f.minScore = 0.05;
  f.maxItems = 20;
  for (const FeedItem& item : feedFrom(kDetectionsJson, f)) {
    if (item.kind == FeedItemKind::kSpecies) {
      TEST_ASSERT_TRUE(item.detail.empty());
      TEST_ASSERT_EQUAL(std::string::npos, item.title.find('%'));
      TEST_ASSERT_EQUAL(std::string::npos, item.title.find("0."));
    }
  }
}

// ---------------------------------------------------------------------------
// Bat passes
// ---------------------------------------------------------------------------

void test_bat_pass_ignores_the_threshold_and_gets_no_species_name() {
  FeedFilter f;
  f.minScore = 0.99;  // far above the fixture's 0.108 score
  f.showBats = true;
  const std::vector<FeedItem> feed = feedFrom(kBatJson, f);

  TEST_ASSERT_EQUAL_size_t(1, feed.size());
  TEST_ASSERT_TRUE(feed[0].isBat());
  TEST_ASSERT_EQUAL_STRING("Bat pass", feed[0].title.c_str());
  TEST_ASSERT_EQUAL_STRING("36.2 kHz", feed[0].detail.c_str());
}

void test_bat_passes_can_be_switched_off() {
  FeedFilter f;
  f.minScore = 0.0;
  f.showBats = false;
  TEST_ASSERT_EQUAL_size_t(0, feedFrom(kBatJson, f).size());
}

// ---------------------------------------------------------------------------
// Feed ordering and collapsing
// ---------------------------------------------------------------------------

void test_feed_is_reverse_chronological() {
  std::vector<FeedItem> items;
  FeedItem a;
  a.title = "Rook";
  a.startUtc = parseIso8601Utc("2026-08-08T09:00:00Z");
  FeedItem b;
  b.title = "Blackbird";
  b.startUtc = parseIso8601Utc("2026-08-08T11:00:00Z");
  FeedItem c;
  c.title = "Magpie";
  c.startUtc = parseIso8601Utc("2026-08-08T10:00:00Z");
  items = {a, b, c};

  FeedFilter f;
  f.maxItems = 10;
  const std::vector<FeedItem> feed = buildFeed(items, f);
  TEST_ASSERT_EQUAL_STRING("Blackbird", feed[0].title.c_str());
  TEST_ASSERT_EQUAL_STRING("Magpie", feed[1].title.c_str());
  TEST_ASSERT_EQUAL_STRING("Rook", feed[2].title.c_str());
}

void test_consecutive_repeats_collapse_into_one_row() {
  FeedFilter f;
  f.minScore = 0.75;
  f.maxItems = 10;
  const std::vector<FeedItem> feed = feedFrom(kDetectionsJson, f);

  // The two woodpigeon detections become a single row carrying the more
  // recent of the two times.
  TEST_ASSERT_EQUAL_INT(2, feed[0].repeats);
  TEST_ASSERT_EQUAL_INT64(parseIso8601Utc("2026-08-08T13:46:39.755370Z"),
                          feed[0].startUtc);
}

void test_feed_is_truncated_to_the_rows_the_screen_has() {
  FeedFilter f;
  f.minScore = 0.5;
  f.maxItems = 1;
  const std::vector<FeedItem> feed = feedFrom(kDetectionsJson, f);
  TEST_ASSERT_EQUAL_size_t(1, feed.size());
  TEST_ASSERT_EQUAL_STRING("Common Woodpigeon", feed[0].title.c_str());
}

void test_empty_response_yields_an_empty_feed() {
  FeedFilter f;
  const std::vector<FeedItem> feed =
      feedFrom(R"JSON({"detections":[]})JSON", f);
  TEST_ASSERT_EQUAL_size_t(0, feed.size());
}

void test_withdrawn_detections_never_reach_the_screen() {
  // ADR-044. `/api/v1/detections` deliberately still returns a withdrawn row -
  // marked, not deleted, so the record stays visible and attributable - and
  // this screen has no marker to render, so it must decline it. The owl below
  // is the measured the development station case: a North American species at 0.96,
  // comfortably above any threshold the operator can set.
  FeedFilter f;
  f.minScore = 0.75;
  f.maxItems = 10;
  const std::vector<FeedItem> feed = feedFrom(
      R"JSON({"detections":[
        {"event_start_utc":"2026-08-08T22:10:00Z","common_name":"Western Screech-Owl",
         "scientific_name":"Megascops kennicottii","taxonomic_group":"bird","score":0.96,
         "withdrawn":true,"detector":{"plugin_id":"birdnet-v2.4"}},
        {"event_start_utc":"2026-08-08T22:05:00Z","common_name":"Tawny Owl",
         "scientific_name":"Strix aluco","taxonomic_group":"bird","score":0.82,
         "withdrawn":false,"detector":{"plugin_id":"birdnet-v2.4"}}
      ]})JSON",
      f);
  TEST_ASSERT_EQUAL_size_t(1, feed.size());
  TEST_ASSERT_EQUAL_STRING("Tawny Owl", feed[0].title.c_str());
}

void test_a_withdrawn_bat_pass_is_refused_too() {
  // Bat passes bypass the score threshold entirely, so the withdrawal check
  // has to happen before the bat branch or it would never apply to one.
  FeedFilter f;
  const std::vector<FeedItem> feed = feedFrom(
      R"JSON({"detections":[
        {"event_start_utc":"2026-08-07T04:25:19Z","common_name":null,"scientific_name":null,
         "taxonomic_group":"bat","score":0.1,"peak_frequency_hz":36241.0,"withdrawn":true,
         "detector":{"plugin_id":"ultrasonic-pass-v1"}}
      ]})JSON",
      f);
  TEST_ASSERT_EQUAL_size_t(0, feed.size());
}

void test_the_streaming_filter_keeps_the_withdrawn_flag() {
  // The failure this guards is silent: if `withdrawn` is not in the
  // ArduinoJson filter it is discarded during the parse, every row reads as
  // standing, and the test above would pass for the wrong reason.
  JsonDocument filter;
  buildDetectionsFilter(filter);
  TEST_ASSERT_TRUE(filter["detections"][0]["withdrawn"].as<bool>());
}

void test_detection_without_a_timestamp_is_dropped() {
  FeedFilter f;
  f.minScore = 0.1;
  const std::vector<FeedItem> feed = feedFrom(
      R"JSON({"detections":[{"common_name":"Rook","scientific_name":"Corvus frugilegus","score":0.9}]})JSON",
      f);
  TEST_ASSERT_EQUAL_size_t(0, feed.size());
}

// ---------------------------------------------------------------------------
// Species count for the footer
// ---------------------------------------------------------------------------

void test_species_count_today_respects_the_threshold() {
  JsonDocument doc = parseFiltered(kHistoryJson, buildHistoryFilter);
  // Woodpigeon, Collared Dove, Rook. "Engine" is not a species; Barn Swallow
  // at 0.6352 never reached the screen so it must not be counted either.
  TEST_ASSERT_EQUAL_INT(3, speciesCountToday(doc.as<JsonObjectConst>(), 0.75));
  TEST_ASSERT_EQUAL_INT(4, speciesCountToday(doc.as<JsonObjectConst>(), 0.5));
  TEST_ASSERT_EQUAL_INT(0, speciesCountToday(doc.as<JsonObjectConst>(), 0.999));
}

void test_history_filter_keeps_the_local_midnight_anchor() {
  JsonDocument doc = parseFiltered(kHistoryJson, buildHistoryFilter);
  const int64_t midnight = parseIso8601Utc(doc["range"]["start_utc"]);
  TEST_ASSERT_EQUAL_INT32(3600, offsetFromLocalMidnight(midnight));
}

// ---------------------------------------------------------------------------
// Station health
// ---------------------------------------------------------------------------

void test_healthy_station_reports_listening_with_no_banner() {
  JsonDocument doc = parseFiltered(kHealthOkJson, buildHealthFilter);
  StationHealth h;
  parseHealth(doc.as<JsonObjectConst>(), h);
  TEST_ASSERT_TRUE(h.state == StationState::kListening);
  TEST_ASSERT_TRUE(h.detail.empty());
  TEST_ASSERT_TRUE(h.liveHardware);
}

void test_synthetic_source_is_reported_as_degraded_and_named() {
  JsonDocument doc = parseFiltered(kHealthSyntheticJson, buildHealthFilter);
  StationHealth h;
  parseHealth(doc.as<JsonObjectConst>(), h);
  TEST_ASSERT_TRUE(h.state == StationState::kDegraded);
  TEST_ASSERT_FALSE(h.liveHardware);
  // The operator has to be able to tell "quiet garden" from "no microphone".
  TEST_ASSERT_FALSE(h.detail.empty());
  TEST_ASSERT_TRUE(h.detail.find("MICROPHONE") != std::string::npos);
}

void test_capture_not_running_is_degraded() {
  JsonDocument doc = parseFiltered(
      R"JSON({"status":"ok","problems":[],
              "capture":{"state":"stopped","source_kind":"alsa","is_live_hardware":true}})JSON",
      buildHealthFilter);
  StationHealth h;
  parseHealth(doc.as<JsonObjectConst>(), h);
  TEST_ASSERT_TRUE(h.state == StationState::kDegraded);
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

void test_settings_defaults_match_the_operator_decision() {
  Settings s;
  TEST_ASSERT_EQUAL_DOUBLE(0.75, s.scoreThreshold);
  TEST_ASSERT_TRUE(s.showBats);
  TEST_ASSERT_FALSE(clampSettings(s));
}

void test_settings_clamping_repairs_impossible_values() {
  Settings s;
  s.scoreThreshold = 5.0;
  s.brightnessPercent = 0;
  s.stationPort = 0;
  s.pollSeconds = 1;
  TEST_ASSERT_TRUE(clampSettings(s));
  TEST_ASSERT_EQUAL_DOUBLE(0.99, s.scoreThreshold);
  TEST_ASSERT_EQUAL_UINT8(5, s.brightnessPercent);
  TEST_ASSERT_EQUAL_UINT16(8080, s.stationPort);
  TEST_ASSERT_EQUAL_UINT16(5, s.pollSeconds);
}

void test_station_base_url() {
  Settings s;
  // 192.0.2.10 is TEST-NET-1 (RFC 5737): documentation-only address space,
  // never any real installation's.
  s.stationHost = "192.0.2.10";
  s.stationPort = 8080;
  TEST_ASSERT_EQUAL_STRING("http://192.0.2.10:8080", stationBaseUrl(s).c_str());
}

void test_unprovisioned_station_host_stays_empty() {
  // Empty means "not yet provisioned" and must survive clamping: repairing
  // it to some address would silently point a fresh unit at one particular
  // installation. main.cpp raises the portal on empty instead.
  Settings s;
  TEST_ASSERT_TRUE(s.stationHost.empty());
  clampSettings(s);
  TEST_ASSERT_TRUE(s.stationHost.empty());
  TEST_ASSERT_EQUAL_STRING("", stationBaseUrl(s).c_str());
}

// ---------------------------------------------------------------------------

int main(int, char**) {
  UNITY_BEGIN();

  RUN_TEST(test_parses_iso8601_with_fractional_seconds);
  RUN_TEST(test_known_epoch_anchors);
  RUN_TEST(test_rejects_timestamps_without_a_zulu_marker);

  RUN_TEST(test_formats_24h_and_12h_clocks);
  RUN_TEST(test_twelve_hour_clock_handles_noon_and_midnight);
  RUN_TEST(test_clock_wraps_backwards_over_midnight);
  RUN_TEST(test_unparseable_time_renders_as_dashes_not_1970);
  RUN_TEST(test_derives_station_utc_offset_from_local_midnight);

  RUN_TEST(test_formats_peak_frequency_in_kilohertz);
  RUN_TEST(test_birdnet_non_taxonomic_classes_are_not_species);

  RUN_TEST(test_threshold_excludes_low_scoring_birds);
  RUN_TEST(test_lowering_the_threshold_admits_more_species);
  RUN_TEST(test_no_feed_item_ever_carries_a_score);

  RUN_TEST(test_bat_pass_ignores_the_threshold_and_gets_no_species_name);
  RUN_TEST(test_bat_passes_can_be_switched_off);

  RUN_TEST(test_feed_is_reverse_chronological);
  RUN_TEST(test_consecutive_repeats_collapse_into_one_row);
  RUN_TEST(test_feed_is_truncated_to_the_rows_the_screen_has);
  RUN_TEST(test_empty_response_yields_an_empty_feed);
  RUN_TEST(test_detection_without_a_timestamp_is_dropped);
  RUN_TEST(test_withdrawn_detections_never_reach_the_screen);
  RUN_TEST(test_a_withdrawn_bat_pass_is_refused_too);
  RUN_TEST(test_the_streaming_filter_keeps_the_withdrawn_flag);

  RUN_TEST(test_species_count_today_respects_the_threshold);
  RUN_TEST(test_history_filter_keeps_the_local_midnight_anchor);

  RUN_TEST(test_healthy_station_reports_listening_with_no_banner);
  RUN_TEST(test_synthetic_source_is_reported_as_degraded_and_named);
  RUN_TEST(test_capture_not_running_is_degraded);

  RUN_TEST(test_settings_defaults_match_the_operator_decision);
  RUN_TEST(test_settings_clamping_repairs_impossible_values);
  RUN_TEST(test_station_base_url);
  RUN_TEST(test_unprovisioned_station_host_stays_empty);

  return UNITY_END();
}
