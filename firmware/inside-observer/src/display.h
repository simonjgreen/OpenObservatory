// Everything that puts pixels on the 240x320 ILI9341.
//
// Design intent, from the operator's brief: this is an ambient living-room
// object, not a dashboard. Few rows, large type, low contrast, no chrome, no
// numbers that could be mistaken for a confidence figure. It should be
// readable from the other side of a room and unremarkable when nobody is
// looking at it.
//
// Memory: no PSRAM on this board, so a 240x320x16bpp framebuffer (150 kB) is
// out of the question. Redraws go through a single persistent 240x40 sprite
// (19 kB) reused for every row, which is what keeps the feed from flickering.
#pragma once

#include <TFT_eSPI.h>

#include <string>
#include <vector>

#include "model/settings.h"
#include "station_source.h"

namespace observer {

// Which screen the device is showing.
enum class Screen : uint8_t {
  kBoot,
  kFeed,
  kSettings,
  kNumberPad,
  kPortal,
  //: Installing new firmware (ADR-050). Deliberately its own screen and not a
  //: banner over the feed: the feed is stale from the moment the download
  //: starts, and a screen that still looks like a live feed while it is not one
  //: is the failure mode the three distinct states exist to prevent.
  kUpdating,
};

// A rectangular touch target. Screens publish these; the input loop hit-tests
// them. Keeping them as data rather than callbacks makes the layout
// inspectable and the hit boxes easy to widen for a resistive panel, which
// wants generous targets.
struct HitBox {
  int16_t x = 0, y = 0, w = 0, h = 0;
  int16_t id = -1;
  bool contains(int16_t px, int16_t py) const {
    return px >= x && px < x + w && py >= y && py < y + h;
  }
};

// Sensitivity presets. The operator configures which detections are named on
// the counter top by picking a described step, never by typing a number: a BirdNET
// score is not a calibrated probability and putting "0.75" in front of someone
// invites them to read it as one. The value behind the step is still a plain
// threshold and is still settable numerically through the provisioning portal,
// where there is room to explain what it is not.
struct SensitivityStep {
  const char* label;
  double threshold;
};
extern const SensitivityStep kSensitivitySteps[];
extern const size_t kSensitivityStepCount;
size_t nearestSensitivityStep(double threshold);

// Widget ids used by the settings and keypad screens.
enum SettingsHit : int16_t {
  kHitNone = -1,
  kHitOpenSettings = 100,
  kHitHost,
  kHitPort,
  kHitSensitivityDown,
  kHitSensitivityUp,
  kHitBats,
  // Retired by ADR-038 (there are no clock times left to format). Kept so the
  // enum's numeric values, which are compared against nothing persisted but are
  // easier to reason about when stable, do not all shift by one.
  kHitClockRetired,
  kHitBrightnessDown,
  kHitBrightnessUp,
  kHitWifiPortal,
  kHitSave,
  kHitCancel,
  kHitPadDigit0 = 200,  // +0..9
  kHitPadDot = 220,
  kHitPadBack,
  kHitPadOk,
  kHitPadCancel,
};

class Display {
 public:
  bool begin();

  // 5..100 percent, PWM on the backlight pin. Applied immediately.
  void setBrightness(uint8_t percent);

  // Result of the panel probe done in begin(), for the serial self-test and
  // the README's "panel identification" section. Zero if the read failed.
  uint32_t panelId() const { return panelId_; }

  void showBoot(const char* line1, const char* line2);

  // The ambient screen. `force` repaints everything; otherwise only the parts
  // whose content changed are redrawn, which keeps the display still.
  void showFeed(const StationSnapshot& snapshot, const Settings& settings,
                bool force);

  // Advance the "4s ago" line on every row whose text actually changed, and
  // nothing else (ADR-038).
  //
  // Called once a second. Repainting the whole screen at that rate on a 240x320
  // SPI panel would be visible as flicker and would burn CPU for nothing, so the
  // elapsed time gets a reserved, fixed-width column of its own and a small
  // 72x18 sprite: a row whose age string is unchanged this second - which is
  // most of them, most of the time, once rows are minutes old - costs zero SPI
  // traffic. Returns the number of rows actually repainted, for the log.
  int tickRelativeTimes(const StationSnapshot& snapshot);

  // The reconnect indicator in the footer: a struck-out WiFi glyph and the
  // seconds until the next attempt (ADR-071). Called once a second, alongside
  // tickRelativeTimes and for the same reason -- it has a reserved column and a
  // small sprite, so a ticking countdown never repaints the footer underneath
  // it. Returns 1 if it actually painted.
  //
  // Draws nothing at all while the link is up. There is no "WiFi OK" state on
  // this screen: a working network is the normal condition of the object and
  // does not need announcing.
  int tickWifiIndicator(const StationSnapshot& snapshot);

  void showSettings(const Settings& draft, const char* transportName);
  void showNumberPad(const char* title, const std::string& value,
                     bool allowDots);
  void showPortal(const char* ssid, const char* ip);

  // The update screen. `percent` < 0 paints the whole screen; 0..100 repaints
  // only the bar, so a 1 MB download does not spend its time on SPI traffic.
  // `note` is one short line under the bar -- an outcome, or empty while the
  // download runs.
  void showUpdating(const char* version, int percent, const char* note);

  const std::vector<HitBox>& hitBoxes() const { return hits_; }

  // Reads a pixel back out of the panel's GRAM. Used only by the boot
  // self-test: it is the one way this firmware can assert something about what
  // is actually on the glass without a human looking at it.
  uint16_t readPixel(int16_t x, int16_t y);

  TFT_eSPI& tft() { return tft_; }

 private:
  void drawHeader(const StationSnapshot& snapshot);
  void drawFooter(const StationSnapshot& snapshot, const Settings& settings);
  void drawRow(int index, int top, const FeedItem& item,
               const Settings& settings);
  void drawRowTime(int top, const std::string& text, bool isBat);
  void drawEmptyState(int top, int height, const StationSnapshot& snapshot,
                      const Settings& settings);
  void drawBanner(int top, const StationSnapshot& snapshot);
  void drawWifiIndicator(const StationSnapshot& snapshot);
  void drawTracked(const char* text, int centreX, int y, uint8_t font,
                   int trackingPx, uint16_t colour);
  void drawFitted(TFT_eSprite& s, const std::string& text, int x, int y,
                  int maxWidth, uint8_t preferredFont, uint16_t colour);
  void addHit(int16_t x, int16_t y, int16_t w, int16_t h, int16_t id);

  TFT_eSPI tft_;
  TFT_eSprite row_{&tft_};
  // A second, much smaller sprite for the one thing on this screen that changes
  // every second. 72x18 is 2.6 kB against the row sprite's 19 kB.
  TFT_eSprite time_{&tft_};
  // Third and smallest sprite, 52x18, for the footer's reconnect countdown.
  // Separate from time_ because that one is 72 wide and would paint over the
  // settings dots.
  TFT_eSprite wifi_{&tft_};
  bool rowSpriteReady_ = false;
  bool timeSpriteReady_ = false;
  bool wifiSpriteReady_ = false;
  uint32_t panelId_ = 0;

  std::vector<HitBox> hits_;

  // Cached rendering of the last feed screen, so a poll that changes nothing
  // repaints nothing.
  std::vector<std::string> lastRowKeys_;
  // Separate from lastRowKeys_ so a ticking clock never repaints a species name.
  std::vector<std::string> lastRowTimes_;
  std::vector<int> rowTops_;
  std::string lastHeaderKey_;
  std::string lastFooterKey_;
  // What the reconnect indicator currently shows, so a second in which the
  // countdown does not change costs no SPI traffic.
  std::string lastWifiKey_;
};

}  // namespace observer
