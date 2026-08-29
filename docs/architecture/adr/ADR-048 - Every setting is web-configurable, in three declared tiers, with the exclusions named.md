---
aliases:
  - ADR-048
tags:
  - adr
---
# ADR-048: Every setting is web-configurable, in three declared tiers, with the exclusions named
**Decision:** A new operator gets from a freshly imaged Pi to a working, tuned
station **without opening a terminal or a text editor**. Every field of
`Settings` is either editable from the web UI — in a declared tier, **live** or
**restart-pinned** — or listed in `site_settings.NON_EDITABLE` with a concrete
hazard that earns the exclusion. The default is editable; the bar for "never"
is a named outcome with no recovery path from the browser, not tidiness and
not "an operator might get it wrong".

**Context.** [[ADR-047]] established the mechanism — `config/runtime.env` as the
one store, written atomically with comments and unknown keys preserved, mode
0600, with `Station.applied_site` keeping "saved" and "in force" honestly
apart — but applied it to a whitelist of 16 site-identity fields. Everything
else, including every detector threshold and the [[ADR-041]] spectrogram floors
and ceilings, meant SSH and a text editor. That is the wrong shape for two
reasons. It makes commissioning a developer task, when the product is an
appliance. And it makes *tuning* — the thing an operator does repeatedly, in
response to what they see — the most awkward operation the system offers, when
it should be the easiest.

The immediate case was a microphone mounted next to a plant rubbing against a
shed: loud, periodic background noise. That is a mounting problem, to be fixed
physically, and this ADR deliberately does not chase the noise floor. But an
operator living with it for a week needs `ultrasonic_min_snr_db`,
`ultrasonic_min_pulses_per_pass`, the band edges and the spectrogram contrast
to hand, and a restart-and-SSH per attempt turns a minute's work into a day's.

**Mechanism.**

- **The audit is code, not prose.** `EDITABLE_SETTINGS` (129 entries) and
  `NON_EDITABLE` (20 entries) between them name every field of `Settings`
  exactly once. `tests/test_site_settings.py::TestTheAuditIsComplete` fails if
  a field is added without a recorded decision, so "not editable, no reason
  given" cannot happen by omission.
- **Tiers.** *Live* means in force now. Most live settings are free — the
  station reads them from `Settings` on every use. The rest are mapped in
  `tuning.py` to the object that holds their value: a `SpectrogramEncoder`
  floor/ceiling, a detector's thresholds via a new `retune()`, a `ClipManager`
  or `RetentionSweeper` attribute. *Restart-pinned* means saved, reported, and
  not injected: coordinates ([[ADR-047]]'s original reasoning — a live swap under a
  running range model changes what "plausible" means mid-stream), and capture
  geometry, because re-negotiating a rate or a ring depth means tearing down
  capture, and **charter item 1 forbids that as a side effect of a form
  submission**.
- **Nothing here can cost a frame of audio.** Every live application is an
  attribute rebind or a threshold swap read per column or per window. No
  device is reopened, no thread joined, no queue drained. The settings write
  itself goes to a thread only for the file I/O, exactly as before.
- **The honesty constraint applies to the settings surface itself.**
  `Station.applied_site` now records what *every* tracked component was built
  with or last retuned to, not only coordinates, and any daylight is reported
  as `pending_restart` — through `GET /api/v1/settings`, the station snapshot,
  `/api/v1/health` notes and the UI banner. Crucially this covers live-tier
  fields: a live setting whose target object does not exist (no ultrasonic
  encoder at 48 kHz) is recorded with an `UNAPPLIED` sentinel that compares
  equal to nothing, so it reads as pending until a restart binds it. Saved is
  never displayed as in force.
- **Validation is the API's job, in two layers.** Per-field: type via Pydantic
  v2, plus declared bounds, enum choices and semantics, reported by field name
  with the limit in the message. Cross-field (`validate_merged`): floor below
  ceiling, ring at least two capture blocks ([[ADR-030]]), retention ladder in
  order, pre-roll inside both the maximum clip length and the native ring,
  plausibility bands in order, at least one sample rate offered. Every rule
  runs against the *merged* configuration and only fires when one of its own
  fields is being changed, so an operator is never trapped by a pre-existing
  inconsistency they are not touching. Validation runs before anything is
  written: a rejected request leaves the file and the process exactly as they
  were, and cannot leave the station unable to start capture.
- **Defaults travel with the fields.** Every field carries its shipped value in
  the payload and a one-click reset in the panel, and clearing a field restores
  it. The [[ADR-041]] measurements are only a reference point if an operator who
  has wandered away from them can get back.
- **Dangerous but legitimate is a warning, not an exclusion.** `source`,
  `audio_device`, `clip_plugins` and `mqtt_tls_insecure` each carry a `danger`
  string the UI requires the operator to acknowledge before saving. Hiding a
  setting does not make it safe; it makes it an SSH session.
- **First run guides rather than fails.** `GET /api/v1/setup` answers the four
  questions a person has on day one — where am I, what is this called and what
  time is it here, is my microphone working, do I want MQTT — and the
  microphone step reads *live capture state*, so a station on the synthetic
  fallback says so instead of ticking a box. Dismissal is a setting
  (`setup_completed`), not browser storage: whether a station has been
  commissioned is a fact about the station. This is a guided flow, not the
  commissioning wizard of Milestone 7; it probes nothing and calibrates
  nothing.

**The exclusions, and why each one earns it.**

- `auth_*` (12 fields) — authentication must not be editable through the
  surface it protects. An unauthenticated session could disable the gate; a
  half-configured one could lock every operator out with no way back but SSH.
- `bind_host`, `bind_port` — a remote-hands lockout. The next request goes to
  an address that no longer answers and the browser cannot follow.
- `data_dir`, `database_dsn` — repointing storage under a running station
  orphans the database mid-write and strands existing clips. This is a stop,
  move, migrate, start operation, and a DSN can additionally carry credentials
  for a host this station has no business reaching.
- `runtime_env_path` — the settings store itself. Repointing it makes the UI
  write to a file the process does not read, which is precisely the
  two-configurations-that-disagree failure the mechanism exists to prevent.
- `replay_path` — the replay source plays a file of the operator's choosing
  into the live audio stream and the spectrogram. From a browser, on a station
  whose shipped default is anonymous LAN access, that is an arbitrary-file-read
  tool wearing a settings field. `source` is editable but its choices are
  narrowed to `auto`/`alsa`/`synthetic` for the same reason.
- `web_dist` — the API serves this directory over HTTP; pointing it anywhere
  publishes that path to the LAN.
- `birdnet_model_dir` — chooses which model binary the process loads. Selecting
  the file a process loads is not a settings decision made from a form; `oo
  models fetch` records provenance and licence acceptance.

**Consequences.** The settings page grew from two sections to thirteen
categories with search, collapse, per-field help, units, bounds and defaults —
all served from the API, so there is no second copy of the catalogue in the
frontend to drift. Five new `Settings` fields were added
(`birdnet_common_prior`, `birdnet_range_threshold` and the three BirdNET band
thresholds) that previously existed only as constructor defaults and had no
environment surface at all; and `setup_completed`. `SpectrogramEncoder.retune`
clears retained history, because columns already quantised through the old dB
window cannot be remapped and rendering two contrasts in one picture would be
worse than losing thirty seconds of backfill.

**For successors.** Adding a field to `Settings` now has a second obligation:
decide its tier and record it, or record why it is excluded. The test will tell
you. If it is live and something long-lived holds its value, map it in
`tuning.py`; pin the restart-tier equivalents in one of the `PINNED_AT_*`
snapshots. A "live" setting that saves and does nothing while reporting itself
applied is the exact dishonesty this ADR exists to prevent, and it is the easy
mistake to make.

### Rollback and smoke test (ADR-048)

No schema change, no new dependency, no migration. Additive to [[ADR-047]]: revert
the commits and the page returns to the 16-field site whitelist. Values already
written to a station's `config/runtime.env` are untouched by either direction —
they are read by `Settings` regardless of whether the UI can edit them.

```bash
# The catalogue, with tiers and defaults:
curl -s http://<station-host>:8080/api/v1/settings \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["fields"]), "fields", len(d["non_editable"]), "excluded")'

# A live tuning change takes effect without a restart:
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"ultrasonic_min_snr_db": 20}'
curl -s http://<station-host>:8080/api/v1/settings \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending_restart"])'   # expect []

# A restart-pinned change is saved and says so:
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"native_ring_seconds": 180}'
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["notes"])'

# A bad value is refused by name, and nothing is written:
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"spectrogram_floor_db": 0}'

# First run:
curl -s http://<station-host>:8080/api/v1/setup | python3 -m json.tool
```

---
Part of the [[ADRS|Architecture Decision Record index]].
