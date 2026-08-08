// Open Observatory - inside observer
//
// A wall/desk display that shows, calmly, what the garden acoustic station is
// hearing. Species name and time. No scores, ever. Bat passes are shown as
// passes with a peak frequency and never as a species.
//
// Board: DIYmalls / Sunton ESP32-2432S028R ("Cheap Yellow Display").
// Data:  the station's read-only REST API, polled over HTTP. See ADR-023.

#include <Arduino.h>
#include <WiFi.h>

#include "board_pins.h"
#include "config_store.h"
#include "display.h"
#include "portal.h"
#include "station_source.h"
#include "touch.h"

namespace {

using namespace observer;

Display display;
Touch touch;
Portal portal;
HttpStationSource source;

Settings settings;
Settings draft;            // edited on the settings screen, committed on Save
StationSnapshot snapshot;

Screen screen = Screen::kBoot;
uint32_t nextPollMs = 0;
uint32_t restartAtMs = 0;

// Number-pad editing state.
std::string padValue;
int16_t padTarget = kHitNone;

// Fallback route into settings if the touch mapping is wrong for this unit:
// three taps anywhere within three seconds.
uint8_t strayTaps = 0;
uint32_t strayWindowMs = 0;

constexpr uint32_t kWifiConnectTimeoutMs = 25000;

void ledsOff() {
  // The RGB LED is active LOW. Driving all three HIGH turns it off, which is
  // what an ambient object in a living room wants, and also clears whatever
  // state the stock firmware left it in.
  for (int pin : {pins::kLedRed, pins::kLedGreen, pins::kLedBlue}) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH);
  }
}

void logBanner() {
  Serial.println();
  Serial.println("========================================================");
  Serial.printf("Open Observatory inside observer %s\n",
                INSIDE_OBSERVER_VERSION);
  Serial.printf("build      : %s %s\n", __DATE__, __TIME__);
  Serial.printf("chip       : %s rev %d, %d cores @ %u MHz\n",
                ESP.getChipModel(), ESP.getChipRevision(),
                ESP.getChipCores(), ESP.getCpuFreqMHz());
  Serial.printf("flash      : %u bytes, sketch %u of %u\n",
                ESP.getFlashChipSize(), ESP.getSketchSize(),
                ESP.getFreeSketchSpace() + ESP.getSketchSize());
  Serial.printf("psram      : %u bytes (expected 0 on this board)\n",
                ESP.getPsramSize());
  Serial.printf("free heap  : %u bytes\n", ESP.getFreeHeap());
  Serial.printf("mac        : %s\n", WiFi.macAddress().c_str());
  Serial.println("========================================================");
}

// Writes a known colour into the panel's GRAM and reads it back. This is the
// only assertion about the glass that can be made without a person looking at
// it, so it is worth making: it proves the SPI link works in both directions
// and that the controller is storing what we send.
void panelReadbackSelfTest() {
  struct Probe {
    const char* name;
    uint16_t colour;
  };
  const Probe probes[] = {
      {"red", TFT_RED}, {"green", TFT_GREEN}, {"blue", TFT_BLUE},
      {"white", TFT_WHITE}, {"black", TFT_BLACK}};

  bool allMatch = true;
  for (const Probe& p : probes) {
    display.tft().fillRect(200, 296, 24, 20, p.colour);
    const uint16_t got = display.readPixel(210, 304);
    // The ILI9341 stores 18-bit colour and hands back 16, so the low bit of
    // each channel can differ. Compare per channel with a tolerance.
    const int dr = abs(((got >> 11) & 0x1F) - ((p.colour >> 11) & 0x1F));
    const int dg = abs(((got >> 5) & 0x3F) - ((p.colour >> 5) & 0x3F));
    const int db = abs((got & 0x1F) - (p.colour & 0x1F));
    const bool ok = dr <= 1 && dg <= 1 && db <= 1;
    allMatch &= ok;
    Serial.printf("[selftest] gram %-5s wrote=0x%04X read=0x%04X %s\n", p.name,
                  p.colour, got, ok ? "MATCH" : "MISMATCH");
  }
  Serial.printf("[selftest] panel readback %s\n",
                allMatch ? "PASSED" : "FAILED (check MISO / SPI_READ_FREQUENCY)");
}

// Prints, line by line, what the feed screen is currently showing. Without a
// camera this is the only record of the rendered result, and it is what a
// successor should diff against when a layout or filtering change lands.
void logRenderedScreen() {
  Serial.println("[screen] +--------------------------------------+");
  Serial.println("[screen] |          LIVE IN THE GARDEN          |");
  if (snapshot.health.state != StationState::kListening) {
    Serial.printf("[screen] | ! %-34s |\n",
                  snapshot.health.detail.empty()
                      ? stateLabel(snapshot.health.state).c_str()
                      : snapshot.health.detail.c_str());
  }
  if (snapshot.feed.empty()) {
    Serial.println("[screen] |   (empty state - see health above)   |");
  }
  for (const FeedItem& item : snapshot.feed) {
    std::string second = formatClock(item.startUtc, snapshot.utcOffsetSeconds,
                                     settings.use24hClock);
    if (!item.detail.empty()) {
      second += "  .  " + item.detail;
    }
    if (item.repeats > 1) {
      second += "  .  x" + std::to_string(item.repeats);
    }
    Serial.printf("[screen] | %-36s |\n", item.title.c_str());
    Serial.printf("[screen] |   %-34s |\n", second.c_str());
  }
  std::string footer;
  if (snapshot.speciesToday < 0) {
    footer = (snapshot.health.state == StationState::kOffline)
                 ? "waiting for the station"
                 : "counting...";
  } else {
    footer = std::to_string(snapshot.speciesToday) + " species today";
  }
  if (snapshot.health.state == StationState::kOffline && snapshot.everSucceeded) {
    footer += " (stale)";
  }
  Serial.printf("[screen] | %-36s |\n", footer.c_str());
  Serial.println("[screen] +--------------------------------------+");
}

void enterFeed(bool force) {
  screen = Screen::kFeed;
  display.showFeed(snapshot, settings, force);
}

void enterSettings() {
  draft = settings;
  screen = Screen::kSettings;
  display.showSettings(draft, source.transportName());
}

void enterPortal() {
  const std::string ip = portal.begin(settings);
  screen = Screen::kPortal;
  display.showPortal(portal.ssid(), ip.c_str());
}

void enterNumberPad(int16_t target) {
  padTarget = target;
  padValue = (target == kHitHost) ? draft.stationHost
                                  : std::to_string(draft.stationPort);
  screen = Screen::kNumberPad;
  display.showNumberPad(target == kHitHost ? "STATION" : "PORT", padValue,
                        target == kHitHost);
}

bool connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  // No arguments: the ESP32 WiFi stack uses whatever SSID and passphrase are
  // already in its own NVS namespace. On a board that ran the stock firmware
  // those are the credentials the operator provisioned there, and this
  // firmware never reads, copies or logs them. If there are none, this fails
  // and we raise the provisioning AP.
  WiFi.begin();

  const uint32_t deadline = millis() + kWifiConnectTimeoutMs;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[wifi] not connected (status=%d)\n", WiFi.status());
    return false;
  }
  Serial.printf("[wifi] connected to \"%s\" as %s, rssi %d dBm\n",
                WiFi.SSID().c_str(), WiFi.localIP().toString().c_str(),
                WiFi.RSSI());
  return true;
}

void handleSettingsHit(int16_t id) {
  switch (id) {
    case kHitHost:
    case kHitPort:
      enterNumberPad(id);
      return;
    case kHitSensitivityDown: {
      size_t step = nearestSensitivityStep(draft.scoreThreshold);
      if (step + 1 < kSensitivityStepCount) {
        ++step;  // further down the list = more inclusive
      }
      draft.scoreThreshold = kSensitivitySteps[step].threshold;
      break;
    }
    case kHitSensitivityUp: {
      size_t step = nearestSensitivityStep(draft.scoreThreshold);
      if (step > 0) {
        --step;
      }
      draft.scoreThreshold = kSensitivitySteps[step].threshold;
      break;
    }
    case kHitBats:
      draft.showBats = !draft.showBats;
      break;
    case kHitClock:
      draft.use24hClock = !draft.use24hClock;
      break;
    case kHitBrightnessDown:
      draft.brightnessPercent =
          (draft.brightnessPercent > 15) ? draft.brightnessPercent - 10 : 5;
      display.setBrightness(draft.brightnessPercent);
      break;
    case kHitBrightnessUp:
      draft.brightnessPercent =
          (draft.brightnessPercent < 90) ? draft.brightnessPercent + 10 : 100;
      display.setBrightness(draft.brightnessPercent);
      break;
    case kHitWifiPortal:
      settings = draft;
      clampSettings(settings);
      saveSettings(settings);
      enterPortal();
      return;
    case kHitSave:
      settings = draft;
      clampSettings(settings);
      saveSettings(settings);
      markConfigured();
      display.setBrightness(settings.brightnessPercent);
      nextPollMs = 0;  // re-poll immediately: the threshold may have moved
      enterFeed(true);
      return;
    case kHitCancel:
      display.setBrightness(settings.brightnessPercent);
      enterFeed(true);
      return;
    default:
      return;
  }
  display.showSettings(draft, source.transportName());
}

void handleNumberPadHit(int16_t id) {
  if (id >= kHitPadDigit0 && id <= kHitPadDigit0 + 9) {
    if (padValue.size() < 40) {
      padValue += static_cast<char>('0' + (id - kHitPadDigit0));
    }
  } else if (id == kHitPadDot) {
    padValue += '.';
  } else if (id == kHitPadBack) {
    if (!padValue.empty()) {
      padValue.pop_back();
    }
  } else if (id == kHitPadOk) {
    if (padTarget == kHitHost) {
      if (!padValue.empty()) {
        draft.stationHost = padValue;
      }
    } else {
      const long v = atol(padValue.c_str());
      if (v > 0 && v < 65536) {
        draft.stationPort = static_cast<uint16_t>(v);
      }
    }
    screen = Screen::kSettings;
    display.showSettings(draft, source.transportName());
    return;
  } else if (id == kHitPadCancel) {
    screen = Screen::kSettings;
    display.showSettings(draft, source.transportName());
    return;
  } else {
    return;
  }
  display.showNumberPad(padTarget == kHitHost ? "STATION" : "PORT", padValue,
                        padTarget == kHitHost);
}

void handleTouch(const TouchPoint& p) {
  int16_t hitId = kHitNone;
  for (const HitBox& box : display.hitBoxes()) {
    if (box.contains(p.x, p.y)) {
      hitId = box.id;
      break;
    }
  }

  switch (screen) {
    case Screen::kFeed:
      if (hitId == kHitOpenSettings) {
        strayTaps = 0;
        enterSettings();
        return;
      }
      // Escape hatch. If the touch axes are mapped wrongly for this unit the
      // settings affordance is unreachable, and the only way to fix the
      // mapping is to get into settings. Three taps anywhere gets there.
      if (millis() - strayWindowMs > 3000) {
        strayTaps = 0;
        strayWindowMs = millis();
      }
      if (++strayTaps >= 3) {
        strayTaps = 0;
        Serial.println("[touch] three-tap fallback: opening settings");
        enterSettings();
      }
      return;

    case Screen::kSettings:
      handleSettingsHit(hitId);
      return;

    case Screen::kNumberPad:
      handleNumberPadHit(hitId);
      return;

    case Screen::kPortal:
      if (hitId == kHitCancel) {
        Serial.println("[portal] cancelled from the screen; restarting");
        restartAtMs = millis() + 300;
      }
      return;

    case Screen::kBoot:
      return;
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(300);
  logBanner();

  ledsOff();

  settings = loadSettings();
  draft = settings;
  Serial.printf("[config] station=%s:%u poll=%us bats=%d clock=%s "
                "brightness=%u%% configured=%d\n",
                settings.stationHost.c_str(), settings.stationPort,
                settings.pollSeconds, settings.showBats ? 1 : 0,
                settings.use24hClock ? "24h" : "12h",
                settings.brightnessPercent, hasBeenConfigured() ? 1 : 0);

  if (!display.begin()) {
    Serial.println("[display] ERROR: unexpected panel geometry");
  }
  panelReadbackSelfTest();

  touch.begin();
  const Touch::SelfTest ts = touch.selfTest();
  Serial.printf("[selftest] xpt2046 z1=%u z2=%u x=%u y=%u irq=%s -> %s\n",
                ts.z1, ts.z2, ts.x, ts.y, ts.irqHigh ? "HIGH" : "LOW",
                ts.present ? "PRESENT" : "NOT RESPONDING");

  display.showBoot("connecting to WiFi", settings.stationHost.c_str());
  display.setBrightness(settings.brightnessPercent);

  if (!connectWifi()) {
    Serial.println("[wifi] no usable credentials; raising provisioning AP");
    enterPortal();
    return;
  }

  snapshot.health.state = StationState::kConnecting;
  enterFeed(true);
  nextPollMs = 0;
}

void loop() {
  if (restartAtMs != 0 && millis() >= restartAtMs) {
    Serial.println("[main] restarting");
    Serial.flush();
    ESP.restart();
  }

  if (portal.running()) {
    portal.handle();
    if (portal.submitted()) {
      Serial.println("[portal] settings submitted; restarting in 2s");
      restartAtMs = millis() + 2000;
    }
  }

  TouchPoint p;
  if (touch.poll(settings, p)) {
    handleTouch(p);
  }

  if (screen == Screen::kFeed && static_cast<int32_t>(millis() - nextPollMs) >= 0) {
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[wifi] link lost; reconnecting");
      WiFi.reconnect();
    }
    source.poll(settings, snapshot);
    display.showFeed(snapshot, settings, false);
    logRenderedScreen();
    nextPollMs = millis() + settings.pollSeconds * 1000UL;
  }

  delay(20);
}
