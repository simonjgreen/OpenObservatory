"""Single source of truth for the human-readable name and title hint of a detection.

``display_name`` was previously computed independently in four places
(``normaliser.py``, and three spots in ``api/app.py``/``history.py``) with subtly
different fallback chains — the REST paths dropped the ``scientific_name`` fallback
that the live WebSocket path had, so the same detection could display differently
depending on how it was fetched. :func:`display_title` is now the only place this
logic lives; every call site imports it.

``title_hint`` is a purely presentational addition: for an ultrasonic bat pass it
composes the peak frequency with a candidate species name inferred from the
frequency band, e.g. ``"45 kHz · common pipistrelle?"``. The trailing ``?`` is
mandatory, not stylistic — the candidate is a guess from frequency alone, never an
identification, and the stored record / taxonomic fields must never carry it (see
``normaliser.ClaimViolation`` and ADR-013). For anything that is not a bat pass,
``title_hint`` is ``None``.
"""

from __future__ import annotations

from . import plausibility

#: Only detections with this taxonomic group are eligible for a frequency-based
#: candidate hint. Currently only the ultrasonic detector emits it.
_BAT_GROUP = "bat"


def _frequency_candidate(hz: float) -> tuple[str | None, str | None]:
    """Best-effort import of the band table owned by ``detectors/ultrasonic.py``.

    That module is being edited concurrently by another agent adding this exact
    function. Imported lazily, and guarded, so this module keeps working (with no
    candidate) whether or not that change has landed yet — and so there is still
    only one table of bands, owned there, not duplicated here.
    """
    try:
        from .detectors.ultrasonic import frequency_candidate
    except ImportError:
        return None, None
    try:
        return frequency_candidate(hz)
    except Exception:  # pragma: no cover - defensive against a mid-flight signature change
        return None, None


def _format_hz(hz: float) -> str:
    return f"{round(hz / 1000.0)} kHz"


def display_title(
    *,
    common_name: str | None,
    scientific_name: str | None,
    label: str | None,
    plugin_id: str | None,
    taxonomic_group: str | None,
    peak_frequency_hz: float | None,
    native_result: dict[str, object] | None,  # reserved, see below
) -> tuple[str, str | None]:
    """Return ``(display_name, title_hint)``.

    ``display_name`` preserves the original normaliser fallback chain:
    ``common_name or scientific_name or label or plugin_id``, falling back to
    ``"unknown"`` if every field is empty. ``title_hint`` is ``None`` unless this is
    a bat pass with a usable peak frequency, in which case it is the frequency plus
    an optional candidate name and ambiguity note, e.g.
    ``"45 kHz · common pipistrelle?"``.

    ``native_result`` is accepted (rather than only the fields currently used) so
    every call site can pass the same set of arguments uniformly; the feeding-buzz
    marker itself is intentionally *not* folded into this string — it is a
    separate, list-friendly flag (see :func:`detection_flags`) that the UI renders
    as its own marker rather than baking into the hint text.
    """
    display_name = common_name or scientific_name or label or plugin_id or "unknown"

    title_hint: str | None = None
    if taxonomic_group == _BAT_GROUP and peak_frequency_hz is not None:
        short_name, ambiguity = _frequency_candidate(peak_frequency_hz)
        hint = _format_hz(peak_frequency_hz)
        if short_name:
            hint = f"{hint} · {short_name}?"
            if ambiguity:
                hint = f"{hint} ({ambiguity})"
        title_hint = hint

    return display_name, title_hint


def detection_flags(native_result: dict[str, object] | None) -> dict[str, bool]:
    """Small derived marker set for list/history rows that don't carry the raw blob.

    The feeding-buzz flag, and ``withdrawn`` (ADR-042) for a row whose claim a
    plausibility review has retracted. ``withdrawn`` is deliberately here as
    well as being a top-level field on the detection payload: this dict is what
    the web UI's ``formatDetectionTitle`` reads for every render site at once,
    so a marker that lives only at the top level would have had to be plumbed
    into five components separately and would have been missed in one of them.
    """
    return {
        "feeding_buzz": bool((native_result or {}).get("has_feeding_buzz")),
        "withdrawn": plausibility.is_withdrawn(native_result),
    }
