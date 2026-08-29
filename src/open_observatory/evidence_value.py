"""Which evidence clips are worth keeping, as pure logic (ADR-074).

PURE. No SQLAlchemy, no FastAPI, no Pydantic -- the rule ``slo.py``,
``plausibility.py`` and ``firmware_store.py`` all follow, so that the retention
sweep, the dry-run report and any future UI all get the identical verdict from
the identical function.

Retention today asks only how old a clip is. That is why 234,761 live clips
occupy 388.8 GB, of which European Robin and Common Woodpigeon alone are
120.2 GB -- 31% of the SSD -- while the 91 species with fewer than 50 clips hold
1.62 GB between them. The disk is not full of interesting things.

**Rarity alone is the wrong axis.** The rarest entries in this station's record
are mostly wrong: California Quail, Asian Brown Flycatcher, Chestnut-backed
Chickadee, none of which can occur in a Surrey garden. A naive rarity bias would
preferentially archive BirdNET's mistakes. So rarity is crossed with the
plausibility band ADR-049 already computes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["Policy", "Verdict", "cap_for", "sampled"]


class Verdict(StrEnum):
    #: Keep indefinitely, exempt from age expiry. Bounded by ``bank_size``.
    BANK = "bank"
    #: Keep under the daily best/median/worst quota, subject to the age tiers.
    QUOTA = "quota"
    #: Kept only because the blind sample drew it. Subject to the age tiers.
    SAMPLE = "sample"
    #: Nothing keeps this; the age tiers may reclaim it.
    EXPIRE = "expire"


@dataclass(frozen=True, slots=True)
class Policy:
    #: Clips a species may bank before falling back to the quota. The ceiling
    #: this puts on the whole archive is what makes the policy bounded by
    #: construction rather than by a high-water mark: 143 species x 200 x
    #: 1.53 MB is about 44 GB even if every species maxes out.
    bank_size: int = 200
    #: Blind sample rate in parts per thousand. 10 = 1%.
    sample_permille: int = 10
    #: Examples kept of a species the range model says cannot be here. Three is
    #: enough to judge a systematic misidentification.
    implausible_cap: int = 3


def sampled(detection_id: str, permille: int) -> bool:
    """Deterministic, blind 1-in-N draw.

    Keyed on the detection id and NOTHING else. Not on score, not on band, not
    on species -- the moment it consults any of those it stops being an
    unbiased estimator and becomes another best-of pile, and the archive loses
    its only means of answering "how often is the detector wrong?".

    Deterministic so the sample is reproducible: an auditor can re-derive
    exactly which rows were drawn, months later, without stored state.
    """
    if permille <= 0:
        return False
    digest = hashlib.sha256(detection_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 1000 < permille


def cap_for(
    species: str,
    *,
    is_common: bool,
    is_implausible: bool,
    policy: Policy,
) -> int:
    """How many detections of ``species`` may be banked, ever.

    ADR-076 replaces ADR-074's per-sweep ``classify()`` verdict with a cap
    applied **once, at promotion**. That is the whole fix for the cliff: a
    verdict recomputed every sweep can flip from BANK to EXPIRE for a whole
    back-catalogue at once, and a cap cannot -- reaching it stops promotion and
    unbanks nothing.

    ``is_implausible`` is read before ``is_common`` deliberately. A species on
    both lists is a systematic misidentification, and three examples are what
    make one judgeable; treating it as merely boring would silence the very
    thing worth investigating.
    """
    if is_implausible:
        return max(0, policy.implausible_cap)
    if is_common:
        return 0
    return max(0, policy.bank_size)


__all__ += ["BAND_WIDTH_HZ", "frequency_band", "sparse_bands"]

#: Bat passes are never given a species, by design, so rarity cannot come from a
#: name. It comes from peak frequency, and the distribution is as skewed as the
#: birds': measured over 66,485 passes on 2026-08-29, 20-25 kHz held 36,180 and
#: 60-65 kHz held four.
BAND_WIDTH_HZ = 5000


def frequency_band(peak_hz: float | None) -> int | None:
    """The 5 kHz band a pass falls in, named by its lower edge in kHz."""
    if peak_hz is None or peak_hz <= 0:
        return None
    return int(peak_hz // BAND_WIDTH_HZ) * (BAND_WIDTH_HZ // 1000)


def sparse_bands(counts: Mapping[int, int], *, permille: int = 10) -> frozenset[int]:
    """Bands holding less than ``permille``/1000 of the passes: keep every one.

    Self-limiting in the same way the species bank is -- a band that stops being
    sparse stops being kept whole -- so this cannot grow without bound.
    """
    total = sum(counts.values())
    if total <= 0:
        return frozenset()
    return frozenset(b for b, n in counts.items() if n * 1000 < total * permille)
