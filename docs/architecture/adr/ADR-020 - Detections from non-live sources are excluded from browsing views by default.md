# ADR-020: Detections from non-live sources are excluded from browsing views by default
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
which is useful for exactly the kind of regression testing ADR-010's `activity-v1`
exists for; the fix is at the presentation layer, not the storage layer.

**Constraint:** Any new endpoint that lists or aggregates detections for a human to
read as observations must apply the same `is_live`/`is_not_live` predicate and
report `excluded_synthetic_count`. An endpoint that aggregates without this filter
and without the count is a regression of this ADR, not a stylistic choice.
