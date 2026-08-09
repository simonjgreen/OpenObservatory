#include "model/detection_feed.h"

#include <algorithm>
#include <cstring>
#include <set>

namespace observer {
namespace {

const char* stringOr(JsonVariantConst v, const char* fallback) {
  const char* s = v.is<const char*>() ? v.as<const char*>() : nullptr;
  return (s != nullptr && s[0] != '\0') ? s : fallback;
}

bool isBatDetection(JsonObjectConst detection) {
  // Two independent signals, because either one on its own is a single point
  // of failure: the normaliser's taxonomic group, and the plugin that emitted
  // it. `ultrasonic-pass-v1` is the only bat-pass detector the station runs.
  const char* group = stringOr(detection["taxonomic_group"], "");
  if (std::strcmp(group, "bat") == 0) {
    return true;
  }
  const char* plugin = stringOr(detection["detector"]["plugin_id"], "");
  return std::strcmp(plugin, "ultrasonic-pass-v1") == 0;
}

}  // namespace

bool isTaxonomicName(const char* scientificName, const char* commonName) {
  if (scientificName == nullptr || scientificName[0] == '\0') {
    return false;
  }
  if (commonName == nullptr || commonName[0] == '\0') {
    return false;
  }
  // BirdNET's non-bird classes round-trip as "Engine_Engine",
  // "Human vocal_Human vocal" and so on: the scientific name is just the
  // common name again. A real binomial never is.
  if (std::strcmp(scientificName, commonName) == 0) {
    return false;
  }
  return std::strchr(scientificName, ' ') != nullptr;
}

bool detectionToItem(JsonObjectConst detection, const FeedFilter& filter,
                     FeedItem& out) {
  const int64_t start = parseIso8601Utc(detection["event_start_utc"]);
  if (start == kInvalidTime) {
    return false;  // an undateable row cannot be placed on a timeline
  }

  // A claim the station has withdrawn (ADR-044). The push channel already
  // filters these server-side, but this HTTP fallback path reads
  // `/api/v1/detections`, which deliberately still *returns* withdrawn rows so
  // that the record stays visible and attributable - marked, not deleted. This
  // screen has no marker to render: no score, no caveat, one line per row, read
  // from across a room. So it declines the row instead. Checked before anything
  // else, so it applies to bat passes as well as named species.
  if (detection["withdrawn"].as<bool>()) {
    return false;
  }

  if (isBatDetection(detection)) {
    if (!filter.showBats) {
      return false;
    }
    // No name, no score, no threshold. A pass is a pass.
    out = FeedItem{};
    out.kind = FeedItemKind::kBatPass;
    out.title = "Bat pass";
    out.detail = formatPeakFrequency(detection["peak_frequency_hz"] | 0.0);
    out.startUtc = start;
    return true;
  }

  const char* common = stringOr(detection["common_name"], "");
  const char* scientific = stringOr(detection["scientific_name"], "");
  if (!isTaxonomicName(scientific, common)) {
    // Either an unidentified acoustic event, or one of BirdNET's non-taxonomic
    // classes. Neither is a garden species and neither is presented as one.
    return false;
  }

  const double score = detection["score"] | 0.0;
  if (score < filter.minScore) {
    return false;
  }

  out = FeedItem{};
  out.kind = FeedItemKind::kSpecies;
  out.title = common;
  out.detail.clear();
  out.startUtc = start;
  return true;
}

size_t collectDetections(JsonObjectConst response, const FeedFilter& filter,
                         std::vector<FeedItem>& out) {
  size_t added = 0;
  for (JsonObjectConst detection :
       response["detections"].as<JsonArrayConst>()) {
    FeedItem item;
    if (detectionToItem(detection, filter, item)) {
      out.push_back(std::move(item));
      ++added;
    }
  }
  return added;
}

std::vector<FeedItem> buildFeed(std::vector<FeedItem> candidates,
                                const FeedFilter& filter) {
  // Newest first. std::stable_sort so that two detections sharing a start
  // instant keep the order the station returned them in, which keeps the
  // display deterministic across polls.
  std::stable_sort(candidates.begin(), candidates.end(),
                   [](const FeedItem& a, const FeedItem& b) {
                     return a.startUtc > b.startUtc;
                   });

  std::vector<FeedItem> feed;
  for (FeedItem& item : candidates) {
    if (!feed.empty() && feed.back().kind == item.kind &&
        feed.back().title == item.title) {
      // Same thing again, immediately below. Fold it in; the row already
      // carries the most recent time because we are walking newest-first.
      feed.back().repeats += item.repeats;
      continue;
    }
    if (feed.size() >= filter.maxItems) {
      break;
    }
    feed.push_back(std::move(item));
  }
  return feed;
}

int speciesCountToday(JsonObjectConst historyResponse, double minScore) {
  std::set<std::string> species;
  for (JsonObjectConst entry : historyResponse["species"].as<JsonArrayConst>()) {
    const char* common = stringOr(entry["common_name"], "");
    const char* scientific = stringOr(entry["scientific_name"], "");
    if (!isTaxonomicName(scientific, common)) {
      continue;
    }
    // `best_score` is the strongest score the station saw for this taxon in
    // the window. If even the best does not clear the threshold, nothing this
    // species did today would have reached the screen, so it must not be
    // counted either - the footer has to agree with the feed.
    const double best = entry["best_score"] | 0.0;
    if (best < minScore) {
      continue;
    }
    species.insert(scientific);
  }
  return static_cast<int>(species.size());
}

void buildDetectionsFilter(JsonDocument& filter) {
  // Streaming filter. The station's detection payload embeds a `media` array
  // per detection with checksums and byte lengths - roughly 1.8 kB a row,
  // against ~200 bytes of the fields we actually render. Filtering during the
  // parse keeps a 45 kB response inside a few kB of heap on a PSRAM-less
  // ESP32.
  JsonObject detection = filter["detections"].add<JsonObject>();
  detection["event_start_utc"] = true;
  detection["common_name"] = true;
  detection["scientific_name"] = true;
  detection["taxonomic_group"] = true;
  detection["score"] = true;
  detection["peak_frequency_hz"] = true;
  detection["detector"]["plugin_id"] = true;
  // Without this the streaming filter would drop `withdrawn` during the parse
  // and every withdrawn row would read as an ordinary one (ADR-044). One
  // boolean per row is the cheapest field on this wire.
  detection["withdrawn"] = true;
  filter["excluded_synthetic_count"] = true;
}

void buildHistoryFilter(JsonDocument& filter) {
  // `range.start_utc` for the `today` window is the station's local midnight,
  // which is how the display learns the station's UTC offset. See
  // observer::offsetFromLocalMidnight.
  filter["range"]["start_utc"] = true;
  JsonObject species = filter["species"].add<JsonObject>();
  species["common_name"] = true;
  species["scientific_name"] = true;
  species["best_score"] = true;
}

}  // namespace observer
