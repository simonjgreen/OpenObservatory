// The transport seam.
//
// The display does not know how it learns about detections. Today there is one
// implementation, HttpStationSource, which polls the station's read-only REST
// API. The station's MQTT publisher is being built separately and does not
// exist yet; when it does, an MqttStationSource implementing this same
// interface can be dropped in without the UI changing, and the broker settings
// are already carried in Settings::mqtt.
//
// ADR-023 records why polling ships first.
#pragma once

#include <string>
#include <vector>

#include "model/detection_feed.h"
#include "model/settings.h"
#include "model/station_health.h"

namespace observer {

// Everything the screen needs, in one struct. A poll that partially fails
// updates what it could and leaves the rest alone, so a hiccup in one endpoint
// does not blank the feed.
struct StationSnapshot {
  StationHealth health;
  std::vector<FeedItem> feed;

  int speciesToday = -1;  // -1 means "not known yet", which is not "zero"
  int32_t utcOffsetSeconds = 0;
  bool offsetKnown = false;

  bool everSucceeded = false;
  uint32_t lastSuccessMillis = 0;
  uint32_t consecutiveFailures = 0;
  std::string lastError;
};

class StationSource {
 public:
  virtual ~StationSource() = default;

  // Refreshes `snapshot` in place. Returns true if the station answered.
  // Implementations must never leave `snapshot` half-updated in a way that
  // would present stale data as fresh: on failure they set health.state to
  // kOffline and leave the previous feed visible, which is honest as long as
  // the status strip says the station is unreachable.
  virtual bool poll(const Settings& settings, StationSnapshot& snapshot) = 0;

  // Human-readable name of the transport, shown on the config page.
  virtual const char* transportName() const = 0;
};

// Polls GET /api/v1/health, /api/v1/detections and /api/v1/history over plain
// HTTP on the local network. Responses are stream-parsed through ArduinoJson
// filters so a 70 kB detections body never lands in the ESP32's heap.
class HttpStationSource : public StationSource {
 public:
  bool poll(const Settings& settings, StationSnapshot& snapshot) override;
  const char* transportName() const override { return "HTTP polling"; }

  // Rows the feed can display. Kept here because it bounds the request size
  // as well as the layout.
  static constexpr size_t kFeedRows = 6;
  // How many recent detections to ask for. Enough that a garden dominated by
  // one vocal species still yields several distinct rows after collapsing.
  static constexpr int kBirdRequestLimit = 40;
  static constexpr int kBatRequestLimit = 8;
  // Milliseconds. The station is on the LAN; anything slower than this is a
  // problem worth showing rather than waiting through.
  static constexpr uint16_t kHttpTimeoutMs = 8000;
};

}  // namespace observer
