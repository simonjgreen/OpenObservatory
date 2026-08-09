# Charter

What this system is for, in the order that settles arguments.

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
| Retention sweep every 10 s vs capture | **Capture.** The sweep starved the event loop 55–150 ms and cost ~1.9 gaps/min; paced to 300 s (ADR-033). |
| Evidence writing vs capture | **Capture.** Evidence got a bounded queue and its own executor after clip I/O overran the ALSA ring. |
| Display latency vs network cost | **Both.** 127 kB per 20 s poll became 49 B per event (ADR-038) — the display got faster *and* cheaper. Efficiency and item 7 were not actually opposed. |
| Confident species claim vs plausibility | **Plausibility.** A 0.96 score on a species absent from the continent is evidence the score is meaningless for that species, not evidence of the bird (ADR-032). |
| Spectrogram history-on-connect vs idle CPU | Item 8 loses to items 1–2: the station does not compute spectrograms for nobody. |

### Failures this ordering would have caught earlier

- Detections persisted from a **synthetic source** for ~29.6 h and presented as
  observations. Item 1 was fine throughout; item 2 was not, and the honesty
  constraint was violated.
- A coverage figure capable of reading **1302%**, and an "audio lost" figure
  over-reporting by **12.9x**. Both were sincere, both were believed, and both
  are honesty failures rather than capture failures.
- **21 North American owls** reaching a wall display in the development area. Item 1 was
  perfect; item 3's claim was not proportional to its evidence.

---

## Open question

**Retention currently deletes what refinement most needs.**

The tiers keep first-of-species and best-per-species clips longest and age
everything else out — optimised for a reference library. Item 5 wants the
opposite: the events most likely to need refining are the **low-confidence and
unresolved** ones, and those are exactly what the current policy discards first.

The live case: the 33–36 kHz cluster at this station is unresolved, needs a
human listening to the audible renderings, and its evidence ages out at 90 days.

A fourth retention rule — **keep the uncertain until it is resolved or
explicitly abandoned** — would close this. It is a behaviour change with a
deletion risk attached and is deliberately left as an operator decision rather
than assumed here.
