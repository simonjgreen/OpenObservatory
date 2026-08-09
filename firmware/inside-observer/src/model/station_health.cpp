#include "model/station_health.h"

#include <cstring>

namespace observer {
namespace {

const char* stringOr(JsonVariantConst v, const char* fallback) {
  const char* s = v.is<const char*>() ? v.as<const char*>() : nullptr;
  return (s != nullptr && s[0] != '\0') ? s : fallback;
}

}  // namespace

void parseHealth(JsonObjectConst response, StationHealth& out) {
  JsonObjectConst capture = response["capture"];
  out.captureState = stringOr(capture["state"], "unknown");
  out.sourceKind = stringOr(capture["source_kind"], "unknown");
  out.liveHardware = capture["is_live_hardware"] | false;

  const char* status = stringOr(response["status"], "unknown");
  JsonArrayConst problems = response["problems"].as<JsonArrayConst>();

  if (!out.liveHardware) {
    // ADR-020's motivating incident in miniature: the station keeps detecting
    // against a synthetic source when the microphone goes away, and those rows
    // are not observations of the garden. Say so on the glass.
    out.state = StationState::kDegraded;
    out.detail = (out.sourceKind == "synthetic")
                     ? "NO MICROPHONE - SYNTHETIC SOURCE"
                     : "NOT LISTENING TO THE MICROPHONE";
    return;
  }

  if (problems.size() > 0) {
    // `problems` entries are strings today but the endpoint is young; accept
    // an object with a `detail` too rather than rendering "null".
    out.state = StationState::kDegraded;
    const char* first = problems[0].is<const char*>()
                            ? problems[0].as<const char*>()
                            : stringOr(problems[0]["detail"], "");
    out.detail = (first != nullptr && first[0] != '\0')
                     ? std::string(first)
                     : std::string("STATION REPORTS A PROBLEM");
    return;
  }

  if (std::strcmp(status, "ok") != 0) {
    out.state = StationState::kDegraded;
    out.detail = "STATION STATUS: " + std::string(status);
    return;
  }

  if (out.captureState != "capturing") {
    out.state = StationState::kDegraded;
    out.detail = "CAPTURE " + out.captureState;
    return;
  }

  out.state = StationState::kListening;
  out.detail.clear();
}

void buildHealthFilter(JsonDocument& filter) {
  filter["status"] = true;
  // ADR-038: the only "now" the HTTP fallback can anchor relative times to.
  filter["checked_at"] = true;
  filter["problems"] = true;
  JsonObject capture = filter["capture"].to<JsonObject>();
  capture["state"] = true;
  capture["source_kind"] = true;
  capture["is_live_hardware"] = true;
  capture["device_label"] = true;
}

std::string stateLabel(StationState state) {
  switch (state) {
    case StationState::kConnecting:
      return "CONNECTING";
    case StationState::kOffline:
      return "STATION OFFLINE";
    case StationState::kDegraded:
      return "DEGRADED";
    case StationState::kListening:
      return "LISTENING";
  }
  return "UNKNOWN";
}

}  // namespace observer
