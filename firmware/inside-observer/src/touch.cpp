#include "touch.h"

#include <Arduino.h>
#include <SPI.h>
#include <XPT2046_Touchscreen.h>

#include "board_pins.h"

namespace observer {
namespace {

// The touch controller runs on VSPI. TFT_eSPI is built with USE_HSPI_PORT, so
// the display has HSPI to itself and the two never share a bus.
//
// The pinned XPT2046_Touchscreen release drives the global `SPI` object and
// calls a bare `SPI.begin()` internally, which on the ESP32 would bring VSPI
// up on its default pins (18/19/23/5) - the microSD slot's pins, not touch's.
// The fix is to initialise the global SPI object with the CYD's touch pins
// first: Arduino-ESP32's SPIClass::begin() returns immediately if the bus is
// already started (core 2.0.17, SPI.cpp:73), so the library's later call is a
// no-op and our pin mapping stands.
SPIClass& touchSpi = SPI;  // VSPI on the ESP32
XPT2046_Touchscreen ts(pins::kTouchCs, pins::kTouchIrq);

constexpr uint32_t kDebounceMs = 220;
constexpr uint16_t kMinPressure = 200;

int16_t mapClamped(int value, int inLo, int inHi, int outLo, int outHi) {
  if (inHi == inLo) {
    return outLo;
  }
  long mapped = map(value, inLo, inHi, outLo, outHi);
  if (mapped < outLo) {
    mapped = outLo;
  }
  if (mapped > outHi) {
    mapped = outHi;
  }
  return static_cast<int16_t>(mapped);
}

// One raw 12-bit conversion from the XPT2046. Used only by the self-test:
// normal reads go through the library.
uint16_t rawRead(uint8_t control) {
  touchSpi.beginTransaction(SPISettings(SPI_TOUCH_FREQUENCY, MSBFIRST, SPI_MODE0));
  digitalWrite(pins::kTouchCs, LOW);
  touchSpi.transfer(control);
  const uint8_t hi = touchSpi.transfer(0x00);
  const uint8_t lo = touchSpi.transfer(0x00);
  digitalWrite(pins::kTouchCs, HIGH);
  touchSpi.endTransaction();
  return (static_cast<uint16_t>(hi) << 8 | lo) >> 3;  // 12-bit, left aligned
}

}  // namespace

bool Touch::begin() {
  pinMode(pins::kTouchCs, OUTPUT);
  digitalWrite(pins::kTouchCs, HIGH);
  touchSpi.begin(pins::kTouchClk, pins::kTouchMiso, pins::kTouchMosi,
                 pins::kTouchCs);
  const bool ok = ts.begin();
  ts.setRotation(0);
  Serial.printf("[touch] XPT2046 on VSPI clk=%d miso=%d mosi=%d cs=%d irq=%d "
                "begin=%d\n",
                pins::kTouchClk, pins::kTouchMiso, pins::kTouchMosi,
                pins::kTouchCs, pins::kTouchIrq, ok ? 1 : 0);
  return ok;
}

Touch::SelfTest Touch::selfTest() {
  SelfTest r;
  // Control bytes: 0xB1 = Z1, 0xC1 = Z2, 0x91 = Y, 0xD1 = X (12-bit,
  // differential, power-down between conversions).
  r.z1 = rawRead(0xB1);
  r.z2 = rawRead(0xC1);
  r.y = rawRead(0x91);
  r.x = rawRead(0xD1);
  r.irqHigh = digitalRead(pins::kTouchIrq) == HIGH;

  // A missing or mis-wired controller reads as all-zero or all-ones on every
  // channel. A present one gives a plausible mixture even with nothing
  // touching it - Z1 near zero, Z2 near full scale.
  const uint16_t all[4] = {r.z1, r.z2, r.x, r.y};
  bool allSame = true;
  for (int i = 1; i < 4; ++i) {
    if (all[i] != all[0]) {
      allSame = false;
    }
  }
  r.present = !allSame;
  return r;
}

bool Touch::poll(const Settings& settings, TouchPoint& out) {
  if (!ts.touched()) {
    wasDown_ = false;
    return false;
  }
  const TS_Point p = ts.getPoint();
  if (p.z < kMinPressure) {
    wasDown_ = false;
    return false;
  }
  if (wasDown_) {
    return false;  // still the same press
  }
  const uint32_t now = millis();
  if (now - lastPressMs_ < kDebounceMs) {
    return false;
  }
  wasDown_ = true;
  lastPressMs_ = now;

  int rawX = p.x;
  int rawY = p.y;
  if (settings.touchSwapXY) {
    const int t = rawX;
    rawX = rawY;
    rawY = t;
  }
  out.rawX = static_cast<int16_t>(rawX);
  out.rawY = static_cast<int16_t>(rawY);
  out.pressure = p.z;

  out.x = mapClamped(rawX, pins::kTouchRawXMin, pins::kTouchRawXMax, 0, 239);
  out.y = mapClamped(rawY, pins::kTouchRawYMin, pins::kTouchRawYMax, 0, 319);
  if (settings.touchFlipX) {
    out.x = 239 - out.x;
  }
  if (settings.touchFlipY) {
    out.y = 319 - out.y;
  }

  // Raw values are logged on every press so a successor can recalibrate this
  // board, or a different one, from a serial trace alone.
  Serial.printf("[touch] raw=(%d,%d) z=%u -> screen=(%d,%d)\n", out.rawX,
                out.rawY, out.pressure, out.x, out.y);
  return true;
}

}  // namespace observer
