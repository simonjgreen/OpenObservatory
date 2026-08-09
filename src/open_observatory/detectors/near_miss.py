"""What the detector proposed and then threw away (ADR-052).

ADR-032 gave BirdNET four suppression counters. They answer "how many" and
nothing else. On the live station, 2026-08-09, an operator could see
``oo_birdnet_suppressed_total{reason="suppressed_implausible_prior"} = 152``
and had no way to learn whether those were 152 correct rejections of North
American owls or 152 wrongly-binned garden birds. A counter that tells you a
number and not the truth behind it is this project's recurring failure mode
(CHARTER.md, "Failures this ordering would have caught earlier"), and it is
the exact thing that makes threshold tuning impossible: you cannot choose a
bar without seeing the distribution it is cutting.

Worse, the four counters do not even cover the case an operator hits first.
They count only *plausibility* bands. A candidate in the ``in_range`` or
``unfiltered`` band that scores 0.54 against a 0.55 bar is counted nowhere at
all -- and that is precisely "I can hear a blackbird and the station reports
nothing".

This module is the record of the rejections, in three shapes, cheapest first:

* **Per-band histograms and counts** -- twenty 0.05-wide bins of rejected
  score per band, plus how many were admitted. A few integer increments per
  candidate. This is the summary that actually decides a threshold: "you
  rejected 400 candidates, 380 of them below 0.2 and 20 between 0.45 and
  0.55" tells you immediately whether moving the bar buys you anything.
* **A per-species tally** -- count, best rejected score, last seen, band and
  prior, keyed by label index. Bounded by construction: at most
  ``max_species`` entries, and never more than the label catalogue holds. This
  is the table a person tunes from, because it names the bird.
* **A bounded ring of individual near misses** -- the most recent
  ``capacity`` rejections with their timestamps, for reading the last few
  minutes directly.

**Nothing here is audio, and nothing here is persisted.** These are facts
about what the *model* proposed, not new evidence about the garden: no clip is
extracted, no row is written, no file is touched. The whole structure lives in
memory and dies with the process, which is deliberate -- it is a diagnostic
for a tuning session, not a second detection log. ADR-049's privacy decision
is untouched by it: a ``Human vocal`` near miss records the same thing
ADR-049 already allows a *detection row* to record ("somebody was talking"),
carries no speech, and cannot be exported to a file.

**Thread safety.** One writer (the detector's analysis call) and any number of
readers (the API's event loop). Every mutation is a single bytecode-level
operation on a built-in container -- ``deque.append`` with ``maxlen``, an
``int`` rebind on a ``__slots__`` attribute, a list-item increment -- so no
lock is taken on the hot path. A reader can observe a snapshot mid-window and
see a histogram one candidate ahead of a species tally. That is acceptable for
a diagnostic and is stated here rather than papered over with a lock the
capture-adjacent path would have to pay for.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, NamedTuple

#: Every band ``band_for`` can return, in the order an operator reads them:
#: easiest bar first, "no bar exists" last. Fixed rather than discovered so a
#: band that has seen nothing still appears in the payload as an explicit zero
#: -- "we rejected none of these" and "we have no idea" must not look alike.
BANDS: tuple[str, ...] = (
    "in_range",
    "unfiltered",
    "non_biological",
    "uncommon",
    "out_of_range",
    "no_prior",
    "implausible",
)

#: Twenty bins of 0.05 across the whole [0, 1] score range. Twenty is chosen
#: to be readable as a row of numbers in a terminal and as a small bar chart in
#: a browser, and because 0.05 is finer than any threshold an operator would
#: sensibly move in one step.
HISTOGRAM_BINS = 20
BIN_WIDTH = 1.0 / HISTOGRAM_BINS


class NearMiss(NamedTuple):
    """One candidate the detector proposed and then refused.

    A ``NamedTuple`` rather than a dataclass: it is constructed on the hot
    path, once per rejected candidate, and the C-level tuple constructor is
    the cheapest immutable record Python offers. The strings are the *same*
    objects the label catalogue already holds, never copies.
    """

    at_ns: int
    label_index: int
    common_name: str
    scientific_name: str | None
    score: float
    occurrence: float | None
    band: str
    threshold: float


@dataclass(slots=True)
class _SpeciesTally:
    """Running summary for one label. Mutable and slotted: updated per
    rejection, so attribute rebinds must be cheap and must not allocate."""

    label_index: int
    common_name: str
    scientific_name: str | None
    band: str
    occurrence: float | None
    rejected: int = 0
    admitted: int = 0
    best_score: float = 0.0
    last_at_ns: int = 0


@dataclass(slots=True)
class _BandStats:
    """Counts and the score histogram for one plausibility band."""

    rejected: int = 0
    admitted: int = 0
    #: Rejected scores, binned. Admitted scores are deliberately not binned:
    #: they are already in the database, where a real query can reach them.
    histogram: list[int] = field(default_factory=lambda: [0] * HISTOGRAM_BINS)


def _bin_of(score: float) -> int:
    """Bin index for a score, clamped. Scores come from a sigmoid so they are
    already in [0, 1); the clamp is cheap insurance rather than a live case."""
    index = int(score * HISTOGRAM_BINS)
    if index < 0:
        return 0
    if index >= HISTOGRAM_BINS:
        return HISTOGRAM_BINS - 1
    return index


class NearMissLedger:
    """Bounded, in-memory record of what a detector rejected and why.

    ``capacity`` bounds the ring of individual records; ``max_species`` bounds
    the per-species table. Both are hard limits, not policies: the ring
    discards its oldest entry on overflow (``deque(maxlen=...)`` does it in C),
    and the species table stops admitting *new* species once full, counting the
    ones it turned away so the omission is visible rather than silent.

    Setting ``capacity`` to 0 disables the ring and skips the record
    construction entirely; the histograms and the species table remain, because
    they are the part that costs almost nothing and answers the tuning question
    on their own.
    """

    def __init__(self, *, capacity: int = 200, max_species: int = 512) -> None:
        self._capacity = max(0, int(capacity))
        self._max_species = max(0, int(max_species))
        self._ring: deque[NearMiss] = deque(maxlen=self._capacity or 1)
        self._bands: dict[str, _BandStats] = {band: _BandStats() for band in BANDS}
        self._species: dict[int, _SpeciesTally] = {}
        self._species_omitted = 0
        self._rejected_total = 0
        self._admitted_total = 0

    # ------------------------------------------------------------------

    @property
    def capacity(self) -> int:
        return self._capacity

    def resize(self, capacity: int) -> None:
        """Change the ring depth on a running detector (ADR-048 live tier).

        ``deque.maxlen`` is read-only, so this rebuilds the ring, keeping the
        newest entries when shrinking. The histograms and the species table are
        untouched: they are the cumulative record and resizing the ring is not
        a request to forget it.
        """
        capacity = max(0, int(capacity))
        if capacity == self._capacity:
            return
        kept = list(self._ring)[-capacity:] if capacity else []
        self._capacity = capacity
        self._ring = deque(kept, maxlen=capacity or 1)

    # -- the hot path ---------------------------------------------------

    def record_rejected(
        self,
        *,
        at_ns: int,
        label_index: int,
        common_name: str,
        scientific_name: str | None,
        score: float,
        occurrence: float | None,
        band: str,
        threshold: float,
    ) -> None:
        """One candidate that failed its band's bar. Called per rejection."""
        stats = self._bands.get(band)
        if stats is None:  # a band this module has not been told about
            stats = self._bands[band] = _BandStats()
        stats.rejected += 1
        stats.histogram[_bin_of(score)] += 1
        self._rejected_total += 1

        tally = self._species.get(label_index)
        if tally is None:
            if len(self._species) >= self._max_species:
                self._species_omitted += 1
            else:
                tally = self._species[label_index] = _SpeciesTally(
                    label_index=label_index,
                    common_name=common_name,
                    scientific_name=scientific_name,
                    band=band,
                    occurrence=occurrence,
                )
        if tally is not None:
            tally.rejected += 1
            tally.last_at_ns = at_ns
            # The band and prior are re-stamped rather than kept from first
            # sight: the operator retunes mid-session (that is the entire
            # point), and a stale band would describe a decision no longer
            # being made.
            tally.band = band
            tally.occurrence = occurrence
            if score > tally.best_score:
                tally.best_score = score

        if self._capacity:
            self._ring.append(
                NearMiss(
                    at_ns=at_ns,
                    label_index=label_index,
                    common_name=common_name,
                    scientific_name=scientific_name,
                    score=score,
                    occurrence=occurrence,
                    band=band,
                    threshold=threshold,
                )
            )

    def record_admitted(self, *, band: str, label_index: int, score: float) -> None:
        """One candidate that cleared its bar. Counted, never ringed --
        an admitted candidate becomes a detection row, which is a far better
        record than anything here. The count exists so a band's rejection
        figure has a denominator."""
        stats = self._bands.get(band)
        if stats is None:
            stats = self._bands[band] = _BandStats()
        stats.admitted += 1
        self._admitted_total += 1
        tally = self._species.get(label_index)
        if tally is not None:
            tally.admitted += 1

    # -- reading it -----------------------------------------------------

    def snapshot(
        self,
        *,
        thresholds: dict[str, float] | None = None,
        limit: int = 50,
        species_limit: int = 40,
    ) -> dict[str, Any]:
        """A JSON-ready view. Called by the API, never on the hot path."""
        thresholds = thresholds or {}
        bands = []
        for band in self._bands:
            stats = self._bands[band]
            threshold = thresholds.get(band)
            bands.append(
                {
                    "band": band,
                    "threshold": None
                    if threshold is None or math.isinf(threshold)
                    else round(threshold, 4),
                    "threshold_unreachable": threshold is not None and math.isinf(threshold),
                    "rejected": stats.rejected,
                    "admitted": stats.admitted,
                    "histogram": {
                        "bin_width": BIN_WIDTH,
                        "counts": list(stats.histogram),
                    },
                }
            )

        species = sorted(
            self._species.values(),
            key=lambda tally: (-tally.rejected, -tally.best_score),
        )[: max(0, species_limit)]

        recent = list(self._ring)[-limit:] if limit > 0 else []
        recent.reverse()  # newest first, which is how a person reads a log

        return {
            "capacity": self._capacity,
            "held": len(self._ring) if self._capacity else 0,
            "rejected_total": self._rejected_total,
            "admitted_total": self._admitted_total,
            "species_tracked": len(self._species),
            "species_omitted": self._species_omitted,
            "bands": bands,
            "species": [
                {
                    "label_index": tally.label_index,
                    "common_name": tally.common_name,
                    "scientific_name": tally.scientific_name,
                    "band": tally.band,
                    "occurrence_probability": None
                    if tally.occurrence is None
                    else round(tally.occurrence, 6),
                    "rejected": tally.rejected,
                    "admitted": tally.admitted,
                    "best_score": round(tally.best_score, 4),
                    "shortfall": _shortfall(tally.best_score, thresholds.get(tally.band)),
                    "last_at_ns": tally.last_at_ns,
                }
                for tally in species
            ],
            "recent": [
                {
                    "at_ns": item.at_ns,
                    "label_index": item.label_index,
                    "common_name": item.common_name,
                    "scientific_name": item.scientific_name,
                    "score": round(item.score, 4),
                    "occurrence_probability": None
                    if item.occurrence is None
                    else round(item.occurrence, 6),
                    "band": item.band,
                    "threshold": None if math.isinf(item.threshold) else round(item.threshold, 4),
                    "shortfall": _shortfall(item.score, item.threshold),
                }
                for item in recent
            ],
        }


def _shortfall(score: float, threshold: float | None) -> float | None:
    """How far short of its bar a score fell.

    ``None`` for the ``implausible`` band, whose bar is ``math.inf``: no
    finite distance describes it, and printing a huge number would imply the
    candidate was merely a long way off rather than refused on principle
    (ADR-032). "We do not know" and "not applicable" stay available all the way
    to the surface -- charter honesty constraint.
    """
    if threshold is None or math.isinf(threshold):
        return None
    return round(threshold - score, 4)
