---
aliases:
  - ADR-032
tags:
  - adr
---
# ADR-032: A near-zero occurrence prior suppresses a BirdNET candidate outright; a missing one gets the strictest bar, not the easiest
**Decision:** `BirdNetDetector._band_for` (`detectors/birdnet.py`) now takes two
arguments that were previously conflated into one: `occurrence` (what the range
model said for this species, or `None`) and `range_model_loaded` (whether a range
model was consulted at all). It adds a fourth band, `implausible`, with an
unreachable (`math.inf`) confidence bar, for any species at or below a new
`birdnet_plausibility_floor` setting (default `0.0005`) — no score admits a
candidate in that band, rather than the previous behaviour of merely raising its
bar to `threshold_out_of_range` (0.90) and letting an uncalibrated score clear it
anyway. Separately, a species the range model is *loaded but silent about*
(`occurrence is None` with `range_model_loaded=True`) is now sorted into a
`no_prior` band using `threshold_out_of_range`, the strictest bar available,
rather than `threshold_in_range` (0.55), the easiest — that case is distinct from
"no range model at all" (`range_model_loaded=False`), which still gets the
pre-existing `unfiltered`/`threshold_in_range` treatment, since there genuinely is
no plausibility information to act on in that case.

The banding logic itself moved to a module-level free function, `band_for`, so
both the live detector and a new historical-repair CLI command,
`oo detections reconcile-plausibility` (`cli.py`, logic in the new
`plausibility_repair.py`), apply exactly one definition of "implausible" rather
than two that could drift apart. The repair command is dry-run by default,
follows the exact shape of [[ADR-024]]'s `oo history reconcile-streams`: it never
deletes a detection row or overwrites its `native_result`, only adds a
`native_result.plausibility_review` block recording the recomputed band,
occurrence and reason, and requires `--apply` plus a confirmation (or `--yes`) to
write anything.

**Reason.** Measured on the live station's own database, 2026-08-08, with the
location filter enabled and coordinates correct (the development station — the
range model itself works: Common Woodpigeon 0.995, European Goldfinch 0.781,
"Engine" 4e-06, so this was never a misconfiguration): a *Flammulated Owl*, a
North American species with no plausible presence in the UK, scored 0.959 at
`occurrence_probability` 8e-06 and was admitted under the old `out_of_range` band,
because BirdNET scores are not calibrated probabilities and nothing stopped an
uncalibrated 0.96 from outweighing a range-model verdict of "essentially
impossible here". A Eurasian Jackdaw at 0.617 and the Flammulated Owl at 0.959 are
not separable by any single score cutoff, so raising 0.90 to 0.97 would only move
the boundary, not fix the conflict — the fix has to act in prior-space, not
score-space, which is why `implausible` suppresses outright rather than raising
the bar further. Separately, 202 of 5833 named detections (3.5%) on the live
station had `occurrence=None` and were judged against the *easiest* available
threshold — a species the range model has nothing to say about is not the same
as a species the model actively endorses, and the pre-existing single-argument
`_band_for(occurrence)` had no way to tell "no range model" and "no prior for
this species" apart, since both produced `occurrence is None`.

**Floor derivation.** `birdnet_plausibility_floor` defaults to `0.0005`, derived
from measured data rather than picked arbitrarily: implausible North American
owls on the live station sit at occurrence 8e-06–1.6e-04, while a genuine,
seasonally-uncommon Tawny Owl sits at 0.019253. The default sits roughly two
orders of magnitude below the Tawny Owl and well above the owls, with margin on
both sides — `tests/test_detectors.py::TestBirdNetAdapter::test_tawny_owl_survives_the_floor`
and `::test_near_zero_prior_is_suppressed_outright_flammulated_owl` assert both
ends of that discriminating case by name, using the exact measured scores.

**Counters.** `_suppressed_out_of_range` previously incremented for every
candidate that fell below *any* of the three original bands' thresholds,
including `uncommon` ones — so it was never actually a count of suppressed
*out-of-range* species, despite the name, and was misleading anyone reading
`GET /api/v1/detectors`. It is now one of four counters, each scoped to exactly
its own band: `_suppressed_implausible_prior`, `_suppressed_no_prior`,
`_suppressed_uncommon`, `_suppressed_out_of_range`. They are surfaced in
`BirdNetDetector.health()`'s detail string and, split by `reason` label, as the
new `oo_birdnet_suppressed_total{plugin_id, reason}` Prometheus gauge in
`api/metrics.py`.

**Suppress at the detector, not hide at the presentation layer, for new
detections — and why that differs from [[ADR-020]]'s precedent.** [[ADR-020]] keeps
non-live rows in the database but excludes them from browsing views by default,
specifically because they are "a true record of detector behaviour" worth
keeping for regression testing (a synthetic tone generator's output is legitimate
fixture material). That reasoning does not transfer here: an `implausible`-band
candidate is not a structurally different *kind* of input the way synthetic audio
is — it is an ordinary live-audio candidate that failed a judgement this
detector already makes internally for every other band, the same way a
below-`min_confidence` candidate is silently never turned into a row today. Going
forward, `implausible` and `no_prior` candidates that fail their band's threshold
are suppressed by `BirdNetDetector.analyse` before a `NativeDetection` is ever
created — consistent with the detector's own existing behaviour, and with the
useful side effect that the API, the MQTT publisher and the ESP32 counter-top display
are automatically consistent with each other, since none of them has anything
new to filter.

**What this decision does not solve: the historical rows, and three consumers
this agent's territory excluded.** The ~5833 detections already in the live
database — including the ~202 `occurrence=None` rows and however many owls
cleared the old 0.90 bar — were written under the old logic and are not retroactively
suppressed by a code change; `oo detections reconcile-plausibility` finds and
flags them (dry-run by default) but does not delete or hide them, per this
project's rule against silently rewriting an operator's historical record.
Making the API, the MQTT publisher, or the ESP32 firmware actually check
`native_result.plausibility_review.implausible` and exclude a flagged row from
presentation is *not implemented in this change* — this agent's territory was
`detectors/birdnet.py`, the BirdNET section of `config.py`, the repair CLI in
`cli.py`, metrics, and docs, explicitly excluding the API, the MQTT publisher and
firmware (owned by a concurrent agent on `web/**` and by the ESP32 work
elsewhere). Until that follow-up lands, a flagged historical row — including
whatever North American owls remain unflagged until an operator runs the repair
command with `--apply` — is still visible everywhere it was before, just
auditable as reviewed in the database once flagged. Tracked in [[HANDOVER]]
section 6.3 item 0.

**What was verified and what was not.** The exact measured priors and scores
above were independently re-queried against the live station's own
`openobservatory.sqlite` (read-only, `mode=ro`) during this work, not merely
trusted from the handover document — Tawny Owl at occurrence 0.019253 with scores
up to 0.995, Flammulated Owl at 8e-06/1e-05 with scores up to 0.988, Eurasian
Jackdaw at 0.772293 with a low-score example at 0.617, Great Horned Owl at
occurrence 0.000159 (below the new floor) and other rows genuinely at
`occurrence=None`. This ADR's code changes were exercised only against unit and
integration tests using stubbed range models and a real local SQLite database —
**not deployed to the Pi**, per this session's hard rule against touching the live
station, and not run against the live database at all (read-only queries only).
Whether `oo detections reconcile-plausibility --apply` behaves correctly against
the live station's actual 5833-row database, under its actual BirdNET model
assets, has not been verified and must be checked — ideally with `--json` piped
to a file first, without `--apply` — before it is ever run there.

---
Part of the [[ADRS|Architecture Decision Record index]].
