---
aliases:
  - ADR-040
tags:
  - adr
---
# ADR-040: The live view's pictures are drawn only while somebody is looking
**Decision:** `Station._handle_block` gates both spectrogram encoders on there
being a live viewer, exactly as it already gated the heterodyne. Two settings
control it: `spectrogram_encode_min_viewers` (default **1**; `0` restores the
old always-on behaviour) and `spectrogram_keep_audible_warm` (default
**false**). The API layer supplies the count via
`Station.set_spectrogram_consumer_count(lambda: hub.count)` — the *live* hub, not
the display clients, because the counter-top display has no canvas and would never make
the work worth doing.

Because encoding stops, the retained history is discarded the moment the gate
closes (`SpectrogramEncoder.reset(clear_history=True)`), and the station now
publishes `viewer_gated` and `history_seconds` per channel so the UI can label a
deliberately blank canvas as *filling* rather than let it read as failure.

**Reason — the steady state of this station is nobody watching.** The operator's
framing is that the counter-top display is the first-class surface and "first class BAU
experience is no web browser open"; the web UI must be fully functional while it
is open, but it is not expected to be open. Work done for an absent browser is
therefore not merely inefficient, it is charged against the event loop whose
stalls [[ADR-033]] showed produce capture gaps. The heterodyne already carried this
reasoning in a comment — "continuously heterodyning 384 kHz for nobody would
waste real CPU on a device that must never be starved of it" — and the
spectrograms simply had not been held to it.

**Measured on the live station, 2026-08-09**, five-minute windows, AudioMoth at
384 kHz, counter-top display connected throughout, `hot_path_cpu_ratio` differenced
across each window rather than read cumulatively:

| Window | Build | Live sockets | Encoders | `hot_path_cpu_ratio` | loop-lag events/min | `gaps_with_loss` |
|---|---|---|---|---|---|---|
| 1 browser | main | 1 | on | **0.1067** | 2.75 | 0 |
| 2 browsers | main | 2 | on | **0.1066** | 2.20 | 0 |
| 2 browsers | ADR-040 | 2 | on | **0.1060** | 2.20 | 0 |
| gate held shut | ADR-040 | 2 | **off** | **0.0159** | 2.00 | 0 |

**The saving is 0.0901 of a core per second of audio — 85% of the per-block hot
path — and it is paid in the state the station is in almost all the time.**
Whole-process CPU with encoders running measures 23.7% of one core's worth on a
four-core Pi.

**The second measurement, which changed the design.** The brief expected the
ultrasonic encoder to dominate: FFT 4096, four sub-windows per hop, ~167 FFTs a
second against the audible channel's ~42 of size 2048. On the target it does not
(`scripts/bench_spectrogram.py`, run on the Pi):

| Encoder | ms per 100 ms block | cpu_ratio |
|---|---|---|
| audible (48 kHz, FFT 2048, 192 bins) | 2.611 | 0.0261 |
| ultrasonic (384 kHz, FFT 4096, 128 bins) | 2.931 | 0.0293 |

Eight times the FFT work for 12% more time, because the FFT is not the expensive
part — the int16→float conversion of a whole block and the per-column max
reduction over 192 log-spaced bins are. So the proposed asymmetry, "keep the
cheap audible one warm and gate only the expensive one", would have bought back
half the history for 47% of the saving. It is implemented and configurable
(`spectrogram_keep_audible_warm`) because the trade is a matter of the operator's
taste, but it is **off** by default: on the evidence there is no cheap channel.

**Why the in-station saving (0.0901) exceeds the bench figure (0.0554).**
`hot_path_seconds` is wall time on the event-loop thread, not CPU time, so it
includes time the thread spent descheduled mid-block. That is not an error to
correct for: the quantity [[ADR-033]] cares about is exactly how long the loop is
unavailable to issue the next ALSA read, and that is what this measures. The
bench measures pure CPU in an uncontended process, and the gap between the two is
the contention the encoders were adding.

**The tension this had to resolve, and did not get to dodge.** `LiveHub`'s
docstring argues snapshot-on-connect matters because "a viewer opening the page
mid-flight would otherwise stare at an empty canvas for a minute, which looks
exactly like a broken pipeline". Gating makes that blank canvas *normal*: with no
encoding there is no history, so a browser opens empty and fills over ~30 s.
Re-introducing the confusion the original design existed to prevent was not
acceptable, so three things are true instead:

- The history is discarded when the gate **closes**, not when it reopens, so the
  invariant is simply "whatever an encoder holds is contiguous and recent". A
  client connecting during an idle period finds nothing to back-fill, rather than
  finding hour-old columns that `history_frame` would date as the last thirty
  seconds. Serving those would not be unhelpful, it would be the pipeline lying
  about what it heard and when.
- The station says which channels are gated and how much they hold, so the UI can
  distinguish "deliberately empty" from "broken" without inferring it.
- The canvas carries the sentence *"filling · this view starts when you open it.
  Detections are recorded continuously."* until it has the selected window of
  data. A brief honest label costs nothing, and it is the whole difference
  between the two states.

  **Corrected 2026-08-09.** The label first read *"history is recorded only
  while the live view is open"*, and the operator immediately read it as the
  station having stopped recording. It had not: detections, evidence clips and
  capture coverage are written continuously and are the durable record; what is
  gated is only this picture, drawn from a memory-only ring that was never
  persisted whether or not a browser was open. Conflating "the spectrogram
  image" with "the history" is precisely the sincere, believable, wrong
  statement the charter's honesty constraint exists to catch, and it survived
  review, a test and an ADR before a human read it in situ.

A second viewer joining a watched station still gets the full backfill: nothing
about snapshot-on-connect is removed, it simply has nothing to send when nothing
has been recorded.

**Why not encode at a reduced rate while idle**, keeping coarse history for
nothing much? Because a canvas whose columns are one second apart on the left and
24 ms apart on the right is a time-warped picture presented as a spectrogram,
which is a worse failure than a blank one. Blank is honest.

**The display is not collateral damage, and this was measured rather than
assumed.** [[ADR-038]]'s channel and the debug UI's live channel share one process
and one event loop, so a browser connecting *could* have cost the first-class
surface. Ninety-second windows on the live station either side of a real browser
connecting over Wi-Fi:

| | Frames to display | Dropped | Queue depth | Mean frame bytes |
|---|---|---|---|---|
| one browser | 10 | **0** | 0 | 54.9 |
| second browser connects | 9 | **0** | 0 | 51.4 |

`display_channel.per_client` reported zero drops and a zero queue in every window
of this session, including the two five-minute windows with encoders running.
No change to either transport was made, because the evidence did not ask for one:
[[ADR-012]]'s single-writer rule and [[ADR-038]]'s separate endpoint are what already
make this hold, and restructuring them speculatively would have put the project's
most expensive bug back in play.

**Cost, stated rather than discovered later:** opening the live view now shows an
empty, labelled canvas that fills over ~30 s, once, per idle period. `columns_emitted`
on an unwatched station is 0, which any future check must expect —
`tests/test_api.py::test_both_spectrogram_channels_exist_at_high_rate` used to
assert the opposite and now connects a viewer first.

**Not verified:** no 72-hour soak has run, and these are five-minute windows.
The saving is measured on the event loop's own clock; the effect on capture gaps
is inferred from [[ADR-033]]'s mechanism rather than demonstrated, because zero gaps
occurred in any window here — before or after.

### Rollback and smoke test (ADR-040)

No schema change, no new dependency. The behaviour reverts with **no deploy**:
set `OO_SPECTROGRAM_ENCODE_MIN_VIEWERS=0` in `config/runtime.env` and restart,
which restores always-on encoding exactly. To revert the code, `git revert` the
commit; the UI's "filling" label is inert on an ungated station because
`viewer_gated` is then `false` and the label only appears while a canvas is
genuinely short of data.

Target smoke test — with no browser open, columns must not advance, and a
browser must start them within a couple of blocks:

```bash
curl -s http://<station-host>:8080/api/v1/station \
  | python3 -c 'import json,sys; print([(s["name"], s["columns_emitted"], s["viewer_gated"], s["history_seconds"]) for s in json.load(sys.stdin)["spectrograms"]])'
# then open http://<station-host>:8080/ and run it again: columns_emitted rises,
# history_seconds climbs towards spectrogram_backfill_s, and the canvas carries
# "filling ..." until it does.

python scripts/measure_live_cost.py --seconds 300 --label "browser open"
python scripts/bench_spectrogram.py     # run ON the Pi; laptop figures are not evidence
python scripts/watch_display_channel.py --seconds 90 --label "browser connecting"
```

---
Part of the [[ADRS|Architecture Decision Record index]].
