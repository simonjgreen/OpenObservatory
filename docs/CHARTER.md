# Charter

What this system is for, in the order that settles arguments.

**Reviewed 2026-08-30.** The priority order and the overriding constraints are
unchanged. The dated figures in "Precedent" and in "Decided: retention does not
preserve the merely uncertain" were re-checked against the code,
[[MILESTONE_STATUS]] and the live station on that date; where one has been
overtaken, a dated note follows it rather than a rewrite.

## How to use this

This is a **conflict-resolution order**. When two things genuinely cannot both
be satisfied, the lower number wins. A change that improves item 7 at the cost
of item 2 is the wrong change, however good it looks in a screenshot.

Priority order is **not** frequency of use. Item 7 runs constantly and item 6
is exercised rarely; item 6 still outranks it, because an inaccurate permanent
record is worse than a slow ephemeral one. Do not "correct" the ordering on
the basis of how often something happens.

Most changes touch no conflict at all. This document is for the ones that do.

---

## Overriding constraints

These are not priorities and cannot be traded against them. A change that
violates one is wrong even if it advances item 1.

### Honesty

**Never claim more than the evidence supports.**

- A detector that cannot identify a species must not name one.
- A score is not a probability, and must never be presented as one.
- A number shown to a human must mean what its label says.
- "Unidentified", "unverified" and "we do not know" are legitimate answers and
  must stay available all the way to the surface.

This is enforced in code, not left to discipline — the normaliser raises rather
than let a non-taxonomic detector emit a species name. That enforcement is
deliberate, because every failure of this kind so far has been *sincere*: the
system was confident, and wrong. See "Precedent" below.

### Privacy

A microphone in a garden records neighbours, visitors and passers-by who never
consented.

- Continuous human speech is not retained by default.
- Evidence retention is bounded and configurable.

No efficiency, accuracy or feature gain justifies relaxing this.

**Reviewed 2026-08-30:** three shipped mechanisms now carry this, and none of
them relaxes it. No clip is written for a human sound class (`clip_human_audio`,
default **False** — [[ADR-049 - Sound categories are not species|ADR-049]]). An acoustic-event detection — the group
`Human vocal` belongs to — keeps its detection row and no recording at all
(`retain_acoustic_event_clips`, default **False** — [[ADR-077 - Acoustic events keep no recordings|ADR-077]]). And the operator
can pause recording for a chosen time, which expires by itself, survives a
restart, and is recorded as a pause rather than read later as a gap
([[ADR-055 - Timed recording pause|ADR-055]]). All three defaults were confirmed on the live station on
2026-08-30. The 48 clips (125 MB, 24 detections) written before the first of
them became the default have never been purged with `--apply`
([[MILESTONE_STATUS]], Milestone 3). [[ADR-077 - Acoustic events keep no recordings|ADR-077]]'s tier is what should now
reclaim them, and on 2026-08-30 it had not yet: two consecutive sweeps spent the
whole 1.5 s budget in the 7-day tier and reported `acoustic_event` skipped, so
that audio is still on the disk.

---

## The priorities

### 1. Capture the audio accurately

Frames, timestamps, continuity. Every downstream fact is derived from this and
none can be better than it.

- Frames, not wall-clock time, address audio.
- One process owns the microphone.
- **Capture always wins.** Every queue between capture and a consumer is
  bounded, every drop is counted and surfaced, and no consumer may apply
  back-pressure to capture.

### 2. Keep running unattended, and know when it was not capturing

This station lives outdoors and is meant to be left alone. It must recover by
itself, and it must be able to prove afterwards what it did and did not hear.

Distinguishing **a quiet night from a dead microphone** is a first-class
requirement, not a diagnostic nicety. A silent gap nobody notices corrupts
items 3 to 6 invisibly, and does so in a way that looks exactly like success.

### 3. Write a record that is accurate enough to be useful, and honest about its own uncertainty

Something happened, at this time, and here is what we can currently say about
it — including how sure we are, and including "unidentified" as a real answer.

This must be cheap and immediate. It is the anchor everything else attaches to,
and it is written while the event is happening, so it cannot afford to be
expensive or clever.

### 4. Store evidence alongside it, efficiently

What makes item 3 checkable and item 5 possible.

Efficiency is part of the requirement, not a competing concern: evidence that
fills the disk or stalls capture has already defeated items 1 and 2.

### 5. Refine the record later, when better information exists — and never silently

Raise a confidence, lower it, replace an identification, or withdraw one.

Refinement ranks below evidence because **evidence is re-processable and a
missed event is not**: a better classifier can be run over stored clips later,
but an event never recorded is gone forever. That is also why item 3 need only
be accurate *enough* — it can be improved, provided item 4 held.

Three rules, or refinement becomes a liar:

- **Only from new information** — a better model, a human ear, a corrected
  occurrence prior. Never from re-reading the same score more optimistically.
- **Preserve the original claim.** The prior verdict stays visible and
  attributable.
- **A refined record must be distinguishable from an original one**, with what
  changed it and when. Otherwise history quietly rewrites itself.

"Withdraw", not "delete". A record the system got wrong is evidence about the
system, and that is how the system improves.

### 6. Present an accurate history when someone digs in

The durable record, on demand. This is what the system is *for*: the long-run
log of what lived in this garden.

An answer that is slow is inconvenient. An answer that is wrong is worse than
no answer, because it will be believed.

### 7. Keep the indoor display as close to real time as can be done

The everyday face of the system and the first-class operating surface: the
normal state of this system is **nobody at a browser**.

It must be current, calm, and honest about its own staleness — a display that
cannot reach the station must look unreachable, never merely quiet.

### 8. Make the live web view interesting and engaging

Genuinely valuable — it is how a person comes to care what the garden is doing —
and genuinely last.

It may not cost items 1 to 7 anything meaningful. It must be fully functional
while open, and close to free when it is not.

---

## Cross-cutting constraints

Weighed continuously, against every item above.

| Constraint | Why it binds |
|---|---|
| **Storage endurance** | The database lives on an SD card with finite write cycles. Write amplification is a real cost, not an abstraction. |
| **Storage capacity** | Bounded, tiered, reclaimed automatically. The species log outlives the audio. |
| **WiFi reliability** | Every remote surface degrades honestly when the network goes, and recovers without help. |
| **Network efficiency** | Bytes on the wire are paid continuously. Send what is needed and nothing more. |

---

## Precedent

A charter is only worth having if it settles real arguments. These are ones it
has already settled, with the evidence that settled them.

| Conflict | Resolution |
|---|---|
| Retention sweep every 10 s vs capture | **Capture.** The sweep starved the event loop 55–150 ms and cost ~1.9 gaps/min; paced to 300 s ([[ADR-033 - Retention is paced\|ADR-033]]). |
| Evidence writing vs capture | **Capture.** Evidence got a bounded queue and its own executor after clip I/O overran the ALSA ring. |
| Display latency vs network cost | **Both.** 127 kB per 20 s poll became 49 B per event ([[ADR-038 - Display push channel\|ADR-038]]) — the display got faster *and* cheaper. Efficiency and item 7 were not actually opposed. |
| Confident species claim vs plausibility | **Plausibility.** A 0.96 score on a species absent from the continent is evidence the score is meaningless for that species, not evidence of the bird ([[ADR-032 - Plausibility bands\|ADR-032]]). |
| Spectrogram history-on-connect vs idle CPU | Item 8 loses to items 1–2: the station does not compute spectrograms for nobody. |

### Failures this ordering would have caught earlier

- Detections persisted from a **synthetic source** for ~29.6 h and presented as
  observations. Item 1 was fine throughout; item 2 was not, and the honesty
  constraint was violated.
- A coverage figure capable of reading **1302%**, and an "audio lost" figure
  over-reporting by **12.9x**. Both were sincere, both were believed, and both
  are honesty failures rather than capture failures.
- **21 North American owls** reaching the counter-top display of a UK garden station.
  Item 1 was
  perfect; item 3's claim was not proportional to its evidence.

---

## Decided: retention does not preserve the merely uncertain

An earlier draft of this charter argued that retention should keep
low-confidence evidence indefinitely, because item 5 needs it. **The operator
rejected that, and was right.**

**The intention** is that refinement runs daily, so that every event meets the
refiner within about a day. If something is still unrefined at 90 days, the
refiner has already examined it and could not improve it — and it will not
spontaneously improve. In the operator's words: *"If we haven't refined by 90
days I see no reason to think we'd ever refine. Unrefined data is close to
worthless to us after a refinement pass has happened."*

**That intention is not yet the measured reality.** The refinement timer's
first real timed pass completed only on 2026-08-12 ([[HANDOVER]], "The
refinement runner's first real pass completed"), and as of that pass
**15,704 bat detections have never been examined by a refiner** ([[ADR-045 - Refinement runner|ADR-045]]).
The daily-cadence argument above governs steady state, once the timer has
been running long enough to have caught up; it does not yet describe this
station's backlog.

**Reviewed 2026-08-30:** the 15,704 above is the 2026-08-12 reading and has not
been re-measured. The runner has run nightly since — 16,586 `refinement` rows on
2026-08-23, the last pass exiting 0 ([[MILESTONE_STATUS]], Milestone 5) — so the
backlog is smaller and may be gone, but the never-examined count is only
readable through `oo refine status` on the station and nobody has read it since.
Treat the figure as dated, not as current.

So the tiers stand as they are. Passive uncertainty does not earn storage.

**Reviewed 2026-08-30: the tiers did not stand.** The 90-day boundary the
argument above rests on no longer exists. [[ADR-061 - Operator keep flag|ADR-061]]'s third addendum
(2026-08-14) retired the 90-day tier as dead code: it issued a query identical to
the 30-day one, so it could never reach a row the 30-day tier had not already
offered. What runs on the station today is 7 days (native), 30 days (audible
only), an acoustic-event tier that keeps no clip at any age
([[ADR-077 - Acoustic events keep no recordings|ADR-077]]), and the watermark. A value dimension — rarity crossed with
plausibility, plus a blind sample ([[ADR-074 - Evidence kept by value|ADR-074]], its mechanism replaced by
[[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]]) — is built, but it shipped rarity-only and `evidence_value_enabled`
is **False** on the station, so none of it is running. The conclusion below
holds: passive uncertainty still earns no storage. What has gone is the tier the
operator's "if we haven't refined by 90 days" named. Nothing now waits 90 days.

Two safeguards keep that rule honest rather than weakening it:

- **Delete on "refinement has run", not on age alone.** The risk is not old
  data, it is data the refiner never actually saw — a failed timer, missing
  model assets, a station that was down. A pure age rule destroys that evidence
  silently, having never examined it once. Each event should carry the fact that
  refinement ran, at what version, with what outcome, and deletion should
  require it.
- **An explicit human hold exempts an item.** The `review` table exists and,
  as of [[ADR-043 - Taxon correction|ADR-043]] (2026-08-09), is written to: the live station's `review` table
  holds 65+ rows. Someone marking something as needing their ear is a
  positive act, and is not the same thing as passive uncertainty.

**Reviewed 2026-08-30: the first safeguard is not in force, and the second
shipped narrower than it is written here.** `retention.py` reads no refinement
column, so the age tiers still delete on age alone and a clip can be reclaimed
having never been examined once — the exact failure the safeguard names. The
schema carries what it would need (`detection.refined_at` /
`refinement_version` / `refinement_outcome`, [[ADR-045 - Refinement runner|ADR-045]]) and
`oo refine status` reports the never-examined count, but nothing gates a
deletion on either; the gap is recorded as open in [[DATA_MODEL]] and
[[HANDOVER]], and closing it changes a live station's deletion policy, so it is
the operator's call. On the hold: a `held` review exempts an item from the two
age tiers but **not** from the watermark reclaim, deliberately
([[ADR-043 - Taxon correction|ADR-043]], [[ADR-061 - Operator keep flag|ADR-061]]). The mark that survives everything, disk pressure
included, is the operator-set `kept` flag ([[ADR-061 - Operator keep flag|ADR-061]]) — 112 detections
carried it on the station on 2026-08-30.

