#include "config_store.h"

#include <Arduino.h>
#include <Preferences.h>

namespace observer {
namespace {

constexpr const char* kNamespace = "inside-obs";

}  // namespace

Settings loadSettings() {
  Settings s;  // compiled defaults
  Preferences p;
  if (!p.begin(kNamespace, /*readOnly=*/true)) {
    Serial.println("[config] no stored settings; using defaults");
    return s;
  }

  s.stationHost = p.getString("host", s.stationHost.c_str()).c_str();
  s.stationPort = p.getUShort("port", s.stationPort);
  s.pollSeconds = p.getUShort("poll", s.pollSeconds);
  s.scoreThreshold = p.getDouble("thresh", s.scoreThreshold);
  s.showBats = p.getBool("bats", s.showBats);
  s.use24hClock = p.getBool("h24", s.use24hClock);
  s.brightnessPercent = p.getUChar("bright", s.brightnessPercent);
  s.fallbackUtcOffsetMinutes = p.getShort("tzmin", s.fallbackUtcOffsetMinutes);

  s.mqtt.enabled = p.getBool("mq_on", s.mqtt.enabled);
  s.mqtt.host = p.getString("mq_host", s.mqtt.host.c_str()).c_str();
  s.mqtt.port = p.getUShort("mq_port", s.mqtt.port);
  s.mqtt.username = p.getString("mq_user", s.mqtt.username.c_str()).c_str();
  s.mqtt.password = p.getString("mq_pass", s.mqtt.password.c_str()).c_str();
  s.mqtt.topicPrefix =
      p.getString("mq_pfx", s.mqtt.topicPrefix.c_str()).c_str();
  p.end();

  if (clampSettings(s)) {
    Serial.println("[config] stored settings contained out-of-range values; "
                   "clamped (see clampSettings)");
  }
  return s;
}

void saveSettings(const Settings& in) {
  Settings s = in;
  clampSettings(s);

  Preferences p;
  if (!p.begin(kNamespace, /*readOnly=*/false)) {
    Serial.println("[config] ERROR: could not open NVS for writing");
    return;
  }
  p.putString("host", s.stationHost.c_str());
  p.putUShort("port", s.stationPort);
  p.putUShort("poll", s.pollSeconds);
  p.putDouble("thresh", s.scoreThreshold);
  p.putBool("bats", s.showBats);
  p.putBool("h24", s.use24hClock);
  p.putUChar("bright", s.brightnessPercent);
  p.putShort("tzmin", s.fallbackUtcOffsetMinutes);

  p.putBool("mq_on", s.mqtt.enabled);
  p.putString("mq_host", s.mqtt.host.c_str());
  p.putUShort("mq_port", s.mqtt.port);
  p.putString("mq_user", s.mqtt.username.c_str());
  p.putString("mq_pass", s.mqtt.password.c_str());
  p.putString("mq_pfx", s.mqtt.topicPrefix.c_str());
  p.end();

  // Log the shape, never the secrets.
  Serial.printf("[config] saved: station=%s:%u threshold=%.2f bats=%d "
                "h24=%d brightness=%u mqtt_host_set=%d\n",
                s.stationHost.c_str(), s.stationPort, s.scoreThreshold,
                s.showBats ? 1 : 0, s.use24hClock ? 1 : 0,
                s.brightnessPercent, s.mqtt.host.empty() ? 0 : 1);
}

bool hasBeenConfigured() {
  Preferences p;
  if (!p.begin(kNamespace, /*readOnly=*/true)) {
    return false;
  }
  const bool configured = p.getBool("done", false);
  p.end();
  return configured;
}

void markConfigured() {
  Preferences p;
  if (p.begin(kNamespace, /*readOnly=*/false)) {
    p.putBool("done", true);
    p.end();
  }
}

}  // namespace observer
