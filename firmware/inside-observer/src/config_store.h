// Persistence for Settings, in the ESP32's NVS partition.
//
// Namespace "inside-obs". This is a different namespace from the WiFi stack's
// "nvs.net80211", where the SSID and passphrase live. That separation is
// deliberate: this firmware reads and writes its own settings and never reads,
// logs or serialises the WiFi credentials.
#pragma once

#include "model/settings.h"

namespace observer {

// Loads settings, falling back to compiled defaults for anything absent.
// Clamps on the way out and reports over serial if it had to.
Settings loadSettings();

void saveSettings(const Settings& s);

// True if the operator has ever completed setup on this device. Used to decide
// whether a first boot should raise the provisioning AP immediately or try the
// credentials the stock firmware left behind.
bool hasBeenConfigured();
void markConfigured();

}  // namespace observer
