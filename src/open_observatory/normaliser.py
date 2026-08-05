"""Normalises native detector output into canonical detections.

Three jobs:

1. **Translate window-relative offsets into absolute stream and UTC bounds**, so a
   detection can be located in the authoritative native recording regardless of
   which derived stream found it.
2. **Enforce the claims a detector is allowed to make.** ADR-010 says the activity
   detector must never emit a species label; this is where that is enforced rather
   than merely documented, because a plugin bug that started emitting names would
   otherwise silently become a product claim.
3. **Suppress duplicates from overlapping windows.** With a 3 s window on a 1.5 s
   stride, the same bird call is analysed twice and would otherwise be recorded
   twice.

The complete original detector output is always preserved in ``native_result``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from .audio.contracts import NS_PER_S, AudioWindow, DetectorMetadata, NativeDetection
from .display import display_title

log = structlog.get_logger(__name__)

#: Plugins that are contractually forbidden from making taxonomic claims.
NON_TAXONOMIC_PLUGINS = frozenset({"activity-v1"})

#: Groups whose members are events, not organisms.
NON_TAXONOMIC_GROUPS = frozenset({"acoustic_event", "noise", "unknown"})


@dataclass(frozen=True, slots=True)
class CanonicalDetection:
    """A detection ready to persist and publish."""

    detection_id: uuid.UUID
    plugin_id: str
    plugin_version: str
    model_id: str
    model_version: str
    model_sha256: str | None
    stream_id: uuid.UUID
    window_id: uuid.UUID
    event_start_utc: datetime
    event_end_utc: datetime
    #: Bounds in the native stream's frame space.
    source_start_frame: int
    source_end_frame: int
    detector_label: str | None
    common_name: str | None
    scientific_name: str | None
    canonical_taxon_id: str | None
    rank: str | None
    taxonomic_group: str
    score: float
    calibrated_probability: float | None
    peak_frequency_hz: float | None
    native_result: dict[str, object]
    #: Convenience for the UI: the best human-readable name available.
    display_name: str
    #: Presentational only: frequency + candidate species for a bat pass, e.g.
    #: "45 kHz · common pipistrelle?". None for anything that is not a bat pass.
    #: Never persisted into common_name/scientific_name/canonical_taxon_id/rank.
    title_hint: str | None

    @property
    def duration_s(self) -> float:
        return (self.event_end_utc - self.event_start_utc).total_seconds()

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.detection_id),
            "detector": {
                "plugin_id": self.plugin_id,
                "plugin_version": self.plugin_version,
                "model_id": self.model_id,
                "model_version": self.model_version,
                "model_sha256": self.model_sha256,
            },
            "stream_id": str(self.stream_id),
            "window_id": str(self.window_id),
            "event_start_utc": self.event_start_utc.isoformat().replace("+00:00", "Z"),
            "event_end_utc": self.event_end_utc.isoformat().replace("+00:00", "Z"),
            "duration_s": round(self.duration_s, 3),
            "source_start_frame": self.source_start_frame,
            "source_end_frame": self.source_end_frame,
            "label": self.detector_label,
            "display_name": self.display_name,
            "title_hint": self.title_hint,
            "common_name": self.common_name,
            "scientific_name": self.scientific_name,
            "canonical_taxon_id": self.canonical_taxon_id,
            "rank": self.rank,
            "taxonomic_group": self.taxonomic_group,
            "score": round(self.score, 6),
            "calibrated_probability": self.calibrated_probability,
            "peak_frequency_hz": round(self.peak_frequency_hz, 1)
            if self.peak_frequency_hz is not None
            else None,
            "native_result": self.native_result,
        }


class ClaimViolation(ValueError):
    """A detector emitted output it is not permitted to emit."""


@dataclass(slots=True)
class NormaliserStats:
    normalised: int = 0
    duplicates_suppressed: int = 0
    claim_violations: int = 0


class Normaliser:
    """Turns native detections into canonical ones, with duplicate suppression."""

    def __init__(self, *, dedupe_overlap_ratio: float = 0.5) -> None:
        self.stats = NormaliserStats()
        self._dedupe_overlap_ratio = dedupe_overlap_ratio
        #: (plugin_id, label) -> (start_frame, end_frame) of the last accepted event.
        self._recent: dict[tuple[str, str | None], tuple[int, int]] = {}

    def reset(self) -> None:
        self._recent.clear()

    def normalise(
        self,
        metadata: DetectorMetadata,
        window: AudioWindow,
        detection: NativeDetection,
        *,
        native_sample_rate: int,
    ) -> CanonicalDetection | None:
        self._check_claims(metadata, detection)

        # Window offsets are in the analysed stream's own rate; convert to that
        # stream's absolute frames, then to native frames for evidence.
        window_rate = window.sample_rate
        start_frame_local = window.start_frame + int(round(detection.offset_start_s * window_rate))
        end_frame_local = window.start_frame + int(round(detection.offset_end_s * window_rate))
        if window_rate == native_sample_rate:
            source_start, source_end = start_frame_local, end_frame_local
        else:
            ratio = native_sample_rate / window_rate
            source_start = int(round(start_frame_local * ratio))
            source_end = int(round(end_frame_local * ratio))

        key = (metadata.plugin_id, detection.label)
        previous = self._recent.get(key)
        if previous is not None and self._overlaps(previous, (source_start, source_end)):
            self.stats.duplicates_suppressed += 1
            return None
        self._recent[key] = (source_start, source_end)
        if len(self._recent) > 4096:
            self._recent.clear()

        start_ns = window.utc_start_ns + int(detection.offset_start_s * NS_PER_S)
        end_ns = window.utc_start_ns + int(detection.offset_end_s * NS_PER_S)

        display_name, title_hint = display_title(
            common_name=detection.common_name,
            scientific_name=detection.scientific_name,
            label=detection.label,
            plugin_id=metadata.plugin_id,
            taxonomic_group=detection.taxonomic_group,
            peak_frequency_hz=detection.peak_frequency_hz,
            native_result=detection.native_result,
        )

        self.stats.normalised += 1
        return CanonicalDetection(
            detection_id=uuid.uuid4(),
            plugin_id=metadata.plugin_id,
            plugin_version=metadata.plugin_version,
            model_id=metadata.model_id,
            model_version=metadata.model_version,
            model_sha256=metadata.model_sha256,
            stream_id=window.stream_id,
            window_id=window.window_id,
            event_start_utc=datetime.fromtimestamp(start_ns / NS_PER_S, tz=UTC),
            event_end_utc=datetime.fromtimestamp(end_ns / NS_PER_S, tz=UTC),
            source_start_frame=source_start,
            source_end_frame=source_end,
            detector_label=detection.label,
            common_name=detection.common_name,
            scientific_name=detection.scientific_name,
            canonical_taxon_id=self._canonical_taxon_id(detection),
            rank=detection.rank,
            taxonomic_group=detection.taxonomic_group,
            score=float(detection.score),
            calibrated_probability=detection.calibrated_probability,
            peak_frequency_hz=detection.peak_frequency_hz,
            native_result=dict(detection.native_result),
            display_name=display_name,
            title_hint=title_hint,
        )

    def _check_claims(self, metadata: DetectorMetadata, detection: NativeDetection) -> None:
        if metadata.plugin_id in NON_TAXONOMIC_PLUGINS:
            offending = [
                name
                for name, value in (
                    ("common_name", detection.common_name),
                    ("scientific_name", detection.scientific_name),
                    ("rank", detection.rank),
                )
                if value
            ]
            if offending:
                self.stats.claim_violations += 1
                raise ClaimViolation(
                    f"{metadata.plugin_id} is not permitted to emit taxonomic fields "
                    f"(ADR-010) but set {', '.join(offending)}"
                )
        if detection.calibrated_probability is not None and not metadata.calibrated:
            self.stats.claim_violations += 1
            raise ClaimViolation(
                f"{metadata.plugin_id} reported a calibrated probability but its "
                "metadata declares calibration is unknown"
            )

    @staticmethod
    def _canonical_taxon_id(detection: NativeDetection) -> str | None:
        """No taxonomy backend yet, so only assert an id we can actually justify.

        A scientific name from a species-rank detection is a usable stable key;
        anything else stays null rather than inventing an identifier that later
        taxonomy work would have to unpick.
        """
        if detection.taxonomic_group in NON_TAXONOMIC_GROUPS:
            return None
        if detection.rank == "species" and detection.scientific_name:
            return f"sci:{detection.scientific_name.lower().replace(' ', '_')}"
        return None

    def _overlaps(self, previous: tuple[int, int], current: tuple[int, int]) -> bool:
        overlap = min(previous[1], current[1]) - max(previous[0], current[0])
        if overlap <= 0:
            return False
        shortest = min(previous[1] - previous[0], current[1] - current[0])
        if shortest <= 0:
            return True
        return overlap / shortest >= self._dedupe_overlap_ratio
