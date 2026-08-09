// Replacing this display's own firmware, over the air, from the station.
// ADR-050.
//
// The Arduino-only half. Every *decision* -- may we install this, is it newer,
// is now a good moment, has the running image earned its place -- is in
// `model/ota_policy.h` and host-tested. This file only does what it is told:
// fetch, hash, write, commit, and the two `esp_ota_*` calls that make a bad
// build put the good one back.
//
// The safety story, in the order the failures actually happen:
//
//   Power loss mid-download. `Update.write()` fills the *inactive* app slot;
//   `otadata` is not touched until `Update.end()`. So the board reboots into
//   the image it was already running, with a partially written spare slot that
//   the next update simply overwrites. Nothing is lost and nothing is decided.
//
//   A truncated or corrupted download. The SHA-256 is computed over every byte
//   as it arrives and compared *before* `Update.end()` is called, so a bad
//   image is never made bootable. On a mismatch the write is aborted and the
//   running image carries on untouched. (The ESP32 bootloader also checks the
//   image's own checksum at boot, which is a second, independent barrier -- but
//   it would fire after the reboot, and this one fires before it.)
//
//   Power loss between `Update.end()` and the reboot. `otadata` now points at
//   the new slot, and the new image boots in ESP_OTA_IMG_PENDING_VERIFY, which
//   is exactly where it would have been anyway. Probation proceeds normally.
//
//   A new image that boots and then crashes. The bootloader in this build has
//   CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y, so an image in PENDING_VERIFY that
//   reboots without being marked valid is rolled back automatically, by the
//   bootloader, with no help from us. A crash loop therefore fixes itself on
//   the second boot.
//
//   A new image that runs happily and cannot reach the station. Nothing reboots
//   it, so the bootloader never gets its chance. This is the case the deadline
//   in `evaluateProbation` exists for: after ten minutes with no hello frame
//   and no completed portal, we mark the image invalid and reboot, and the
//   bootloader puts the previous slot back.
#pragma once

#include <cstdint>
#include <string>

#include "model/ota_policy.h"
#include "model/settings.h"

namespace observer {

enum class OtaResult : uint8_t {
  kFetchFailed,     // could not reach the image, or the station said no
  kSizeMismatch,    // Content-Length disagreed with the offer
  kBeginFailed,     // no spare slot, or the slot is too small
  kWriteFailed,     // flash write or a short read
  kDigestMismatch,  // what arrived is not what was offered
  kCommitFailed,    // Update.end() refused
  kInstalled,       // committed; the caller should reboot
};

const char* otaResultText(OtaResult result);

// Called with 0..100 as the image is written, at most once per whole percent,
// so a caller can put something on the glass without being flooded.
using OtaProgressFn = void (*)(int percent);

// Fetches `offer.path` from the configured station over plain HTTP, verifies
// the SHA-256 of every byte received against `offer.sha256`, and only then
// makes the new image bootable. Does not reboot; that is the caller's, so it
// can put a word on the screen and flush the log first.
//
// Plain HTTP, deliberately and with the trade named: this station and this
// display are two boxes on one domestic LAN with no certificate authority
// between them, and the integrity guarantee comes from the digest rather than
// from the transport. What that does *not* buy is authenticity -- anyone who
// can already answer for the station's IP on this LAN can offer an image, and
// the digest they supply will match the image they supply. Signing the image
// with a key baked into the firmware is the fix, and it is deliberately not in
// this change: it needs key custody the operator has not been asked about yet.
// See ADR-050, "What this does not defend against".
OtaResult otaInstall(const Settings& settings, const FirmwareOffer& offer,
                     OtaProgressFn progress);

// True when the running image has been written by OTA and has not yet been
// marked valid -- ESP_OTA_IMG_PENDING_VERIFY. False for a cable-flashed image,
// which has no otadata entry to be pending in.
bool otaOnProbation();

// "this build works". Cancels the pending rollback. Idempotent.
void otaConfirmRunningImage();

// "this build does not work". Marks the running image invalid and reboots; the
// bootloader then selects the other slot. Does not return.
void otaRollBackAndReboot();

// e.g. "app0" / "app1". For the boot banner, so a serial capture says which
// slot is running without anyone having to work it out.
std::string otaRunningSlot();

}  // namespace observer
