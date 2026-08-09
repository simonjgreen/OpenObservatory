// Captive-portal provisioning, as the stock firmware did it.
//
// The device raises an open access point called "Observatory-<mac>", answers every DNS
// query with its own address so phones open the setup page by themselves, and
// serves one form. The operator types the WiFi credentials there, on their own
// device, and they go straight into the ESP32 WiFi stack's own NVS namespace.
//
// This firmware never asks anyone for credentials out of band, never embeds
// them, never writes them to its own settings blob and never logs them.
#pragma once

#include "model/settings.h"

namespace observer {

class Portal {
 public:
  // Raises the AP and starts serving. Returns the AP's IP as a string.
  std::string begin(Settings& settings);

  // Pumps DNS and HTTP. Call from loop().
  void handle();

  void end();

  bool running() const { return running_; }

  // Set once the operator submits the form. The caller reboots so the new
  // credentials are used from a clean state.
  bool submitted() const { return submitted_; }

  const std::string& ssid() const { return ssid_; }

 private:
  std::string ssid_;
  bool running_ = false;
  bool submitted_ = false;
  Settings* settings_ = nullptr;
};

}  // namespace observer
