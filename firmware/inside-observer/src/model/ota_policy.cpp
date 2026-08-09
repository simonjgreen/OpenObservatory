#include "model/ota_policy.h"

#include <cstdlib>
#include <vector>

namespace observer {
namespace {

// Splits a dotted version into its numeric components. Returns false for
// anything isPlausibleVersion would reject, so callers never see a half-parsed
// version they might then order wrongly.
bool components(const std::string& value, std::vector<uint32_t>& out) {
  out.clear();
  if (value.empty() || value.size() > 15) {
    return false;
  }
  uint32_t current = 0;
  size_t digits = 0;
  for (const char c : value) {
    if (c == '.') {
      if (digits == 0) {
        return false;  // empty component: "1..2", ".1", "1."
      }
      out.push_back(current);
      current = 0;
      digits = 0;
      continue;
    }
    if (c < '0' || c > '9') {
      return false;
    }
    if (digits >= 5) {
      return false;  // a five-digit component is not a version, it is a typo
    }
    current = current * 10 + static_cast<uint32_t>(c - '0');
    ++digits;
  }
  if (digits == 0) {
    return false;
  }
  out.push_back(current);
  return out.size() <= 4;
}

}  // namespace

bool isPlausibleVersion(const std::string& value) {
  std::vector<uint32_t> parts;
  return components(value, parts);
}

int compareVersions(const std::string& a, const std::string& b) {
  std::vector<uint32_t> left;
  std::vector<uint32_t> right;
  if (!components(a, left) || !components(b, right)) {
    return 0;
  }
  const size_t width = left.size() > right.size() ? left.size() : right.size();
  for (size_t i = 0; i < width; ++i) {
    const uint32_t l = i < left.size() ? left[i] : 0;
    const uint32_t r = i < right.size() ? right[i] : 0;
    if (l != r) {
      return l < r ? -1 : 1;
    }
  }
  return 0;
}

bool isSha256Hex(const std::string& value) {
  if (value.size() != 64) {
    return false;
  }
  for (const char c : value) {
    const bool digit = c >= '0' && c <= '9';
    const bool lower = c >= 'a' && c <= 'f';
    if (!digit && !lower) {
      return false;
    }
  }
  return true;
}

const char* verdictReason(UpdateVerdict verdict) {
  switch (verdict) {
    case UpdateVerdict::kNoOffer:
      return "no offer";
    case UpdateVerdict::kMalformed:
      return "offer malformed (version, digest or path)";
    case UpdateVerdict::kNotNewer:
      return "offered build is not newer than the running one";
    case UpdateVerdict::kTooLarge:
      return "image does not fit an app slot";
    case UpdateVerdict::kDeferBusy:
      return "deferred: someone is using the display";
    case UpdateVerdict::kDeferActive:
      return "deferred: a detection is on the glass";
    case UpdateVerdict::kDeferOffline:
      return "deferred: no feed from the station";
    case UpdateVerdict::kGo:
      return "installing";
  }
  return "unknown";
}

bool isDeferral(UpdateVerdict verdict) {
  return verdict == UpdateVerdict::kDeferBusy ||
         verdict == UpdateVerdict::kDeferActive ||
         verdict == UpdateVerdict::kDeferOffline;
}

UpdateVerdict evaluateOffer(const FirmwareOffer& offer,
                            const std::string& runningVersion,
                            const UpdateContext& context) {
  if (!offer.present()) {
    return UpdateVerdict::kNoOffer;
  }
  // Everything about the offer is checked before anything about the moment, so
  // a rubbish offer is reported as rubbish rather than as "deferred", which
  // would look like a station that is merely being polite.
  if (!isPlausibleVersion(offer.version) || !isSha256Hex(offer.sha256) ||
      offer.path.empty() || offer.path[0] != '/' || offer.sizeBytes == 0) {
    return UpdateVerdict::kMalformed;
  }
  if (!isPlausibleVersion(runningVersion) ||
      compareVersions(offer.version, runningVersion) <= 0) {
    // Strictly newer, and only from a running version we can order. Equal is
    // refused too: re-flashing the build already on the glass costs a blank
    // screen and buys nothing.
    return UpdateVerdict::kNotNewer;
  }
  if (offer.sizeBytes > kAppSlotBytes) {
    return UpdateVerdict::kTooLarge;
  }

  // Now the moment. All three of these are deferrals, not refusals: the offer
  // stays on the table and is re-evaluated, because "not right now" is almost
  // always followed by "fine, now" within a few minutes on a device whose
  // normal state is nobody looking at it.
  if (!context.stationReachable) {
    return UpdateVerdict::kDeferOffline;
  }
  if (context.portalRunning || !context.onFeedScreen) {
    return UpdateVerdict::kDeferBusy;
  }
  if (context.msSinceTouch < kQuietAfterTouchMs) {
    return UpdateVerdict::kDeferBusy;
  }
  if (context.newestRowAgeSeconds >= 0 &&
      context.newestRowAgeSeconds < kQuietAfterDetectionSeconds) {
    return UpdateVerdict::kDeferActive;
  }
  return UpdateVerdict::kGo;
}

ProbationVerdict evaluateProbation(const ProbationContext& context) {
  if (!context.onProbation) {
    return ProbationVerdict::kNotOnProbation;
  }
  // Either proof is enough, and they are different proofs. A hello frame means
  // the build does the job. A completed portal means the build got a human out
  // of a WiFi hole without a cable, which is the only thing that matters when
  // there is no feed to reach.
  if (context.stationHelloSeen || context.portalCompleted) {
    return ProbationVerdict::kConfirm;
  }
  if (context.msSinceBoot >= kProbationDeadlineMs) {
    return ProbationVerdict::kRollBack;
  }
  return ProbationVerdict::kWaiting;
}

}  // namespace observer
