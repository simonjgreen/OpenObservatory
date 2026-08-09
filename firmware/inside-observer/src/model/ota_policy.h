// When this display may replace its own firmware, and when it must put the
// previous one back. ADR-050.
//
// PURE. No Arduino, no WiFi, no `Update`, no flash. Everything here is a
// decision; `src/ota.{h,cpp}` is the machinery that acts on one. That split is
// not tidiness: a bricked display is a physical trip to a shelf in someone's
// house, which is the exact cost this whole feature exists to buy out, so the
// rules that decide "install this" and "put the old one back" are the part that
// has to be tested on a laptop rather than discovered on the glass.
//
// Two independent decisions live here.
//
//   evaluateOffer()     -- may we install what the station just offered?
//                          Refuses anything malformed, anything not strictly
//                          newer, and anything that cannot fit the slot; then
//                          defers while a person is using the display or while
//                          something is actually happening in the garden.
//
//   evaluateProbation() -- we are running an image that has never proved
//                          itself. Confirm it, keep waiting, or roll back?
//
// The second is the one that matters. The ESP32 bootloader in this build has
// CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y (verified in
// framework-arduinoespressif32 3.20017's tools/sdk/esp32/sdkconfig), so a
// freshly written image boots exactly once in ESP_OTA_IMG_PENDING_VERIFY. If it
// reboots without being marked valid, the bootloader puts the previous slot
// back by itself. That covers a crash loop for free. It does *not* cover an
// image that runs happily and simply cannot reach the station -- that one never
// reboots, so it never gets rolled back. Hence a deadline.
#pragma once

#include <cstdint>
#include <string>

namespace observer {

// The largest image either app slot can hold: 0x1F0000 = 2,031,616 bytes.
// Mirrors partitions/inside-observer.csv, and is checked before a single byte
// is written so an oversized image is refused rather than discovered halfway.
constexpr uint32_t kAppSlotBytes = 0x1F0000;

// What the station offered, parsed off the `u` frame.
struct FirmwareOffer {
  std::string version;   // dotted numeric, e.g. "0.2.0"
  std::string sha256;    // 64 lowercase hex characters, of the whole image
  std::string path;      // e.g. "/api/v1/firmware/image"
  uint32_t sizeBytes = 0;

  bool present() const { return !version.empty(); }
};

// A version string this build is willing to reason about: 1-4 dot-separated
// runs of digits, nothing else. Deliberately narrow. A suffix scheme this
// firmware does not understand ("0.2.0-rc1") would have to be ordered by
// guesswork, and guessing which of two images is newer is how a display
// installs an older build over a newer one and then refuses to take the newer
// one back. Refusing to parse it is the honest answer.
bool isPlausibleVersion(const std::string& value);

// Negative, zero or positive as `a` is older than, the same as, or newer than
// `b`. Missing components read as zero, so "0.2" == "0.2.0". Both arguments
// must satisfy isPlausibleVersion(); the result is 0 for anything else, which
// callers must treat as "not newer".
int compareVersions(const std::string& a, const std::string& b);

// Exactly 64 lowercase hex characters. Anything else means we could not verify
// what we downloaded, and an unverifiable image is not installed.
bool isSha256Hex(const std::string& value);

// What the display is doing right now, as far as an update is concerned.
struct UpdateContext {
  //: The calm feed. False while settings, the number pad, the boot screen or
  //: the provisioning portal are up.
  bool onFeedScreen = true;
  //: The provisioning portal is serving. Never update over the top of the one
  //: recovery path that does not need a cable.
  bool portalRunning = false;
  //: A usable push feed. Also the only way we could fetch the image at all.
  bool stationReachable = true;
  //: Since the last touch. A finger on the glass means a person is here.
  uint32_t msSinceTouch = 0xFFFFFFFFu;
  //: Age of the newest row on the screen, in seconds. Negative when the feed
  //: is empty.
  int64_t newestRowAgeSeconds = -1;
};

// A person is at the display if they touched it within this long. Two minutes:
// long enough to cover reading the feed and thinking about it, short enough
// that a single accidental brush does not defer an update for an evening.
constexpr uint32_t kQuietAfterTouchMs = 120000;

// Something is happening in the garden if the newest row is younger than this.
// An ambient display's one time-critical moment is a detection appearing; going
// black for ninety seconds a second after a barn owl lands is the single worst
// time this could pick.
constexpr int64_t kQuietAfterDetectionSeconds = 60;

enum class UpdateVerdict : uint8_t {
  kNoOffer,       // nothing on the table
  kMalformed,     // version, digest or size we will not act on
  kNotNewer,      // same or older than what is running
  kTooLarge,      // will not fit an app slot
  kDeferBusy,     // a person is using the display
  kDeferActive,   // a detection is on the glass right now
  kDeferOffline,  // no feed, so no image either
  kGo,
};

// One line, for the serial log. Never rendered on the glass.
const char* verdictReason(UpdateVerdict verdict);

// True for the verdicts that mean "ask again in a minute" rather than "no".
bool isDeferral(UpdateVerdict verdict);

UpdateVerdict evaluateOffer(const FirmwareOffer& offer,
                            const std::string& runningVersion,
                            const UpdateContext& context);

// ---------------------------------------------------------------------------
// Probation
// ---------------------------------------------------------------------------

// How long a freshly installed image has to prove it can reach the station
// before it is rolled back. Ten minutes, not one: a station restarting at the
// same moment, a DHCP lease, or a 2.4 GHz band having a bad afternoon are all
// several minutes' worth of not-the-firmware's-fault, and rolling back over one
// of those would make the update path look broken when it worked.
constexpr uint32_t kProbationDeadlineMs = 600000;

struct ProbationContext {
  //: The running image is in ESP_OTA_IMG_PENDING_VERIFY.
  bool onProbation = false;
  //: A hello frame has been understood since boot. Not "WiFi associated" and
  //: not "socket opened" -- the thing this firmware exists to do is show the
  //: station's feed, so the proof it works is the station's feed arriving.
  bool stationHelloSeen = false;
  //: The operator completed the provisioning portal on this boot. That is a
  //: pass: the portal is the recovery path, they reached it, it worked, and a
  //: rollback on the restart that follows would throw away the credentials
  //: they just typed.
  bool portalCompleted = false;
  uint32_t msSinceBoot = 0;
};

enum class ProbationVerdict : uint8_t {
  kNotOnProbation,
  kWaiting,
  kConfirm,
  kRollBack,
};

ProbationVerdict evaluateProbation(const ProbationContext& context);

}  // namespace observer
