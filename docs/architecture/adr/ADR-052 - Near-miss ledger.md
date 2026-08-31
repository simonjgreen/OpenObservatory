---
aliases:
  - ADR-052
tags:
  - adr
---
# ADR-052: A counter is not a diagnostic — record what BirdNET proposed and refused, in a bounded ring with per-band score histograms
**Status:** active. Extends [[ADR-032 - Plausibility bands|ADR-032]]'s four suppression counters with the
evidence behind them, and is wired through [[ADR-048 - Web-configurable settings|ADR-048]]'s settings mechanism.

**Context, measured on the live station, 2026-08-09.** The operator was
listening to the live stream, could hear bird calls, and saw no detections for
them. BirdNET was demonstrably healthy: 998 windows analysed, **zero dropped**,
0.26 s lag — the audio was reaching the model. `oo_birdnet_suppressed_total`
reported **152 suppressed as `implausible_prior`** and 35 as `out_of_range`
inside an hour, and every named detection since 16:40 scored **0.553–0.974**,
piled hard against `birdnet_threshold_in_range = 0.55`.

And that was the entire body of evidence available. `birdnet.py` incremented a
counter and dropped the candidate on the floor: no log line, no table, no
endpoint, nothing anywhere recording **which species** was suppressed, at what
score, with what occurrence prior. So the operator could see that 152 things
were rejected and had no way to learn whether those were 152 correct
rejections of American owls or 152 wrongly-binned garden birds.

That is this project's own recurring failure mode, and the charter names it: a
coverage figure capable of reading 1302%, an "audio lost" figure over-reporting
by 12.9x, four counters that were confidently lying. *A number shown to a human
must mean what its label says* — and `152` means nothing at all on its own.

**Two of the four counters' scope is also wrong for this question.** By
[[ADR-032 - Plausibility bands|ADR-032]]'s design, `_count_suppressed` counts only the *plausibility* bands. A
candidate in the `in_range` or `unfiltered` band that scores 0.54 against a
0.55 bar is counted **nowhere** — and that is precisely the case an operator
hits first when they can hear a blackbird and the station reports nothing.

**Decision.** A new `detectors/near_miss.py` holds a `NearMissLedger`, which
every `BirdNetDetector` now owns and writes to for every rejected *and* every
admitted candidate, in every band. Three shapes, cheapest first:

1. **Per-band score histograms and counts** — twenty 0.05-wide bins of
   rejected score per band, plus the admitted count so the rejection figure has
   a denominator. This is the summary that actually chooses a threshold: "you
   rejected 400 candidates, 380 of them below 0.2 and 20 between 0.45 and 0.55"
   answers the question; "400" does not. A few integer increments.
2. **A per-species tally** — count, best rejected score, shortfall, band,
   prior, last seen, keyed by label index. This is the table a person tunes
   from, because it names the bird.
3. **A bounded ring of individual near misses** with timestamps, so the last
   few minutes can be lined up against what a person actually heard.

Served at `GET /api/v1/detectors/near-misses` (duck-typed on
`near_miss_snapshot`, exactly as `plausibility_snapshot` is — a detector with
no plausibility bands is absent rather than carrying an empty stub), rendered
in the web UI's `?view=diagnose` depth as **Rejected candidates**, directly
under the detector panel where "998 windows, 0 dropped" is read, and exported
as `oo_birdnet_candidates_{rejected,admitted}_total{plugin_id, band}` — a
different series from `oo_birdnet_suppressed_total`, because it covers bands
that metric deliberately does not.

**Measured cost, on the hardware, before anything else.** [[ADR-033 - Retention is paced|ADR-033]] is the
precedent that makes this non-negotiable: a 10-second retention sweep cost
~1.9 capture gaps/minute. Measured with `scripts/bench_near_miss.py` on the
Raspberry Pi 5 itself (the module and a driver piped to the station's own
interpreter over stdin — roughly 60 ms of CPU in total, less than one BirdNET
window, nothing written to the station and nothing restarted):

| | Pi 5 | dev laptop |
|---|---|---|
| `record_rejected`, ring + histogram + species | **2.00 µs** | 0.73 µs |
| `record_rejected`, histogram + species only (ring off) | **0.97 µs** | 0.28 µs |
| `record_admitted` | — | 0.09 µs |
| `snapshot()` (API read, off the hot path) | — | 0.065 ms |

At ten rejected candidates per window that is **20 µs per window against
BirdNET's own 72.36 ms p95 — 0.028%**, or 1.3e-5 CPU-seconds per second of
audio at the 1.5 s stride, against a station `hot_path_cpu_ratio` measured at
**0.105** the same day. It is also not on the capture path at all:
`DetectorWorker._analyse_sync` runs the plugin on its own worker thread, so
this is spent where BirdNET's 72 ms is already spent, not where capture's
frames are.

**On by default, and the justification for that rather than for a flag.** A
diagnostic you must remember to enable *before* the thing you want to diagnose
happens is not there when you need it — the operator's whole problem was
retrospective. At 0.028% of the detector's own budget there is no version of
this worth switching off, and a fifth-decimal saving is a worse trade than the
certainty that the record exists when someone asks. What *is* a setting is the
ring depth: `birdnet_near_miss_ring`, default 200, live-tier, web-editable
([[ADR-048 - Web-configurable settings|ADR-048]]), mapped in `tuning.py` so it genuinely takes effect on a running
detector. 0 keeps the histograms and the species tally — the part that decides
a threshold — and stops keeping individual rows. A resize keeps the cumulative
record, because comparing it either side of a threshold change is the entire
use.

**Bounded, by construction, at three points.** The ring is a
`deque(maxlen=...)`, which discards in C. The species table stops admitting
*new* species at `max_species` (512) and counts the ones it turned away, so the
omission is visible rather than silent — and is bounded above by the label
catalogue regardless. The histograms are seven bands × twenty ints, fixed for
the life of the process. There is no growth term anywhere. Nothing is written
to the database, so the SD card's write budget is untouched.

**Privacy: metadata, not evidence.** No audio is extracted for a rejected
candidate, no clip is written, no row is persisted; the whole structure lives
in memory and dies with the process. This does not reopen [[ADR-049 - Sound categories are not species|ADR-049]]'s decision
that human sound gets a detection row and no clip: a near-miss record carries
the same class of fact [[ADR-049 - Sound categories are not species|ADR-049]] already permits a detection row to carry
("something the model thought was a human vocal, at 18:55"), contains no
speech, and cannot be exported to a file. It is deliberately *less* durable
than the rows [[ADR-049 - Sound categories are not species|ADR-049]] allows.

**Honesty details that are not decoration.**

- The `implausible` band's bar is `math.inf` ([[ADR-032 - Plausibility bands|ADR-032]]). Its `threshold` and
  `shortfall` are reported as `null`, never as a large number: a distance would
  imply the candidate was merely a long way off rather than refused on
  principle, and "not applicable" stays available to the surface.
- Every band appears in the payload even having seen nothing, as an explicit
  zero. "We rejected none of these" and "we have no idea" must not look alike.
- The thresholds on the payload come from the **detector instance**, not from
  `Settings`. A settings write and a detector retune are two steps; reporting
  the saved value next to rejections judged under the old one is exactly the
  saved-versus-in-force dishonesty [[ADR-048 - Web-configurable settings|ADR-048]] exists to prevent.
- The payload states that nothing below `min_confidence` is ever a candidate,
  so the histogram's lowest bins are structurally empty and are not evidence
  that the model is quiet down there.
- A species' band and prior are re-stamped on every rejection rather than kept
  from first sight: the operator retunes mid-session, and a stale band would
  describe a decision no longer being made.

**Thread safety, stated rather than locked.** One writer (the detector's
analysis thread) and any number of readers (the API event loop). Every mutation
is a single operation on a built-in container, so no lock is taken on a path
adjacent to the detector's budget. A reader can see a histogram one candidate
ahead of a species tally. That is acceptable for a diagnostic and is written
down instead of being papered over.

**Known limitations.**

- **In-memory only: a restart loses the record.** That is the deliberate
  privacy and SD-endurance trade (no rows, no bytes, no writes), and it means
  the ledger cannot answer "what did you reject last Tuesday". For that,
  `oo detections reconcile-plausibility` still reads the durable table.
- **No `oo` subcommand.** The CLI has no HTTP client and inventing one for this
  would be a larger change than the feature; the `curl` recipes in
  [[DEPLOYMENT_AND_OPERATIONS]] cover the terminal case exactly.
- **The score histograms are not in Prometheus.** Twenty buckets × seven bands
  is a scrape's worth of series for something read interactively; the per-band
  totals are exported and the distribution is served on demand.
- **Only BirdNET has one.** The activity and ultrasonic detectors have no
  plausibility bands and are correctly absent from the endpoint rather than
  carrying an empty stub.
- **Not yet run against real BirdNET inference.** The model assets are
  unbundled ([[ADR-006 - Model install and licensing|ADR-006]]) and this was never deployed — the operator was tuning on
  the station throughout. Every test here uses a stub interpreter with the
  exact measured scores and priors from the live database, and the cost figure
  is from the Pi. Whether the species table's 512-entry bound is generous or
  tight against a real dawn chorus has not been observed and should be checked
  on the first deploy.

**Reviewed 2026-08-29:** it has since been deployed and has run against real
BirdNET inference (`range_model_loaded: true`, 59,153 windows analysed), so the
question above has an answer: the species table is **full**, at
`species_tracked: 512` with `species_omitted: 334`. The 512-entry bound is tight
rather than generous, and it is not an operator setting — only the ring depth is
passed to the ledger (`NearMissLedger(capacity=near_miss_ring)`,
`src/open_observatory/detectors/birdnet.py:319`). The omission stays visible in
the payload as intended, but the per-species tally is no longer the complete
table described above, and a species turned away is missing from it however
often it is refused. The scope argument holds as written: on the same read,
`oo_birdnet_suppressed_total` reported 5,141 `implausible_prior` against an
identical `oo_birdnet_candidates_rejected_total{band="implausible"}`, while the
3,203 rejections in the `in_range` band were carried by the new series alone.

### Migration

**None.** No schema change, no new dependency, no Alembic revision. One new
`Settings` field with a shipped default; Alembic head stays `0006_refinement`.

### Rollback and smoke test (ADR-052)

`git revert` restores the previous behaviour exactly: the endpoint disappears,
the panel degrades to its "not available" state rather than breaking the page
(the fetch 404s and is caught), and `birdnet_near_miss_ring` in an operator's
`config/runtime.env` becomes an unread key, which `RuntimeEnvStore` preserves.
Nothing was written to the database, so there is nothing to unwind.

```bash
# 1. The cost, on the target device, before anything else.
PYTHONPATH=src ./.venv/bin/python scripts/bench_near_miss.py

# 2. What was refused, named.
curl -s http://<station-host>:8080/api/v1/detectors/near-misses \
  | python3 -c '
import json,sys
d = json.load(sys.stdin)["detectors"][0]
print(d["rejected_total"], "rejected /", d["admitted_total"], "kept")
for s in d["species"][:10]:
    print(" ", s["common_name"], s["rejected"], s["best_score"], s["band"], s["shortfall"])'

# 3. The histogram that chooses the bar.
curl -s 'http://<station-host>:8080/api/v1/detectors/near-misses?limit=0&species_limit=0' \
  | python3 -c '
import json,sys
for b in json.load(sys.stdin)["detectors"][0]["bands"]:
    if b["rejected"]: print(b["band"], b["threshold"], b["histogram"]["counts"])'

# 4. An unreachable bar reports no distance at all (expect None/true).
curl -s http://<station-host>:8080/api/v1/detectors/near-misses \
  | python3 -c '
import json,sys
print([(b["band"], b["threshold"], b["threshold_unreachable"])
       for b in json.load(sys.stdin)["detectors"][0]["bands"]
       if b["band"] == "implausible"])'

# 5. The ring depth is genuinely live (expect [] pending, then capacity 40).
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"birdnet_near_miss_ring": 40}'
curl -s http://<station-host>:8080/api/v1/settings \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending_restart"])'
curl -s http://<station-host>:8080/api/v1/detectors/near-misses \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["detectors"][0]["capacity"])'

# 6. Per-band accounting reaches Prometheus, including the in_range band that
#    oo_birdnet_suppressed_total deliberately does not cover.
curl -s http://<station-host>:8080/metrics | grep oo_birdnet_candidates

# 7. Nothing new is persisted: this feature must not move the detection count.
#    Compare across a few minutes of rejections.
curl -s http://<station-host>:8080/metrics | grep oo_detections_persisted_total
```

**Reviewed 2026-08-30:** re-read on the station after 7 h 40 m of uptime
(18,902 windows analysed, `range_model_loaded: true`). The ledger is behaving as
designed and honestly: the `implausible` band reports `threshold: null` with
`threshold_unreachable: true` and a `null` shortfall on every species in it, and
all seven bands are present including `unfiltered` and `no_prior` at explicit
zeroes. The cross-check of the previous note repeats exactly —
`oo_birdnet_candidates_rejected_total{band="implausible"}` and
`oo_birdnet_suppressed_total{reason="suppressed_implausible_prior"}` both read
2,017 — while the 1,176 `in_range` rejections are carried by the new series alone,
which is the scope argument this ADR was written for.

The 512-entry species bound is confirmed tight rather than generous, and the
previous note's "the table is full" is a property of uptime, not of the station:
after a restart the count starts again, and this read caught it mid-climb at
`species_tracked: 466`, `species_omitted: 0`. It will reach the cap within a day of
every restart. `max_species` itself is not in the payload — only `species_tracked`
and `species_omitted` are — so an operator watching the omission count has no
denominator to read it against.

---
Part of the [[ADRS|Architecture Decision Record index]].
