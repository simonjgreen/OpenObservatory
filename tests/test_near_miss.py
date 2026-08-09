"""ADR-052: the near-miss ledger.

The failure this guards against is the one the four ADR-032 counters already
committed -- a number with no truth behind it. So these tests assert the
*content* of the record (which species, what score, which bar, what prior),
not merely that a total went up, and they assert the bounds, because an
unbounded diagnostic on the detector's hot path would defeat charter item 1
long before anyone read it.
"""

from __future__ import annotations

import math

from open_observatory.detectors.near_miss import BANDS, HISTOGRAM_BINS, NearMissLedger


def _reject(ledger: NearMissLedger, **overrides: object) -> None:
    payload: dict[str, object] = {
        "at_ns": 1_700_000_000_000_000_000,
        "label_index": 7,
        "common_name": "European Robin",
        "scientific_name": "Erithacus rubecula",
        "score": 0.41,
        "occurrence": 0.83,
        "band": "in_range",
        "threshold": 0.55,
    }
    payload.update(overrides)
    ledger.record_rejected(**payload)  # type: ignore[arg-type]


class TestTheRingIsBounded:
    def test_the_ring_never_grows_past_its_capacity(self) -> None:
        ledger = NearMissLedger(capacity=10)
        for index in range(1000):
            _reject(ledger, at_ns=index, score=0.5)
        snapshot = ledger.snapshot(limit=500)
        assert snapshot["held"] == 10
        assert len(snapshot["recent"]) == 10
        # ...and the cumulative counts are not truncated with it: the ring is
        # a sample of the record, the histogram *is* the record.
        assert snapshot["rejected_total"] == 1000
        binned = sum(sum(band["histogram"]["counts"]) for band in snapshot["bands"])
        assert binned == 1000

    def test_capacity_zero_keeps_the_histogram_and_stops_keeping_rows(self) -> None:
        """The justification for the setting: the counting part is what
        chooses a threshold, and it survives turning the rows off."""
        ledger = NearMissLedger(capacity=0)
        for _ in range(50):
            _reject(ledger, score=0.5)
        snapshot = ledger.snapshot()
        assert snapshot["recent"] == []
        assert snapshot["held"] == 0
        assert snapshot["rejected_total"] == 50
        in_range = next(b for b in snapshot["bands"] if b["band"] == "in_range")
        assert in_range["rejected"] == 50

    def test_the_species_table_is_bounded_and_says_what_it_dropped(self) -> None:
        ledger = NearMissLedger(capacity=5, max_species=3)
        for index in range(20):
            _reject(ledger, label_index=index, common_name=f"Species {index}")
        snapshot = ledger.snapshot()
        assert snapshot["species_tracked"] == 3
        assert snapshot["species_omitted"] == 17
        # The histograms still saw every one of them, so the omission costs
        # the naming, not the count.
        assert snapshot["rejected_total"] == 20

    def test_resizing_keeps_the_newest_rows_and_the_whole_history(self) -> None:
        ledger = NearMissLedger(capacity=100)
        for index in range(100):
            _reject(ledger, at_ns=index)
        ledger.resize(5)
        snapshot = ledger.snapshot(limit=50)
        assert ledger.capacity == 5
        assert [row["at_ns"] for row in snapshot["recent"]] == [99, 98, 97, 96, 95]
        assert snapshot["rejected_total"] == 100


class TestTheHistogramIsDecisionUseful:
    def test_scores_land_in_the_bin_a_threshold_choice_would_use(self) -> None:
        """The summary the operator actually needs: 'you rejected 400, 380 of
        them below 0.2 and 20 between 0.45 and 0.55'."""
        ledger = NearMissLedger(capacity=0)
        for _ in range(380):
            _reject(ledger, score=0.11)
        for _ in range(20):
            _reject(ledger, score=0.47)
        band = next(
            b for b in ledger.snapshot()["bands"] if b["band"] == "in_range"
        )
        counts = band["histogram"]["counts"]
        assert len(counts) == HISTOGRAM_BINS
        assert counts[2] == 380  # 0.10-0.15
        assert counts[9] == 20  # 0.45-0.50
        assert sum(counts) == 400

    def test_every_band_appears_even_with_nothing_in_it(self) -> None:
        """A band that has seen nothing must read as an explicit zero. 'We
        rejected none of these' and 'we have no idea' must not look alike
        (charter honesty constraint)."""
        snapshot = NearMissLedger(capacity=1).snapshot()
        assert {band["band"] for band in snapshot["bands"]} == set(BANDS)
        assert all(band["rejected"] == 0 for band in snapshot["bands"])


class TestTheRecordNamesWhatWasRefused:
    def test_a_rejection_carries_species_score_prior_band_and_bar(self) -> None:
        ledger = NearMissLedger(capacity=4)
        _reject(
            ledger,
            common_name="Eurasian Blackbird",
            scientific_name="Turdus merula",
            score=0.538,
            occurrence=0.9312,
            band="in_range",
            threshold=0.55,
        )
        row = ledger.snapshot(thresholds={"in_range": 0.55})["recent"][0]
        assert row["common_name"] == "Eurasian Blackbird"
        assert row["scientific_name"] == "Turdus merula"
        assert row["score"] == 0.538
        assert row["occurrence_probability"] == 0.9312
        assert row["band"] == "in_range"
        assert row["threshold"] == 0.55
        assert row["shortfall"] == 0.012

    def test_an_unreachable_bar_reports_no_distance_at_all(self) -> None:
        """ADR-032's `implausible` band has an infinite bar. A shortfall
        would imply the candidate was merely a long way off, rather than
        refused on principle; 'not applicable' stays available to the surface.
        """
        ledger = NearMissLedger(capacity=4)
        _reject(ledger, band="implausible", threshold=math.inf, score=0.97)
        snapshot = ledger.snapshot(thresholds={"implausible": math.inf})
        assert snapshot["recent"][0]["threshold"] is None
        assert snapshot["recent"][0]["shortfall"] is None
        band = next(b for b in snapshot["bands"] if b["band"] == "implausible")
        assert band["threshold"] is None
        assert band["threshold_unreachable"] is True

    def test_the_species_table_ranks_by_how_much_was_thrown_away(self) -> None:
        ledger = NearMissLedger(capacity=0)
        for _ in range(3):
            _reject(ledger, label_index=1, common_name="Great Tit", score=0.20)
        for _ in range(41):
            _reject(ledger, label_index=2, common_name="Eurasian Blackbird", score=0.31)
        _reject(ledger, label_index=2, common_name="Eurasian Blackbird", score=0.54)
        species = ledger.snapshot(thresholds={"in_range": 0.55})["species"]
        assert [row["common_name"] for row in species] == [
            "Eurasian Blackbird",
            "Great Tit",
        ]
        assert species[0]["rejected"] == 42
        assert species[0]["best_score"] == 0.54
        assert species[0]["shortfall"] == 0.01

    def test_admitted_candidates_give_the_rejection_count_a_denominator(self) -> None:
        ledger = NearMissLedger(capacity=0)
        _reject(ledger, label_index=3, common_name="Robin", score=0.4)
        ledger.record_admitted(band="in_range", label_index=3, score=0.8)
        snapshot = ledger.snapshot()
        band = next(b for b in snapshot["bands"] if b["band"] == "in_range")
        assert (band["rejected"], band["admitted"]) == (1, 1)
        assert snapshot["species"][0]["admitted"] == 1

    def test_a_retune_restamps_the_band_rather_than_leaving_a_stale_one(self) -> None:
        """The operator retunes mid-session -- that is the point of the panel
        -- so a species' recorded band must describe the decision now being
        made, not the first one ever made about it."""
        ledger = NearMissLedger(capacity=0)
        _reject(ledger, label_index=9, band="uncommon", occurrence=0.05, threshold=0.75)
        _reject(ledger, label_index=9, band="in_range", occurrence=0.2, threshold=0.55)
        row = ledger.snapshot()["species"][0]
        assert row["band"] == "in_range"
        assert row["occurrence_probability"] == 0.2
