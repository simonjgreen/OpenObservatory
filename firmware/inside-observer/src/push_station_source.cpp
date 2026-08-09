#include "push_station_source.h"

#include <Arduino.h>
#include <WiFi.h>

#include <algorithm>

#include "model/push_frame.h"

namespace observer {
namespace {

//: The one instance. WebSocketsClient's event callback is a free-standing
//: std::function invoked from inside socket_.loop(), so it needs a way back to
//: the object; there is exactly one display feed on this device, so a file-local
//: pointer is honest and avoids capturing `this` in a callback whose lifetime
//: the library owns.
PushStationSource* g_source = nullptr;

std::string formatThreshold(double v) {
  char buf[16];
  snprintf(buf, sizeof(buf), "%.4f", v);
  return std::string(buf);
}

}  // namespace

void PushStationSource::begin(const Settings& settings) {
  g_source = this;

  // The filter goes in the URL, so the station applies it. The ESP32 never
  // receives-and-discards a detection: that is the whole lesson of the polled
  // transport, where forty rows were fetched to render six.
  std::string path = "/api/v1/display?min_score=" +
                     formatThreshold(settings.scoreThreshold) +
                     "&bats=" + (settings.showBats ? "true" : "false") +
                     "&rows=6";

  socket_.begin(settings.stationHost.c_str(), settings.stationPort, path.c_str());
  socket_.onEvent([](WStype_t type, uint8_t* payload, size_t length) {
    if (g_source == nullptr) {
      return;
    }
    switch (type) {
      case WStype_CONNECTED:
        g_source->connected_ = true;
        Serial.printf("[push] connected to %s\n",
                      payload != nullptr ? reinterpret_cast<char*>(payload) : "");
        break;
      case WStype_DISCONNECTED:
        if (g_source->connected_) {
          ++g_source->reconnects_;
        }
        g_source->connected_ = false;
        g_source->helloSeen_ = false;
        Serial.println("[push] disconnected");
        break;
      case WStype_TEXT:
        g_source->handleText(payload, length);
        break;
      default:
        // Binary, ping, pong, fragments. This channel is text-only by design;
        // anything else is the library's own housekeeping.
        break;
    }
  });
  // Reconnect on its own, and prove liveness at the protocol level as well as
  // at the application level: a TCP connection to a Pi that has stopped
  // answering can stay "open" for minutes otherwise.
  socket_.setReconnectInterval(5000);
  socket_.enableHeartbeat(15000, 4000, 2);
  started_ = true;
  Serial.printf("[push] opening ws://%s:%u%s\n", settings.stationHost.c_str(),
                settings.stationPort, path.c_str());
}

void PushStationSource::handleText(const uint8_t* payload, size_t length) {
  ++framesReceived_;
  bytesReceived_ += length;
  if (inbox_.size() >= kInboxMax) {
    // Bounded, with an explicit drop policy, like every other queue in this
    // system. The oldest goes: a display that has fallen behind should catch up
    // to now rather than replay a backlog.
    inbox_.erase(inbox_.begin());
    ++framesDropped_;
  }
  inbox_.emplace_back(reinterpret_cast<const char*>(payload), length);
}

void PushStationSource::applyFrame(const std::string& raw,
                                   StationSnapshot& snapshot) {
  PushFrame frame;
  if (!parsePushFrame(raw.c_str(), frame)) {
    Serial.printf("[push] refused a frame: %.80s\n", raw.c_str());
    return;
  }

  lastFrameMillis_ = millis();

  if (frame.hasServerNow) {
    // The only clock this device has. Anchors the monotonic base; it never
    // becomes the base itself, so a station clock step cannot make rows age
    // backwards. See StationClock.
    snapshot.clock.anchor(frame.serverNow);
  }
  if (frame.speciesToday >= 0) {
    snapshot.speciesToday = frame.speciesToday;
  }
  if (frame.hasState) {
    snapshot.health.state = frame.state;
    snapshot.health.detail = frame.detail;
    snapshot.health.liveHardware = frame.state == StationState::kListening;
  }

  switch (frame.type) {
    case PushFrameType::kHello:
      helloSeen_ = true;
      if (frame.heartbeatSeconds > 0) {
        heartbeatSeconds_ = static_cast<uint32_t>(frame.heartbeatSeconds);
      }
      // Replace rather than merge: the snapshot is the station's answer to
      // "what should be on the screen right now", and a display that reconnects
      // must not carry forward rows the station no longer considers current.
      candidates_ = frame.items;
      Serial.printf("[push] hello: %u rows, %d species today, hb %us\n",
                    static_cast<unsigned>(frame.items.size()),
                    frame.speciesToday,
                    static_cast<unsigned>(heartbeatSeconds_));
      break;

    case PushFrameType::kDetection:
      for (const FeedItem& item : frame.items) {
        candidates_.insert(candidates_.begin(), item);
        Serial.printf("[push] +%s%s%s (%u B)\n", item.title.c_str(),
                      item.detail.empty() ? "" : "  ",
                      item.detail.c_str(),
                      static_cast<unsigned>(raw.size()));
      }
      if (candidates_.size() > kCandidateMax) {
        candidates_.resize(kCandidateMax);
      }
      break;

    case PushFrameType::kHeartbeat:
    case PushFrameType::kUnknown:
      break;
  }

  // Only ordering, collapsing and truncation apply here. The threshold and the
  // bat switch were applied by the station, in the URL this socket was opened
  // with, and there is no score on this wire for the device to re-check against
  // even if it wanted to - which is the point: the number cannot reach the glass
  // because it never reaches the device. Changing either setting reconnects.
  FeedFilter filter;
  filter.maxItems = HttpStationSource::kFeedRows;
  snapshot.feed = buildFeed(candidates_, filter);

  snapshot.everSucceeded = true;
  snapshot.lastSuccessMillis = millis();
  snapshot.consecutiveFailures = 0;
  snapshot.lastError.clear();
  snapshot.transport = transportName();
}

bool PushStationSource::poll(const Settings& settings,
                             StationSnapshot& snapshot) {
  if (!started_) {
    begin(settings);
  }
  if (WiFi.status() != WL_CONNECTED) {
    snapshot.health.state = StationState::kOffline;
    snapshot.health.detail = "NO WIFI";
    snapshot.lastError = "wifi disconnected";
    return false;
  }

  socket_.loop();

  // Drain the inbox. Cheap: every frame is tens of bytes and the feed is
  // rebuilt from at most 48 candidates.
  if (!inbox_.empty()) {
    std::vector<std::string> batch;
    batch.swap(inbox_);
    for (const std::string& raw : batch) {
      applyFrame(raw, snapshot);
    }
  }

  if (!connected_ || !helloSeen_) {
    return false;
  }

  // Staleness. A feed that has gone quiet must *look* stale rather than merely
  // look calm: on this device silence is the normal state, so the absence of
  // detections proves nothing and only the heartbeat does.
  const uint32_t staleAfterMs =
      heartbeatSeconds_ * 1000UL * kMissedBeatsBeforeStale;
  if (lastFrameMillis_ != 0 && millis() - lastFrameMillis_ > staleAfterMs) {
    snapshot.health.state = StationState::kOffline;
    snapshot.health.detail = "STATION UNREACHABLE";
    snapshot.lastError = "no heartbeat";
    return false;
  }
  return true;
}

}  // namespace observer
