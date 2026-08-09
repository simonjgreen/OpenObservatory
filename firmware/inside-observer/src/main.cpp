// Open Observatory - inside observer
//
// A wall/desk display that shows, calmly, what the garden acoustic station is
// hearing. Species name and time. No scores, ever. Bat passes are shown as
// passes with a peak frequency and never as a species.
//
// Board: DIYmalls / Sunton ESP32-2432S028R ("Cheap Yellow Display").
// Data:  a WebSocket the station pushes detections down, tens of bytes each,
//        with HTTP polling kept as the fallback. See ADR-038, then ADR-023.
//
// Times are elapsed, not clock: "4s ago", "1m ago", "1h ago", self-ticking once
// a second off a monotonic base so they stay live between frames and keep
// counting honestly while the feed is down.

#include <Arduino.h>
#include <WiFi.h>

#include "board_pins.h"
#include "config_store.h"
#include "display.h"
#include "model/relative_time.h"
#include "portal.h"
#include "push_station_source.h"
#include "station_source.h"
#include "touch.h"

namespace {

using namespace observer;

Display display;
Touch touch;
Portal portal;

// The push channel is the transport. The poller is kept, wired up and actually
// used whenever the socket is down, so the seam stays real rather than
// theoretical - a fallback nobody ever runs is not a fallback.
PushStationSource push;
HttpStationSource http;

Settings settings;
Settings draft;            // edited on the settings screen, committed on Save
StationSnapshot snapshot;

Screen screen = Screen::kBoot;
uint32_t nextServiceMs = 0;
uint32_t nextTickMs = 0;
uint32_t nextFallbackMs = 0;
uint32_t pushDownSinceMs = 0;
bool onFallback = false;
uint32_t nextScreenLogMs = 0;
uint32_t timeRepaints = 0;
uint32_t lastFramesRendered = 0xFFFFFFFFu;  // forces the first paint
StationState lastStateRendered = StationState::kConnecting;
uint32_t restartAtMs = 0;

// How long the socket must be down before the poller is woken. Long enough that
// a WiFi hiccup or a station restart does not put 127 kB of polling back on the
// wire for the sake of one missed heartbeat; short enough that a display cannot
// sit blank for minutes if the push channel is genuinely broken.
constexpr uint32_t kFallbackAfterMs = 60000;

// Once a second, because that is the unit the smallest relative times are in.
constexpr uint32_t kTickMs = 1000;

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
    std::string second = snapshot.clock.anchored()
                             ? formatRelative(snapshot.clock.ageOf(item.startUtc))
                             : std::string("--");
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
  display.showSettings(draft, snapshot.transport);
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
    case kHitClockRetired:
      return;  // the row in that slot is read-only now: it reports the transport
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
      // The threshold and the bat switch are applied by the *station*, in the
      // URL the socket was opened with, so changing either has to reopen the
      // socket. Restarting is the blunt way to do that and the honest one: it
      // re-runs the whole connect handshake, so the screen is repopulated from
      // the station's answer rather than from rows filtered under the old rule.
      Serial.println("[main] settings saved; restarting to reopen the feed");
      restartAtMs = millis() + 400;
      enterFeed(true);
      return;
    case kHitCancel:
      display.setBrightness(settings.brightnessPercent);
      enterFeed(true);
      return;
    default:
      return;
  }
  display.showSettings(draft, snapshot.transport);
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
    display.showSettings(draft, snapshot.transport);
    return;
  } else if (id == kHitPadCancel) {
    screen = Screen::kSettings;
    display.showSettings(draft, snapshot.transport);
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
  // No clock format here any more: ADR-038 replaced clock times with elapsed
  // ones, so `use24hClock` survives in NVS but no longer decides anything.
  Serial.printf("[config] station=%s:%u fallback_poll=%us bats=%d "
                "brightness=%u%% configured=%d\n",
                settings.stationHost.c_str(), settings.stationPort,
                settings.pollSeconds, settings.showBats ? 1 : 0,
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

  push.begin(settings);
  snapshot.health.state = StationState::kConnecting;
  snapshot.transport = push.transportName();
  enterFeed(true);
  nextServiceMs = 0;
  nextTickMs = millis() + kTickMs;
  pushDownSinceMs = millis();
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

  // The monotonic base every "4s ago" on the screen is counted from. Fed
  // unconditionally, on every pass, so it keeps counting whether or not anything
  // is connected - which is the whole reason elapsed times can be trusted while
  // the feed is down.
  snapshot.clock.tick(millis());

  if (screen == Screen::kFeed &&
      static_cast<int32_t>(millis() - nextServiceMs) >= 0) {
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("[wifi] link lost; reconnecting");
      WiFi.reconnect();
    }

    const bool ok = push.poll(settings, snapshot);
    nextServiceMs = millis() + push.serviceIntervalMs(settings);

    if (ok) {
      if (onFallback) {
        Serial.println("[main] push feed is back; standing the poller down");
        onFallback = false;
      }
      pushDownSinceMs = 0;
      snapshot.transport = push.transportName();
    } else {
      if (pushDownSinceMs == 0) {
        pushDownSinceMs = millis();
      }
      // Fall back to polling only after the socket has been down for a while.
      // Waking the poller for every reconnect would put the 127 kB per 20 s that
      // this whole change removes straight back on the wire.
      if (millis() - pushDownSinceMs > kFallbackAfterMs &&
          static_cast<int32_t>(millis() - nextFallbackMs) >= 0) {
        if (!onFallback) {
          Serial.println("[main] push feed down for a minute; falling back to "
                         "HTTP polling");
          onFallback = true;
        }
        http.poll(settings, snapshot);
        nextFallbackMs = millis() + http.serviceIntervalMs(settings);
      }
    }

    // Only when the *content* moved. This block runs every 10 ms - it is
    // servicing a socket, not fetching anything - and showFeed rebuilds a key
    // string per row every time it is called, which at 100 Hz is real work done
    // on a device whose whole job is to sit still. The elapsed times are not
    // this block's business: they belong to the one-second tick below.
    const uint32_t framesNow = push.framesReceived();
    const StationState stateNow = snapshot.health.state;
    if (framesNow != lastFramesRendered || stateNow != lastStateRendered) {
      lastFramesRendered = framesNow;
      lastStateRendered = stateNow;
      display.showFeed(snapshot, settings, false);
    }
  }

  // The tick. Only the rows whose words actually changed are repainted, and each
  // of those is a 72x18 sprite push, not a screen redraw - a feed whose newest
  // row is minutes old costs nothing at all most seconds.
  if (screen == Screen::kFeed &&
      static_cast<int32_t>(millis() - nextTickMs) >= 0) {
    nextTickMs = millis() + kTickMs;
    // Counted, not assumed. Without a camera on the glass this number is the
    // only evidence that the clock is actually ticking and that it is repainting
    // rows rather than the screen: it should be roughly one per second while the
    // newest row is under a minute old, and fall away to nothing once every row
    // is measured in minutes.
    timeRepaints += display.tickRelativeTimes(snapshot);

    // Once a minute, put the rendered screen and the transport's counters in the
    // log. Without a camera this is the only record of what is on the glass, and
    // the bytes-per-frame figure is what ADR-038 is judged on.
    if (static_cast<int32_t>(millis() - nextScreenLogMs) >= 0) {
      nextScreenLogMs = millis() + 60000;
      logRenderedScreen();
      Serial.printf("[push] frames=%lu bytes=%lu mean=%lu B/frame dropped=%lu "
                    "reconnects=%lu ticks=%lu transport=%s heap=%u\n",
                    static_cast<unsigned long>(push.framesReceived()),
                    static_cast<unsigned long>(push.bytesReceived()),
                    static_cast<unsigned long>(
                        push.framesReceived() > 0
                            ? push.bytesReceived() / push.framesReceived()
                            : 0),
                    static_cast<unsigned long>(push.framesDropped()),
                    static_cast<unsigned long>(push.reconnects()),
                    static_cast<unsigned long>(timeRepaints),
                    snapshot.transport,
                    static_cast<unsigned>(ESP.getFreeHeap()));
    }
  }

  delay(20);
}
