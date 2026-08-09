#include "station_source.h"

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClient.h>

namespace observer {
namespace {

// Fetches `url` and stream-parses it through `filter` into `doc`.
// Returns an empty string on success, or a short error suitable for the
// status strip.
std::string fetchJson(const std::string& url, JsonDocument& filter,
                      JsonDocument& doc, uint16_t timeoutMs) {
  WiFiClient client;
  HTTPClient http;
  http.setTimeout(timeoutMs);
  http.setConnectTimeout(timeoutMs);
  // The station serves several requests per poll; reusing the connection keeps
  // the ESP32's single TCP socket budget comfortable.
  http.setReuse(true);

  if (!http.begin(client, url.c_str())) {
    return "bad station URL";
  }
  const int code = http.GET();
  if (code != HTTP_CODE_OK) {
    const std::string err =
        (code > 0) ? ("HTTP " + std::to_string(code))
                   : std::string(HTTPClient::errorToString(code).c_str());
    http.end();
    return err.empty() ? std::string("no answer") : err;
  }

  const DeserializationError jsonErr = deserializeJson(
      doc, http.getStream(), DeserializationOption::Filter(filter));
  http.end();
  if (jsonErr) {
    return std::string("bad JSON: ") + jsonErr.c_str();
  }
  return std::string();
}

std::string urlWithQuery(const Settings& s, const char* path,
                         const std::string& query) {
  std::string url = stationBaseUrl(s) + path;
  if (!query.empty()) {
    url += "?" + query;
  }
  return url;
}

std::string formatThreshold(double v) {
  char buf[16];
  snprintf(buf, sizeof(buf), "%.4f", v);
  return std::string(buf);
}

}  // namespace

bool HttpStationSource::poll(const Settings& settings,
                             StationSnapshot& snapshot) {
  if (WiFi.status() != WL_CONNECTED) {
    snapshot.health.state = StationState::kOffline;
    snapshot.health.detail = "NO WIFI";
    snapshot.lastError = "wifi disconnected";
    ++snapshot.consecutiveFailures;
    lastPollOk_ = false;
    return false;
  }

  const uint32_t started = millis();

  // ---- 1. Health. Cheapest request, and the one that decides whether the
  //         rest of the screen means anything.
  {
    JsonDocument filter;
    buildHealthFilter(filter);
    JsonDocument doc;
    const std::string err = fetchJson(urlWithQuery(settings, "/api/v1/health", ""),
                                      filter, doc, kHttpTimeoutMs);
    if (!err.empty()) {
      snapshot.health.state = StationState::kOffline;
      snapshot.health.detail = "STATION UNREACHABLE";
      snapshot.lastError = err;
      ++snapshot.consecutiveFailures;
      Serial.printf("[station] health failed: %s\n", err.c_str());
      lastPollOk_ = false;
      return false;
    }
    JsonObjectConst root = doc.as<JsonObjectConst>();
    parseHealth(root, snapshot.health);
    // The station's own idea of now, which is the only thing that can anchor
    // this device's relative times while the push channel is down. `checked_at`
    // is generated at the moment the response is built, so it is as close to
    // "now" as anything this transport can offer.
    const int64_t checkedAt = parseIso8601Utc(root["checked_at"]);
    if (checkedAt != kInvalidTime) {
      snapshot.clock.anchor(checkedAt);
    }
  }

  FeedFilter feedFilter;
  feedFilter.minScore = settings.scoreThreshold;
  feedFilter.showBats = settings.showBats;
  feedFilter.maxItems = kFeedRows;

  std::vector<FeedItem> candidates;

  // ---- 2. Named detections. The threshold is applied server-side as well as
  //         client-side: server-side to keep the payload small, client-side
  //         because the rule has to hold whatever the transport does (see the
  //         host tests).
  {
    JsonDocument filter;
    buildDetectionsFilter(filter);
    JsonDocument doc;
    const std::string query =
        "limit=" + std::to_string(kBirdRequestLimit) +
        "&identified_only=true&min_score=" +
        formatThreshold(settings.scoreThreshold) + "&include_synthetic=false";
    const std::string err = fetchJson(
        urlWithQuery(settings, "/api/v1/detections", query), filter, doc,
        kHttpTimeoutMs);
    if (!err.empty()) {
      snapshot.health.state = StationState::kOffline;
      snapshot.health.detail = "STATION UNREACHABLE";
      snapshot.lastError = err;
      ++snapshot.consecutiveFailures;
      Serial.printf("[station] detections failed: %s\n", err.c_str());
      lastPollOk_ = false;
      return false;
    }
    collectDetections(doc.as<JsonObjectConst>(), feedFilter, candidates);
  }

  // ---- 3. Bat passes, separately. `identified_only=true` above excludes them
  //         by construction - a pass has no taxon - and they are never score
  //         filtered, so they need their own request.
  if (settings.showBats) {
    JsonDocument filter;
    buildDetectionsFilter(filter);
    JsonDocument doc;
    const std::string query = "limit=" + std::to_string(kBatRequestLimit) +
                              "&group=bat&include_synthetic=false";
    const std::string err = fetchJson(
        urlWithQuery(settings, "/api/v1/detections", query), filter, doc,
        kHttpTimeoutMs);
    if (err.empty()) {
      collectDetections(doc.as<JsonObjectConst>(), feedFilter, candidates);
    } else {
      // Non-fatal: birds still render. Say so in the log, not on the glass.
      Serial.printf("[station] bat query failed (feed still valid): %s\n",
                    err.c_str());
    }
  }

  snapshot.feed = buildFeed(std::move(candidates), feedFilter);

  // ---- 4. Today's species count, and the station's real UTC offset.
  {
    JsonDocument filter;
    buildHistoryFilter(filter);
    JsonDocument doc;
    const std::string err =
        fetchJson(urlWithQuery(settings, "/api/v1/history", "window=today"),
                  filter, doc, kHttpTimeoutMs);
    if (err.empty()) {
      JsonObjectConst root = doc.as<JsonObjectConst>();
      snapshot.speciesToday =
          speciesCountToday(root, settings.scoreThreshold);
      const int64_t midnight = parseIso8601Utc(root["range"]["start_utc"]);
      if (midnight != kInvalidTime) {
        snapshot.utcOffsetSeconds = offsetFromLocalMidnight(midnight);
        snapshot.offsetKnown = true;
      }
    } else {
      Serial.printf("[station] history failed (count unchanged): %s\n",
                    err.c_str());
    }
  }

  if (!snapshot.offsetKnown) {
    snapshot.utcOffsetSeconds = settings.fallbackUtcOffsetMinutes * 60;
  }

  snapshot.everSucceeded = true;
  snapshot.lastSuccessMillis = millis();
  snapshot.consecutiveFailures = 0;
  snapshot.lastError.clear();
  snapshot.transport = transportName();
  lastPollOk_ = true;

  Serial.printf("[station] poll ok in %lu ms: state=%s rows=%u species=%d "
                "offset=%+ld s heap=%u\n",
                static_cast<unsigned long>(millis() - started),
                stateLabel(snapshot.health.state).c_str(),
                static_cast<unsigned>(snapshot.feed.size()),
                snapshot.speciesToday,
                static_cast<long>(snapshot.utcOffsetSeconds),
                static_cast<unsigned>(ESP.getFreeHeap()));
  return true;
}

}  // namespace observer
