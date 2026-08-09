// The `/api/v1/display` wire format, parsed.
//
// ADR-038. The station pushes three kinds of text frame, all compact JSON with
// one- and two-character keys and no whitespace. Measured on the wire: 40-57
// bytes for a detection, 43 for a heartbeat, 150 for the whole connect
// snapshot - so every frame fits inside a single Ethernet MTU with room to
// spare, and this device never reassembles anything.
//
//   hello       {"t":"h","v":1,"now":1786196799,"hb":10,"st":"L","sp":14,
//                "f":[{"n":"Common Woodpigeon","at":1786196799,"r":3},
//                     {"b":1,"at":1786226651,"k":36.2}]}
//   detection   {"t":"d","n":"Common Woodpigeon","at":1786196799,"sp":15}
//   heartbeat   {"t":"s","now":1786196799,"st":"L","sp":14}
//
// Keys:
//   t    frame type: "h" hello, "d" detection, "s" heartbeat
//   v    wire version. Hello only. A version this build does not know is
//        refused outright rather than half-parsed, and the display falls back
//        to HTTP polling.
//   now  the station's Unix epoch seconds. Hello and heartbeat. This is the
//        only clock this device has; see StationClock.
//   hb   heartbeat period in seconds. Hello only. The display treats three
//        missed heartbeats as "the feed is stale" and says so on the glass.
//   st   "L" listening, "D" degraded. Offline is a fact only this device can
//        know, so the station never sends it.
//   d    the station's own words for a degraded state. Absent when listening.
//   sp   distinct species today. Absent from a detection frame when the count
//        did not move. -1 is never sent; the display's own "not counted yet".
//   f    the connect snapshot: the rows the screen has space for, already
//        collapsed. Hello only.
//
// Row keys, shared by `f` entries and by a detection frame:
//   n    species display name. Absent on a bat pass.
//   at   event start, whole Unix epoch seconds, UTC.
//   b    1 when this is a bat pass.
//   k    peak frequency in kHz, one decimal. Bat passes only.
//   r    detections collapsed into this row. Absent when 1.
//
// There is no score field, and there is no way to add one without changing this
// file: ADR-023's rule is structural on this wire, not a convention.
//
// Pure. No Arduino, no WiFi, no socket - host-tested against the exact byte
// sequences the Python side emits.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include <ArduinoJson.h>

#include "model/detection_feed.h"
#include "model/station_health.h"

namespace observer {

//: Bumped in lockstep with WIRE_VERSION in src/open_observatory/display_channel.py.
constexpr int kPushWireVersion = 1;

enum class PushFrameType : uint8_t {
  kUnknown,
  kHello,
  kDetection,
  kHeartbeat,
};

struct PushFrame {
  PushFrameType type = PushFrameType::kUnknown;

  int version = 0;             // hello only; 0 when absent
  int64_t serverNow = 0;       // hello and heartbeat
  bool hasServerNow = false;
  int heartbeatSeconds = 0;    // hello only; 0 when absent

  StationState state = StationState::kListening;
  bool hasState = false;
  std::string detail;

  int speciesToday = -1;       // -1 = the frame did not carry one
  std::vector<FeedItem> items; // hello: the snapshot. detection: exactly one.
};

// Parses one frame. Returns false for anything that is not a frame this build
// understands - malformed JSON, an unknown `t`, or a hello announcing a wire
// version this firmware was not written against. A false return is a reason to
// fall back to HTTP, not a reason to blank the screen.
bool parsePushFrame(const char* json, PushFrame& out);

// One `f` entry or one detection frame's row fields, as a FeedItem.
//
// The honesty rules are re-applied here rather than trusted to the station: a
// frame carrying `b` becomes "Bat pass" with no name, whatever else the frame
// says, so no future server change can put a species name on a pass.
bool pushItem(JsonObjectConst row, FeedItem& out);

}  // namespace observer
