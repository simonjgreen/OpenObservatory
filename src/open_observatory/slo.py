"""The five capture SLOs of ADR-073, as pure arithmetic.

PURE. No SQLAlchemy, no FastAPI, no Pydantic -- the same rule
``plausibility.py`` and ``firmware_store.py`` follow, and for the same reason:
these numbers appear in the health payload, in ``/metrics``, in the acceptance
record and in the operations guide, and every one of those callers must get
the identical figure from the identical function.

The reason this module exists at all is that ``continuity_ratio`` --
``frames / expected_frames`` -- silently adds together three unrelated things:

* **coverage**  the capture process was not running
* **integrity** frames were dropped while it *was* running
* **drift**     the device's crystal is not the host's (ADR-072)

Only the first two are missing audio. Drift is audio that exists, is correct,
and is merely labelled a few seconds off -- and it dominates the ratio. On the
2026-08-25 soak drift was **100%** of the reported shortfall: nothing was lost
and the station still read 99.9943% "complete".
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DeficitSplit", "split_deficit"]


@dataclass(frozen=True, slots=True)
class DeficitSplit:
    """One stream's frame deficit, separated into what it actually means."""

    deficit_frames: int
    #: Audio that is genuinely gone. The station's own confirmed-loss counter
    #: (ADR-039), never inferred from the deficit.
    lost_frames: int
    #: The remainder: the crystal, and accepted (ADR-072).
    drift_frames: int
    lost_seconds: float
    drift_seconds: float
    #: SLO B. Of the audio that should have been captured while running, the
    #: fraction that was. **Blind to drift by construction.**
    integrity_ratio: float


def split_deficit(
    *,
    expected_frames: int,
    frames: int,
    missing_frames: int,
    sample_rate: int,
) -> DeficitSplit:
    """Separate a frame deficit into confirmed loss and crystal drift.

    ``missing_frames`` is authoritative for loss: it is what ``AlsaSource``
    confirmed as gone after watching a deficit step fail to come back down
    (ADR-039). Everything else in the deficit is drift by definition, which is
    why drift is a residual here rather than a measurement -- there is no
    third thing it could be.
    """
    deficit = max(0, expected_frames - frames)
    lost = max(0, missing_frames)
    # Clamped rather than allowed negative. The two inputs come from different
    # code paths, and a negative "drift" would silently offset real loss.
    drift = max(0, deficit - lost)

    rate = sample_rate or 1
    integrity = 1.0 if expected_frames <= 0 else max(0.0, 1.0 - lost / expected_frames)

    return DeficitSplit(
        deficit_frames=deficit,
        lost_frames=lost,
        drift_frames=drift,
        lost_seconds=round(lost / rate, 4),
        drift_seconds=round(drift / rate, 4),
        integrity_ratio=round(integrity, 9),
    )
