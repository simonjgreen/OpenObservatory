// How the display describes the station's condition to a person walking past.
//
// The requirement this file exists to satisfy: a screen that looks silent must
// be distinguishable from a screen that is broken. Three failure shapes get
// three distinct, visible presentations - no feed and no explanation is not an
// acceptable state.
//
// Pure: no Arduino, no WiFi. Host-tested.
#pragma once

#include <string>

#include <ArduinoJson.h>

namespace observer {

enum class StationState : uint8_t {
  kConnecting,  // no successful poll yet since boot
  kOffline,     // the station did not answer
  kDegraded,    // reachable, but not listening to the real microphone
  kListening,   // capturing from hardware, no reported problems
};

struct StationHealth {
  StationState state = StationState::kConnecting;
  // A short, honest line for the status strip. Never empty except when
  // listening normally.
  std::string detail;
  bool liveHardware = false;
  std::string captureState;  // "capturing", "degraded", ...
  std::string sourceKind;    // "alsa", "synthetic", "replay"
};

// Parses `GET /api/v1/health`. `out.state` is set to kDegraded or kListening;
// callers own kOffline and kConnecting, which are transport facts rather than
// anything the station can report about itself.
void parseHealth(JsonObjectConst response, StationHealth& out);

// The filter used to stream-parse the health response on device.
void buildHealthFilter(JsonDocument& filter);

// Short label for the status strip, e.g. "NO MICROPHONE", "STATION OFFLINE".
std::string stateLabel(StationState state);

}  // namespace observer
