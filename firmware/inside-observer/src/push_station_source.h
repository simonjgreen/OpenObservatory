// The push transport: a WebSocket to the station's `/api/v1/display` channel.
//
// ADR-038. What this replaces, measured on the live station: four REST requests
// every 20 s costing ~315 ms of server query time and ~127 kB of payload, of
// which 71 kB was forty full detection records fetched to render six rows. What
// it costs instead: one connection, 150 bytes on connect, 40-57 bytes per
// detection, and a 43-byte heartbeat every ten seconds.
//
// Directly to the Pi, not through the Home Assistant broker: the station and the
// display are the same system, and CLAUDE.md is local-first.
//
// The pure parts - frame parsing, the monotonic clock, feed maintenance - live
// under src/model/ and are host-tested. This file is the Arduino-only shell
// around them: a socket, a bounded inbox, and a staleness clock.
#pragma once

#include <string>
#include <vector>

#include <WebSocketsClient.h>

#include "station_source.h"

namespace observer {

class PushStationSource : public StationSource {
 public:
  void begin(const Settings& settings) override;
  bool poll(const Settings& settings, StationSnapshot& snapshot) override;
  // The socket needs servicing far more often than a poll interval: this is
  // "run the network stack", not "fetch something".
  uint32_t serviceIntervalMs(const Settings& settings) const override {
    (void)settings;
    return 10;
  }
  bool connected() const override { return connected_; }
  const char* transportName() const override { return "push (WebSocket)"; }

  // True once a hello frame has been understood. A socket that connects but
  // speaks a wire version this build does not know is *not* usable, and the
  // fallback must treat it as such.
  bool usable() const { return helloSeen_; }

  uint32_t framesReceived() const { return framesReceived_; }
  uint32_t bytesReceived() const { return bytesReceived_; }
  uint32_t framesDropped() const { return framesDropped_; }
  uint32_t reconnects() const { return reconnects_; }

  // Frames the inbox holds between one poll() and the next. Small on purpose:
  // this loop runs every 20 ms, so anything beyond a handful means the display
  // has stalled, and in that case the newest frames are the ones worth keeping.
  static constexpr size_t kInboxMax = 16;
  // Candidate rows kept for re-collapsing. Six rows on the glass, but a garden
  // dominated by one species needs headroom before distinct rows appear.
  static constexpr size_t kCandidateMax = 48;
  // Missed heartbeats before the feed is declared stale. Three, so a single
  // dropped packet on a domestic 2.4 GHz band does not put a red rule on the
  // counter top.
  static constexpr uint32_t kMissedBeatsBeforeStale = 3;

 private:
  void handleText(const uint8_t* payload, size_t length);
  void applyFrame(const std::string& raw, StationSnapshot& snapshot);

  WebSocketsClient socket_;
  bool started_ = false;
  bool connected_ = false;
  bool helloSeen_ = false;

  std::vector<std::string> inbox_;
  std::vector<FeedItem> candidates_;

  uint32_t heartbeatSeconds_ = 10;
  uint32_t lastFrameMillis_ = 0;

  uint32_t framesReceived_ = 0;
  uint32_t bytesReceived_ = 0;
  uint32_t framesDropped_ = 0;
  uint32_t reconnects_ = 0;
};

}  // namespace observer
