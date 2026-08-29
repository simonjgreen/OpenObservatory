---
aliases:
  - ADR-041
tags:
  - adr
---
# ADR-041: The ultrasonic spectrogram gets its own floor/ceiling, measured on the live station
**Decision:** `Settings` gains `ultrasonic_spectrogram_floor_db` (default
**-85.0**) and `ultrasonic_spectrogram_ceiling_db` (default **-30.0**),
independent of `spectrogram_floor_db`/`spectrogram_ceiling_db` (unchanged,
-95.0/-15.0, and still the audible channel's only range).
`Station._build_spectrograms` passes the new pair to the ultrasonic encoder
instead of the two-value literal (`-105.0`/`-25.0`) that had sat there,
unmeasured, since the channel was first built (`13c5ba6`). Both values are
published per-channel by `describe_spectrograms()` exactly as before, so the
UI's badges keep showing the mapping that is actually in effect rather than a
hidden constant.

**Symptom.** The operator: *"the ultrasonic spectrograph visual is suffering a
contrast problem at the low end where background noise is largely showing as
orange."* Confirmed from a station screenshot: the 15-45 kHz region rendered
saturated bright orange throughout, with the picture only falling back to dark
purple above ~50 kHz. The audible panel looked correct, so this was specific
to the ultrasonic channel, not the colour map (`web/src/components/Spectrogram.tsx`'s
`observatory` ramp is a standard perceptually-ordered inferno-style ramp,
unchanged by this ADR).

**Measured, not guessed.** The two channels were sharing one floor/ceiling
pair chosen for 48 kHz audio, and the ultrasonic channel's own hardcoded pair
(-105/-25) had never been checked against a real recording either. Rather than
pick new numbers by eye, `scripts/measure_ultrasonic_contrast.py` was written
and run against the live station (2026-08-09, AudioMoth capturing at 384 kHz,
30 s sample, a live-view WebSocket connection held open for the duration --
required, because [[ADR-040]] gates the encoders on there being a viewer):

```
columns sampled: 1250 (~30.0 s)
current encoder range: floor=-105.0 ceiling=-25.0
15-45 kHz (bat band) (61 bins): p1=-72.1  p50=-66.7  p95=-61.1  p99=-59.2  max=-53.5 dBFS
>=50 kHz (quiet band) (61 bins): p1=-82.1  p50=-76.1  p95=-69.9  p99=-58.9  max=-55.1 dBFS
```

Against the old -105..-25 dB (span 80) ramp, that noise floor sat at roughly
**48%-58%** of the way up -- squarely inside the ramp's orange-to-yellow top
third (the `observatory` ramp turns orange at t~0.6-0.7) once ordinary
variance (p95, p99) is accounted for. That is the saturation the operator
saw, and it is daytime background: no bats, and the AudioMoth's gain is
documented as too hot ([[HANDOVER]] sec6.3 item 4, still unresolved -- it
needs a physical switch change nobody has made), so this noise floor is
higher than a well-gained capture would show and the display has to cope with
that rather than wait for the hardware fix.

**Numbers chosen from the measurement, not by eye:**

- **Floor -85.0 dB** -- roughly 3 dB below the lowest p1 observed across
  either band (-82.1, the quiet band above 50 kHz), so genuine quiet renders
  close to black rather than pinned at zero, and the floor is not so tight
  that a slightly quieter night clips into "digitally silent".
- **Ceiling -30.0 dB** -- the measured bat-band p50 (-66.7) plus 36 dB, chosen
  because `ultrasonic-pass-v1`'s own score already saturates to 1.0 at a peak
  SNR of noise-floor+36 dB (`detectors/ultrasonic.py`, the score expression
  `0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db - 12)/24)` maxes its second
  term once `peak_snr_db - 12 >= 24`, i.e. `peak_snr_db >= 36`). A call the
  detector already calls "as strong as it gets" now reads as visually
  near-white, and the p50-p99 noise band (-66.7 to -59.2, roughly 33-51% up
  the new 55 dB span) stays in the ramp's dark purple/magenta lower half
  instead of its saturated top.
- The detector's own detection threshold (noise+12 dB, ~-54.7 here) lands
  around the ramp's 50-55% mark -- visibly brighter/redder than the ambient
  noise sitting below it, which is the contrast the operator asked for: *"a
  bat pass at 35 kHz should be clearly brighter than the noise around it."*

**Why not per-frame auto-scaling.** The charter's honesty constraint governs
this: the display is a scientific instrument, not a photograph, and levels
are dBFS relative to digital full scale, never calibrated SPL -- the UI
footer already says so and this change does not touch that wording. Per-frame
normalisation would make every night look identical regardless of how much
was actually happening, destroying the one thing the operator uses this
picture for -- telling a loud night from a quiet one. No adaptive behaviour
was added. The floor and ceiling are fixed constants, chosen from a real
distribution, and visible in the API exactly as `spectrogram_floor_db`/
`spectrogram_ceiling_db` already were -- there is no hidden auto-scale to
disclose because none was built.

**Verified in a real browser, 2026-08-09.** It is daytime; no live bat pass
was available, so the synthetic bat scene (`--source synthetic --scene
dawn-chorus`-equivalent ultrasonic content) was used against a second,
disposable station instance to confirm a strong ultrasonic transient renders
clearly brighter than the surrounding noise under the new range, while the
live station's actual noise floor (measured above) now renders in the dark
purple/magenta band rather than orange. Screenshots taken before and after.

**Not verified:** no 72-hour soak; the measurement is one 30 s daytime sample,
not a range across seasons, temperatures or the AudioMoth's eventual
lower-gain setting. If the hardware gain fix in HANDOVER.md sec6.3 item 4
lands, the noise floor will drop and these defaults should be re-measured --
they are a configured default an operator can override
(`OO_ULTRASONIC_SPECTROGRAM_FLOOR_DB` / `OO_ULTRASONIC_SPECTROGRAM_CEILING_DB`
in `config/runtime.env`), not a hardcoded fact about the hardware.

**And a confound learned after the fact (2026-08-09).** The microphone was, at
the time of this measurement, sitting next to a plant rubbing against a shed —
loud, periodic, mechanical noise that the operator intends to remove by moving
the microphone, not by changing any setting. The −85 dBFS floor here was
therefore derived from a noise floor that includes a temporary physical fault.

That does not invalidate the change: the noise the operator complained about was
saturating the ramp, and it no longer does. But **these numbers describe a
microphone in the wrong place.** When it moves, re-run
`scripts/measure_ultrasonic_contrast.py` and expect the floor to want lowering.
Do not treat −85/−30 as a characterisation of the hardware, and do not derive
any detector threshold from them in the meantime — see HANDOVER.md §6.3 item 4.

### Rollback and smoke test (ADR-041)

No schema change, no new dependency (`websockets` is already pinned). Revert
by setting `OO_ULTRASONIC_SPECTROGRAM_FLOOR_DB=-105` and
`OO_ULTRASONIC_SPECTROGRAM_CEILING_DB=-25` in `config/runtime.env` and
restarting -- no deploy needed -- or `git revert` the commit.

```bash
python scripts/measure_ultrasonic_contrast.py --host <station-host> --seconds 30
curl -s http://<station-host>:8080/api/v1/station \
  | python3 -c 'import json,sys; print([(s["name"], s["floor_db"], s["ceiling_db"]) for s in json.load(sys.stdin)["spectrograms"]])'
# expect [("audible", -95.0, -15.0), ("ultrasonic", -85.0, -30.0)]
# then open http://<station-host>:8080/ with the ultrasonic panel selected and
# confirm the noise floor reads dark rather than saturated orange.
```

---
Part of the [[ADRS|Architecture Decision Record index]].
