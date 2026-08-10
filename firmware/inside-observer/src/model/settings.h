// Operator-settable configuration for the inside observer.
//
// Pure data plus its validation rules, so the clamping is host-tested. NVS
// persistence lives in config_store.{h,cpp}; the captive portal and the
// on-glass config page both write this struct and nothing else.
//
// WiFi credentials are deliberately NOT in here. They live in the ESP32's own
// `nvs.net80211` namespace, written by the WiFi stack when the operator
// provisions through the portal, and this firmware never reads them back,
// never logs them and never serialises them.
#pragma once

#include <cstdint>
#include <string>

namespace observer {

struct MqttSettings {
  // Not wired up yet. The station's MQTT publisher is being built separately;
  // ADR-023 records the decision to ship HTTP polling first. These fields
  // exist now so the operator's broker details survive the firmware update
  // that turns MQTT on, and so the config UI does not have to change shape.
  std::string host;
  uint16_t port = 1883;
  std::string username;
  std::string password;
  std::string topicPrefix = "openobservatory";
  bool enabled = false;
};

struct Settings {
  // --- Station transport --------------------------------------------------
  // Empty until the operator provisions one (portal or on-glass config).
  // Deliberately no shipped default: a baked-in address is true of exactly
  // one installation, and a display polling somebody else's station -- or a
  // stranger's LAN, once this firmware is public -- is worse than a display
  // that says plainly it has not been told where its station is. main.cpp
  // raises the provisioning portal when this is empty.
  std::string stationHost;
  uint16_t stationPort = 8080;
  uint16_t pollSeconds = 20;

  // --- Presentation -------------------------------------------------------
  // The single score threshold. Filters what appears. Never rendered.
  double scoreThreshold = 0.75;
  bool showBats = true;
  bool use24hClock = true;
  uint8_t brightnessPercent = 70;

  // --- Touch orientation --------------------------------------------------
  // The XPT2046's raw axes do not have a fixed relationship to the panel's
  // orientation across board revisions, and there is no way to determine the
  // right combination without a finger on the glass. Defaults follow the
  // canonical CYD example; if touch lands in the wrong place these three flags
  // fix it from the provisioning portal without a reflash.
  bool touchSwapXY = false;
  bool touchFlipX = false;
  bool touchFlipY = false;

  // Fallback UTC offset in minutes, used only until the station tells us its
  // real one (see offsetFromLocalMidnight). Zero -- UTC -- deliberately: the
  // only offset that is not somebody's local assumption (ADR-047).
  int16_t fallbackUtcOffsetMinutes = 0;

  MqttSettings mqtt;

  // Prefix of the AP raised for provisioning. The full SSID appends the last
  // three bytes of the device's own MAC -- "Observatory-1F86A4" -- for two
  // reasons the old name failed on.
  //
  // It used to be "Aura" -- the name of the weather-display project this board
  // was originally built for, kept so the operator would recognise it. That was the wrong trade: it names a product
  // this device no longer runs, tells someone scanning for it nothing about
  // what it is, and -- the part that actually breaks -- two of these boards in
  // one house would raise two identically named open access points, with no
  // way to tell which is which or any guarantee of joining the intended one.
  //
  // The MAC suffix is per-device, stable across reflashes and OTA updates, and
  // already printed in the boot banner, so an operator holding a board can
  // match it to an SSID without guessing. See `Settings::provisioningApSsid()`.
  static constexpr const char* kProvisioningApPrefix = "Observatory-";

  // "Observatory-1F86A4". Built from the station MAC's last three bytes, which
  // is what `WiFi.macAddress()` reports and what the boot banner prints.
  // Pure and testable: takes the MAC rather than reading the radio, so the
  // host tests can exercise it without an ESP32.
  static std::string provisioningApSsid(const std::string& mac);
};

// Clamps every field into a range the firmware can actually honour. Returns
// true if anything had to be changed, which the caller logs - silently
// repairing a bad value and saying nothing is how a config bug hides.
bool clampSettings(Settings& s);

// Base URL, e.g. "http://192.0.2.10:8080". No trailing slash. Empty when no
// station host has been provisioned -- callers must not fetch from it.
std::string stationBaseUrl(const Settings& s);

}  // namespace observer
