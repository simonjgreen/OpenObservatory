# Feeding-buzz flagging and frequency-band event titles

Design, 2026-08-05. Approved in conversation before writing.

## Problem

Two requests against the live station:

1. Flag which bat passes contain a terminal or feeding buzz.
2. Show the frequency band, and the most likely species, after the "bat pass" label
   in the UI's event titles.

## What already exists

`_find_pulses` (`src/open_observatory/detectors/ultrasonic.py`) already extracts every
pulse individually as `Pulse(offset_s, duration_s, peak_hz, snr_db)` at roughly 0.5 ms
resolution. `_summarise` then reduces the train to a median inter-pulse interval and
discards the series. A feeding buzz is exactly what a median conceals, so the evidence
is being computed and thrown away. No new signal processing is required.

`FREQUENCY_HINTS` and `frequency_hint()` already map a peak frequency to a coarse UK
group, and `_summarise` already writes `frequency_group_hint` into `native_result`. The
UI has simply never displayed it.

## Constraints discovered while designing

These shaped the design and are recorded because they are not obvious:

- **No Alembic migration environment exists** (ADR-007). `create_all()` will not add a
  column to the existing SQLite database on the Pi. **The design must add no columns.**
- **`display_name` is computed in four places** with different fallbacks:
  `normaliser.py:162` (live WebSocket), `api/app.py:706` (`_detection_payload`),
  `api/app.py:462` (`taxa_activity`), `history.py:243` (`species_summary`). The REST
  variants drop the `scientific_name` fallback the live one has, so the same detection
  can display differently depending on how it was fetched.
- **`native_result` is not uniformly available.** Live WebSocket frames always carry it;
  `GET /detections/{id}` carries it; `GET /detections` omits it unless
  `include_native=True`; `history.py`'s aggregation never selects it. Anything the UI
  needs in a list must therefore not live only in `native_result`.
- **`peak_frequency_hz` is a real column** and is present in every payload shape.
- **The ultrasonic detector is not configurable.** `station.py:424` constructs it as
  `UltrasonicDetector(native_sample_rate=native_rate)`, so `min_snr_db`,
  `min_pulses_per_pass` and the band cannot be set from `runtime.env` — despite
  `HANDOVER.md` §6.3 instructing a successor to tune exactly those.

## Decision on species naming

The requested title names a candidate species. `ultrasonic-pass-v1` declares in its
metadata that it is not a classifier, and the normaliser raises `ClaimViolation` if a
non-taxonomic detector emits a species name. The bands genuinely overlap: 17–21 kHz
covers noctule and serotine and is also where bush-crickets call, which are insects.

**The species guess lives in the presentation layer only.** Titles show a candidate with
a mandatory `?` and, where relevant, an ambiguity marker. The stored record, the
detection's taxonomic fields (`common_name`, `scientific_name`, `canonical_taxon_id`,
`rank`) and anything published keep `label = "bat pass"` and no species name. The
normaliser's guard is untouched and must continue to pass.

This is a deliberate deviation from the wording in `DETECTOR_STRATEGY.md` and
`TARGET_DIAGNOSTICS.md` and needs recording — see "Documentation" below.

## Design

### 1. Buzz detection

In `_summarise`, keep the pulse list and analyse the interval series rather than only its
median. A feeding buzz is flagged when:

- there is a run of at least `buzz_min_pulses` consecutive inter-pulse intervals below
  `buzz_max_interval_ms`; **and**
- that run's median interval is below `buzz_interval_ratio` × the whole train's median
  interval.

The ratio test is what distinguishes a genuine terminal collapse from a bat that was
simply calling fast throughout the pass. Both conditions must hold.

Defaults, conservative and tunable: `buzz_max_interval_ms = 12.0`,
`buzz_min_pulses = 5`, `buzz_interval_ratio = 0.4`.

Added to `native_result` on a bat pass:

| Field | Meaning |
|---|---|
| `has_feeding_buzz` | bool, both conditions met |
| `buzz_offset_s` | offset of the first pulse of the buzz run, relative to the detection |
| `buzz_min_interval_ms` | shortest interval within the run |
| `buzz_pulse_count` | pulses in the run |
| `min_interval_ms` | shortest interval anywhere in the train, emitted on every pass |

`min_interval_ms` is emitted whether or not a buzz is flagged, so a threshold that turns
out to be wrong can be re-judged from stored data without re-running audio.

The buzz is an attribute of the pass, not a separate detection: one pass remains one
event, and the buzz is intrinsically part of it.

### 2. Candidate naming

Extend the band table to carry, per band, a short display name and an optional ambiguity
note, retaining the existing group strings:

| Band | Short name | Ambiguity |
|---|---|---|
| 17–21 kHz | noctule / serotine | may be a bush-cricket |
| 21–26 kHz | noctule / serotine | — |
| 26–38 kHz | Myotis / barbastelle | — |
| 38–50 kHz | common pipistrelle | — |
| 50–62 kHz | soprano pipistrelle | — |
| 62–90 kHz | greater horseshoe | — |
| 90–130 kHz | lesser horseshoe | — |

Derived from `peak_frequency_hz` at payload-construction time, in Python, so there is one
source of truth and no new column. Because `peak_frequency_hz` is present in every
payload shape, this works for live frames, list responses and detail responses alike.

### 3. Collapsing the duplication

A single `display_title(...)` helper in Python replaces the four independent
`display_name` fallbacks, returning both the plain `display_name` (unchanged semantics)
and a new presentational `title_hint` field, which is `null` for anything that is not an
ultrasonic pass.

A single `formatDetectionTitle(detection)` in TypeScript replaces the five render sites
that currently print `display_name` directly: `Pipeline.tsx:516`, `Suggestions.tsx:215`
and `:312`, `History.tsx:328`, `DetectionDrawer.tsx:74`, `Spectrogram.tsx:427`.

Rendered result, with the hint styled distinctly from the label:

```
bat pass · 45 kHz · common pipistrelle?
bat pass · 21 kHz · noctule / serotine?  (may be a bush-cricket)
bat pass · 45 kHz · common pipistrelle? · feeding buzz
```

The drawer additionally shows the full group string, the buzz figures, and an explicit
statement that the candidate is inferred from frequency and is not an identification.

### 4. Buzz in list responses

`_detection_payload` gains a small derived `flags` object (`{"feeding_buzz": true}`),
computed from the row's `native_result` column, so history and list rows can show the
marker without shipping the entire raw blob per row. `history.py`'s aggregation is not
changed; per-detection rows are where the marker belongs.

### 5. Detector configuration

Wire the ultrasonic detector's existing constructor arguments to `Settings`, alongside
the three new buzz keys: `ultrasonic_min_snr_db`, `ultrasonic_min_pulses_per_pass`,
`ultrasonic_band_hz`, `ultrasonic_pass_gap_s`, `ultrasonic_buzz_max_interval_ms`,
`ultrasonic_buzz_min_pulses`, `ultrasonic_buzz_interval_ratio`. Defaults must equal the
current constructor defaults exactly, so behaviour is unchanged until someone sets one.

## Testing

- Synthetic pulse train with a terminal interval collapse → buzz flagged, offset and
  count correct.
- Synthetic train with uniform long intervals → not flagged.
- Synthetic train that is fast throughout, with no terminal collapse → **not** flagged.
  This is the test that justifies the ratio condition.
- Train too short to satisfy `buzz_min_pulses` → not flagged.
- Band-boundary cases for candidate naming, including the ambiguity marker at 17–21 kHz
  and a frequency above the top band returning no candidate.
- A normaliser test asserting a detection carrying a `title_hint` still has no
  `common_name`, `scientific_name` or `canonical_taxon_id`, and that `ClaimViolation`
  still fires if one is set.
- Frontend tests for `formatDetectionTitle` covering: a bird (unchanged), a bat pass with
  and without a candidate, with and without a buzz, and an ambiguous band.
- Existing suites must stay green: 161 Python, 38 frontend.

## Documentation

- ADR-013 gains a note that the UI names candidate species presentationally while the
  record does not, with the reasoning.
- `DETECTOR_STRATEGY.md` and `TARGET_DIAGNOSTICS.md` currently say the detector offers a
  coarse group hint only; both need the same clarification.
- `HANDOVER.md` §6.3 item 6 (review the ultrasonic false-positive rate) should note that
  buzz figures and `min_interval_ms` are now available as evidence for that tuning.

## Out of scope

- Any species classifier. BatDetect2 remains Milestone 5.
- The night scheduler and the detector configuration wiring it needs. Both are separate
  Milestone 5 items sequenced *before* this work, because tuning buzz thresholds against
  data containing a full day of daytime broadband transients would be tuning against
  noise the scheduler is about to remove.
- Filtering or aggregating history by buzz.
- Persisting a buzz flag as its own column, which would need a migration path that does
  not exist yet.
