// Pin map for the DIYmalls / Sunton ESP32-2432S028R ("Cheap Yellow Display").
//
// Every value here was taken from a published reference, not from inspection
// or inference. Sources, both fetched 2026-08-08:
//
//   [1] witnessmenow/ESP32-Cheap-Yellow-Display
//       https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/main/PINS.md
//       https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/main/DisplayConfig/User_Setup.h
//       https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/main/Examples/Basics/2-TouchTest/2-TouchTest.ino
//   [2] Random Nerd Tutorials CYD pinout reference
//       https://randomnerdtutorials.com/esp32-cheap-yellow-display-cyd-pinout-esp32-2432s028r/
//       https://randomnerdtutorials.com/cheap-yellow-display-esp32-2432s028r/
//
// The two sources agree on every value below.
//
// THE TRAP: the ILI9341 display is on HSPI (GPIO 12/13/14/15) and the XPT2046
// touch controller is on a SEPARATE bus, VSPI, remapped off its default pins
// to GPIO 25/32/39/33. Wiring touch onto the display's bus - or letting
// TFT_eSPI drive it via TOUCH_CS - does not work on this board. TOUCH_CS is
// therefore deliberately absent from platformio.ini.
//
// SECOND TRAP: the microSD slot sits on VSPI's *default* hardware pins
// (5/18/19/23), so touch (remapped VSPI) and SD cannot share the bus without
// care. This firmware never touches the SD card, which sidesteps it entirely.
#pragma once

#include <cstdint>

namespace observer {
namespace pins {

// --- Display, HSPI. Configured through TFT_eSPI build flags, repeated here
//     only for documentation; TFT_eSPI owns these lines at runtime. [1][2]
constexpr int kTftMiso = 12;
constexpr int kTftMosi = 13;
constexpr int kTftSclk = 14;
constexpr int kTftCs = 15;
constexpr int kTftDc = 2;
constexpr int kTftRst = -1;  // tied to the board reset line
constexpr int kTftBacklight = 21;  // active HIGH

// --- Touch, XPT2046 on VSPI with non-default pins. [1][2]
constexpr int kTouchClk = 25;
constexpr int kTouchMosi = 32;
constexpr int kTouchMiso = 39;  // input-only pin
constexpr int kTouchCs = 33;
constexpr int kTouchIrq = 36;   // input-only pin

// --- microSD, VSPI default pins. Unused by this firmware. [1][2]
constexpr int kSdSclk = 18;
constexpr int kSdMiso = 19;
constexpr int kSdMosi = 23;
constexpr int kSdCs = 5;

// --- On-board RGB LED. ACTIVE LOW: HIGH is off. [2]
constexpr int kLedRed = 4;
constexpr int kLedGreen = 16;
constexpr int kLedBlue = 17;

// --- Light-dependent resistor on an ADC pin, and the DAC-driven amp. [1][2]
constexpr int kLdr = 34;
constexpr int kSpeaker = 26;

// LEDC channel used for backlight PWM. Channel 0 is free on this board;
// nothing else in this firmware uses LEDC.
constexpr uint8_t kBacklightPwmChannel = 0;
constexpr uint32_t kBacklightPwmFrequencyHz = 5000;
constexpr uint8_t kBacklightPwmResolutionBits = 8;

// Raw XPT2046 coordinate range used as the default calibration. From Random
// Nerd Tutorials' CYD getting-started sketch [2], which maps
//   x: 200..3700, y: 240..3800
// These are one unit's empirical values, not a specification. The firmware
// prints raw touch coordinates over serial at CORE_DEBUG_LEVEL>=1 so a
// successor can re-derive them for a different board.
constexpr int kTouchRawXMin = 200;
constexpr int kTouchRawXMax = 3700;
constexpr int kTouchRawYMin = 240;
constexpr int kTouchRawYMax = 3800;

}  // namespace pins
}  // namespace observer
