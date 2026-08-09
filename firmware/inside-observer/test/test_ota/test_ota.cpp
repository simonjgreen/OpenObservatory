// Host tests for the over-the-air update rules (ADR-050).
//
//   pio test -e native
//
// These are the tests that stand in for hardware we cannot brick twice. The
// device is not connected while this is being written, so nothing here has been
// observed on the glass; what it *can* do is pin every decision that leads to a
// flash write or a rollback, so that the parts a laptop can check are checked.
//
// Two rules under test, and they are different in kind:
//
//   evaluateOffer     -- may we install this, and is now the moment?
//   evaluateProbation -- has the image now running earned the right to stay?

#include <string>

#include <unity.h>

#include "model/ota_policy.h"
#include "model/push_frame.h"

using namespace observer;

namespace {

const char* kGoodDigest =
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

FirmwareOffer goodOffer() {
  FirmwareOffer offer;
  offer.version = "0.2.1";
  offer.sha256 = kGoodDigest;
  offer.path = "/api/v1/firmware/image";
  offer.sizeBytes = 993284;
  return offer;
}

// A display nobody is touching, showing a feed whose newest row is an hour old.
// The state this device is in almost all of the time.
UpdateContext quietContext() {
  UpdateContext context;
  context.onFeedScreen = true;
  context.portalRunning = false;
  context.stationReachable = true;
  context.msSinceTouch = 3600000;
  context.newestRowAgeSeconds = 3600;
  return context;
}

}  // namespace

// ---------------------------------------------------------------------------
// Version ordering
// ---------------------------------------------------------------------------

void test_versions_order_numerically_not_lexically(void) {
  // The whole reason this is not a string compare: "0.10.0" is newer than
  // "0.9.0" and sorts before it.
  TEST_ASSERT_TRUE(compareVersions("0.10.0", "0.9.0") > 0);
  TEST_ASSERT_TRUE(compareVersions("0.9.0", "0.10.0") < 0);
  TEST_ASSERT_TRUE(compareVersions("1.0.0", "0.99.99") > 0);
  TEST_ASSERT_TRUE(compareVersions("2.0.0", "10.0.0") < 0);
}

void test_missing_components_read_as_zero(void) {
  TEST_ASSERT_EQUAL_INT(0, compareVersions("0.2", "0.2.0"));
  TEST_ASSERT_EQUAL_INT(0, compareVersions("1", "1.0.0.0"));
  TEST_ASSERT_TRUE(compareVersions("0.2.1", "0.2") > 0);
}

void test_a_version_this_build_cannot_order_is_refused_outright(void) {
  // Refusing to parse is the honest answer. Guessing where "0.2.0-rc1" sits
  // relative to "0.2.0" is how a display installs a release candidate over a
  // release and then declines to take the release back.
  TEST_ASSERT_FALSE(isPlausibleVersion("0.2.0-rc1"));
  TEST_ASSERT_FALSE(isPlausibleVersion("v0.2.0"));
  TEST_ASSERT_FALSE(isPlausibleVersion(""));
  TEST_ASSERT_FALSE(isPlausibleVersion("0..2"));
  TEST_ASSERT_FALSE(isPlausibleVersion(".2"));
  TEST_ASSERT_FALSE(isPlausibleVersion("2."));
  TEST_ASSERT_FALSE(isPlausibleVersion("1.2.3.4.5"));
  TEST_ASSERT_FALSE(isPlausibleVersion("100000.0.0"));
  TEST_ASSERT_TRUE(isPlausibleVersion("0.2.0"));
  TEST_ASSERT_TRUE(isPlausibleVersion("12"));
}

void test_an_unorderable_version_compares_as_equal_so_it_is_never_newer(void) {
  // compareVersions returns 0 for anything it cannot parse, and evaluateOffer
  // requires strictly greater than zero. The two together mean a malformed
  // version can never be mistaken for an upgrade.
  TEST_ASSERT_EQUAL_INT(0, compareVersions("0.3.0-rc1", "0.2.0"));
  TEST_ASSERT_EQUAL_INT(0, compareVersions("0.2.0", "banana"));
}

// ---------------------------------------------------------------------------
// The digest
// ---------------------------------------------------------------------------

void test_a_digest_must_be_64_lowercase_hex_characters(void) {
  TEST_ASSERT_TRUE(isSha256Hex(kGoodDigest));
  TEST_ASSERT_FALSE(isSha256Hex(""));
  TEST_ASSERT_FALSE(isSha256Hex(std::string(63, 'a')));
  TEST_ASSERT_FALSE(isSha256Hex(std::string(65, 'a')));
  // Uppercase is refused rather than folded: the station emits lowercase, and
  // accepting both means two spellings of the same fact on one wire.
  TEST_ASSERT_FALSE(isSha256Hex(std::string(64, 'A')));
  TEST_ASSERT_FALSE(isSha256Hex(std::string(64, 'z')));
}

// ---------------------------------------------------------------------------
// Whether to install at all
// ---------------------------------------------------------------------------

void test_a_newer_build_on_a_quiet_display_is_installed(void) {
  TEST_ASSERT_EQUAL(UpdateVerdict::kGo,
                    evaluateOffer(goodOffer(), "0.2.0", quietContext()));
}

void test_the_running_build_is_never_reinstalled_over_itself(void) {
  TEST_ASSERT_EQUAL(UpdateVerdict::kNotNewer,
                    evaluateOffer(goodOffer(), "0.2.1", quietContext()));
}

void test_an_older_build_is_refused(void) {
  TEST_ASSERT_EQUAL(UpdateVerdict::kNotNewer,
                    evaluateOffer(goodOffer(), "0.3.0", quietContext()));
}

void test_an_offer_with_no_usable_digest_is_refused_before_anything_is_written(
    void) {
  FirmwareOffer offer = goodOffer();
  offer.sha256 = "not-a-digest";
  TEST_ASSERT_EQUAL(UpdateVerdict::kMalformed,
                    evaluateOffer(offer, "0.2.0", quietContext()));
}

void test_an_offer_that_names_a_host_rather_than_a_path_is_refused(void) {
  // The path is fetched from the station this socket is already talking to.
  // A frame that could redirect the fetch could point this display at
  // somebody else's binary.
  FirmwareOffer offer = goodOffer();
  offer.path = "http://192.0.2.9/evil.bin";
  TEST_ASSERT_EQUAL(UpdateVerdict::kMalformed,
                    evaluateOffer(offer, "0.2.0", quietContext()));
}

void test_an_image_too_large_for_a_slot_is_refused_before_the_first_byte(void) {
  FirmwareOffer offer = goodOffer();
  offer.sizeBytes = kAppSlotBytes + 1;
  TEST_ASSERT_EQUAL(UpdateVerdict::kTooLarge,
                    evaluateOffer(offer, "0.2.0", quietContext()));
  offer.sizeBytes = kAppSlotBytes;
  TEST_ASSERT_EQUAL(UpdateVerdict::kGo,
                    evaluateOffer(offer, "0.2.0", quietContext()));
}

void test_an_image_of_no_size_is_refused(void) {
  FirmwareOffer offer = goodOffer();
  offer.sizeBytes = 0;
  TEST_ASSERT_EQUAL(UpdateVerdict::kMalformed,
                    evaluateOffer(offer, "0.2.0", quietContext()));
}

void test_the_slot_size_matches_the_partition_table(void) {
  // If this fails, partitions/inside-observer.csv and ota_policy.h have drifted
  // and an oversized image would be caught by Update.begin() on the device
  // instead of by the offer check, which is later and less clear.
  TEST_ASSERT_EQUAL_UINT32(0x1F0000u, kAppSlotBytes);
  TEST_ASSERT_EQUAL_UINT32(2031616u, kAppSlotBytes);
}

// ---------------------------------------------------------------------------
// Whether *now* is the moment
// ---------------------------------------------------------------------------

void test_an_update_waits_while_somebody_is_at_the_display(void) {
  UpdateContext context = quietContext();
  context.msSinceTouch = 5000;
  TEST_ASSERT_EQUAL(UpdateVerdict::kDeferBusy,
                    evaluateOffer(goodOffer(), "0.2.0", context));
  // ...and stops waiting once they have gone.
  context.msSinceTouch = kQuietAfterTouchMs;
  TEST_ASSERT_EQUAL(UpdateVerdict::kGo,
                    evaluateOffer(goodOffer(), "0.2.0", context));
}

void test_an_update_never_starts_over_the_settings_or_portal_screens(void) {
  UpdateContext context = quietContext();
  context.onFeedScreen = false;
  TEST_ASSERT_EQUAL(UpdateVerdict::kDeferBusy,
                    evaluateOffer(goodOffer(), "0.2.0", context));

  context = quietContext();
  context.portalRunning = true;
  TEST_ASSERT_EQUAL(UpdateVerdict::kDeferBusy,
                    evaluateOffer(goodOffer(), "0.2.0", context));
}

void test_an_update_waits_while_something_is_happening_in_the_garden(void) {
  // The one time-critical moment an ambient display has is a detection
  // appearing. Going black a second after a barn owl lands is the worst
  // possible ninety seconds to pick.
  UpdateContext context = quietContext();
  context.newestRowAgeSeconds = 3;
  TEST_ASSERT_EQUAL(UpdateVerdict::kDeferActive,
                    evaluateOffer(goodOffer(), "0.2.0", context));

  context.newestRowAgeSeconds = kQuietAfterDetectionSeconds;
  TEST_ASSERT_EQUAL(UpdateVerdict::kGo,
                    evaluateOffer(goodOffer(), "0.2.0", context));
}

void test_an_empty_feed_does_not_read_as_a_fresh_detection(void) {
  UpdateContext context = quietContext();
  context.newestRowAgeSeconds = -1;  // no rows at all
  TEST_ASSERT_EQUAL(UpdateVerdict::kGo,
                    evaluateOffer(goodOffer(), "0.2.0", context));
}

void test_no_feed_means_no_update(void) {
  UpdateContext context = quietContext();
  context.stationReachable = false;
  TEST_ASSERT_EQUAL(UpdateVerdict::kDeferOffline,
                    evaluateOffer(goodOffer(), "0.2.0", context));
}

void test_a_bad_offer_is_reported_as_bad_rather_than_as_deferred(void) {
  // Ordering matters for the log more than for the outcome: an operator
  // watching a rollout that never lands needs to see "malformed", not
  // "deferred", which reads as a station being polite.
  UpdateContext context = quietContext();
  context.stationReachable = false;
  context.msSinceTouch = 0;
  FirmwareOffer offer = goodOffer();
  offer.sha256.clear();
  TEST_ASSERT_EQUAL(UpdateVerdict::kMalformed,
                    evaluateOffer(offer, "0.2.0", context));
}

void test_deferrals_and_refusals_are_told_apart(void) {
  // main.cpp drops the offer on a refusal and keeps it on a deferral. Getting
  // this backwards either retries a hopeless offer forever or throws away a
  // good one because somebody was standing at the display.
  TEST_ASSERT_TRUE(isDeferral(UpdateVerdict::kDeferBusy));
  TEST_ASSERT_TRUE(isDeferral(UpdateVerdict::kDeferActive));
  TEST_ASSERT_TRUE(isDeferral(UpdateVerdict::kDeferOffline));
  TEST_ASSERT_FALSE(isDeferral(UpdateVerdict::kMalformed));
  TEST_ASSERT_FALSE(isDeferral(UpdateVerdict::kNotNewer));
  TEST_ASSERT_FALSE(isDeferral(UpdateVerdict::kTooLarge));
  TEST_ASSERT_FALSE(isDeferral(UpdateVerdict::kGo));
  TEST_ASSERT_FALSE(isDeferral(UpdateVerdict::kNoOffer));
}

void test_no_offer_is_not_an_error(void) {
  TEST_ASSERT_EQUAL(UpdateVerdict::kNoOffer,
                    evaluateOffer(FirmwareOffer{}, "0.2.0", quietContext()));
}

void test_a_running_version_we_cannot_order_blocks_every_update(void) {
  // Belt and braces on the build itself: if someone sets
  // INSIDE_OBSERVER_VERSION to something unorderable, this display stops
  // accepting updates rather than accepting all of them.
  TEST_ASSERT_EQUAL(UpdateVerdict::kNotNewer,
                    evaluateOffer(goodOffer(), "0.2.0-dev", quietContext()));
}

// ---------------------------------------------------------------------------
// Probation
// ---------------------------------------------------------------------------

void test_a_cable_flashed_image_has_nothing_to_prove(void) {
  ProbationContext context;
  context.onProbation = false;
  context.msSinceBoot = 99999999;
  TEST_ASSERT_EQUAL(ProbationVerdict::kNotOnProbation,
                    evaluateProbation(context));
}

void test_a_new_image_is_confirmed_the_moment_it_reaches_the_station(void) {
  ProbationContext context;
  context.onProbation = true;
  context.stationHelloSeen = true;
  context.msSinceBoot = 4000;
  TEST_ASSERT_EQUAL(ProbationVerdict::kConfirm, evaluateProbation(context));
}

void test_a_new_image_waits_before_the_deadline(void) {
  ProbationContext context;
  context.onProbation = true;
  context.msSinceBoot = kProbationDeadlineMs - 1;
  TEST_ASSERT_EQUAL(ProbationVerdict::kWaiting, evaluateProbation(context));
}

void test_a_new_image_that_never_reaches_the_station_rolls_itself_back(void) {
  ProbationContext context;
  context.onProbation = true;
  context.msSinceBoot = kProbationDeadlineMs;
  TEST_ASSERT_EQUAL(ProbationVerdict::kRollBack, evaluateProbation(context));
}

void test_completing_the_provisioning_portal_counts_as_proof(void) {
  // The portal is the recovery path that needs no cable. A build that served it
  // successfully is not a bricked build, and rolling back on the restart that
  // follows would throw away credentials the operator has just typed.
  ProbationContext context;
  context.onProbation = true;
  context.stationHelloSeen = false;
  context.portalCompleted = true;
  context.msSinceBoot = kProbationDeadlineMs * 4;
  TEST_ASSERT_EQUAL(ProbationVerdict::kConfirm, evaluateProbation(context));
}

void test_the_probation_deadline_is_generous_enough_for_a_slow_station(void) {
  // Ten minutes. A station restarting at the same moment, a DHCP lease and a
  // domestic 2.4 GHz band having a bad afternoon are all several minutes of
  // not-the-firmware's-fault, and a rollback over one of those would make a
  // working update path look broken.
  TEST_ASSERT_EQUAL_UINT32(600000u, kProbationDeadlineMs);
}

// ---------------------------------------------------------------------------
// The frame the offer arrives in
// ---------------------------------------------------------------------------

void test_an_update_frame_parses(void) {
  const std::string frame =
      std::string("{\"t\":\"u\",\"fv\":\"0.2.1\",\"sha\":\"") + kGoodDigest +
      "\",\"sz\":993284,\"p\":\"/api/v1/firmware/image\"}";
  PushFrame parsed;
  TEST_ASSERT_TRUE(parsePushFrame(frame.c_str(), parsed));
  TEST_ASSERT_EQUAL(PushFrameType::kFirmwareOffer, parsed.type);
  TEST_ASSERT_EQUAL_STRING("0.2.1", parsed.offer.version.c_str());
  TEST_ASSERT_EQUAL_STRING(kGoodDigest, parsed.offer.sha256.c_str());
  TEST_ASSERT_EQUAL_STRING("/api/v1/firmware/image", parsed.offer.path.c_str());
  TEST_ASSERT_EQUAL_UINT32(993284u, parsed.offer.sizeBytes);
  // And nothing about it disturbs the feed.
  TEST_ASSERT_EQUAL_UINT32(0u, static_cast<uint32_t>(parsed.items.size()));
}

void test_an_update_frame_with_no_version_is_refused_by_the_parser(void) {
  PushFrame parsed;
  TEST_ASSERT_FALSE(parsePushFrame("{\"t\":\"u\",\"sz\":10}", parsed));
}

void test_a_rubbish_offer_survives_parsing_and_is_stopped_by_the_policy(void) {
  // The parser reports what arrived; evaluateOffer is the only thing that
  // decides. Two stages, so a frame that is odd but well-formed is refused with
  // a reason rather than dropped silently.
  const char* frame =
      "{\"t\":\"u\",\"fv\":\"9.9.9\",\"sha\":\"nope\",\"sz\":1,"
      "\"p\":\"/api/v1/firmware/image\"}";
  PushFrame parsed;
  TEST_ASSERT_TRUE(parsePushFrame(frame, parsed));
  TEST_ASSERT_EQUAL(UpdateVerdict::kMalformed,
                    evaluateOffer(parsed.offer, "0.2.0", quietContext()));
}

void test_the_feed_frames_still_carry_no_offer(void) {
  PushFrame parsed;
  TEST_ASSERT_TRUE(parsePushFrame(
      "{\"t\":\"d\",\"n\":\"Common Woodpigeon\",\"at\":1786263086}", parsed));
  TEST_ASSERT_FALSE(parsed.offer.present());
}

int main(int, char**) {
  UNITY_BEGIN();

  RUN_TEST(test_versions_order_numerically_not_lexically);
  RUN_TEST(test_missing_components_read_as_zero);
  RUN_TEST(test_a_version_this_build_cannot_order_is_refused_outright);
  RUN_TEST(test_an_unorderable_version_compares_as_equal_so_it_is_never_newer);

  RUN_TEST(test_a_digest_must_be_64_lowercase_hex_characters);

  RUN_TEST(test_a_newer_build_on_a_quiet_display_is_installed);
  RUN_TEST(test_the_running_build_is_never_reinstalled_over_itself);
  RUN_TEST(test_an_older_build_is_refused);
  RUN_TEST(test_an_offer_with_no_usable_digest_is_refused_before_anything_is_written);
  RUN_TEST(test_an_offer_that_names_a_host_rather_than_a_path_is_refused);
  RUN_TEST(test_an_image_too_large_for_a_slot_is_refused_before_the_first_byte);
  RUN_TEST(test_an_image_of_no_size_is_refused);
  RUN_TEST(test_the_slot_size_matches_the_partition_table);

  RUN_TEST(test_an_update_waits_while_somebody_is_at_the_display);
  RUN_TEST(test_an_update_never_starts_over_the_settings_or_portal_screens);
  RUN_TEST(test_an_update_waits_while_something_is_happening_in_the_garden);
  RUN_TEST(test_an_empty_feed_does_not_read_as_a_fresh_detection);
  RUN_TEST(test_no_feed_means_no_update);
  RUN_TEST(test_a_bad_offer_is_reported_as_bad_rather_than_as_deferred);
  RUN_TEST(test_deferrals_and_refusals_are_told_apart);
  RUN_TEST(test_no_offer_is_not_an_error);
  RUN_TEST(test_a_running_version_we_cannot_order_blocks_every_update);

  RUN_TEST(test_a_cable_flashed_image_has_nothing_to_prove);
  RUN_TEST(test_a_new_image_is_confirmed_the_moment_it_reaches_the_station);
  RUN_TEST(test_a_new_image_waits_before_the_deadline);
  RUN_TEST(test_a_new_image_that_never_reaches_the_station_rolls_itself_back);
  RUN_TEST(test_completing_the_provisioning_portal_counts_as_proof);
  RUN_TEST(test_the_probation_deadline_is_generous_enough_for_a_slow_station);

  RUN_TEST(test_an_update_frame_parses);
  RUN_TEST(test_an_update_frame_with_no_version_is_refused_by_the_parser);
  RUN_TEST(test_a_rubbish_offer_survives_parsing_and_is_stopped_by_the_policy);
  RUN_TEST(test_the_feed_frames_still_carry_no_offer);

  return UNITY_END();
}
