#include "model/push_frame.h"

#include <cstring>

#include "model/time_utils.h"

namespace observer {
namespace {

const char* stringOr(JsonVariantConst v, const char* fallback) {
  const char* s = v.is<const char*>() ? v.as<const char*>() : nullptr;
  return (s != nullptr && s[0] != '\0') ? s : fallback;
}

}  // namespace

bool pushItem(JsonObjectConst row, FeedItem& out) {
  if (!row["at"].is<int64_t>()) {
    return false;  // an undateable row cannot be placed on a timeline
  }
  out = FeedItem{};
  out.startUtc = row["at"].as<int64_t>();
  const int repeats = row["r"] | 1;
  out.repeats = repeats > 0 ? repeats : 1;

  const bool isBat = (row["b"] | 0) != 0;
  if (isBat) {
    // No name, no score, whatever else the frame contains. `ultrasonic-pass-v1`
    // detects passes, not species (ADR-013), and this is where that stops being
    // a promise the station makes and becomes one the glass keeps.
    out.kind = FeedItemKind::kBatPass;
    out.title = "Bat pass";
    out.detail = formatPeakFrequency((row["k"] | 0.0) * 1000.0);
    return true;
  }

  const char* name = stringOr(row["n"], "");
  if (name[0] == '\0') {
    return false;  // neither a pass nor a named thing: nothing to draw
  }
  out.kind = FeedItemKind::kSpecies;
  out.title = name;
  out.detail.clear();
  return true;
}

bool parsePushFrame(const char* json, PushFrame& out) {
  if (json == nullptr || json[0] == '\0') {
    return false;
  }
  // No filter: these frames are tens of bytes. The whole reason this channel
  // exists is that nothing arriving on it needs to be filtered away.
  JsonDocument doc;
  if (deserializeJson(doc, json)) {
    return false;
  }
  JsonObjectConst root = doc.as<JsonObjectConst>();
  const char* type = stringOr(root["t"], "");

  out = PushFrame{};
  if (std::strcmp(type, "h") == 0) {
    out.type = PushFrameType::kHello;
    out.version = root["v"] | 0;
    if (out.version != kPushWireVersion) {
      // A frame shaped by rules this build does not know. Refusing it is what
      // makes the HTTP fallback real: a half-understood feed that renders
      // plausible-looking rows would be worse than no feed at all.
      return false;
    }
    out.heartbeatSeconds = root["hb"] | 0;
    for (JsonObjectConst row : root["f"].as<JsonArrayConst>()) {
      FeedItem item;
      if (pushItem(row, item)) {
        out.items.push_back(std::move(item));
      }
    }
  } else if (std::strcmp(type, "d") == 0) {
    out.type = PushFrameType::kDetection;
    FeedItem item;
    if (!pushItem(root, item)) {
      return false;
    }
    out.items.push_back(std::move(item));
  } else if (std::strcmp(type, "s") == 0) {
    out.type = PushFrameType::kHeartbeat;
  } else {
    return false;
  }

  if (root["now"].is<int64_t>()) {
    out.serverNow = root["now"].as<int64_t>();
    out.hasServerNow = true;
  }
  if (root["sp"].is<int>()) {
    out.speciesToday = root["sp"].as<int>();
  }
  const char* state = stringOr(root["st"], "");
  if (state[0] != '\0') {
    out.hasState = true;
    out.state = (state[0] == 'D') ? StationState::kDegraded
                                  : StationState::kListening;
    out.detail = stringOr(root["d"], "");
    if (out.state == StationState::kDegraded && out.detail.empty()) {
      out.detail = "STATION REPORTS A PROBLEM";
    }
  }
  return true;
}

}  // namespace observer
