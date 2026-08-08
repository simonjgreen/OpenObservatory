#include "model/settings.h"

#include <cstdio>

namespace observer {
namespace {

template <typename T>
bool clampInto(T& value, T lo, T hi) {
  if (value < lo) {
    value = lo;
    return true;
  }
  if (value > hi) {
    value = hi;
    return true;
  }
  return false;
}

}  // namespace

bool clampSettings(Settings& s) {
  bool changed = false;

  if (s.stationHost.empty()) {
    s.stationHost = "station.example";
    changed = true;
  }
  if (s.stationHost.size() > 63) {
    s.stationHost.resize(63);
    changed = true;
  }
  if (s.stationPort == 0) {
    s.stationPort = 8080;
    changed = true;
  }

  changed |= clampInto<uint16_t>(s.pollSeconds, 5, 600);
  // A threshold of 0 would put every acoustic-event-grade guess on the wall;
  // a threshold of 1 would guarantee an empty screen. Neither is useful, and
  // both look like a broken display rather than a configured one.
  changed |= clampInto<double>(s.scoreThreshold, 0.05, 0.99);
  changed |= clampInto<uint8_t>(s.brightnessPercent, 5, 100);
  changed |= clampInto<int16_t>(s.fallbackUtcOffsetMinutes, -12 * 60, 14 * 60);

  if (s.mqtt.port == 0) {
    s.mqtt.port = 1883;
    changed = true;
  }
  if (s.mqtt.topicPrefix.empty()) {
    s.mqtt.topicPrefix = "openobservatory";
    changed = true;
  }

  return changed;
}

std::string stationBaseUrl(const Settings& s) {
  char buf[96];
  std::snprintf(buf, sizeof(buf), "http://%s:%u", s.stationHost.c_str(),
                static_cast<unsigned>(s.stationPort));
  return std::string(buf);
}

}  // namespace observer
