---
aliases:
  - ADR-020
tags:
  - adr
---
# ADR-020: Detections from non-live sources are excluded from browsing views by default
**Status:** active.

**Decision:** Every endpoint that presents detections as observations —
`GET /api/v1/detections`, `GET /api/v1/detections/{id}`, `GET /api/v1/taxa/activity`,
and the timeline/species/unidentified sections of `GET /api/v1/history` — excludes
rows whose stream's `source_kind` is not `alsa` unless the caller passes
`include_synthetic=true`. Excluded list/aggregate responses report
`include_synthetic` and `excluded_synthetic_count` alongside the results, so an empty
result is distinguishable from a quiet night rather than looking identical to one.
`GET /api/v1/detections/{id}` on an excluded row returns `404` with an explanatory
detail (`include_synthetic=true` to retrieve it) rather than a silent `200`, because
the detail view is reached from a list that already excludes it by default, so the
honest answer to "why can't I find it" is the same 404 the list implied. `history`'s
`coverage` block is deliberately unaffected by this filter: it already separates
`seconds_from_microphone` from total coverage and is not a wildlife view, so
excluding synthetic rows from it would hide, not surface, the fact that the
microphone was absent.

Rows are still stored, not discarded — they are a true record of what the detector
did, and useful for testing — but they carry `source_kind` and a derived
`is_live_source` boolean so every consumer can make the same distinction without
re-deriving it. "Live" means `source_kind == "alsa"` specifically
(`history.LIVE_SOURCE_KIND`); "non-live" is everything else, which includes
`replay` as well as `synthetic` — a fixture WAV replayed for testing is exactly as
misleading in a browsing view as the synthetic tone generator is, and both are
excluded by the same predicate (`history.is_not_live`, which also treats a
`NULL`/missing `source_kind` as non-live rather than assuming it is genuine).

**Reason:** On 2026-08-08 the AudioMoth's mode switch was moved to `USB/OFF`, so it
stopped presenting an ALSA card. `OO_SOURCE=auto` correctly fell back to the
synthetic scene and correctly reported itself degraded in `/api/v1/health`, but
detectors kept running against synthetic audio and their detections were persisted
alongside genuine ones with no visible distinction. The live database gained 5 bird
detections attributed to *Grey-winged Inca-Finch* — a South American species with no
plausible presence at this station — plus 515 acoustic events, and both were
indistinguishable from real records in the history and species views. Deleting the
rows would have destroyed a true record of detector behaviour on synthetic input,
which is useful for exactly the kind of regression testing [[ADR-010 - activity-v1, the first plugin|ADR-010]]'s `activity-v1`
exists for; the fix is at the presentation layer, not the storage layer.

**Constraint:** Any new endpoint that lists or aggregates detections for a human to
read as observations must apply the same `is_live`/`is_not_live` predicate and
report `excluded_synthetic_count`. An endpoint that aggregates without this filter
and without the count is a regression of this ADR, not a stylistic choice.

**Reviewed 2026-08-29:** the decision holds, and was checked against the running
station rather than only against the code — read-only queries over the incident
window (2026-08-08 11:36–12:02 UTC, three `synthetic` streams).
`GET /api/v1/detections` returned nothing and reported
`excluded_synthetic_count: 568`; the same query with `include_synthetic=true`
returned those rows carrying `source_kind: "synthetic"` and
`is_live_source: false`; the detail endpoint answered `404` with the sentence
above, and `200` under the override; `history` returned no `species` and no
`unidentified` while `timeline.excluded_synthetic_count` was 568 and `coverage`
still reported 1450.5 s captured against `seconds_from_microphone: 0.0` — the
filter left standing the one number that says the microphone was absent, which
is exactly what the paragraph above asks of it.

Two corrections. The tally in **Reason** was taken while the incident was still
running: the final count is **6** *Incaspiza ortizi* rows and **562** acoustic
events, not 5 and 515. And the **Constraint** is written stricter than what was
built — three surfaces added since apply the predicate but do not report the
count. `GET /api/v1/detections/export` filters on `is_live` and honours
`include_synthetic`, but its JSON body carries only `count` and its CSV none;
the counter-top display's socket ([[ADR-038 - Display push channel|ADR-038]])
filters in SQL and has no field to report one in; and the MQTT publisher ([[ADR-025 - MQTT and Home Assistant|ADR-025]]) suppresses on the *current* capture status
(`is_live_hardware`) rather than on the row's own `source_kind`, which is the
right answer for a live notification but is not the predicate named here. None
of the three leaks a non-live row, so the exclusion itself holds everywhere; it
is the reporting half of the constraint that is partial. The web UI has the same
gap, and a narrower one than it first looks: while capture is synthetic the
header and the operator summary say so in as many words, but nothing in
`web/src` reads `excluded_synthetic_count`, so an operator browsing back to the
08-08 window from a healthy station is never told how many rows were hidden. It
is not left unexplained, though: `web/src/components/History.tsx` draws the
coverage bar's non-`alsa` spans in a hatched style of their own with
`source_kind` in the tooltip, and the figures beneath it read 0s from the
microphone against 1450.5 s captured. That is the `coverage` block carrying the
explanation — the reason it is exempt from this filter — rather than this ADR's
count.

---
Part of the [[ADRS|Architecture Decision Record index]].
