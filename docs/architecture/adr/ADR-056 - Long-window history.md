---
aliases:
  - ADR-056
tags:
  - adr
---
# ADR-056: History longer than a day is a different question, a different shape and a different table
**Status:** design spike. The range grammar is implemented; everything else is a
proposal, and none of it was built. What was built and what was only proposed is separated explicitly in
"What this change actually did" at the end. Nothing here was deployed: the
station was read read-only and every measurement was taken against a copy.

**Decision (proposed).** Browsing history beyond about a day is served from a
small, rebuildable **roll-up** rather than from the `detection` table; the
**server** keeps sole authority over bucket size and aligns buckets to the
station's **local** calendar rather than to UTC epoch multiples; the **view
changes shape** as the window grows, from the live-shaped timeline to a
per-day period view to a species-by-week season view; and coverage over a long
window is presented as an explicit, complete **list of outages** rather than as
a bar whose informative parts are narrower than a pixel. The window itself
stays what it already is — **one opaque string** — so a range grammar was all
that was needed to make `/history`, `/detections` and `/detections/export`
answer "every bat pass in July".

---

### The question the operator asked

> "I also realise as a broad thing there is no custom time period browser or
> anything longer than 24 hours. I think this wants a design spike to consider."

He is right, and the gap is charter item 6 — *present an accurate history when
someone digs in*, "the long-run log of what lived in this garden". The station
has been recording since 4 August and holds **107,801 detections** as of
2026-08-10 13:39Z. The windows on offer were `last hour`, `dawn chorus`,
`last night`, `today`, `yesterday`, `24 hours`. Questions the system could not
answer at all: how did this week compare with last, when did the swifts arrive,
show me every bat pass in July, what changed after the microphone moved.

---

### How everything below was measured

A copy of the live database (150.6 MB, 107,801 detections, 77 audio streams,
1,788 capture gaps, 48,989 media assets, spanning 2026-08-04 18:44Z to
2026-08-10 13:39Z) was pulled with `scp` and verified with
`PRAGMA integrity_check` before use. The station itself was only ever read:
three `GET /api/v1/history` calls for calibration, no deploy, no restart, no
write.

Because six days cannot answer a question about three months, a **grown copy**
was built by replaying the station's own rows backwards in time — every row is
a real row from this station, with its real JSON payload, string lengths and
group mix; only the primary keys and the timestamps change. Sixty-one epochs
of six days gives **6,575,861 detections across 367 days, 6.40 GB**, at
17,918 detections/day. Row extents were snapshotted before the loop so a later
epoch cannot re-select an earlier epoch's copies (the first attempt did, and
inflated the density by 23% — the corrected build is the one used throughout).

**Calibration to the real hardware.** Three windows measured end-to-end on the
Pi 5 against its own database, with the row count each covers:

| window | rows in window | Pi, whole endpoint |
|---|---:|---:|
| `last-night` | 24,297 | 0.325 s |
| `last-24h` | 40,430 | 0.606 s |
| 6 days (`since`/`until`) | 107,801 | 1.611 s |

That is a straight line through the origin at **15–17 µs per detection row**
(15.4 µs/row between the first and third points, 17.4 between the first and
second, intercept within noise of zero). **16 µs/row is used for every Pi
projection below, and every such projection is arithmetic, not a measurement.**
The same work on the benchmark desktop is 1.6 µs/row, so this Pi is about
**10×** slower on this workload — a little worse than the 5–6× [[ADR-037 - Prune the dead indexes|ADR-037]]
measured, which is consistent with this being CPU-bound on text parsing rather
than I/O-bound.

Detections per day, measured, by plugin:

| day | activity-v1 | birdnet | ultrasonic | total |
|---|---:|---:|---:|---:|
| 2026-08-05 | 12,058 | 1,590 | 2,616 | 16,264 |
| 2026-08-06 | 11,746 | 2,761 | 502 | 15,009 |
| 2026-08-07 | 1,628 | 426 | 35 | 2,089 (the [[ADR-024 - Coverage bounded by frames\|ADR-024]] outage) |
| 2026-08-08 | 10,018 | 1,370 | 1,489 | 12,877 |
| 2026-08-09 | 16,770 | 4,335 | 2,912 | 24,017 |
| 2026-08-10 (13.6 h) | 25,602 | 3,227 | 2,207 | 31,036 so far |

**This is materially higher than [[ADR-037 - Prune the dead indexes|ADR-037]]'s 12,900–16,300/day**, measured eight
days earlier. 9 August is 24,017 and 10 August had already passed 31,000 before
lunch. The projections below use **20,000/day**; anyone rerunning them should
re-measure rather than trust that number, and [[ADR-037 - Prune the dead indexes|ADR-037]]'s growth table is now at
the top of its own range (150.6 MB over 5.70 days = **26.4 MB/day**, against
the 20.8 MB/day it measured).

---

### Finding 1: the cost of a window is the rows it contains, and nothing else

Measured on the grown copy, through the real `history.py` functions, with a
deliberately small 2 MB page cache:

| window | rows | auto bucket | buckets out | `timeline()` | `species_summary()` | `coverage()` |
|---|---:|---:|---:|---:|---:|---:|
| 1 day | 40,354 | 900 s | 96 | 46 ms | 15 ms | 8 ms |
| 7 days | 148,155 | 7,200 s | 63 | 169 ms | 57 ms | 10 ms |
| 30 days | 539,005 | 43,200 s | 50 | 629 ms | 209 ms | 15 ms |
| 90 days | 1,617,015 | 86,400 s | 91 | **1,898 ms** | 647 ms | 30 ms |
| 365 days | 6,557,466 | 86,400 s | 366 | **7,744 ms** | 2,758 ms | 106 ms |

Forcing a 30-day window to 15-minute buckets — 2,880 buckets instead of 50 —
changed the timeline's cost from 629 ms to **631 ms**. On the real database, a
6-day window at automatic buckets took the Pi 1.611 s and the same window at
900-second buckets took **1.555 s**.

**Bucket count is free. It is not the problem, and it was never going to be.**
The query is a range scan over every detection in the window followed by an
aggregate; the number of groups it collapses into changes nothing. The
operator's instinct that 2,880 buckets over a month is absurd is right about the
*chart* and wrong about the *database*. It is also worth recording that the
server **already** chooses the bucket: `History.tsx` never sends
`bucket_seconds`, and `choose_bucket_seconds` picks 600 s for a twelve-hour
night and 900 s for a 24-hour window. The client asking for a resolution is a
capability the API has (`bucket_seconds`, capped at 86,400) and the UI has
never used, and that is the right division — the server is the only party that
knows what the span costs.

Projected to the Pi at 16 µs/row and 20,000 detections/day:

| window | rows | Pi, projected |
|---|---:|---:|
| 7 days | 140,000 | **2.2 s** |
| 30 days | 600,000 | **9.6 s** |
| 90 days | 1,800,000 | **29 s** |
| 365 days | 7,300,000 | **117 s** |

Seven days is a spinner. Thirty is a page that looks broken. Ninety is a
request an operator will assume has failed, cancel, and retry — three times, on
a Pi that is also capturing audio at 384 kHz.

**[[ADR-037 - Prune the dead indexes|ADR-037]]'s own trip-wire has already fired.** It said the recommendation would
change "if a query that scans the *whole* detection history is added to the UI
or API". `GET /api/v1/history?since=…` is unbounded today: it takes an
arbitrary `since` with no cap, no `until` requirement and no authentication by
default. Nobody added a full-history endpoint deliberately; one has existed for
some time as a consequence of a convenience parameter.

---

### Finding 2: no index fixes this, and the reason is worth knowing

All measured on the grown copy, 90-day window, isolated SQL:

| variant | 90-day timeline | index cost |
|---|---:|---:|
| `count(*)` only — the pure index range scan | **46 ms** | — |
| as shipped (bucketed group-by + `audio_stream` join) | 1,463 ms | — |
| as shipped, without the join | 1,285 ms | — |
| `+ (event_start_utc, taxonomic_group, stream_id, score)` covering index | 1,228 ms | **576 MB/year** |
| integer-epoch column + covering index ([[ADR-037 - Prune the dead indexes\|ADR-037]] option E) | 535 ms | 228 MB/year |
| group-by with **no** bucket expression at all | 392 ms | — |
| `substr()` on the ISO text instead of date parsing | 616 ms | — |

Reading 1.6 million index entries costs **46 ms**. Everything else — a factor
of thirty — is CPU spent above the storage layer: `strftime('%s', …)` parsing
an ISO-8601 *text* timestamp, twice per row (the modulo form in
`bucket_expression` evaluates it twice; SQLite does not eliminate the common
subexpression), and a temp b-tree for the group-by.

Three consequences.

- **A covering index buys 16% and costs 576 MB a year.** Rejected outright.
  [[ADR-037 - Prune the dead indexes|ADR-037]] dropped four indexes for 9.76 MB; adding one 59 times larger to buy
  nothing would be an unusually expensive way to ignore that ADR.
- **[[ADR-037 - Prune the dead indexes|ADR-037]]'s option E — integer epoch timestamps — is worth about 2× on this
  workload specifically.** That is new evidence for a decision that [[ADR-037 - Prune the dead indexes|ADR-037]]
  banked for the PostgreSQL move, and it should be recorded there: this is the
  one query shape that cares. It is still not enough on its own (535 ms desktop
  is ~4 s on the Pi at 90 days) and it is still a breaking rewrite of every row.
  **Bank it; do not bring it forward for this.**
- The floor for *any* per-row approach is the 392 ms of scanning and grouping,
  which is ~4 s on the Pi at 90 days. **The row-per-detection shape cannot
  answer a seasonal question interactively, however it is indexed.**

---

### Finding 3: a roll-up is a write amplifier only if you write it on the hot path

Two candidate tables, both **derivable from `detection` by definition** — a
cache, not a record, and rebuildable from the detections at any time:

- `detection_bucket(bucket_start_utc, taxonomic_group) → detections, best_score`
  at a **900-second grain**;
- `species_day(day_start_utc, group, common_name, scientific_name, label) →
  detections, best_score, first_utc, last_utc`.

Built from the grown year and measured:

| | rows | size | per row |
|---|---:|---:|---:|
| `detection_bucket` | 50,569 | 1.57 MB | 31 B |
| `species_day` | 18,317 | 2.78 MB | 152 B |

**4.35 MB for a year**, against 6.40 GB of detail: **0.07%**. Serving the same
questions from it:

| window | timeline, detail table | timeline, roll-up | species, detail | species, roll-up |
|---|---:|---:|---:|---:|
| 7 days | 169 ms | **0.3 ms** | 57 ms | **0.3 ms** |
| 30 days | 629 ms | **0.9 ms** | 209 ms | **0.9 ms** |
| 90 days | 1,898 ms | **2.9 ms** | 647 ms | **2.6 ms** |
| 365 days | 7,744 ms | **12.0 ms** | 2,758 ms | **11.8 ms** |

A whole year's phenology — first and last day every species was heard, with its
total — is **7.4 ms** for 101 species. On the Pi, at the measured 10× factor,
those are 29 ms and 120 ms. Interactive, on the device, over a season.

**The write cost, which is the part the charter cares about.** The database
lives on an SD card and write amplification is a real cost, so the roll-up was
costed the way [[ADR-037 - Prune the dead indexes|ADR-037]] costed inserts: WAL bytes per detection insert, one
commit per detection, `wal_autocheckpoint=0` so every dirtied page is counted.

| maintenance strategy | µs/row | WAL B/row | extra writes/day at 20,000/day |
|---|---:|---:|---:|
| as shipped, no roll-up | 35.4 | 38,147 | — |
| roll-up table present, rebuilt **daily in batch** | 27.8 | 38,112 | **~20 kB** |
| roll-up maintained by an `AFTER INSERT` trigger | 28.6 | 42,212 | **~81 MB** |

An incremental trigger adds **4,065 WAL bytes to every single detection**, a
10.7% increase on the station's whole database write volume, forever, to
maintain a 4 MB table. Rebuilding yesterday in one batch costs **20 kB and
0.09 s of CPU, once a day** — 7.2 MB a year, **0.08%** of the ~9.6 GB/year the
detections themselves write.

That is the whole answer to "a roll-up table is a write amplifier, cost it".
**It is, if it is written per event; it is not, if it is written per day.** The
station already has a daily batch process with a CPU fence — the refinement
runner ([[ADR-045 - Refinement runner|ADR-045]]) — and this belongs beside it, not in
`Station._insert_detection`.

Two rules keep it honest rather than making it a second, divergent record:

- **The detection table always wins.** The roll-up is served only when the
  requested bucket is a whole multiple of the 900-second grain *and* the window
  is longer than the crossover; anything finer or shorter goes to the detail
  table, which is fast at those sizes anyway. A test should assert the two
  paths return identical numbers for a window both can serve.
- **Rebuildable, and marked with what built it.** A roll-up row records the day
  it covers and the version of the code that summarised it, so a changed
  definition (a new detector, a corrected filter, a withdrawn detection under
  [[ADR-044 - Withdrawn detections|ADR-044]]) is a rebuild of affected days rather than a permanent lie. This also
  makes refinement safe: refinement changes rows, so the days it touched are
  re-rolled.

Note what this roll-up is **not**. [[ADR-037 - Prune the dead indexes|ADR-037]]'s option D proposed *replacing*
`acoustic_event` rows with a per-minute summary — destroying per-event detail
permanently, which that ADR correctly left to the operator's judgement. This is
additive: nothing is deleted, every detection remains addressable, and the
roll-up can be dropped entirely with no loss. It does not ask the operator for
anything.

---

### Finding 4: UTC-aligned day buckets file 15% of this station's bats on the wrong day

`bucket_expression` truncates to multiples of the bucket size in **epoch
seconds**. For any bucket up to an hour that is invisible. At 86,400 seconds —
which `choose_bucket_seconds` selects for any window over about twelve days —
buckets begin at **UTC midnight**, and the station presents local time.

In British Summer Time that puts the local hour 00:00–01:00 into the *previous*
day's column. Measured on the live database:

| group | detections in 23:00–24:00 UTC (00:00–01:00 local) | share of that group |
|---|---:|---:|
| bat | 1,592 | **15.15%** |
| acoustic_event | 143 | 0.17% |
| bird | 1 | 0.01% |
| all | 1,736 | 1.61% |

The first hour after local midnight is prime bat time, so the group that most
needs a per-night reading is the one most damaged by a per-UTC-day one. A
column labelled "5 August" that contains a sixth of the night of the 6th
violates the charter's flat requirement that *a number shown to a human must
mean what its label says*, and it does it silently, in the exact view a
seasonal question would use.

It cannot be fixed by adding to the friendly ladder either. A week is 604,800
seconds and epoch zero was a Thursday, so epoch-multiple "weeks" start on
Thursdays. Months and years are not fixed-length at all.

**So: buckets of a day or more must be *calendar* buckets, computed in the
station's timezone.** In SQL that is `date(event_start_utc, '<offset> hours')`
or a `local_day` column on the roll-up; the roll-up makes this nearly free,
because a 900-second grain re-aggregates exactly into local days for every
timezone whose offset is a whole multiple of 15 minutes — which is all of them.
Storing UTC 900-second buckets and deriving the local calendar at query time
also means a change to the station's `timezone` setting re-labels history
correctly instead of corrupting it.

`choose_bucket_seconds` should also grow a stated ceiling. Its `friendly` ladder
ends at 86,400 and silently returns that for anything larger, so a ten-year
window quietly becomes 3,650 buckets. Above a day the ladder should be
calendar-named — day, week, month — not a number of seconds.

**Reviewed 2026-08-29:** the finding holds; the crossover quoted in it does not.
`choose_bucket_seconds` targets 120 buckets, so 86,400 s is selected only above
about **sixty** days, not twelve — as Finding 1's own table shows, a 30-day window
gets 43,200 s. The ladder is unchanged since this was written
(`src/open_observatory/history.py:400`), so this was arithmetic rather than
drift. It moves the misfiling later rather than removing it: 43,200 s buckets
appear above about twenty days and are anchored to 00:00 and 12:00 UTC, which in
British Summer Time is 01:00 and 13:00 local, so the first local hour of a day
still lands in the previous column.

---

### Finding 5: the coverage bar already fails, and the window length is not why

Charter item 2 — distinguishing a quiet night from a dead microphone — does not
weaken as the window grows. The current presentation cannot carry it, and
measurement shows it stopped carrying it some time ago.

Running the station's own `coverage()` over the whole record (5.70 days,
2026-08-04 18:44Z to 2026-08-10 11:37Z): **fraction captured 0.6439**, 76
stream spans, 1,778 capture-gap rows, and the complement of the merged live
intervals is **71 distinct outages**:

| outage length | count | total |
|---|---:|---:|
| ≥ 1 s | 71 | 2,949 min |
| ≥ 10 s | 30 | 2,946 min |
| ≥ 60 s | 10 | 2,938 min |
| ≥ 300 s | 6 | 2,932 min |
| ≥ 1 h | 5 | 2,905 min |

The five longest are 1,777 min (the [[ADR-024 - Coverage bounded by frames|ADR-024]] hang, 7–8 August), 432 min, 343
min, 180 min and 173 min. Now render those into a phone-width bar of ~360 px:

| window | one pixel is | outages narrower than a pixel |
|---|---:|---:|
| 6 days | 24 min | **65 of 71** |
| 42 days | 168 min | **66 of 71** |
| 365 days | 1,460 min | **70 of 71** |

**At the window the UI already offers, 65 of 71 real outages are invisible.**
Six weeks is not where this breaks; six days is. The operator's instinct that a
two-hour outage becomes invisible at fortnight scale is right, and understated —
a two-hour outage is *already* only three-quarters of a pixel at six days.

There is a payload problem stacked on top of it. `coverage()` returns one span
object per `audio_stream` row in the window. Measured on the grown year:

| window | spans returned | coverage JSON |
|---|---:|---:|
| 1 day | 72 | 24 kB |
| 30 days | 441 | 142 kB |
| 90 days | 1,201 | **386 kB** |
| 365 days | 4,672 | **1,501 kB** |

A one-and-a-half-megabyte JSON array of stream spans, sent over garden WiFi, so
that a browser can draw 4,672 rectangles into 360 pixels. That is squarely
against the charter's network-efficiency constraint and it buys nothing legible.

**Proposal: at long spans, render the complement.** Coverage is normally ~99%;
the *gaps* are the information, and there are few of them. Three changes:

1. **Bucket the coverage server-side into the same buckets as the timeline** —
   fraction captured per bucket — and draw it as a strip beneath the bars, so
   a day that was only 60% covered reads immediately and its bar is understood
   as an undercount rather than a quiet day.
2. **Return the outage list explicitly, complete above a threshold**, longest
   first, with start, end, duration and cause where one is known — plus a count
   and total of those below the threshold, so nothing is hidden silently. Then
   a two-hour outage in a six-week window is *guaranteed* to be stated in words
   whatever its pixel width. This is the mechanism that makes charter item 2
   survive scale: it stops depending on geometry.
3. **Stop returning per-stream spans for long windows.** They are a diagnostic,
   not a picture; keep them under the existing operator/diagnostic depth toggle
   ([[ADR-028 - One depth toggle|ADR-028]]) or behind an explicit parameter.

The suspect-stream machinery from [[ADR-024 - Coverage bounded by frames|ADR-024]] must survive this unchanged: a
`suspect_stream_count` in a season view is exactly the kind of thing that must
not be averaged away, and it is one integer. The same is true of
`seconds_paused` and the pause list ([[ADR-055 - Timed recording pause|ADR-055]]) — a paused fortnight must not
read as a broken one.

**Reviewed 2026-08-29:** the geometry argument holds; the payload one has largely
gone away. The span counts above come from a copy grown out of the 4–10 August
era, when capture restarted about twelve times a day. It no longer does:
`GET /api/v1/history?window=last-7d` on the station returns **3** stream spans in
a 1.3 kB `coverage` object, and outages now run at two or three a week
([[ADR-073 - Five capture SLOs|ADR-073]]). A year at that rate is a few hundred spans, not 4,672, so proposal
3 is the least urgent of the three. Proposals 1 and 2 stand — one outage still has
to be legible whatever its pixel width.

---

### Finding 6: the live-shaped view is right for a day and wrong for a season

`History.tsx` borrows the live view's frame: one stacked bar chart against a
time axis, one coverage bar, one species table. That is a good shape for "the
shape of last night" and a bad shape for "when did the swifts arrive", because
a year of stacked bars answers a question nobody asked and hides the one they
did.

**Proposal: three shapes, chosen by span, not three pages.**

| span | shape | what it answers |
|---|---|---|
| ≤ 48 h | **today's view, unchanged** | the shape of a night; when activity peaked |
| 2 days – ~6 weeks | **period view**: one column per local day, stacked by group; coverage as a per-day fraction strip; outage list; species table gains a sparkline per species | how this week compared with last; what changed after the microphone moved |
| > ~6 weeks | **season view**: species × week matrix (a phenology grid), first-heard / last-heard table, no per-detection timeline at all | when the swifts arrived; which weeks the bats were out |

Two notes on this.

`species_summary` **already returns `first_seen_utc` and `last_seen_utc`**. Over
a wide range those two columns *are* the arrival and departure dates. The
phenology question needs no new aggregate — only a range wide enough to ask it
over and a table that puts those columns first instead of last. That is a
genuinely cheap win once the roll-up makes the range affordable.

The season view must keep the honesty markers the day view has. A phenology grid
is exactly the artefact that gets screenshotted and believed, so
`excluded_withdrawn_count`, `excluded_synthetic_count` and the "counts of
detections, not of animals" note have to travel with it, and a first-heard date
resting on a single detection needs to look different from one resting on
forty. A cell should carry its detection count, not just a colour.

---

### The range control: what is actually good on a phone in a garden

The obvious answer is a pair of date pickers, and the obvious answer is wrong
here as the primary control. On a phone it is six-plus taps to express "last
week", two native pickers with different behaviour on iOS and Android, and it
invites spans the station cannot answer. It is a good *escape hatch* and a bad
default.

**Proposal: granularity plus a stepper.**

- A row of granularity chips: `day` · `week` · `month` · `season` · `year`,
  alongside the existing sub-day named windows.
- A `‹ ›` pair that steps the window back and forward by its own length, and a
  "now" affordance to return.
- A `custom…` link that reveals two native date inputs, for the rare precise
  case.

Three properties earn this over a picker:

1. **"How did this week compare with last?" is one tap** — the `‹` button. No
   comparison mode, no dual-range query shape, no second aggregate. The
   comparison the operator asked for falls out of navigation.
2. **It is thumb-sized and stateless.** The whole control is (granularity,
   anchor), which is one string on the wire — and [[ADR-054 - Responsive layout|ADR-054]]'s `segmented-wrap`
   already handles a chip row that will not fit a phone width.
3. **It maps onto calendar ranges exactly**, which is what Finding 4 says the
   buckets have to be anyway.

Relative ranges (`last 7 days`) and calendar ranges (`July`) are different
questions and both are wanted: relative for "what has been happening lately",
calendar for "what did July look like", which is the one that compares across
years. Both are in the grammar. Open-ended ranges are already expressible as
`since=` with no `until`.

**"Everything" is deliberately not offered.** A full-history aggregate with no
time bound is the exact query [[ADR-037 - Prune the dead indexes|ADR-037]] identified as the one thing that degrades
with database size, and offering a button for it before the roll-up exists
would be building the trap that ADR's trip-wire describes. Once the roll-up is
there, "everything" costs 12 ms and becomes the easiest thing on the page.

---

### Alternatives rejected

| # | Option | Why not |
|---|---|---|
| A | **Do nothing; cap windows at 24 h** | This is charter item 6, the thing the system is *for*. The record already spans longer than the UI can ask about, and the gap widens daily. |
| B | **Date/datetime pickers as the primary control** | Six taps for "last week", divergent native behaviour, and it hands the user a way to request a 3-year aggregate that takes the Pi two minutes. Kept as the `custom…` escape hatch. |
| C | **Index the detection table harder** | Measured: a covering index buys **16%** (1,463 → 1,228 ms at 90 days) for **576 MB/year**. [[ADR-037 - Prune the dead indexes\|ADR-037]] dropped four indexes to save 9.76 MB; this would undo that 59 times over to buy nothing. |
| D | **Integer-epoch timestamps now ([[ADR-037 - Prune the dead indexes\|ADR-037]] option E)** | Measured at ~2× on this workload — real, and worth recording against that ADR — but still ~4 s on the Pi at 90 days, and it is a breaking rewrite of every row. Stays banked for the PostgreSQL move, as [[ADR-037 - Prune the dead indexes\|ADR-037]] decided. |
| E | **Maintain the roll-up with an `AFTER INSERT` trigger** | Measured: **+4,065 WAL bytes per detection**, +10.7% on the station's whole write volume, forever, on an SD card, to maintain a 4 MB table. The daily batch costs 20 kB/day for the same result. |
| F | **[[ADR-037 - Prune the dead indexes\|ADR-037]] option D — replace `acoustic_event` rows with a per-minute summary** | Destroys per-event detail permanently and needs the operator's judgement, which this proposal does not have and does not need. The roll-up here is additive and droppable. |
| G | **Cache the `/history` JSON responses** | Does not compose across ranges (a cached July does not help "June to August"), invalidation has to understand refinement and withdrawal, and the *first* request still costs 29 s. A roll-up is a cache with a schema, which is the version that composes. |
| H | **Sample or approximate the counts for wide windows** | Violates the honesty constraint outright: a number shown to a human must mean what its label says, and "about 4,000 detections" in a chart that elsewhere means exactly what it says is the kind of sincere error the charter's precedent table is made of. |
| I | **PostgreSQL + TimescaleDB continuous aggregates** | The right *idea* — this is a continuous aggregate — but it needs a database server the station does not have, on a machine whose first rule is that capture wins. The roll-up proposed here is the same concept in 4 MB and one daily batch, and it works on the SQLite the station actually runs. Revisit at the PostgreSQL move, where it may replace the batch job. |
| J | **Aggregate in the browser** | 1.6 million rows over garden WiFi. The module docstring in `history.py` already settled this. |
| K | **A second "reports" page, separate from HISTORY** | Two places to look for the same fact, and the shape should follow the question, not the URL. One view that changes shape with span keeps a single answer to "what happened". |

---

### What this change actually did

**Implemented, tested, not deployed:**

- `history.resolve_range(name, timezone, *, now)` — a strict range grammar
  returning `None` for anything it does not recognise. It understands the six
  original windows unchanged, plus rolling relative ranges (`last-7d`,
  `last-36h`, bounded by `MAX_RELATIVE_DAYS`/`MAX_RELATIVE_HOURS`), calendar
  periods (`this-week`, `last-week`, `this-month`, `last-month`, `this-year`,
  `last-year`, ISO weeks starting Monday) and calendar literals (`2026`,
  `2026-07`, `2026-W32`, `2026-08-05`, bounded to years 2000–2999).
- Everything resolves in the station's timezone and is stored in UTC. A
  calendar month starts at **local** midnight — 1 July 2026 begins at
  2026-06-30T23:00Z in British Summer Time — and a month spanning a clock
  change is 743 hours, not 744. Both are asserted by tests.
- **No window ever ends in the future.** An unfinished period is truncated at
  `now`, so "this month" on the tenth is ten days long. This is not cosmetic:
  `coverage()` divides captured seconds by the window's length, so an
  untruncated in-progress month would report ~30% captured and look exactly
  like the dead microphone charter item 2 exists to distinguish it from. A
  test walks all 24 hours of a day across all the new names asserting it,
  mirroring the existing test that caught `last-night` resolving into the
  future.
- `resolve_named_range` is now a thin lenient wrapper over `resolve_range`,
  keeping its documented fall-back to `last-hour` for unknown names and the
  test that asserts it. Behaviour for every existing caller is unchanged.
- Because `window` is an opaque string threaded through `/api/v1/history`,
  `/api/v1/detections` and `/api/v1/detections/export`, **no endpoint changed**
  and "every bat pass in July" is now a URL:
  `GET /api/v1/detections/export?window=2026-07&group=bat&format=csv`.
- `last-7d` ("7 days") added to the dashboard's window chips and to
  `/api/v1/history/windows`. **Deliberately the only new chip.** Seven days is
  ~140,000 rows and a projected 2.2 s on the Pi, which is a spinner; thirty
  days is 9.6 s and ninety is 29 s, which are not. The longer ranges resolve
  correctly server-side today and are reachable by URL and by export; they are
  not put on screen until there is something behind them that can answer
  quickly. The reason is written into the component so it is not re-litigated
  by guesswork.

**Proposed and not built:** the roll-up tables and their daily batch job, the
calendar-aligned buckets, the coverage/outage response change, the period and
season views, and the granularity-plus-stepper control.

**Not verified:**

- Nothing here ran on the Pi 5 beyond the three calibration `GET`s. Every Pi
  figure above 1.6 s is arithmetic from the measured 16 µs/row, and is labelled
  as such.
- Nothing ran against PostgreSQL. `date(…, '<offset> hours')` in Finding 4 is
  SQLite syntax and would need the same dialect branch `bucket_expression`
  already has.
- The grown copy replays six real days sixty-one times, so it has the right row
  shapes and the right density but only 101 distinct species and a repeating
  seasonal pattern. It is a fair model of *cost* and a poor model of *biology*;
  `species_day`'s 18,317 rows/year would be somewhat larger with a real year's
  species list, and the size conclusion (4.35 MB against 6.40 GB) is not
  sensitive to that.
- The daily-batch write cost was measured on the desktop, not on the SD card.
  The *ratio* against a per-insert trigger transfers; the absolute figure does
  not.
- The 0.6439 coverage figure in Finding 5 is over the whole record including
  the [[ADR-024 - Coverage bounded by frames|ADR-024]] hang and is not a claim about the station's current health.

### Trigger — do the roll-up when any of these is true

| Metric | Threshold |
|---|---|
| A window longer than 7 days is put on screen | immediately |
| p95 of `GET /api/v1/history` on the Pi | > 3 s |
| Sustained detections/day, 7-day mean | > 30,000 (already close: 9 August was 24,017) |
| Anyone asks for a species-by-week or phenology view | immediately — it has no cheap form without one |

**Reviewed 2026-08-29:** the implemented half is intact and the load has passed
one of these thresholds. `resolve_range` and its calendar grammar are still there
(`src/open_observatory/history.py:174`), `last-7d` is still the only chip longer
than a day (`web/src/components/History.tsx:97`) and the station's
`GET /api/v1/history/windows` lists it; nothing proposed here was built — there is
no `detection_bucket` or `species_day` anywhere in `src/` or `alembic/versions/`,
and `coverage()` still returns one span per stream. What moved is the load.
`GET /api/v1/history?window=last-7d` took **3.85 s** on the station today over
281,732 detections — 13.7 µs/row, so the 16 µs/row model itself held — against the
2.2 s projected here, because the seven-day mean is now about 40,000 detections a
day rather than the 20,000 the projections assumed. **The detections/day trigger
has fired.** The p95 trigger cannot be settled from outside: nothing reports a
request-latency percentile, on `/metrics` or anywhere else. The single
measurement above is past its line, and [[ADR-037 - Prune the dead indexes|ADR-037]]'s own 2026-08-29 review
measured `window=this-year` at 12.6 s.

---
Part of the [[ADRS|Architecture Decision Record index]].
