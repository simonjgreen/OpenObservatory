---
aliases:
  - ADR-037
tags:
  - adr
---
# ADR-037: Detection-table growth is real but slow; prune the five indexes nothing reads, and defer the rest
**Status:** options B and C accepted and implemented, 2026-08-09, and verified
running on the station 2026-08-29. Options A and D–K remain open, exactly as
originally proposed — the operator has not chosen among them and this update does
not relitigate them. Three of this ADR's own revisit triggers have since fired;
see the 2026-08-29 review note. No schema change, no
migration, no index drop and no model edit was made for this ADR *when it was
first written*; the original research below is preserved verbatim except for
this status line and the title. What was actually built, and how it differs
from the research's predictions, is recorded in **"B and C: what was
implemented"** at the end of this entry — read that section for the current
truth about the schema; everything above it is the research that led there.
Every number below was measured on **2026-08-09** against a
read-only copy of the live station's `openobservatory.sqlite`
(`ssh <user>@<station-host>`, `scp`, opened `mode=ro` or on a local copy; the
station's own file was never opened for writing and the station was never
deployed to or restarted).

### The question

The operator looked at `source_start_frame` / `source_end_frame` /
`audio_stream.frame_count` — `BigInteger` columns counting frames at 384,000 Hz,
so eleven digits after half a day — and said *"that index is going to get
insanely large, one to give some consideration to."*

Three separate concerns are tangled in that sentence. They have three different
answers.

---

### 1. Frame-value magnitude: **unfounded**, and there is no such index

Largest frame value anywhere in the live database: **24,830,943,552**
(`detection.source_end_frame`, on the 2026-08-06 stream that the orphan-recovery
path closed). Signed 64-bit tops out at **9,223,372,036,854,775,807**. The
station is using **0.00000027%** of the range — a factor of **3.7 × 10⁸** of
headroom, which at a continuous 384 kHz is **about 761,000 years** of unbroken
capture.

Frames also do **not** accumulate across the station's life. They are per-stream:
`min(source_start_frame)` across all 61,453 detections is **0**, and each stream's
maximum detection frame tracks its own `frame_count` (the largest live stream:
`frame_count` 16,613,836,800, max detection frame 16,614,944,704). `StreamClock`
anchors to the first block of each stream, so a restart resets the counter. Even
without the reset the answer would be the same.

**And there is no index on any frame column.** `SELECT name FROM sqlite_master
WHERE type='index' AND sql LIKE '%frame%'` returns nothing. `source_start_frame`
and `source_end_frame` are plain unindexed payload; SQLite stores them as
varints, measured at **9.7 bytes each on average**, 1.19 MB for the pair across
the whole table — 1.3% of the file.

**What the operator probably actually saw** is the *number*, not an index: an
eleven-digit integer in `/api/v1/detections` JSON or in a `sqlite3` dump, which
looks alarming and is not. The concern was worth raising and it is worth
retiring: the frame columns are among the cheapest things in the row, and their
magnitude will never matter.

Do not "fix" this by narrowing the type or resetting the counter. Frame identity
is what makes evidence reproducible (ADR invariant: frames, not timestamps,
address audio), and `BigInteger` is also what makes the PostgreSQL profile
identical.

---

### 2. Row and index growth: **real, slow, and a quarter of it is waste**

#### Measured size attribution (dbstat, whole file, 2026-08-09)

The file is 94.0 MB (89.6 MiB; `page_size` 4096 × `page_count` 22,953,
freelist 0), holding 61,453 detections, 26,853 media assets and 26,853
detection-media links accumulated over **4.53 days**.

| Object | MB | % of file | Notes |
|---|---:|---:|---|
| `detection` (table) | 44.77 | 47.6% | 728 B/row |
| `media_asset` (table) | 16.83 | 17.9% | 627 B/row |
| `ix_detection_station_start` | 4.71 | 5.0% | **never used** |
| `ix_detection_group_start` | 3.34 | 3.6% | used |
| `ix_detection_station_id` | 2.85 | 3.0% | **never used** |
| `ix_detection_stream_id` | 2.83 | 3.0% | used |
| `sqlite_autoindex_detection_1` (PK) | 2.77 | 2.9% | unavoidable |
| `ix_detection_detector_id` | 2.73 | 2.9% | **never used** |
| `ix_detection_event_start_utc` | 2.44 | 2.6% | used — the workhorse |
| `detection_media` (table) | 2.31 | 2.5% | |
| `sqlite_autoindex_detection_media_1` | 2.23 | 2.4% | covering, used |
| `ix_detection_taxonomic_group` | 1.44 | 1.5% | **never used** (prefix of `group_start`) |
| `sqlite_autoindex_media_asset_1` | 1.22 | 1.3% | used |
| `ix_media_asset_created_at` | 1.05 | 1.1% | used |
| `ix_detection_canonical_taxon_id` | 0.76 | 0.8% | **never used** |
| `ix_media_asset_kind` | 0.64 | 0.7% | used |
| `capture_gap` + its 3 indexes | 0.51 | 0.5% | |
| `ix_media_asset_reclaimed_at` | 0.23 | 0.2% | used (added by [[ADR-035 - Alembic environment\|ADR-035]]'s `0002`) |
| everything else (audio_stream, health_event, auth, schema) | 0.26 | 0.3% | |

Indexes are **27.0 MB, 28.7% of the file**. Detection indexes alone are 23.87 MB
— **more than half the size of the table they index**.

#### Which indexes actually earn their keep

Two independent checks agree.

`EXPLAIN QUERY PLAN` over the real queries in `history.py`, `api/app.py`,
`retention.py` and `station.py`:

```
timeline (12 h bucketed)      SEARCH detection USING INDEX ix_detection_event_start_utc
species_summary               SEARCH detection USING INDEX ix_detection_group_start
GET /detections?limit=200     SEARCH detection USING INDEX ix_detection_event_start_utc
GET /detections&group=bird    SEARCH detection USING INDEX ix_detection_group_start
station: max(event_end) /stream SEARCH detection USING INDEX ix_detection_stream_id
retention tier scan           SEARCH detection USING INDEX ix_detection_event_start_utc
retention exemplar scan       SEARCH detection USING INDEX sqlite_autoindex_detection_1
```

And a grep: no code anywhere filters or orders by `Detection.station_id`,
`Detection.canonical_taxon_id`, or `Detection.detector_id` (the only reference to
`detector_id` is a join *driven from* `detection` into `detector`'s primary key,
which uses `detector`'s index, not `detection`'s). `ix_detection_taxonomic_group`
is a strict prefix of `ix_detection_group_start`, so the planner never has a
reason to choose it.

**Five of the nine detection indexes are dead**: `ix_detection_station_start`
(4.71 MB), `ix_detection_station_id` (2.85), `ix_detection_detector_id` (2.73),
`ix_detection_taxonomic_group` (1.44), `ix_detection_canonical_taxon_id` (0.76)
— **12.49 MB, 13.3% of the whole database**, growing forever, written on every
one of ~14,000 inserts a day, and read by nothing.

`station_id` deserves a specific note: it holds **one distinct value** in 61,453
rows. It costs 32 B/row inline plus two indexes totalling 7.56 MB, for a column
whose entire information content today is a constant.

#### What insert throughput and SD-card writes actually cost

Measured on a 15.3 M-row copy, inserting 21,500 rows one commit at a time (the
pattern `Station._insert_detection` actually uses — one `session_scope()`, one
transaction, per detection), with `wal_autocheckpoint=0` so the WAL records every
page image written:

| configuration | µs/row | WAL B/row | ≈ card writes/day at 21,500/day |
|---|---:|---:|---:|
| as shipped: 9 indexes, random UUID4 PK | 44.7 | 17,355 | ~746 MB |
| 5 dead indexes dropped | 13.4 | 13,162 | ~566 MB |
| dropped + time-ordered (UUIDv7-style) PK | 11.3 | 6,852 | ~295 MB |

(The "card writes" column doubles the WAL figure, because a checkpoint writes the
same pages again into the main file. It is an upper bound: real commits are
smaller than the benchmark's and some pages are re-dirtied within one checkpoint
window. It is measured on x86; the *ratios* transfer, the absolute µs do not.)

Two things fall out. Dropping the dead indexes makes an insert **3.3× cheaper in
CPU** and cuts its write volume by **24%**. And the single biggest source of
write amplification that remains is the **random UUID4 primary key**: every
insert dirties a different random leaf page of a 15 M-entry b-tree. A
time-ordered key halves the writes again. Neither is urgent (see §3), but the
second is free at the PostgreSQL move and expensive afterwards.

#### Growth projection, with the range stated honestly

The clean measurement is the file itself: **94.0 MB over 4.53 days = 20.8 MB/day
= 7.6 GB/year**, which is consistent with the per-row costs
(14,000 detections × 1,116 B + 5,967 media assets × 913 B ≈ 21 MB/day).

Per-day detection counts actually observed:

| day | activity-v1 | birdnet | ultrasonic | total |
|---|---:|---:|---:|---:|
| 2026-08-05 | 12,058 | 1,590 | 2,616 | 16,264 |
| 2026-08-06 | 11,746 | 2,761 | 502 | 15,009 |
| 2026-08-07 | 1,628 | 426 | 35 | 2,089 (capture outage) |
| 2026-08-08 | 10,018 | 1,370 | 1,489 | 12,877 |

The brief's **~21,500/day is an over-estimate**: it was extrapolated from an 11.9 h
window containing a busy bat night. Measured full days are **12,900–16,300**.
This is also the **annual maximum**, not the mean — early August is peak bird and
peak bat. Bats disappear entirely from roughly November to March (2,616 → 35 rows
between two days here shows how volatile that term already is), and dawn chorus
collapses. `activity-v1` is the term that will *not* fall much, because wind and
rain are acoustic events too.

| horizon | low (8,000/day) | central (14,000/day) | high (21,500/day) |
|---|---:|---:|---:|
| detection rows, 1 yr | 2.9 M | 5.1 M | 7.9 M |
| database, 1 yr | 4.3 GB | 7.6 GB | 11.7 GB |
| database, 3 yr | 13 GB | 23 GB | 35 GB |
| database, 10 yr | 43 GB | 76 GB | 117 GB |

Assumptions: current schema and current index set; media assets keep accruing at
the observed 5,967/day (their rows are never deleted either — [[ADR-026 - Tiered clip retention|ADR-026]] only sets
`reclaimed_at`); no roll-up; SQLite page size 4096.

#### Does SQLite actually get slow? No.

Built two grown copies from the live rows (5.10 M and 15.30 M detections, 5.76 GB
and 17.29 GB files) and timed the real queries with a deliberately small 2 MB page
cache:

| query | today (61 k) | 1 year (5.1 M) | 3 years (15.3 M) |
|---|---:|---:|---:|
| timeline, last night, 10 min buckets | 13.7 ms | 7.6 ms | 7.8 ms |
| species_summary, last night | 4.3 ms | 2.9 ms | 2.6 ms |
| `GET /detections?limit=200` | 0.5 ms | 0.3 ms | 0.3 ms |
| recent-species panel, 24 h | 21.0 ms | 11.3 ms | 11.5 ms |
| retention exemplar scan | 94.9 ms | 64.4 ms | 66.1 ms |
| **full-history species list (no time bound)** | 13.3 ms | **743.6 ms** | **2,310.8 ms** |

Every query the station actually runs is **flat in database size**, because every
one of them is a bounded range scan on `event_start_utc` (or on `taxonomic_group,
event_start_utc`). They get *faster* on the grown copies only because the b-tree
was rebuilt densely. The retention exemplar scan — the one unbounded read
[[ADR-026 - Tiered clip retention|ADR-026]] deliberately allowed — is bounded in practice by *live* media assets, not
by history, and did not grow.

The only thing that degrades is a whole-database aggregation with no time
predicate. **No endpoint does one today.** That is the thing to protect.

Calibration to the real hardware: `GET /api/v1/history?range=last-night` on the
live Pi measured **128 ms** end to end (against ~20 ms of equivalent SQL on the
benchmark machine), so the Pi is roughly **5–6×** slower. `GET
/api/v1/detections?limit=200` measured **454 ms** on the Pi against 0.3 ms of
SQL — that endpoint's cost is **JSON serialisation of 200 full rows including
`native_result`**, not the index, and no amount of schema work will change it.

**Conclusion for §2:** the growth is real and permanent, but at 7.6 GB/year with
flat query latency it is a *housekeeping* problem, not an architectural one. The
one genuinely indefensible thing is 12.49 MB (13.3%) of indexes that no query
reads.

---

### 3. Where the data lives: **not the emergency it was in August**

The database is on the SD card by [[ADR-021 - Clips on their own device|ADR-021]]'s deliberate choice, and heavy SD I/O
caused capture overruns on 2026-08-05. But:

- **Capacity is a non-issue.** `/dev/mmcblk0p2` is 237.8 GB with **194 GB free**
  (14% used). At the central 7.6 GB/year that is 25 years; at the pessimistic
  11.7 GB/year, 16 years.
- **The database is a small fraction of the card's write load.** Measured from
  `/proc/diskstats` over 120 s on the live station: **16,328 sectors = 8.36 MB
  written to `mmcblk0` in two minutes = 6.0 GB/day** from all causes. The
  database's *net* growth is 20.8 MB/day; its amplified write volume is on the
  order of 0.3–0.7 GB/day by the table in §2. journald alone is already holding
  665.6 MB. I could not isolate the database's exact share — that would need
  per-file instrumentation the station does not have — so treat 0.3–0.7 GB/day as
  modelled, not measured.
- **6 GB/day is ~2.2 TB/year.** A 256 GB card's TBW rating is the number that
  matters here and it is not printed on the station's card, but even a poor 20 TBW
  gives ~9 years and a high-endurance 100+ TBW gives decades.
- The `-wal` file has been steady at **4,288,952 bytes** across half an hour of
  observation — SQLite's default `wal_autocheckpoint` of 1000 pages is holding it
  at ~4 MB and reusing the space in place. Nothing is running away.

For contrast, the **SSD** took **146,616 sectors = 75 MB in the same 120 s
(~54 GB/day)** and holds 43 GB of clips after five days, with **zero** assets
reclaimed so far because nothing has aged past [[ADR-026 - Tiered clip retention|ADR-026]]'s 7-day native tier yet.
Clip storage is two to three orders of magnitude more data than the database and
is where the storage engineering actually is.

---

### Per-record representation: measured, not modelled

Every variant below was materialised from the live station's own 61,453 rows and
measured with dbstat, each with the same three indexes that queries actually use
(`event_start`, `group_start`, `stream_id`):

| variant | table MB | idx MB | total MB | B/row | vs shipped |
|---|---:|---:|---:|---:|---:|
| as shipped (32-char hex UUID text, ISO timestamp text, inline strings, full JSON) | 44.77 | 7.59 | 52.36 | 852.0 | — |
| + UUIDs as `BLOB(16)` | 39.77 | 6.59 | 46.36 | 754.4 | −11.5% |
| + timestamps as integer epoch-µs | 36.73 | 4.36 | 41.10 | 668.7 | −21.5% |
| + species/label strings normalised to a `taxon` lookup | 32.76 | 3.64 | 36.44 | 592.9 | −30.4% |
| + `native_result` stripped of constant/duplicated keys | 21.25 | 3.64 | 24.93 | 405.6 | **−52.4%** |
| + `native_result` dropped for `acoustic_event` rows | 12.46 | 3.64 | 16.13 | 262.5 | **−69.2%** |

Per-column payload in the shipped schema (sum over all rows / 61,453):

| column | avg B | total MB | comment |
|---|---:|---:|---|
| `native_result` | **356.6** | **21.91** | the single largest cost, 53% of row payload |
| 5 × UUID columns | 32.0 each | 9.86 | SQLAlchemy `Uuid` on SQLite persists 32-char hex **text** |
| 3 × timestamp columns | 26.0 each | 4.81 | ISO text `2026-08-04 18:44:08.972733` |
| `score` | 16.5 | 1.01 | IEEE double |
| `detector_label` | 15.8 | 0.97 | repeats heavily |
| `taxonomic_group` | 11.8 | 0.72 | 3 distinct values in 61,453 rows |
| `source_start/end_frame` | 9.7 each | 1.19 | the operator's original worry |
| `common_name`+`scientific_name`+`canonical_taxon_id`+`rank` | 10.9 combined | 0.67 | 95 distinct species |
| `calibrated_probability` | 1.0 | 0.06 | **100% NULL** — 61,453 of 61,453 |

Findings worth acting on, and worth *not* acting on:

- **`native_result` is the prize.** 356.6 B/row average. Roughly half of it is
  constant or already duplicated elsewhere: `detector` (implied by `detector_id`),
  `model_id` (on the `detector` row), `score_definition` /
  `confidence_definition` (constant per detector version), `band_hz` (constant per
  configuration), `hint_is_not_identification` (a constant `true`), `label`
  (duplicated in `detector_label`), `confidence` (duplicated in `score`),
  `peak_frequency_hz` (duplicated in the typed column). Stripping exactly those
  keys takes the average to **178.7 B** and the row to 405.6 B — a **52%**
  reduction in the whole detection footprint, with **no measurement lost**: every
  removed key is either a constant recoverable from the detector version or a
  copy of a column that is already there. This is the highest-value change on the
  list and it is not a schema change at all; it is what `normaliser.py` chooses to
  persist.
- **UUID-as-text costs 16 B/column × 5 columns inline, plus the same again in every
  index that covers one** — measured at 97.6 B/row, 11.5% of the table+index
  footprint. Free on PostgreSQL, where `uuid` is natively 16 bytes. On SQLite it
  needs a `TypeDecorator` and it makes `sqlite3` output unreadable, which on a
  station where the operator debugs by hand is a real cost.
- **Timestamps as ISO text cost 26 B where an integer epoch costs ~7**, measured at
  85.7 B/row across three columns and the two timestamp indexes. Note this is the
  *only* change here that touches the "UTC internally, local for presentation"
  rule, and it honours it more strictly, not less — an integer epoch has no
  timezone to get wrong.
- **String normalisation is worth less than it looks.** 95 distinct species and 3
  taxonomic groups across 61,453 rows sounds like a huge win, but SQLite already
  stores NULL in one byte and the species strings only appear on the 12.5% of rows
  that have a species at all. A full `taxon` lookup table is **97 rows and 32.8 kB**
  and saves 75.8 B/row (8.9%) — real, but it adds a join to `species_summary` and
  `taxa/activity` and an interning cache to the write path, for the third-smallest
  saving on this list. **Not worth doing for its own sake**; worth doing if the
  schema is being rewritten anyway.
- **`calibrated_probability` is 100% NULL** and costs one byte per row (61 kB
  total). It is the contract that says a score is not a probability. **Keep it.**
  `rank`, `canonical_taxon_id`, `common_name`, `scientific_name` are NULL on the
  53,753 unidentified rows and cost one byte each there — already efficient.

---

### The `acoustic_event` roll-up question

48,221 of 61,453 rows (**78.5%**) are `activity-v1` `acoustic_event` rows with no
species. Two facts decide this:

1. **Not one of them has an evidence clip.** `SELECT ... FROM detection d WHERE
   taxonomic_group='acoustic_event' AND EXISTS(...detection_media...)` returns
   **0 of 48,221**. Their frame bounds address audio that no longer exists once the
   capture ring wraps, so the "exactly reproducible evidence" property those
   columns exist for is, for these rows specifically, already unrealisable.
2. **They arrive at 18.4 rows per active minute** (48,221 rows across 2,616
   distinct minutes, 67 distinct hours). A per-minute roll-up would compress them
   **18.4×**: ~2,616 rows instead of 48,221 for this sample, ~1,440 rows/day
   instead of ~11,000.

What a per-minute roll-up would preserve: that the detector was firing, how often,
how loud (min/max/mean SNR), the peak-frequency distribution (there are only 209
distinct `peak_frequency_hz` values and 101 distinct 2-dp scores in the whole
population — these are already quantised), and therefore everything the
synthetic-source incident actually needed, which was *"activity-v1 was producing
detections at a normal rate against a scene that was not real."*

What would be lost, and it is not nothing: the exact second of an individual
event; the ability to say "there was a sound at 03:41:07.2 lasting 981 ms with a
peak at 1218.8 Hz"; and the ability to retrospectively re-attribute an individual
acoustic event to a species if a future detector is run over archived audio.
Given that no archived audio for these rows exists, the last one is theoretical.

Combined with dropping `native_result` on these rows, a roll-up would take the
database from 20.8 MB/day to roughly **5 MB/day** — a 4× reduction. It is the
largest single lever available. It is also the only one on this list that
**destroys information**, and it needs the operator's judgement, not an
engineer's.

---

### Evidence-clip encoding (in scope by the operator's request; ADR-026 territory)

Measured on two real clips pulled from the station, encoded with the Pi's own
`ffmpeg` build locally:

| clip | source | FLAC -0 | FLAC -5 | FLAC -8 | lossless round-trip |
|---|---:|---:|---:|---:|---|
| native 384 kHz mono s16, 6.01 s | 4,615,852 B | 0.557 | **0.531** | 0.530 | byte-identical PCM |
| 48 kHz playback rendering, 6.00 s | 576,356 B | 0.660 | **0.560** | 0.560 | byte-identical PCM |

Clips are **43 GB after five days (~8.6 GB/day)**, three orders of magnitude more
than the database. FLAC at `-5` would take that to **~4.6 GB/day** and is provably
lossless (verified: the decoded PCM hashes identically to the source), which is
the only acceptable property for evidence.

Why this is **not** recommended now, despite being by far the largest byte saving
available anywhere in the system:

- Encoding ~10 hours of clip audio a day is real CPU on a machine whose first rule
  is that capture always wins. It would land on `_evidence_executor` — correctly,
  *not* the default pool the ALSA read shares ([[ADR-021 - Clips on their own device|ADR-021]]) — but that executor is
  already the busiest non-capture thread, and 2026-08-05 is on record for what
  happens when evidence I/O contends with capture. **I did not measure FLAC encode
  time on the Pi 5 itself** (that would have meant putting load on a live station),
  only on a desktop, so the CPU figure is unquantified and must be measured before
  anyone acts on this.
- The SSD is **10% full (43 GB of 458 GB)** and [[ADR-026 - Tiered clip retention|ADR-026]]'s tiers have not fired once
  yet — nothing is 7 days old. The compression would be solving a problem the
  tiering has not been given a chance to solve.
- It changes `media_asset.mime_type`, the `/api/v1/media/{id}` response, the web
  UI's audio element and the checksum semantics. That is a feature, not a tune-up.

**Revisit when the SSD passes 250 GB or the retention watermark starts reclaiming
clips younger than 30 days** — at that point 47% off every native clip is worth
the CPU, and a Pi-side encode benchmark is the first step.

---

### Options, honestly

| # | Option | Saving | Cost / risk | Verdict |
|---|---|---|---|---|
| A | **Do nothing** | 0 | Database reaches ~23 GB in 3 years on a card with 194 GB free; every real query stays flat; ~12.5 MB of dead index grows forever and triples insert CPU | Defensible. Nothing here is on fire. |
| B | **Drop the 5 unused detection indexes** | 12.49 MB now (13.3%), ~1.0 GB/yr, 3.3× cheaper inserts, −24% WAL writes | One Alembic revision (`0003`); reversible in one `CREATE INDEX`; needs a deploy. Risk: a *future* multi-station or per-species query would want two of them back | **Recommended** |
| C | **Stop persisting constant/duplicated `native_result` keys** | 52% of the detection footprint, ~3.5 GB/yr | `normaliser.py` change; no schema change; old rows keep their verbose JSON so nothing historical is rewritten. Needs care: it is an audit record and a successor must be able to reconstruct what was dropped from the detector version | **Recommended next**, after B, as a separate reviewed change |
| D | **Roll up `acoustic_event` rows per minute** | 4× overall (20.8 → ~5 MB/day) | Destroys per-event detail permanently. New table, new write path, new query path, and the history UI has to learn about two row shapes | Operator's call. Defer until C is done and re-measured. |
| E | **Re-encode UUIDs/timestamps** (`BLOB(16)`, integer epoch) | 21.5% | Breaking schema change, rewrite of every row, `TypeDecorator`, unreadable in `sqlite3` | **Do it at the PostgreSQL move or not at all.** Free there, expensive later. |
| F | **Time-ordered primary key (UUIDv7)** | halves insert write amplification (13,162 → 6,852 WAL B/row) | Breaking key change; UUIDs stay UUIDs so the contract is unchanged | **Do it at the PostgreSQL move.** Cheapest big win available *at that moment*, unavailable cheaply after. |
| G | **`taxon` lookup table** | 8.9% | Join on two hot query paths, interning cache on the write path | Only if E is happening anyway. |
| H | **Move the database to the SSD** | 0 bytes; removes DB writes from the SD card | Contradicts [[ADR-021 - Clips on their own device\|ADR-021]]'s explicit reasoning: the station keeps capturing, detecting and serving history with the SSD unplugged *because* the database is on the always-present system disk. Buys ~0.3–0.7 GB/day of SD writes against 6 GB/day total | **No.** [[ADR-021 - Clips on their own device\|ADR-021]] already decided this and the numbers do not overturn it. |
| I | **PostgreSQL** ([[ADR-007 - SQLite in developer mode\|ADR-007]]'s DSN swap, now that [[ADR-035 - Alembic environment\|ADR-035]] exists) | 0 bytes by itself | The migration environment exists but has never been run against a real PostgreSQL 16; adds a server to a machine that has none | Not for size. Do it when concurrent writers or `LISTEN/NOTIFY` are needed, and take E+F+G with it. |
| J | **PostgreSQL + TimescaleDB compression** | plausibly 5–10× on a columnar-compressed hypertable | Everything in I, plus a feature flag the spec allows but nothing has exercised, plus compressed chunks are effectively read-mostly | Premature. Revisit only if D is rejected *and* the trigger below fires. |
| K | **FLAC for evidence clips** | ~47% of 8.6 GB/day — the biggest saving in the system | CPU on the evidence executor, unmeasured on the Pi; touches [[ADR-026 - Tiered clip retention\|ADR-026]] | Defer; see trigger above. |

---

### Recommendation

**Take B now. Take C next, as its own reviewed change. Leave everything else
alone, and bank E, F and G for the PostgreSQL move.**

B is the only change that is pure profit: it removes 13.3% of the database, makes
every detection insert 3.3× cheaper, cuts SD-card write volume by a quarter, and
cannot make any query slower, because `EXPLAIN QUERY PLAN` shows no query uses
the indexes being removed. It is one Alembic revision, reversible in five
`CREATE INDEX` statements.

C is larger in bytes and slightly larger in judgement, which is why it should not
ride along with B.

Everything else is either premature (the database will not be uncomfortable for
years, and query latency is flat), destructive (D), or much cheaper if bundled
with a migration that is going to happen anyway (E, F, G).

**Scheduling:** B requires a deploy and the operator has a soak planned for the
coming working week. Land it **before** the soak starts or **after** it finishes,
never during — a restart resets every counter the soak is measuring. The soak is
also the ideal place to confirm the insert-cost improvement on real hardware.

**What would change this recommendation:**

- If a query that scans the *whole* detection history is added to the UI or API —
  a lifetime species list, an all-time chart — then flat latency stops being true
  (2.3 s at 3 years on a fast desktop, so ~12 s on the Pi) and the answer becomes
  D or J rather than B.
- If `activity-v1`'s rate rises materially (a noisier site, a lowered threshold),
  D moves from "operator's call" to "necessary".
- If the SD card turns out to be a low-endurance consumer part, the write-
  amplification numbers stop being academic and F becomes urgent rather than
  opportunistic.
- If PostgreSQL is adopted for an unrelated reason, E+F+G should be taken in the
  same migration and this ADR's cost/benefit is rewritten.

---

### Trigger — revisit this ADR when any of these is true

| Metric | Threshold | Why that number |
|---|---|---|
| `openobservatory.sqlite` file size | **> 10 GB** | ~1.3 years at the central rate; still 5% of the card, but the point where a 3-year projection starts to matter |
| SD card free space | **< 50 GB** | 21% remaining; well before anything is at risk, and the database will not be the cause |
| p95 of `GET /api/v1/history` on the Pi | **> 1 s** | 8× today's measured 128 ms; would mean the flat-latency finding has stopped holding |
| Sustained detections/day, 7-day mean | **> 40,000** | ~2.5× the observed maximum; halves every time-to-threshold above |
| SSD usage | **> 250 GB** | The FLAC decision (K), which is a bigger lever than anything in the database |

None of these is close today. The file is 94 MB, the card has 194 GB free,
`/history` answers in 128 ms, the busiest measured day was 16,264 detections, and
the SSD is at 43 GB.

### What was not verified

- FLAC encode cost **on the Pi 5** — measured only on a desktop, so option K's CPU
  figure is unquantified.
- The database's exact share of the card's 6.0 GB/day of writes; only the total was
  measured, the split is modelled from the WAL benchmark.
- The SD card's TBW rating, which is what turns write-amplification numbers into a
  lifetime.
- Whether recent main-branch changes (`68f4234`, the acoustic-event filter fix) have
  reached the running station, which would lower the `activity-v1` rate the
  projections use.
- Nothing here has been run on PostgreSQL; [[ADR-007 - SQLite in developer mode|ADR-007]]'s "the DSN swap is
  configuration-only" is still unverified beyond SQLite, exactly as [[ADR-035 - Alembic environment|ADR-035]] says.

---

### B and C: what was implemented (2026-08-09)

**Options A and D–K above are unchanged and still open** — this section
covers only B and C, which the operator accepted. Both were re-verified
against `main` as it stood on 2026-08-09 (which had moved since the research
above was written — in particular, [[ADR-032 - Plausibility bands|ADR-032]]'s `plausibility_repair.py` did not
exist yet when the original indexes-nothing-reads grep was run) and against a
fresh read-only copy of the live database (63,234 detections, up from 61,453),
not merely re-trusted from the research above.

#### B: four indexes dropped, one kept after re-verification found it in use

Re-running the two-part check (`EXPLAIN QUERY PLAN` over every query in
`history.py`, `api/app.py`, `plausibility_repair.py`, `retention.py`,
`station.py`, `mqtt/publisher.py`, `mqtt/discovery.py`, plus a grep for any
filter/order-by touching the five candidate columns) turned up exactly the
case this ADR asked a successor to watch for:
`plausibility_repair.find_implausible_detections` ([[ADR-032 - Plausibility bands|ADR-032]], landed on `main` after
this ADR's research) joins **from** `detector` (filtered by
`Detector.plugin_id == BIRDNET_PLUGIN_ID`) **into** `detection`, and
`EXPLAIN QUERY PLAN` against the live database shows SQLite satisfies that
join with `SEARCH detection USING INDEX ix_detection_detector_id` — not the
`detector` table's own primary key, and not a full scan of `detection`. That
is the reverse join direction from the one the original research checked (it
looked for filters *on* `Detection.detector_id`, which indeed does not exist
anywhere; it did not anticipate a filter on `Detector.plugin_id` driving the
join the other way). **`ix_detection_detector_id` is kept.**

The other four were reconfirmed dead by the same method and dropped:

| Index | Live DB size | Re-verified dead because |
|---|---:|---|
| `ix_detection_station_start` | 4.71 MB | No query filters or orders by `station_id`; one distinct value in 63,234 rows |
| `ix_detection_station_id` | 2.85 MB | Same column, same finding, duplicates the composite above |
| `ix_detection_taxonomic_group` | 1.44 MB | Strict prefix of `ix_detection_group_start`; `EXPLAIN QUERY PLAN` on a bare `taxonomic_group = ?` filter still chooses `ix_detection_group_start` |
| `ix_detection_canonical_taxon_id` | 0.76 MB | Only ever appears in a `SELECT` list (`retention.py`'s exemplar scan reads it into Python after the query runs), never in `WHERE` or `ORDER BY` |

**9.76 MB dropped, not the originally estimated 12.49 MB** — the difference
is entirely `ix_detection_detector_id` (2.73 MB), kept because it is in
active use. This is a smaller number than the research predicted, for exactly
the reason the research itself flagged as the risk to watch.

Implemented as Alembic revision `0004_drop_dead_detection_indexes`
(`alembic/versions/20260809_0003_000000000004_drop_dead_detection_indexes.py`
— [[ADR-035 - Alembic environment|ADR-035]]'s baseline used `0001`–`0003`, so `0004` is the next free number;
the `0003` this ADR originally said to use had already been taken by the
authentication tables). Plain `DROP INDEX` / `CREATE INDEX`, following
revision `0002`'s precedent — no batch mode needed for a bare index change on
SQLite, and both statements are standard SQL on PostgreSQL 16 too ([[ADR-007 - SQLite in developer mode|ADR-007]]).
`alembic check` reports no drift after upgrading to head. `downgrade -1`
recreates all four indexes in one command;
`tests/test_migrations.py::test_0004_drops_and_restores_the_four_dead_detection_indexes`
asserts both directions, and the existing
`test_initial_revision_matches_create_all` / `test_upgrade_head_from_empty_matches_create_all_tables`
tests independently confirm no drift between `create_all()` and `alembic
upgrade head`. `db/models.py`'s `Detection.station_id`, `.canonical_taxon_id`
and `.taxonomic_group` columns had their `index=True` removed, and
`ix_detection_station_start` was removed from `__table_args__`, to keep
`create_all()` and Alembic in agreement. `detector_id` keeps `index=True`,
annotated in the model with why.

**Measured insert/WAL cost was not re-benchmarked on real hardware this
session** — ADR-037's original 44.7→13.4 µs/row and 17,355→13,162 WAL B/row
figures were measured with all five indexes dropped, not four; dropping four
instead of five should land close to but not exactly on that number, and the
actual figure needs a fresh benchmark (ideally during the deploy, per the
scheduling note below, since the soak is "the ideal place to confirm the
insert-cost improvement on real hardware" the original research already
named).

#### C: `native_result` stripped of provably redundant keys, not the full ADR-predicted set

`normaliser.py` now calls `_strip_redundant_native_result` before persisting
`native_result` (wired into `Normaliser.normalise`). It drops a key only when
its **value**, not merely its name, is proven to duplicate something already
persisted elsewhere on the same row:

| Key | Duplicates | Verified on live data (63,234 rows) |
|---|---|---|
| `detector` | `Detector.plugin_id`, reachable via `detection.detector_id` | 0 mismatches across all 3 plugins |
| `model_id` | `Detector.model_id`, same join | 0 mismatches, 7,976/7,976 birdnet-v2.4 rows that carry the key |
| `label` | `Detection.detector_label` | 0 mismatches, 7,976/7,976 birdnet-v2.4 rows |
| `confidence` | `Detection.score` | matches to float32 rounding (≤ ~5e-7) on 7,976/7,976 birdnet-v2.4 rows; compared with `math.isclose(rel_tol=1e-4, abs_tol=1e-6)` |
| `peak_frequency_hz` | `Detection.peak_frequency_hz` | matches to the native result's own 1 dp rounding (≤ 0.05 Hz observed) on 49,726/49,726 activity-v1 rows; compared with `math.isclose(rel_tol=1e-3, abs_tol=0.1)` |

**`occurrence_probability` and `plausibility_band` are never touched** — they
are [[ADR-032 - Plausibility bands|ADR-032]]'s audit trail for why a candidate was or was not admitted, not a
duplicate of anything, exactly as this work was instructed to preserve.

**This is a materially smaller set than the original research proposed, and
the reason is a real finding, not caution for its own sake.** The research
above listed `model_id`, `score_definition`, `confidence_definition`,
`band_hz` and `hint_is_not_identification` together as "constant per detector
version" and safe to drop. Re-querying the live database found that is true
for `model_id` (persisted on the `Detector` row, so it is safe regardless)
but **false for `score_definition`**: `activity-v1`'s `native_result` carries
two distinct `score_definition` strings —
`"clamp((snr_db - min_snr_db) / 25 dB, 0, 1)"` on 5,010 rows from
2026-08-04T18:44–19:26, and `"clamp((snr_db - min_snr_db) / 30 dB, 0, 1)"` on
44,716 rows from 2026-08-04T19:26 onward — **under the exact same
`detector_id`, with no `plugin_version` or `model_version` bump between
them.** `config.py` confirms why: `activity_band_hz` and
`ultrasonic_band_hz`, and by extension the `min_snr_db`-derived formula text,
are operator-configurable settings, not baked into the versioned model
identity, and `Detector.configuration` (which records only `stream_kind`,
`sample_rate`, `duration_s`, `stride_s`) does not capture them. So "the
detector version recorded on the row" — this work's own recoverability
requirement — **does not reliably reconstruct `score_definition`,
`confidence_definition`, `band_hz` or `hint_is_not_identification`** on this
station's real history: a successor reading only current source code for
`activity-v1`'s current `plugin_version` would recover the *current* formula
and silently misattribute it to rows written under the old one. Those four
keys are therefore **kept**, unconditionally, for every plugin — not just
where a second value was actually observed — because the failure mode (a
config change with no version bump) is structural, not specific to
`activity-v1`, and nothing rules it out for `birdnet-v2.4` or
`ultrasonic-pass-v1` in the future.

**Measured saving, real data, JSON-text bytes (`len(json.dumps(...,
separators=(",", ":")))`), before vs. after, weighted by the live row
counts per plugin:**

| Plugin | Rows | Before (avg B/row) | After (avg B/row) | Reduction |
|---|---:|---:|---:|---:|
| `activity-v1` | 49,726 | 314.1 | 262.1 | 16.6% |
| `birdnet-v2.4` | 7,976 | 354.4 | 226.7 | 36.0% |
| `ultrasonic-pass-v1` | 5,532 | 513.9 | 481.9 | 6.2% |
| **All detections** | **63,234** | **336.6** | **276.8** | **17.8%** |

That is **17.8%, not the originally estimated 52%.** The gap is exactly the
keys withheld above: `score_definition`/`confidence_definition`/`band_hz`/
`hint_is_not_identification` were the largest byte contributors in the
original 356.6 B/row estimate, and none of them turned out to be safely
droppable on real data. `label_index`, `logit`, `week`,
`plausibility_band`, `threshold_applied`, `range_model_used`, `snr_db`,
`snr_statistic`, `spectral_centroid_hz`, `duration_ms`,
`noise_floor_db_median`, `pulse_count`, `median_peak_hz`, and the rest of
`ultrasonic-pass-v1`'s per-pulse measurements are not duplicates of anything
and were never candidates for removal. Applied to the whole database, 17.8%
of 21.9 MB (the measured `native_result` total from the original research,
now larger at 63,234 rows) is on the order of **3.9 MB today, growing at
roughly 17.8% of whatever `native_result` would otherwise cost per year** —
a real saving, materially smaller than "roughly 3.5 GB/yr" and worth stating
plainly rather than letting the original estimate stand uncorrected.

**Historical rows are untouched, as required.** `_strip_redundant_native_result`
only runs inside `Normaliser.normalise`, on the path that builds a new row;
nothing rewrites `native_result` on any of the 63,234 rows already in any
database. A successor reading an old row still sees every key it was written
with.

**Recovering a dropped key.** For `detector` and `model_id`: join
`detection.detector_id` to `detector.id` and read `plugin_id` /
`model_id` directly — this is exact, for every row, forever, because those
are persisted database columns, not code. For `label`: read
`detection.detector_label`. For `confidence`: read `detection.score` (exact
value; `native_result`'s copy was only ever a rounded display value in the
first place, so nothing is lost by using the typed column instead). For
`peak_frequency_hz`: read `detection.peak_frequency_hz` (same — the typed
column has strictly *more* precision than the dropped copy did).

Tests: `tests/test_pipeline.py::TestNormaliser` —
`test_native_result_drops_keys_that_duplicate_persisted_columns` (positive
case, plus asserting `occurrence_probability`/`plausibility_band`/an
unrelated key like `week` survive untouched),
`test_native_result_keeps_a_key_whose_value_does_not_actually_match` (a
same-named key with a different value is not a false-positive duplicate),
`test_native_result_peak_frequency_hz_duplicate_is_dropped_within_rounding`
(the rounding-tolerance case measured on real data), and
`test_native_result_keeps_configurable_and_formula_fields` (`band_hz` and
`score_definition` are never stripped — the regression test for the finding
above).

#### What is still required before this reaches the station

**Not deployed.** Per this session's rule, the Pi is owned by a concurrent
agent building a push channel and must not be restarted. Both changes are
committed on this branch only. **A deploy is required**, and per this ADR's
own scheduling note: land it **before or after** the operator's planned soak,
never during — a restart resets every counter the soak measures. The soak
remains the right moment to take the real insert/WAL measurement B's
implementation did not get to.

The full test suite (389 baseline + 5 new = 394 passed, 6 skipped),
`ruff check .` (clean) and `mypy src` (29 pre-existing errors, none added)
were run locally against a Python 3.12 venv; none of this was exercised
against PostgreSQL or on the Pi 5 itself.

**Reviewed 2026-08-29:** B and C reached the station, and three of the five
triggers in the table above have since fired.

- **Deployed.** The "Not deployed" paragraph above is closed.
  `deploy/deploy.sh:92` runs `alembic upgrade head` as an explicit pre-restart
  step ([[ADR-042 - Migrations run in deploy.sh|ADR-042]]) and the revision chain is linear from `0004` to
  `0011_retention_live_asset_indexes`, so a station running [[ADR-061 - Operator keep flag|ADR-061]]'s keep
  flag has run this one. C is visible in the station's own output: a
  `birdnet-v2.4` row fetched from `GET /api/v1/detections/{id}` today carries
  `confidence_definition`, `occurrence_probability`, `plausibility_band`,
  `week`, `logit`, `label_index`, `threshold_applied` and `range_model_used`,
  and no `detector`, `model_id`, `label` or `confidence` — exactly the set this
  section says is dropped and the set it says is kept. **The real insert/WAL
  measurement B never got is still not taken**, on the station or anywhere else.
- **Trigger: detections/day.** The 7-day mean for 2026-08-22 to 2026-08-28,
  summed from `GET /api/v1/history` on the station at 86,400 s buckets, is
  **40,706/day** (284,942 detections over a week that was 99.97% captured),
  past the >40,000 line above. The projections in §2 assumed 14,000/day central
  and 21,500/day high; the station holds roughly 897,000 detections after
  26 days. Every time-to-threshold in §2 and §3 is therefore about a third of
  what it says.
- **Trigger: SSD past 250 GB.** Fired. The figure and what to do about it are
  [[ADR-074 - Evidence kept by value|ADR-074]]'s, which answered it with value-based retention rather than
  option K — no FLAC exists anywhere in the tree, so K is still open and its
  Pi-side encode benchmark is still unrun.
- **Trigger: a query that scans the whole detection history.** Fired, and
  [[ADR-056 - Long-window history|ADR-056]] recorded it first. Two exist: `GET /api/v1/history` takes an
  uncapped `since`, and `window=this-year` measured **12.6 s** on the station
  today — the ~12 s this ADR predicted for three years, arriving in one month;
  and `GET /api/v1/taxa/search` (`review.search_taxa`,
  `src/open_observatory/review.py:166`, [[ADR-043 - Taxon correction|ADR-043]]) groups the whole detection
  table with no time predicate at all, 0.62 s today against a ~10 ms baseline
  and linear in history. What is holding this down is the web UI offering
  nothing longer than `last-7d` (`web/src/components/History.tsx:97`), not the
  schema. By this ADR's own terms the answer for the *next* increment is D or
  J, not another index.
- **One dropped index now has a reader.** `review.resolve_taxon`
  (`src/open_observatory/review.py:159`, [[ADR-043 - Taxon correction|ADR-043]]) is
  `WHERE canonical_taxon_id = ? LIMIT 1` — the precise `WHERE` shape whose
  absence justified dropping `ix_detection_canonical_taxon_id`. Unindexed it
  scans `detection`, and for a taxon id the station has never produced it scans
  all of it. This is the per-species risk B was told to watch for, arriving;
  `downgrade -1` still recreates the index.
- **Option D's premise no longer holds.** "Not one of them has an evidence
  clip" was true of 48,221 `acoustic_event` rows in August. Today 11 of the 500
  most recent `acoustic_event` rows carry `playback` and `evidence_native`
  clips, and [[ADR-074 - Evidence kept by value|ADR-074]] measures the class at 13.2 GB of evidence. A
  per-minute roll-up now has evidence to re-home, which it did not before.
- **Not checkable from here.** The database file size (>10 GB) and SD free
  space (<50 GB) triggers: no endpoint reports either, and this review was
  HTTP-read-only. Whether `ix_detection_detector_id` is *still* the index
  SQLite chooses for that join is likewise unverified — the query is unchanged
  since 2026-08-09, but the table has grown fourteen-fold under it.

---
Part of the [[ADRS|Architecture Decision Record index]].
