---
aliases:
  - ADR-079
tags:
  - adr
---
# ADR-079: An ANALYTICS section answers "what is the pattern", from a roll-up of local days, beside HISTORY's "what happened"
**Status:** accepted, 2026-09-14. Builds the roll-up [[ADR-056 - Long-window history|ADR-056]] designed and measured but did not build, and supersedes that ADR's rejected alternative **K** (*a second page, separate from HISTORY*). Everything else in ADR-056 stands: the range grammar, the honesty rules, the cost measurements and the reason a trigger was rejected.
**Relates to:** [[ADR-044 - Withdrawn detections|ADR-044]] and [[ADR-043 - Taxon correction|ADR-043]] (what a named count may include), [[ADR-005 - No biodiversity score|ADR-005]] (what it may not become), [[ADR-020 - Non-live sources excluded|ADR-020]] (only the microphone counts), [[ADR-054 - Responsive layout|ADR-054]] and [[ADR-027 - Spacing and type scale|ADR-027]] (how it is drawn), [[ADR-078 - Database on the evidence SSD|ADR-078]] (where its tables live).

### The request, and what it is really for

The operator asked, on 2026-09-14, for a section distinct from LIVE and HISTORY in which the record can be sliced over months: species volumes through the year, activity by time of day, whether bird hours compress as the days shorten, whether bat passes thin as the nights lengthen, which week holds the most robins, when a green woodpecker is likeliest. And then the point that decides the design: *the primary objective is not those reports. It is that such questions exist as bookmarks that act as demonstrators that the machine is working properly.*

That reframes an analytics page as an instrument check. A chart of bat passes that does not sit between the dusk and dawn lines is not a fact about bats; it is a detector, a clock or a coordinate that is wrong. So every shipped question carries a sentence saying what a working station shows, and the section's job is to make the disagreement visible.

### Why a separate section, against ADR-056's alternative K

ADR-056 rejected a second page because *"the shape should follow the question, not the URL"* and *"one view that changes shape with span keeps a single answer to 'what happened'."* Both are right about HISTORY and neither applies here, because this is a different question with a different source:

| | HISTORY | ANALYTICS |
|---|---|---|
| Question | what happened | what is the pattern |
| Unit | a detection, with its evidence | a count of detections, with its coverage |
| Reads | the detection table, live | the roll-up tables, rebuilt hourly |
| Shows | timeline, species list, clips, the drawer | series, hour matrix, phenology grid, span against the light |
| Longest window offered | 7 days (measured: 3.85 s on the Pi) | everything recorded (milliseconds) |
| Can it show a detection? | yes, every one | never |

There is one fact both can state -- how many detections of X on day D -- and a test pins that they agree (`tests/test_analytics.py::test_the_detection_table_always_wins`, ADR-056's first honesty rule). Nothing else overlaps, so K's "two places to look for the same fact" does not arise. ADR-056's proposed period and season shapes *inside* HISTORY are therefore not built; the season view is the phenology grid here, and HISTORY stays at seven days.

### Decision 1: the roll-up, as ADR-056 designed it, with three additions

Six tables under `analytics_*`, one builder, no trigger. ADR-056's arithmetic was 16 µs per detection row on the station, 40,706 detections a day in late August, a per-insert trigger at +4,065 WAL bytes per detection, and a daily batch at about 20 kB a day. The builder is `oo analytics rebuild`, run by `open-observatory-analytics.timer` every hour under the same fence as the refinement runner (cores 2–3, `Nice=19`, idle I/O, `ProtectHome=read-only`). Per run: today, yesterday, any day a new review touched, up to forty never-built days newest first, and up to six days built more than a week ago -- so a withdrawal or refinement reaches the roll-up within a week with nobody tracking which rows changed. `BUILDER_VERSION` is stamped on every day; a bump rebuilds them all.

The three additions to ADR-056's `detection_bucket` and `species_day`:

1. **Local calendar days, always.** ADR-056 finding 4 measured 15.15% of this station's bats filed on the wrong UTC date. Every roll-up row keys on a `YYYY-MM-DD` in the station's timezone, hours are local wall-clock hours (23 on the day the clocks go forward, 25 on the day they go back, both tested), and a *night* is noon to noon keyed by the evening's date. The timezone is stored with the day; a change to it makes the day stale.
2. **The light.** `schedule.py` gains sunrise and sunset beside civil twilight (the same NOAA arithmetic at zenith 90.833°, no new dependency). Each day stores its four solar moments and the captured seconds inside daylight, from dusk to midnight, and from midnight to dawn; each group stores its detections inside those windows and its first, last, 5th- and 95th-percentile moments. That is what lets "does bird activity compress with daylight" be drawn as bars against curves, and "passes per hour of night" divide by the hours the microphone was on during that night rather than by the clock.
3. **Coverage travels with every count.** Captured and paused seconds per local hour, so any chart can show what the microphone was doing under any cell, and a low bar over a dark strip reads as *deaf*, never as *quiet* (charter item 2).

### Decision 2: what a named count may contain

The one roll-up table that names things, `analytics_taxon_hour`, follows the surface rules already in force and adds the one the queue owed:

- withdrawn rows out, counted on the day row (ADR-044);
- synthetic and replay rows out everywhere, counted (ADR-020);
- **a human correction is the name, and a human rejection is an exclusion** -- the first surface to honour a review at all ([[HANDOVER]] queue item 12; HISTORY and `/taxa/activity` still do not, and say so);
- a bat pass is its 5 kHz band (`45–50 kHz`), never a species (ADR-013).

The group tables count detections and name nothing, so a withdrawn detection -- which did occur -- stays in them, exactly as it stays in `history.timeline`. Every response carries `excluded_withdrawn_count`, `excluded_rejected_count`, `excluded_synthetic_count` and `days_built` against `range.days`, and the UI prints them under every chart. A day never built is a hatched hole, not a zero.

Not decided, deliberately: no composite index, score or "biodiversity" figure (ADR-005). The span chart plots rate against day length as points and draws no fitted line: a person can see a slope, and a coefficient would claim more than a garden's worth of points supports.

### Decision 3: the surface

A third button in the mode switch, `ANALYTICS`, with its own URL state (`?section=analytics&q=<question>` or `&chart=…&range=…&group=…&label=…`), so **the bookmark is the URL**: a question can be sent to someone, refreshed, or kept in the browser. `useHistoryBrowser`'s mode was never in the URL and is not changed.

Four views, each a pure-maths module plus an SVG component with HTML text around it, in the idiom `History.tsx` set (stretched `viewBox`, `preserveAspectRatio="none"`, text never inside the SVG):

| View | Answers | Chart |
|---|---|---|
| `series` | how much, over time | stacked bars per day/night/week/month, coverage strip beneath, per-captured-hour toggle |
| `hours` | what time of day | date × hour heat-map with sunrise/sunset and civil dawn/dusk drawn over it, plus the 24-hour profile |
| `taxa` | who, when | label × week/month grid, each cell a count and a shade, click to open that label's series |
| `span` | how it follows the light | per day, a bar from the 5th to the 95th percentile detection against the solar curves; then rate per captured hour against day (birds) or night (bats) length |

Colours are the History timeline's group colours, unchanged, because a bird must be the same green on every screen. The dataviz validator was run against them on the dark surface: the noise grey fails its chroma floor on purpose (noise is not wildlife) and the two blues sit close, so every stacked chart carries a legend, 1.5-unit gaps between segments and a hover tooltip naming the group -- the secondary encoding that rule permits. Heat-map and grid ramps are one hue each, from the panel surface to the group colour, square-rooted so a dawn hour holding a quarter of the day does not black out the rest.

Eight shipped questions live in `analytics.QUESTIONS` and are served by `GET /api/v1/analytics/questions`, so the UI and the documentation read one list. Saved questions are rows on the station (`analytics_report`), for the reason ADR-048 gave for `setup_completed`: a garden station is opened from a phone and a laptop, and "the question I asked last month" belongs to the station.

### What this ADR does not do

- It does not touch HISTORY, `/api/v1/history`, or `/taxa/activity`; queue item 12 is closed for analytics only.
- It does not run anything in the capture process. The builder is a separate unit; the API reads only the roll-up.
- It does not expose refinement proposals (ADR-045): the builder reads `review`, never `refinement`.
- It does not add a metric. `GET /api/v1/analytics/status` reports `age_seconds` and `days_missing`; the rail prints "stale" past three hours and the count of missing days.
- It does not offer "custom" date pickers. `range` accepts the whole ADR-056 grammar (`2026-08`, `2026-W32`, `last-30d`, …) plus `all`; the UI offers five chips.

### Consequences

- Six tables and an hourly writer on the evidence SSD (ADR-078). Growth is a few hundred rows a day; a year is single-digit megabytes.
- A second process writing the database under a five-second `busy_timeout`, in transactions of one day each: the same shape as the refinement runner, which has run nightly since 2026-08-10.
- A fresh install answers "everything recorded" with holes for about an hour per forty days of history until the catch-up completes; the status line says how many days are missing.

### Rollback

Disable the timer (`systemctl disable --now open-observatory-analytics.timer`) and redeploy the previous web bundle; the tables are inert and can be dropped with `alembic downgrade 0012_detection_banked_at`. Nothing else reads them.

### Smoke test

    oo analytics rebuild --limit 3
    oo analytics status --json | jq '{days_built, days_missing, age_seconds}'
    curl -s 'http://127.0.0.1:8080/api/v1/analytics/hours?range=last-30d&group=bat' | jq '.columns[-1].solar, .profile'
    curl -s 'http://127.0.0.1:8080/api/v1/analytics/questions' | jq '.questions[].id'

Then open `?section=analytics&q=bat-hours` and check the colour lies between the dashed twilight lines.

### Revisit when

| Trigger | Action |
|---|---|
| A build of one day exceeds ~10 s on the station | Cut `CATCHUP_DAYS_PER_RUN`, or add a per-day `(local_date)` covering index on `detection` -- measure first |
| HISTORY grows a period or season shape | Read the roll-up there too, through the same functions, or say why not |
| The PostgreSQL move (ADR-007) | Continuous aggregates may replace the batch (ADR-056 alternative I) |
| Environmental sensors arrive (Milestone 9) | `analytics_day` is where a day's temperature and rain belong |

---
Part of the [[ADRS|Architecture Decision Record index]].
