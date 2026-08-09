// The transport seam.
//
// The display does not know how it learns about detections. There are two
// implementations:
//
//   PushStationSource  a WebSocket to the station's `/api/v1/display` channel.
//                      The default since ADR-038. Tens of bytes per detection,
//                      pushed when it happens.
//   HttpStationSource  polling of the read-only REST API. ADR-023's original
//                      transport, kept as the fallback for whenever the socket
//                      is down - and kept *exercised*, so it stays real rather
//                      than theoretical.
//
// The station's MQTT publisher exists now, but this display deliberately does
// not use it: the broker runs on the Home Assistant box, and routing two devices
// that are the same system through a third would make the wall display depend on
// something neither of them needs. ADR-038 records that choice.
#pragma once

#include <string>
#include <vector>

#include "model/detection_feed.h"
#include "model/settings.h"
#include "model/station_clock.h"
#include "model/station_health.h"

namespace observer {

// Everything the screen needs, in one struct. An update that partially fails
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

  // The monotonic base every "4s ago" on the screen is counted from. Lives here
  // rather than in a source because both transports feed it and the display
  // reads it every second, whichever one is currently connected.
  StationClock clock;

  // Which transport last delivered. Logged, and shown on the settings page, so
  // "it is running on the fallback" is a visible fact rather than a guess.
  const char* transport = "none";
};

class StationSource {
 public:
  virtual ~StationSource() = default;

  // Called once, after WiFi is up.
  virtual void begin(const Settings& settings) { (void)settings; }

  // Brings `snapshot` up to date. Returns true if the station answered.
  //
  // Implementations must never leave `snapshot` half-updated in a way that would
  // present stale data as fresh: on failure they set health.state to kOffline
  // and leave the previous feed visible, which is honest as long as the status
  // strip says the station is unreachable.
  virtual bool poll(const Settings& settings, StationSnapshot& snapshot) = 0;

  // How soon the main loop should call poll() again. A polling source answers
  // with its poll interval; a push source answers with "as often as you can",
  // because its poll() is only servicing a socket that is mostly idle.
  virtual uint32_t serviceIntervalMs(const Settings& settings) const = 0;

  // True while this source is actually delivering. Drives the fallback.
  virtual bool connected() const = 0;

  // Human-readable name of the transport, shown on the config page.
  virtual const char* transportName() const = 0;
};

// Polls GET /api/v1/health, /api/v1/detections and /api/v1/history over plain
// HTTP on the local network. Responses are stream-parsed through ArduinoJson
// filters so a 70 kB detections body never lands in the ESP32's heap.
//
// Retained as the fallback path, not as the primary one. Measured cost per
// cycle on the live station, which is what ADR-038 exists to stop paying:
// ~315 ms of server query time and ~127 kB of payload, to render six rows.
class HttpStationSource : public StationSource {
 public:
  bool poll(const Settings& settings, StationSnapshot& snapshot) override;
  uint32_t serviceIntervalMs(const Settings& settings) const override {
    return static_cast<uint32_t>(settings.pollSeconds) * 1000UL;
  }
  bool connected() const override { return lastPollOk_; }
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

 private:
  bool lastPollOk_ = false;
};

}  // namespace observer
