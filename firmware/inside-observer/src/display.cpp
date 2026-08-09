#include "display.h"

#include <Arduino.h>

#include <algorithm>

#include "board_pins.h"
#include "model/relative_time.h"

namespace observer {
namespace {

// --- Palette -------------------------------------------------------------
// Deliberately low contrast and slightly green. A living room at night does
// not want a white-on-black terminal glowing at it.
constexpr uint16_t kBg = 0x0861;        // #0A0E0C  near-black, green cast
constexpr uint16_t kInk = 0xEF3B;       // #ECE7DA  warm off-white
constexpr uint16_t kInkDim = 0x7C4F;    // #7C8A7C  muted sage, for times
constexpr uint16_t kRule = 0x2168;      // #202D22  hairline separator
constexpr uint16_t kAccent = 0x6C6D;    // #6E8F6E  soft green, title
constexpr uint16_t kBat = 0xA4DA;       // #A79BD6  soft violet, bat passes
constexpr uint16_t kWarn = 0xDD09;      // #D8A24A  amber, degraded
constexpr uint16_t kAlarm = 0xC32B;     // #C4665C  dull red, offline
constexpr uint16_t kPanel = 0x18E3;     // slightly lifted panel fill

// --- Layout --------------------------------------------------------------
constexpr int kScreenW = 240;
constexpr int kScreenH = 320;
constexpr int kHeaderH = 46;
constexpr int kFooterH = 30;
constexpr int kRowH = 40;
constexpr int kBannerH = 34;
constexpr int kMargin = 14;
// The reserved column for the elapsed time, at the left of a row's second line.
// Fixed width so the detail that follows it does not shuffle sideways once a
// second as "9s ago" becomes "10s ago"; wide enough for the longest string
// formatRelative can produce ("99d+ ago"), which the host tests bound at eight
// characters.
constexpr int kTimeW = 72;
constexpr int kTimeH = 18;
constexpr int kSecondLineY = 24;
constexpr int kFeedTop = kHeaderH;
constexpr int kFeedBottom = kScreenH - kFooterH;

int feedRowsFor(bool hasBanner) {
  const int height = (kFeedBottom - kFeedTop) - (hasBanner ? kBannerH : 0);
  return height / kRowH;
}

}  // namespace

const SensitivityStep kSensitivitySteps[] = {
    {"Only the clearest", 0.90},
    {"Confident", 0.80},
    {"Balanced", 0.75},
    {"Inclusive", 0.65},
    {"Everything named", 0.50},
};
const size_t kSensitivityStepCount =
    sizeof(kSensitivitySteps) / sizeof(kSensitivitySteps[0]);

size_t nearestSensitivityStep(double threshold) {
  size_t best = 0;
  double bestDelta = 1e9;
  for (size_t i = 0; i < kSensitivityStepCount; ++i) {
    const double delta = fabs(kSensitivitySteps[i].threshold - threshold);
    if (delta < bestDelta) {
      bestDelta = delta;
      best = i;
    }
  }
  return best;
}

bool Display::begin() {
  // Backlight off until the panel has something on it, so a boot never shows
  // the ILI9341's power-on noise.
  ledcSetup(pins::kBacklightPwmChannel, pins::kBacklightPwmFrequencyHz,
            pins::kBacklightPwmResolutionBits);
  ledcAttachPin(pins::kTftBacklight, pins::kBacklightPwmChannel);
  ledcWrite(pins::kBacklightPwmChannel, 0);

  tft_.init();
  tft_.setRotation(0);  // portrait, 240x320, ribbon at the bottom
  tft_.fillScreen(kBg);
  tft_.setSwapBytes(true);

  // Read the controller identity back over MISO. On this board MISO is wired
  // (GPIO 12), so the read works and tells us which panel revision we have:
  // the ESP32-2432S028R ships with either an ILI9341-family or an ST7789
  // controller depending on board revision, and they need different
  // inversion/colour-order settings.
  // Not every panel on this board answers 0x04 (RDDID); 0xD3 (RDID4) is the
  // one that reliably returns 0x00 93 41 on an ILI9341 and something else on
  // an ST7789, so both are read and both are reported.
  panelId_ = 0;
  uint32_t rddid = 0;
  for (uint8_t i = 1; i <= 3; ++i) {
    rddid = (rddid << 8) | tft_.readcommand8(0x04, i);
  }
  for (uint8_t i = 1; i <= 4; ++i) {
    panelId_ = (panelId_ << 8) | tft_.readcommand8(0xD3, i);
  }
  Serial.printf("[display] panel probe: RDDID(0x04)=0x%06X RDID4(0xD3)=0x%08X "
                "RDDST(0x09)=0x%02X%02X%02X%02X\n",
                static_cast<unsigned>(rddid), static_cast<unsigned>(panelId_),
                tft_.readcommand8(0x09, 1), tft_.readcommand8(0x09, 2),
                tft_.readcommand8(0x09, 3), tft_.readcommand8(0x09, 4));

  rowSpriteReady_ = false;
  row_.setColorDepth(16);
  if (row_.createSprite(kScreenW, kRowH) != nullptr) {
    rowSpriteReady_ = true;
  } else {
    Serial.println("[display] WARNING: row sprite allocation failed; "
                   "falling back to direct draws (expect flicker)");
  }

  timeSpriteReady_ = false;
  time_.setColorDepth(16);
  if (time_.createSprite(kTimeW, kTimeH) != nullptr) {
    timeSpriteReady_ = true;
  } else {
    Serial.println("[display] WARNING: time sprite allocation failed; the "
                   "one-second tick will draw direct (expect flicker)");
  }

  Serial.printf("[display] ILI9341 init ok, %dx%d rotation=%d panel_id=0x%06X "
                "row_sprite=%d free_heap=%u\n",
                tft_.width(), tft_.height(), 0,
                static_cast<unsigned>(panelId_), rowSpriteReady_ ? 1 : 0,
                static_cast<unsigned>(ESP.getFreeHeap()));
  return tft_.width() == kScreenW && tft_.height() == kScreenH;
}

void Display::setBrightness(uint8_t percent) {
  if (percent > 100) {
    percent = 100;
  }
  ledcWrite(pins::kBacklightPwmChannel, (percent * 255) / 100);
}

uint16_t Display::readPixel(int16_t x, int16_t y) {
  return tft_.readPixel(x, y);
}

void Display::addHit(int16_t x, int16_t y, int16_t w, int16_t h, int16_t id) {
  hits_.push_back(HitBox{x, y, w, h, id});
}

void Display::drawTracked(const char* text, int centreX, int y, uint8_t font,
                          int trackingPx, uint16_t colour) {
  // TFT_eSPI has no letter spacing. The title gets it drawn by hand because
  // wide tracking on a short all-caps line is most of what makes this read as
  // a calm label rather than a status readout.
  tft_.setTextDatum(TL_DATUM);
  tft_.setTextColor(colour, kBg);
  int width = 0;
  for (const char* p = text; *p; ++p) {
    char one[2] = {*p, 0};
    width += tft_.textWidth(one, font) + trackingPx;
  }
  width -= trackingPx;
  int x = centreX - width / 2;
  for (const char* p = text; *p; ++p) {
    char one[2] = {*p, 0};
    tft_.drawString(one, x, y, font);
    x += tft_.textWidth(one, font) + trackingPx;
  }
}

void Display::drawFitted(TFT_eSprite& s, const std::string& text, int x, int y,
                         int maxWidth, uint8_t preferredFont,
                         uint16_t colour) {
  s.setTextDatum(TL_DATUM);
  s.setTextColor(colour, kBg);
  uint8_t font = preferredFont;
  std::string out = text;
  if (s.textWidth(out.c_str(), font) > maxWidth && font != 2) {
    font = 2;  // step down one size before we start deleting letters
  }
  if (s.textWidth(out.c_str(), font) > maxWidth) {
    while (out.size() > 1 &&
           s.textWidth((out + "..").c_str(), font) > maxWidth) {
      out.pop_back();
    }
    out += "..";
  }
  s.drawString(out.c_str(), x, y, font);
}

void Display::showBoot(const char* line1, const char* line2) {
  hits_.clear();
  lastRowKeys_.clear();
  lastRowTimes_.clear();
  rowTops_.clear();
  lastHeaderKey_.clear();
  lastFooterKey_.clear();

  tft_.fillScreen(kBg);
  drawTracked("LIVE IN THE GARDEN", kScreenW / 2, 140, 2, 3, kAccent);
  tft_.setTextDatum(TC_DATUM);
  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString(line1, kScreenW / 2, 170, 2);
  if (line2 != nullptr && line2[0] != '\0') {
    tft_.drawString(line2, kScreenW / 2, 190, 2);
  }
}

void Display::drawHeader(const StationSnapshot& snapshot) {
  tft_.fillRect(0, 0, kScreenW, kHeaderH, kBg);
  drawTracked("LIVE IN THE GARDEN", kScreenW / 2, 14, 2, 4, kAccent);
  // Hairline rule. Two pixels of very dark green rather than one of grey:
  // a single bright line reads as a border, two dim ones read as a fold.
  tft_.drawFastHLine(kMargin, kHeaderH - 6, kScreenW - 2 * kMargin, kRule);
  (void)snapshot;
}

void Display::drawBanner(int top, const StationSnapshot& snapshot) {
  const bool offline = snapshot.health.state == StationState::kOffline;
  const uint16_t colour = offline ? kAlarm : kWarn;

  tft_.fillRect(0, top, kScreenW, kBannerH, kBg);
  // A left rule rather than a filled bar: loud enough to be unmissable across
  // a room, quiet enough not to dominate the object.
  tft_.fillRect(kMargin - 6, top + 3, 3, kBannerH - 8, colour);

  std::string line = snapshot.health.detail;
  if (line.empty()) {
    line = stateLabel(snapshot.health.state);
  }
  tft_.setTextDatum(TL_DATUM);
  tft_.setTextColor(colour, kBg);
  // Truncate rather than wrap: a two-line banner would eat a feed row.
  std::string out = line;
  while (out.size() > 1 &&
         tft_.textWidth(out.c_str(), 2) > kScreenW - 2 * kMargin) {
    out.pop_back();
  }
  tft_.drawString(out.c_str(), kMargin, top + 4, 2);
}

void Display::drawRow(int index, int top, const FeedItem& item,
                      const Settings& settings) {
  const uint16_t titleColour = item.isBat() ? kBat : kInk;

  // Everything on the second line *except* the elapsed time, which lives in its
  // own reserved column and is repainted on its own schedule. Splitting them is
  // what lets the clock tick once a second without redrawing a species name 86
  // thousand times a day.
  std::string tail;
  if (!item.detail.empty()) {
    tail += item.detail;
  }
  if (item.repeats > 1) {
    if (!tail.empty()) {
      tail += "  \xB7  ";  // middle dot in the built-in font's high range
    }
    tail += "x" + std::to_string(item.repeats);
  }

  if (rowSpriteReady_) {
    row_.fillSprite(kBg);
    drawFitted(row_, item.title, kMargin, 2, kScreenW - 2 * kMargin, 4,
               titleColour);
    row_.setTextDatum(TL_DATUM);
    row_.setTextColor(kInkDim, kBg);
    row_.drawString(tail.c_str(), kMargin + kTimeW, kSecondLineY, 2);
    row_.pushSprite(0, top);
  } else {
    tft_.fillRect(0, top, kScreenW, kRowH, kBg);
    tft_.setTextDatum(TL_DATUM);
    tft_.setTextColor(titleColour, kBg);
    tft_.drawString(item.title.c_str(), kMargin, top + 2, 4);
    tft_.setTextColor(kInkDim, kBg);
    tft_.drawString(tail.c_str(), kMargin + kTimeW, top + kSecondLineY, 2);
  }
  (void)index;
  (void)settings;
}

void Display::drawRowTime(int top, const std::string& text, bool isBat) {
  // Slightly brighter for a bat, matching its title colour, because a violet row
  // reading "12s ago" in sage green looks like two unrelated things.
  const uint16_t colour = isBat ? kBat : kInkDim;
  if (timeSpriteReady_) {
    time_.fillSprite(kBg);
    time_.setTextDatum(TL_DATUM);
    time_.setTextColor(colour, kBg);
    time_.drawString(text.c_str(), 0, 0, 2);
    time_.pushSprite(kMargin, top + kSecondLineY);
  } else {
    tft_.fillRect(kMargin, top + kSecondLineY, kTimeW, kTimeH, kBg);
    tft_.setTextDatum(TL_DATUM);
    tft_.setTextColor(colour, kBg);
    tft_.drawString(text.c_str(), kMargin, top + kSecondLineY, 2);
  }
}

int Display::tickRelativeTimes(const StationSnapshot& snapshot) {
  if (!snapshot.clock.anchored()) {
    // No epoch yet, so no age can be claimed. The rows stay blank in that column
    // rather than showing an invented "now".
    return 0;
  }
  int repainted = 0;
  const size_t rows = std::min(rowTops_.size(),
                               std::min(lastRowTimes_.size(), snapshot.feed.size()));
  for (size_t i = 0; i < rows; ++i) {
    const std::string text =
        formatRelative(snapshot.clock.ageOf(snapshot.feed[i].startUtc));
    if (text == lastRowTimes_[i]) {
      continue;  // this row's words have not changed this second
    }
    drawRowTime(rowTops_[i], text, snapshot.feed[i].isBat());
    lastRowTimes_[i] = text;
    ++repainted;
  }
  return repainted;
}

void Display::drawEmptyState(int top, int height,
                             const StationSnapshot& snapshot,
                             const Settings& settings) {
  tft_.fillRect(0, top, kScreenW, height, kBg);
  const int centreY = top + height / 2 - 30;

  const char* headline = "Nothing yet";
  const char* body1 = "The station is listening.";
  const char* body2 = "";
  uint16_t colour = kInkDim;

  switch (snapshot.health.state) {
    case StationState::kConnecting:
      headline = "Connecting";
      body1 = settings.stationHost.c_str();
      break;
    case StationState::kOffline:
      headline = "Station offline";
      body1 = "Cannot reach";
      body2 = settings.stationHost.c_str();
      colour = kAlarm;
      break;
    case StationState::kDegraded:
      headline = "Not listening";
      body1 = "The station is running but";
      body2 = "no microphone audio is reaching it.";
      colour = kWarn;
      break;
    case StationState::kListening:
      headline = "Nothing yet";
      body1 = "Nothing has passed the";
      body2 = "sensitivity setting today.";
      break;
  }

  tft_.setTextDatum(TC_DATUM);
  tft_.setTextColor(colour, kBg);
  tft_.drawString(headline, kScreenW / 2, centreY, 4);
  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString(body1, kScreenW / 2, centreY + 34, 2);
  if (body2[0] != '\0') {
    tft_.drawString(body2, kScreenW / 2, centreY + 52, 2);
  }
}

void Display::drawFooter(const StationSnapshot& snapshot,
                         const Settings& settings) {
  const int top = kFeedBottom;
  tft_.fillRect(0, top, kScreenW, kFooterH, kBg);
  tft_.drawFastHLine(kMargin, top + 2, kScreenW - 2 * kMargin, kRule);

  std::string left;
  if (snapshot.speciesToday < 0) {
    // Never "0 species today" before we have actually counted: an unknown
    // count and a genuinely empty day are different facts.
    left = (snapshot.health.state == StationState::kOffline)
               ? "waiting for the station"
               : "counting...";
  } else if (snapshot.speciesToday == 1) {
    left = "1 species today";
  } else {
    left = std::to_string(snapshot.speciesToday) + " species today";
  }
  if (snapshot.health.state == StationState::kOffline && snapshot.everSucceeded) {
    left += " (stale)";
  }

  tft_.setTextDatum(TL_DATUM);
  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString(left.c_str(), kMargin, top + 9, 2);

  // Settings affordance: three dots, bottom right, with a generously large
  // hit box because this is a resistive panel and a fingertip is imprecise.
  const int dotY = top + 15;
  for (int i = 0; i < 3; ++i) {
    tft_.fillCircle(kScreenW - kMargin - 20 + i * 8, dotY, 2, kInkDim);
  }
  addHit(kScreenW - 70, top, 70, kFooterH, kHitOpenSettings);
  (void)settings;
}

void Display::showFeed(const StationSnapshot& snapshot,
                       const Settings& settings, bool force) {
  hits_.clear();

  const bool hasBanner =
      snapshot.health.state != StationState::kListening;
  const int rows = feedRowsFor(hasBanner);

  // Keys describe what a region currently shows. Repainting only when a key
  // changes is what makes this sit still on a wall instead of blinking every
  // twenty seconds.
  const std::string headerKey =
      stateLabel(snapshot.health.state) + "|" + snapshot.health.detail;
  if (force || headerKey != lastHeaderKey_) {
    drawHeader(snapshot);
    lastHeaderKey_ = headerKey;
    force = true;  // geometry may have shifted; repaint the body too
  }

  int top = kFeedTop;
  if (hasBanner) {
    // Only when the header key changed, i.e. when `force` was just set: the
    // banner text is part of that key, so repainting it every poll would blink
    // an unchanged warning at the room.
    if (force) {
      drawBanner(top, snapshot);
    }
    top += kBannerH;
  }

  // Deliberately does NOT include the elapsed time: that changes every second
  // and has its own, much cheaper repaint path (tickRelativeTimes). Folding it
  // into this key would repaint a species name once a second for no reason.
  std::vector<std::string> keys;
  for (int i = 0; i < rows && i < static_cast<int>(snapshot.feed.size()); ++i) {
    const FeedItem& item = snapshot.feed[i];
    keys.push_back(item.title + "|" + item.detail + "|" +
                   std::to_string(item.repeats) + "|" +
                   std::to_string(item.startUtc));
  }

  if (snapshot.feed.empty()) {
    rowTops_.clear();
    lastRowTimes_.clear();
    const std::string emptyKey = "EMPTY|" + headerKey;
    if (force || lastRowKeys_.size() != 1 || lastRowKeys_[0] != emptyKey) {
      drawEmptyState(top, kFeedBottom - top, snapshot, settings);
      lastRowKeys_ = {emptyKey};
    }
  } else {
    if (force || lastRowKeys_.size() != keys.size()) {
      tft_.fillRect(0, top, kScreenW, kFeedBottom - top, kBg);
      lastRowKeys_.assign(keys.size(), std::string());
      lastRowTimes_.assign(keys.size(), std::string());
    }
    lastRowTimes_.resize(keys.size());
    rowTops_.assign(keys.size(), 0);
    for (size_t i = 0; i < keys.size(); ++i) {
      rowTops_[i] = top + static_cast<int>(i) * kRowH;
      if (force || lastRowKeys_[i] != keys[i]) {
        drawRow(static_cast<int>(i), rowTops_[i], snapshot.feed[i], settings);
        lastRowKeys_[i] = keys[i];
        // The row sprite just painted over the time column too, so the cached
        // string no longer describes what is on the glass.
        lastRowTimes_[i].clear();
      }
    }
    // Clear any rows the feed shrank out of.
    const int usedBottom = top + static_cast<int>(keys.size()) * kRowH;
    if (usedBottom < kFeedBottom) {
      tft_.fillRect(0, usedBottom, kScreenW, kFeedBottom - usedBottom, kBg);
    }
    tickRelativeTimes(snapshot);
  }

  const std::string footerKey =
      std::to_string(snapshot.speciesToday) + "|" +
      std::to_string(static_cast<int>(snapshot.health.state));
  if (force || footerKey != lastFooterKey_) {
    drawFooter(snapshot, settings);
    lastFooterKey_ = footerKey;
  } else {
    // The hit box lives with the footer; re-register it even when we skip the
    // repaint, or the settings affordance stops responding after one poll.
    addHit(kScreenW - 70, kFeedBottom, 70, kFooterH, kHitOpenSettings);
  }
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

namespace {

// The settings page is laid out at fixed offsets rather than by accumulating a
// cursor, because 320 px is a hard limit and an accumulator silently pushes the
// Save button off the bottom of a panel nobody can scroll.
//
//   0..34    title
//   38..64   station
//   64..90   port
//   90..106  "naming sensitivity" caption
//   106..134 sensitivity stepper
//   136..162 bat passes
//   162..188 clock
//   188..204 "brightness" caption
//   204..232 brightness stepper + bar
//   234..260 wifi + mqtt
//   262..272 footnote
//   278..312 save / cancel
constexpr int kSettingsRowH = 26;
constexpr int kRowStation = 38;
constexpr int kRowPort = 64;
constexpr int kCapSensitivity = 90;
constexpr int kRowSensitivity = 108;
constexpr int kRowBats = 136;
constexpr int kRowClock = 162;
constexpr int kCapBrightness = 188;
constexpr int kRowBrightness = 206;
constexpr int kRowWifi = 234;
constexpr int kRowFootnote = 262;
constexpr int kRowButtons = 278;

}  // namespace

void Display::showSettings(const Settings& draft, const char* transportName) {
  hits_.clear();
  lastRowKeys_.clear();
  lastRowTimes_.clear();
  rowTops_.clear();
  lastHeaderKey_.clear();
  lastFooterKey_.clear();

  tft_.fillScreen(kBg);
  drawTracked("SETTINGS", kScreenW / 2, 12, 2, 4, kAccent);
  tft_.drawFastHLine(kMargin, 34, kScreenW - 2 * kMargin, kRule);

  auto row = [&](int y, const char* name, const std::string& value,
                 uint16_t colour, int16_t id) {
    tft_.setTextDatum(TL_DATUM);
    tft_.setTextColor(kInkDim, kBg);
    tft_.drawString(name, kMargin, y + 4, 2);
    tft_.setTextDatum(TR_DATUM);
    tft_.setTextColor(colour, kBg);
    tft_.drawString(value.c_str(), kScreenW - kMargin, y + 4, 2);
    addHit(0, y, kScreenW, kSettingsRowH, id);
  };
  auto caption = [&](int y, const char* text) {
    tft_.setTextDatum(TC_DATUM);
    tft_.setTextColor(kInkDim, kBg);
    tft_.drawString(text, kScreenW / 2, y, 2);
  };
  auto stepper = [&](int y, int16_t downId, int16_t upId) {
    const int bw = 34, bh = 26;
    tft_.drawRoundRect(kMargin, y, bw, bh, 4, kInkDim);
    tft_.drawRoundRect(kScreenW - kMargin - bw, y, bw, bh, 4, kInkDim);
    tft_.setTextDatum(MC_DATUM);
    tft_.setTextColor(kInk, kBg);
    tft_.drawString("-", kMargin + bw / 2, y + bh / 2, 4);
    tft_.drawString("+", kScreenW - kMargin - bw / 2, y + bh / 2, 4);
    // Hit boxes are much larger than the drawn buttons. On a resistive panel
    // with an uncalibrated mapping, a target the size of its own outline is a
    // target nobody can hit.
    addHit(0, y - 6, 62, bh + 12, downId);
    addHit(kScreenW - 62, y - 6, 62, bh + 12, upId);
  };

  row(kRowStation, "Station", draft.stationHost, kInk, kHitHost);
  row(kRowPort, "Port", std::to_string(draft.stationPort), kInk, kHitPort);

  // Named steps only. No score, and no threshold number, reaches the glass.
  caption(kCapSensitivity, "Naming sensitivity");
  stepper(kRowSensitivity, kHitSensitivityDown, kHitSensitivityUp);
  tft_.setTextDatum(MC_DATUM);
  tft_.setTextColor(kInk, kBg);
  tft_.drawString(kSensitivitySteps[nearestSensitivityStep(draft.scoreThreshold)]
                      .label,
                  kScreenW / 2, kRowSensitivity + 13, 2);

  row(kRowBats, "Bat passes", draft.showBats ? "always shown" : "hidden",
      draft.showBats ? kBat : kInkDim, kHitBats);
  // Where "Clock" used to be. ADR-038 replaced clock times with elapsed times,
  // so there is no 12/24-hour choice left to make; what is worth knowing in its
  // place is which transport is actually feeding the glass, because the HTTP
  // fallback is a real running state and not a theoretical one. Read-only: it
  // reports, it does not set.
  row(kRowClock, "Feed", transportName, kAccent, kHitNone);

  // Brightness as a bar. A percentage here would be the only number on the
  // screen, and one number invites reading the rest as numbers too.
  caption(kCapBrightness, "Brightness");
  stepper(kRowBrightness, kHitBrightnessDown, kHitBrightnessUp);
  {
    const int barX = kMargin + 44;
    const int barW = kScreenW - 2 * (kMargin + 44);
    tft_.drawRoundRect(barX, kRowBrightness + 7, barW, 12, 3, kInkDim);
    const int fill = (barW - 4) * draft.brightnessPercent / 100;
    tft_.fillRect(barX + 2, kRowBrightness + 9, fill, 8, kAccent);
  }

  row(kRowWifi, "WiFi + MQTT", "set up ->", kAccent, kHitWifiPortal);

  tft_.setTextDatum(TL_DATUM);
  tft_.setTextColor(0x4A69, kBg);
  tft_.drawString((std::string("Feed via ") + transportName +
                   ". MQTT stored, not connected.")
                      .c_str(),
                  kMargin, kRowFootnote, 1);

  // --- Save / Cancel -----------------------------------------------------
  const int btnY = kRowButtons;
  const int btnW = 100;
  tft_.fillRoundRect(kMargin, btnY, btnW, 34, 6, 0x2408);
  tft_.drawRoundRect(kMargin, btnY, btnW, 34, 6, kAccent);
  tft_.setTextDatum(MC_DATUM);
  tft_.setTextColor(kInk, 0x2408);
  tft_.drawString("Save", kMargin + btnW / 2, btnY + 17, 2);
  addHit(0, btnY - 6, btnW + kMargin, 46, kHitSave);

  tft_.fillRoundRect(kScreenW - kMargin - btnW, btnY, btnW, 34, 6, kBg);
  tft_.drawRoundRect(kScreenW - kMargin - btnW, btnY, btnW, 34, 6, kInkDim);
  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString("Cancel", kScreenW - kMargin - btnW / 2, btnY + 17, 2);
  addHit(kScreenW - btnW - kMargin, btnY - 6, btnW + kMargin, 46, kHitCancel);
}

// ---------------------------------------------------------------------------
// Numeric keypad, for the station address and port
// ---------------------------------------------------------------------------

void Display::showNumberPad(const char* title, const std::string& value,
                            bool allowDots) {
  hits_.clear();
  tft_.fillScreen(kBg);
  drawTracked(title, kScreenW / 2, 12, 2, 4, kAccent);

  tft_.fillRoundRect(kMargin, 36, kScreenW - 2 * kMargin, 34, 5, kPanel);
  tft_.setTextDatum(MC_DATUM);
  tft_.setTextColor(kInk, kPanel);
  tft_.drawString(value.empty() ? "_" : value.c_str(), kScreenW / 2, 53, 4);

  // 3 x 4 grid. Buttons are 72 x 46 with 6 px gutters: a resistive panel and a
  // fingertip need targets this size.
  const int cols = 3, rowsN = 4;
  const int bw = 72, bh = 42, gapX = 6, gapY = 5;
  const int gridW = cols * bw + (cols - 1) * gapX;
  const int x0 = (kScreenW - gridW) / 2;
  const int y0 = 80;  // grid ends at 80 + 4*47 = 268, buttons at 272..306

  const char* faces[12] = {"1", "2", "3", "4",  "5", "6",
                           "7", "8", "9", ".", "0", "<"};
  const int16_t ids[12] = {
      kHitPadDigit0 + 1, kHitPadDigit0 + 2, kHitPadDigit0 + 3,
      kHitPadDigit0 + 4, kHitPadDigit0 + 5, kHitPadDigit0 + 6,
      kHitPadDigit0 + 7, kHitPadDigit0 + 8, kHitPadDigit0 + 9,
      kHitPadDot,        kHitPadDigit0 + 0, kHitPadBack};

  for (int i = 0; i < 12; ++i) {
    const int r = i / cols, c = i % cols;
    const int bx = x0 + c * (bw + gapX);
    const int by = y0 + r * (bh + gapY);
    const bool disabled = (ids[i] == kHitPadDot) && !allowDots;
    tft_.fillRoundRect(bx, by, bw, bh, 5, disabled ? kBg : kPanel);
    tft_.drawRoundRect(bx, by, bw, bh, 5, disabled ? kRule : kInkDim);
    tft_.setTextDatum(MC_DATUM);
    tft_.setTextColor(disabled ? kRule : kInk, disabled ? kBg : kPanel);
    tft_.drawString(faces[i], bx + bw / 2, by + bh / 2, 4);
    if (!disabled) {
      addHit(bx - 2, by - 2, bw + 4, bh + 4, ids[i]);
    }
  }

  const int btnY = y0 + rowsN * (bh + gapY) + 2;
  tft_.fillRoundRect(x0, btnY, 111, 34, 6, 0x2408);
  tft_.drawRoundRect(x0, btnY, 111, 34, 6, kAccent);
  tft_.setTextDatum(MC_DATUM);
  tft_.setTextColor(kInk, 0x2408);
  tft_.drawString("OK", x0 + 55, btnY + 17, 2);
  addHit(x0, btnY - 4, 111, 42, kHitPadOk);

  tft_.drawRoundRect(x0 + 117, btnY, 111, 34, 6, kInkDim);
  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString("Cancel", x0 + 117 + 55, btnY + 17, 2);
  addHit(x0 + 117, btnY - 4, 111, 42, kHitPadCancel);
}

// ---------------------------------------------------------------------------
// Provisioning portal
// ---------------------------------------------------------------------------

void Display::showPortal(const char* ssid, const char* ip) {
  hits_.clear();
  lastRowKeys_.clear();
  lastRowTimes_.clear();
  rowTops_.clear();
  lastHeaderKey_.clear();
  lastFooterKey_.clear();

  tft_.fillScreen(kBg);
  drawTracked("SET UP", kScreenW / 2, 40, 2, 4, kAccent);

  tft_.setTextDatum(TC_DATUM);
  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString("Join this WiFi network", kScreenW / 2, 100, 2);
  tft_.setTextColor(kInk, kBg);
  tft_.drawString(ssid, kScreenW / 2, 124, 4);

  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString("then open", kScreenW / 2, 168, 2);
  tft_.setTextColor(kInk, kBg);
  tft_.drawString(ip, kScreenW / 2, 190, 4);

  tft_.setTextColor(kInkDim, kBg);
  tft_.drawString("Your phone should open the page", kScreenW / 2, 232, 2);
  tft_.drawString("by itself.", kScreenW / 2, 250, 2);

  tft_.setTextColor(0x4A69, kBg);
  tft_.drawString("Touch to cancel", kScreenW / 2, 292, 2);
  addHit(0, 270, kScreenW, 50, kHitCancel);
}

}  // namespace observer
