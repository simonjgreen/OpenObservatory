"""Which clips are worth keeping (ADR-074).

The disk is not full of interesting things. It is full of robins: European
Robin and Common Woodpigeon alone hold 120.2 GB, 31% of the SSD, while the 91
species with fewer than 50 clips hold 1.62 GB between them.
"""

from __future__ import annotations

from open_observatory import evidence_value as ev

POLICY = ev.Policy()
ID = "0123456789abcdef0123456789abcdef"


def test_an_uncommon_plausible_species_banks_until_the_bank_is_full() -> None:
    """The heron. 139 clips today, and every one is worth keeping."""
    v = ev.classify(
        banded_already=139,
        band="in_range",
        is_common=False,
        detection_id=ID,
        policy=POLICY,
    )
    assert v == ev.Verdict.BANK


def test_the_bank_is_self_limiting() -> None:
    """Past K the same species falls back to the quota.

    This is what bounds the whole policy: 143 species x 200 x 1.53 MB is a
    44 GB ceiling even if every species maxes out, instead of the 313 GB/year
    that keeping the tail forever would cost.
    """
    v = ev.classify(
        banded_already=200,
        band="in_range",
        is_common=False,
        detection_id=ID,
        policy=POLICY,
    )
    assert v == ev.Verdict.QUOTA


def test_a_common_species_never_banks_at_all() -> None:
    """Robin would fill its 200 in a day, so it goes straight to quota."""
    v = ev.classify(
        banded_already=0,
        band="in_range",
        is_common=True,
        detection_id=ID,
        policy=POLICY,
    )
    assert v == ev.Verdict.QUOTA


def test_an_implausible_rarity_is_capped_not_banked() -> None:
    """California Quail cannot occur in a Surrey garden.

    Rarity alone is the wrong axis: the rarest entries in this station's record
    are mostly BirdNET's mistakes. Three examples are enough to judge a
    systematic error; the 3,000th adds nothing.
    """
    under = ev.classify(
        banded_already=1,
        band="implausible",
        is_common=False,
        detection_id=ID,
        policy=POLICY,
    )
    assert under == ev.Verdict.BANK

    over = ev.classify(
        banded_already=3,
        band="implausible",
        is_common=False,
        detection_id=ID,
        policy=POLICY,
    )
    assert over == ev.Verdict.EXPIRE


def test_the_blind_sample_rescues_roughly_one_percent_of_the_expired() -> None:
    """Without it the archive cannot estimate a false-positive rate.

    A best-of collection is selected on the very variable you would want to
    measure, so it can never answer "how often is the detector wrong?".
    """
    kept = sum(
        1
        for i in range(10_000)
        if ev.classify(
            banded_already=0,
            band="in_range",
            is_common=True,
            detection_id=f"{i:032x}",
            policy=ev.Policy(sample_permille=10),
        )
        is ev.Verdict.SAMPLE
    )
    assert 70 <= kept <= 130, f"expected ~1% sampled, got {kept}/10000"


def test_the_sample_is_deterministic_and_blind() -> None:
    """Same id, same verdict, whatever the score or species says.

    Reproducible so an auditor can re-derive the sample; blind so it stays an
    unbiased estimator.
    """
    a = ev.classify(
        banded_already=0,
        band="in_range",
        is_common=True,
        detection_id=ID,
        policy=POLICY,
    )
    b = ev.classify(
        banded_already=0,
        band="uncommon",
        is_common=True,
        detection_id=ID,
        policy=POLICY,
    )
    assert a == b, "the sample must not depend on species or band"


def test_a_zero_permille_sample_disables_sampling_entirely() -> None:
    for i in range(500):
        v = ev.classify(
            banded_already=0,
            band="in_range",
            is_common=True,
            detection_id=f"{i:032x}",
            policy=ev.Policy(sample_permille=0),
        )
        assert v is not ev.Verdict.SAMPLE
