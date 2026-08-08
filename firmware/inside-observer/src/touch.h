// XPT2046 resistive touch on the CYD's second SPI bus.
//
// The single most common way to get this board wrong: the ILI9341 is on HSPI
// (12/13/14/15) and the XPT2046 is on VSPI *remapped* to 25/32/39/33. They are
// not the same bus, and TFT_eSPI's built-in touch support (TOUCH_CS) cannot
// drive this panel's controller because it assumes the display's bus.
#pragma once

#include <cstdint>

#include "model/settings.h"

namespace observer {

struct TouchPoint {
  int16_t x = 0;
  int16_t y = 0;
  int16_t rawX = 0;
  int16_t rawY = 0;
  uint16_t pressure = 0;
};

class Touch {
 public:
  bool begin();

  // Returns true once per press, on release-free debounce. Screen coordinates
  // are in the display's portrait frame (0..239, 0..319).
  bool poll(const Settings& settings, TouchPoint& out);

  // Boot self-test: talks to the XPT2046 directly and reports what came back.
  // This can prove the controller is present and answering on the bus without
  // anyone touching the glass - which is the only part of touch that can be
  // verified without a person in the room.
  struct SelfTest {
    bool present = false;
    uint16_t z1 = 0;
    uint16_t z2 = 0;
    uint16_t x = 0;
    uint16_t y = 0;
    bool irqHigh = false;
  };
  SelfTest selfTest();

 private:
  uint32_t lastPressMs_ = 0;
  bool wasDown_ = false;
};

}  // namespace observer
