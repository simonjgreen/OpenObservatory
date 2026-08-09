#include "ota.h"

#include <Arduino.h>
#include <HTTPClient.h>
#include <Update.h>
#include <WiFi.h>
#include <WiFiClient.h>

#include <esp_ota_ops.h>
#include <esp_partition.h>
#include <mbedtls/sha256.h>

namespace observer {
namespace {

// Read in 1 kB bites. Large enough that the loop is not the bottleneck on a
// ~1 MB image, small enough that the buffer is a rounding error against the
// ~220 kB of free heap this firmware runs with.
constexpr size_t kChunkBytes = 1024;

// A stalled download must not hold the display in a blank "updating" screen
// forever.
constexpr uint32_t kStallTimeoutMs = 20000;

std::string hex(const uint8_t* bytes, size_t length) {
  static const char* digits = "0123456789abcdef";
  std::string out;
  out.reserve(length * 2);
  for (size_t i = 0; i < length; ++i) {
    out.push_back(digits[bytes[i] >> 4]);
    out.push_back(digits[bytes[i] & 0x0F]);
  }
  return out;
}

}  // namespace

const char* otaResultText(OtaResult result) {
  switch (result) {
    case OtaResult::kFetchFailed:
      return "could not fetch the image";
    case OtaResult::kSizeMismatch:
      return "image size disagreed with the offer";
    case OtaResult::kBeginFailed:
      return "no usable app slot";
    case OtaResult::kWriteFailed:
      return "flash write failed or the download stalled";
    case OtaResult::kDigestMismatch:
      return "SHA-256 mismatch: image discarded";
    case OtaResult::kCommitFailed:
      return "commit refused";
    case OtaResult::kInstalled:
      return "installed";
  }
  return "unknown";
}

OtaResult otaInstall(const Settings& settings, const FirmwareOffer& offer,
                     OtaProgressFn progress) {
  WiFiClient client;
  HTTPClient http;
  const std::string url = stationBaseUrl(settings) + offer.path;
  Serial.printf("[ota] fetching %s (%u bytes, sha %.12s...)\n", url.c_str(),
                static_cast<unsigned>(offer.sizeBytes), offer.sha256.c_str());

  if (!http.begin(client, url.c_str())) {
    return OtaResult::kFetchFailed;
  }
  http.setTimeout(kStallTimeoutMs);
  const int status = http.GET();
  if (status != HTTP_CODE_OK) {
    Serial.printf("[ota] station answered %d\n", status);
    http.end();
    return OtaResult::kFetchFailed;
  }
  const int declared = http.getSize();
  if (declared < 0 || static_cast<uint32_t>(declared) != offer.sizeBytes) {
    // The offer said how big the image is and the station has just said
    // something else. One of the two is wrong and there is no way to tell
    // which, so nothing is written. A chunked response (getSize() < 0) is
    // refused for the same reason: an image of unknown length cannot be
    // checked against the offer before it is committed.
    Serial.printf("[ota] size mismatch: offered %u, served %d\n",
                  static_cast<unsigned>(offer.sizeBytes), declared);
    http.end();
    return OtaResult::kSizeMismatch;
  }

  if (!Update.begin(offer.sizeBytes, U_FLASH)) {
    Serial.printf("[ota] Update.begin refused: %s\n", Update.errorString());
    http.end();
    return OtaResult::kBeginFailed;
  }

  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts_ret(&sha, /*is224=*/0);

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buffer[kChunkBytes];
  uint32_t written = 0;
  int lastPercent = -1;
  uint32_t lastProgressMs = millis();
  OtaResult failure = OtaResult::kInstalled;

  while (written < offer.sizeBytes) {
    const size_t available = stream->available();
    if (available == 0) {
      if (!client.connected() || millis() - lastProgressMs > kStallTimeoutMs) {
        Serial.printf("[ota] download stalled at %u/%u bytes\n",
                      static_cast<unsigned>(written),
                      static_cast<unsigned>(offer.sizeBytes));
        failure = OtaResult::kWriteFailed;
        break;
      }
      delay(5);
      continue;
    }
    size_t want = available < kChunkBytes ? available : kChunkBytes;
    const uint32_t remaining = offer.sizeBytes - written;
    if (want > remaining) {
      want = remaining;
    }
    const int got = stream->readBytes(buffer, want);
    if (got <= 0) {
      delay(5);
      continue;
    }
    // Hash first, then write. The order does not matter for correctness -- the
    // comparison happens before anything is committed either way -- but it
    // means the digest covers exactly the bytes we believed we had, not
    // whatever survived a flash driver's opinion of them.
    mbedtls_sha256_update_ret(&sha, buffer, static_cast<size_t>(got));
    if (Update.write(buffer, static_cast<size_t>(got)) !=
        static_cast<size_t>(got)) {
      Serial.printf("[ota] flash write failed: %s\n", Update.errorString());
      failure = OtaResult::kWriteFailed;
      break;
    }
    written += static_cast<uint32_t>(got);
    lastProgressMs = millis();

    if (progress != nullptr) {
      const int percent =
          static_cast<int>((static_cast<uint64_t>(written) * 100) / offer.sizeBytes);
      if (percent != lastPercent) {
        lastPercent = percent;
        progress(percent);
      }
    }
  }

  uint8_t digest[32] = {0};
  mbedtls_sha256_finish_ret(&sha, digest);
  mbedtls_sha256_free(&sha);
  http.end();

  if (failure != OtaResult::kInstalled) {
    Update.abort();
    return failure;
  }

  const std::string got = hex(digest, sizeof(digest));
  if (got != offer.sha256) {
    // Nothing has been committed at this point: `otadata` still points at the
    // running image, so aborting here leaves a display that has lost nothing
    // but ninety seconds.
    Serial.printf("[ota] digest mismatch\n  offered %s\n  received %s\n",
                  offer.sha256.c_str(), got.c_str());
    Update.abort();
    return OtaResult::kDigestMismatch;
  }
  Serial.printf("[ota] %u bytes verified against the offered digest\n",
                static_cast<unsigned>(written));

  // The only line in this file that changes which image boots next.
  if (!Update.end(/*evenIfRemaining=*/false)) {
    Serial.printf("[ota] commit refused: %s\n", Update.errorString());
    return OtaResult::kCommitFailed;
  }
  return OtaResult::kInstalled;
}

bool otaOnProbation() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (running == nullptr) {
    return false;
  }
  esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
  if (esp_ota_get_state_partition(running, &state) != ESP_OK) {
    // No otadata entry for this partition: a cable-flashed image, which has
    // nothing to prove and nothing to roll back to.
    return false;
  }
  return state == ESP_OTA_IMG_PENDING_VERIFY;
}

void otaConfirmRunningImage() {
  const esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
  Serial.printf("[ota] running image marked valid (%s)\n", esp_err_to_name(err));
}

void otaRollBackAndReboot() {
  Serial.println("[ota] this build never reached the station; rolling back");
  Serial.flush();
  const esp_err_t err = esp_ota_mark_app_invalid_rollback_and_reboot();
  // Only reached if the rollback was refused -- typically because there is no
  // other valid image to go back to, which on a two-slot board means the
  // previous slot was never written. Restarting is still the right move: the
  // bootloader will pick whatever it can boot.
  Serial.printf("[ota] rollback refused (%s); restarting anyway\n",
                esp_err_to_name(err));
  Serial.flush();
  ESP.restart();
}

std::string otaRunningSlot() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  return running != nullptr ? std::string(running->label) : std::string("?");
}

}  // namespace observer
