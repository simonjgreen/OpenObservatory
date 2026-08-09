// The feed model: what the inside observer shows, and what it refuses to show.
//
// This translation unit is pure. It has no Arduino, no WiFi, no TFT_eSPI, and
// no globals, so every rule below is exercised by the host test suite
// (`pio test -e native`) without touching hardware.
//
// The honesty rules this file enforces, all of them decisions taken by the
// operator and recorded in ADR-023:
//
//  * No numeric score is ever carried into a FeedItem. The threshold decides
//    what appears; the number itself does not reach the screen. BirdNET scores
//    are not calibrated probabilities and rendering one as a percentage would
//    misrepresent it.
//  * A bat pass is never given a species name and never scored.
//    `ultrasonic-pass-v1` detects passes, not species. Bat passes bypass the
//    score threshold entirely - the threshold is a bird-naming control.
//  * A BirdNET label whose scientific name is not a binomial (Engine, Siren,
//    Human vocal, ...) is not a species and is not presented as one.
//  * A detection the station has marked `withdrawn` never reaches the screen
//    (ADR-042). The API keeps such rows and marks them, because the record
//    stays visible and attributable there; this display has nowhere to put a
//    caveat, so it shows nothing rather than an unqualified claim.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include <ArduinoJson.h>

#include "model/time_utils.h"

namespace observer {

enum class FeedItemKind : uint8_t {
  kSpecies,  // a named taxon, from a taxonomic detector
  kBatPass,  // an ultrasonic pass: no species, no score, peak frequency only
};

struct FeedItem {
  FeedItemKind kind = FeedItemKind::kSpecies;
  std::string title;    // "Common Woodpigeon" | "Bat pass"
  std::string detail;   // "" | "36.2 kHz"
  int64_t startUtc = kInvalidTime;
  int repeats = 1;      // consecutive detections collapsed into this row

  bool isBat() const { return kind == FeedItemKind::kBatPass; }
};

struct FeedFilter {
  // Single configurable score threshold. Never displayed.
  double minScore = 0.75;
  // Bat passes are always shown when this is on, whatever their score.
  bool showBats = true;
  // Rows the screen has space for.
  size_t maxItems = 6;
};

// True when `scientificName` looks like a real binomial rather than one of
// BirdNET's non-taxonomic classes. BirdNET emits "Engine_Engine",
// "Human vocal_Human vocal" and friends, which the station faithfully stores
// with rank "species"; the discriminator that actually works is that a real
// binomial differs from the common name.
bool isTaxonomicName(const char* scientificName, const char* commonName);

// Converts one element of the `detections` array into a feed row.
// Returns false when the detection must not appear at all.
bool detectionToItem(JsonObjectConst detection, const FeedFilter& filter,
                     FeedItem& out);

// Reads a whole `GET /api/v1/detections` response body into candidate rows.
// Order and duplicate collapsing are NOT applied here - call buildFeed.
size_t collectDetections(JsonObjectConst response, const FeedFilter& filter,
                         std::vector<FeedItem>& out);

// Sorts newest-first, collapses runs of the same title into a single row
// carrying the most recent time and a repeat count, and truncates to
// filter.maxItems.
//
// Collapsing matters: a wood pigeon that called 194 times today would
// otherwise fill every row with one bird. The row still reports the most
// recent occurrence, so nothing is invented.
std::vector<FeedItem> buildFeed(std::vector<FeedItem> candidates,
                                const FeedFilter& filter);

// Distinct species seen in a `GET /api/v1/history?window=today` response,
// counting only rows that clear the threshold and are genuinely taxonomic.
// Bat passes are not species and are never counted here.
int speciesCountToday(JsonObjectConst historyResponse, double minScore);

// The ArduinoJson filters used on the device to stream-parse the two large
// responses without ever holding the whole body in RAM. Kept here so the host
// tests parse exactly the shape the firmware parses.
void buildDetectionsFilter(JsonDocument& filter);
void buildHistoryFilter(JsonDocument& filter);

}  // namespace observer
