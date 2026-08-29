"""Which clips are worth keeping (ADR-074).

The disk is not full of interesting things. It is full of robins: European
Robin and Common Woodpigeon alone hold 120.2 GB, 31% of the SSD, while the 91
species with fewer than 50 clips hold 1.62 GB between them.
"""

from __future__ import annotations

from open_observatory import evidence_value as ev

ID = "0123456789abcdef0123456789abcdef"


def test_the_blind_sample_rescues_roughly_one_percent_of_the_expired() -> None:
    """Without it the archive cannot estimate a false-positive rate.

    A best-of collection is selected on the very variable you would want to
    measure, so it can never answer "how often is the detector wrong?".

    ADR-076 deletes `classify()` -- the per-sweep BANK/QUOTA/SAMPLE/EXPIRE
    verdict -- but `sampled()` is the one piece of it that survives unchanged
    (it still decides SAMPLE vs. QUOTA for a clip `cap_for` did not bank), so
    this exercises it directly rather than through the deleted function.
    """
    kept = sum(1 for i in range(10_000) if ev.sampled(f"{i:032x}", 10))
    assert 70 <= kept <= 130, f"expected ~1% sampled, got {kept}/10000"


def test_the_sample_is_deterministic_and_blind() -> None:
    """Same id, same verdict, whatever the score or species says.

    Reproducible so an auditor can re-derive the sample; blind so it stays an
    unbiased estimator.
    """
    a = ev.sampled(ID, 10)
    b = ev.sampled(ID, 10)
    assert a == b, "the sample must not depend on anything but the id and rate"


def test_a_zero_permille_sample_disables_sampling_entirely() -> None:
    for i in range(500):
        assert ev.sampled(f"{i:032x}", 0) is False


def test_frequency_bands_are_five_kilohertz_wide() -> None:
    assert ev.frequency_band(22_000) == 20
    assert ev.frequency_band(63_400) == 60
    assert ev.frequency_band(None) is None


def test_the_stations_own_bat_distribution_marks_the_right_bands_sparse() -> None:
    """Measured 2026-08-29 over 66,485 passes.

    20-25 kHz holds 36,180; 60-65 kHz holds four. That band is the bat
    equivalent of the heron and a flat 1% sample would leave it an expected
    0.04 passes -- discarding the rarest signal the station has.
    """
    counts = {
        15: 7040,
        20: 36180,
        25: 768,
        30: 8655,
        35: 7603,
        40: 1336,
        45: 3328,
        50: 1464,
        55: 107,
        60: 4,
    }
    sparse = ev.sparse_bands(counts, permille=10)
    assert 60 in sparse
    assert 55 in sparse
    assert 20 not in sparse
    assert 30 not in sparse


def test_a_band_that_stops_being_sparse_stops_being_kept_whole() -> None:
    """Self-limiting, like the species bank."""
    counts = {20: 1000, 55: 900}
    assert 55 not in ev.sparse_bands(counts, permille=10)


def test_no_passes_at_all_yields_no_sparse_bands_rather_than_all_of_them() -> None:
    assert ev.sparse_bands({}, permille=10) == frozenset()


class TestCapFor:
    """ADR-076: the cap is decided once, at promotion, not re-litigated."""

    def test_uncommon_species_gets_the_full_bank(self) -> None:
        assert ev.cap_for("Grey Heron", is_common=False, is_implausible=False,
                          policy=ev.Policy()) == 200

    def test_common_species_banks_nothing_new(self) -> None:
        assert ev.cap_for("European Robin", is_common=True, is_implausible=False,
                          policy=ev.Policy()) == 0

    def test_implausible_species_is_capped_at_three_examples(self) -> None:
        assert ev.cap_for("California Quail", is_common=False, is_implausible=True,
                          policy=ev.Policy()) == 3

    def test_implausible_beats_common(self) -> None:
        """A species on both lists is a misidentification, not a boring bird.

        Three examples are what makes a systematic misidentification judgeable.
        Reading `common` first would silence it instead, which is exactly the
        failure ADR-074's suggestion rule guards against for the same reason.
        """
        assert ev.cap_for("Ambiguous", is_common=True, is_implausible=True,
                          policy=ev.Policy()) == 3

    def test_a_zero_bank_size_disables_the_bank_without_disabling_the_policy(self) -> None:
        assert ev.cap_for("Grey Heron", is_common=False, is_implausible=False,
                          policy=ev.Policy(bank_size=0)) == 0
