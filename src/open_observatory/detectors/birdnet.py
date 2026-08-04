"""BirdNET adapter.

ADR-006 forbids bundling BirdNET model assets: the BirdNET *code* is
permissively licensed but the released model checkpoints carry separate
non-commercial/share-alike terms. So this adapter ships no weights. It reports
``unavailable`` with a specific, actionable message until the operator runs
``oo models fetch``, and the licence metadata it exposes is surfaced in the UI.

Two models are used, as BirdNET intends:

* the **classifier** — 3 s of 48 kHz mono audio in, 6522 logits out;
* the **range (MData) model** — (latitude, longitude, week) in, per-species
  occurrence probability out.

The range model is what separates "sounds like species X" from "species X is
plausible here, this week". A confident-but-implausible identification is held to
a much higher confidence bar than a garden regular, and the band that decision
fell into is recorded on the detection so a reviewer can see why.

Inference uses ``ai_edge_litert`` (the maintained successor to
``tflite-runtime``, which has no cp312 aarch64 wheel), falling back to
``tflite_runtime`` where that is what is installed.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import structlog

from ..audio.contracts import (
    AudioWindow,
    DetectorHealth,
    DetectorMetadata,
    NativeDetection,
    WindowSpec,
)
from .base import DetectorContext, DetectorUnavailable

log = structlog.get_logger(__name__)

PLUGIN_VERSION = "1.0.0"

CLASSIFIER_FILE = "birdnet.tflite"
RANGE_FILE = "birdnet_mdata.tflite"
LABELS_FILE = "birdnet_labels.txt"

MODEL_ID = "BirdNET_GLOBAL_6K_V2.4"
MODEL_VERSION = "2.4"
LICENCE_NAME = "CC BY-NC-SA 4.0 (model assets); Apache-2.0 (BirdNET code)"
LICENCE_URL = "https://github.com/kahst/BirdNET-Analyzer#license"


def _load_interpreter(path: Path, threads: int):  # type: ignore[no-untyped-def]
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        try:
            from tflite_runtime.interpreter import Interpreter  # type: ignore[no-redef]
        except ImportError as exc:
            raise DetectorUnavailable(
                "no TFLite runtime installed; install the 'birdnet' extra "
                "(ai-edge-litert)"
            ) from exc
    interpreter = Interpreter(model_path=str(path), num_threads=threads)
    interpreter.allocate_tensors()
    return interpreter


def birdnet_week(when: datetime) -> int:
    """BirdNET divides the year into 48 weeks — four per calendar month."""
    return (when.month - 1) * 4 + min(4, int(when.day / 7.25) + 1)


def parse_label(label: str) -> tuple[str | None, str]:
    """BirdNET labels are ``Scientific name_Common Name``."""
    if "_" in label:
        scientific, common = label.split("_", 1)
        return scientific.strip() or None, common.strip()
    return None, label.strip()


class _RangeModel:
    """Caches per-week occurrence probabilities for the station's location."""

    def __init__(self, path: Path, latitude: float, longitude: float, threads: int) -> None:
        self._interpreter = _load_interpreter(path, threads)
        self._input = self._interpreter.get_input_details()[0]["index"]
        self._output = self._interpreter.get_output_details()[0]["index"]
        self.latitude = latitude
        self.longitude = longitude
        self._week: int | None = None
        self._probabilities: np.ndarray | None = None

    def probabilities(self, week: int) -> np.ndarray:
        if self._week != week or self._probabilities is None:
            payload = np.array(
                [[self.latitude, self.longitude, float(week)]], dtype=np.float32
            )
            self._interpreter.set_tensor(self._input, payload)
            self._interpreter.invoke()
            self._probabilities = self._interpreter.get_tensor(self._output)[0].copy()
            self._week = week
        return self._probabilities


class BirdNetDetector:
    """BirdNET GLOBAL 6K V2.4 over 3-second windows of the audible stream."""

    metadata = DetectorMetadata(
        plugin_id="birdnet-v2.4",
        plugin_version=PLUGIN_VERSION,
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        model_sha256=None,
        taxonomy_version="BirdNET GLOBAL 6K V2.4 labels (en_uk)",
        licence_name=LICENCE_NAME,
        licence_url=LICENCE_URL,
        claim=(
            "Reports candidate bird species for 3-second windows, as a model "
            "score rather than a calibrated probability. Scores are not "
            "identifications; a location/date occurrence model raises the bar "
            "for species implausible here this week."
        ),
        resource_class="moderate",
        calibrated=False,
        external_network="none",
    )

    def __init__(
        self,
        *,
        model_dir: Path,
        sample_rate: int = 48000,
        stride_s: float = 1.5,
        threads: int = 2,
        min_confidence: float = 0.12,
        use_location_filter: bool = True,
        #: Occurrence probability above which a species counts as a local regular.
        common_prior: float = 0.15,
        #: Occurrence probability below which a species is out of range entirely.
        range_threshold: float = 0.03,
        #: Confidence bars for the three plausibility bands.
        threshold_in_range: float = 0.55,
        threshold_uncommon: float = 0.75,
        threshold_out_of_range: float = 0.90,
        max_per_window: int = 5,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.window_spec = WindowSpec(
            stream_kind="audible48",
            sample_rate=sample_rate,
            duration_s=3.0,
            stride_s=stride_s,
            max_delivery_latency_s=30.0,
            priority=20,
        )
        self._threads = threads
        self._min_confidence = min_confidence
        self._use_location_filter = use_location_filter
        self._common_prior = common_prior
        self._range_threshold = range_threshold
        self._thresholds = {
            "in_range": threshold_in_range,
            "uncommon": threshold_uncommon,
            "out_of_range": threshold_out_of_range,
        }
        self._max_per_window = max_per_window

        self._interpreter: object | None = None
        self._input_index = 0
        self._output_index = 0
        self._expected_samples = int(3.0 * sample_rate)
        self._labels: list[str] = []
        self._parsed: list[tuple[str | None, str]] = []
        self._range: _RangeModel | None = None
        self._timezone = UTC
        self._windows = 0
        self._suppressed_out_of_range = 0
        self._species_in_range = 0
        self._week = 0

    # ------------------------------------------------------------------

    def missing_assets(self) -> list[str]:
        return [
            name
            for name in (CLASSIFIER_FILE, RANGE_FILE, LABELS_FILE)
            if not (self.model_dir / name).exists()
        ]

    async def initialise(self, context: DetectorContext) -> None:
        missing = self.missing_assets()
        if missing:
            raise DetectorUnavailable(
                f"BirdNET model assets not installed: {', '.join(missing)} missing from "
                f"{self.model_dir}. Run 'oo models fetch' to download them and accept "
                f"the model licence ({LICENCE_NAME})."
            )

        classifier_path = self.model_dir / CLASSIFIER_FILE
        digest = hashlib.sha256(classifier_path.read_bytes()).hexdigest()
        # Record the exact weights in use. Provenance is part of the contract, so
        # this instance's metadata now names the file it actually loaded rather
        # than the class-level placeholder.
        self.metadata = replace(self.metadata, model_sha256=digest)

        self._interpreter = _load_interpreter(classifier_path, self._threads)
        details_in = self._interpreter.get_input_details()[0]  # type: ignore[attr-defined]
        details_out = self._interpreter.get_output_details()[0]  # type: ignore[attr-defined]
        self._input_index = details_in["index"]
        self._output_index = details_out["index"]
        self._expected_samples = int(details_in["shape"][-1])

        expected_duration = self._expected_samples / self.window_spec.sample_rate
        if abs(expected_duration - self.window_spec.duration_s) > 1e-6:
            # The model, not our configuration, is authoritative about its input.
            log.warning(
                "birdnet.window_override",
                model_samples=self._expected_samples,
                model_duration_s=round(expected_duration, 4),
            )
            self.window_spec = WindowSpec(
                stream_kind=self.window_spec.stream_kind,
                sample_rate=self.window_spec.sample_rate,
                duration_s=expected_duration,
                stride_s=self.window_spec.stride_s,
                max_delivery_latency_s=self.window_spec.max_delivery_latency_s,
                priority=self.window_spec.priority,
            )

        self._labels = (
            (self.model_dir / LABELS_FILE).read_text(encoding="utf-8").splitlines()
        )
        self._labels = [line for line in self._labels if line.strip()]
        self._parsed = [parse_label(entry) for entry in self._labels]
        output_classes = int(details_out["shape"][-1])
        if output_classes != len(self._labels):
            raise DetectorUnavailable(
                f"label/model mismatch: model emits {output_classes} classes but "
                f"{LABELS_FILE} has {len(self._labels)} entries"
            )

        try:
            self._timezone = ZoneInfo(context.timezone)
        except Exception:
            self._timezone = UTC

        if self._use_location_filter and context.latitude is not None and context.longitude is not None:
            self._range = _RangeModel(
                self.model_dir / RANGE_FILE,
                context.latitude,
                context.longitude,
                self._threads,
            )
            log.info(
                "birdnet.range_model_enabled",
                latitude=context.latitude,
                longitude=context.longitude,
            )
        else:
            self._range = None
            log.info(
                "birdnet.range_model_disabled",
                reason="station coordinates not configured"
                if context.latitude is None
                else "disabled by configuration",
            )

        log.info(
            "birdnet.ready",
            labels=len(self._labels),
            samples=self._expected_samples,
            sha256=digest[:12],
        )

    # ------------------------------------------------------------------

    async def analyse(self, window: AudioWindow) -> list[NativeDetection]:
        if self._interpreter is None:
            return []
        samples = np.asarray(window.pcm, dtype=np.float32)
        if samples.shape[0] < self._expected_samples:
            samples = np.pad(samples, (0, self._expected_samples - samples.shape[0]))
        elif samples.shape[0] > self._expected_samples:
            samples = samples[: self._expected_samples]

        self._interpreter.set_tensor(  # type: ignore[attr-defined]
            self._input_index, samples.reshape(1, -1)
        )
        self._interpreter.invoke()  # type: ignore[attr-defined]
        logits = self._interpreter.get_tensor(self._output_index)[0]  # type: ignore[attr-defined]
        # BirdNET emits logits; a sigmoid gives the conventional "confidence".
        confidences = 1.0 / (1.0 + np.exp(-np.clip(logits, -15.0, 15.0)))
        self._windows += 1

        local_time = datetime.fromtimestamp(window.utc_start_ns / 1e9, self._timezone)
        week = birdnet_week(local_time)
        self._week = week
        prior: np.ndarray | None = None
        if self._range is not None:
            prior = self._range.probabilities(week)
            self._species_in_range = int((prior >= self._range_threshold).sum())

        candidates = np.where(confidences >= self._min_confidence)[0]
        # Strongest first, so a per-window cap keeps the best rather than the
        # lowest-indexed species.
        candidates = candidates[np.argsort(-confidences[candidates])]

        detections: list[NativeDetection] = []
        for index in candidates:
            confidence = float(confidences[index])
            occurrence = float(prior[index]) if prior is not None else None
            band, threshold = self._band_for(occurrence)
            if confidence < threshold:
                if band != "in_range":
                    self._suppressed_out_of_range += 1
                continue
            scientific, common = self._parsed[index]
            detections.append(
                NativeDetection(
                    offset_start_s=0.0,
                    offset_end_s=self._expected_samples / window.sample_rate,
                    score=confidence,
                    label=self._labels[index],
                    common_name=common,
                    scientific_name=scientific,
                    rank="species",
                    taxonomic_group="bird",
                    calibrated_probability=None,
                    native_result={
                        "detector": "birdnet-v2.4",
                        "model_id": MODEL_ID,
                        "label": self._labels[index],
                        "label_index": int(index),
                        "logit": float(logits[index]),
                        "confidence": round(confidence, 6),
                        "confidence_definition": "sigmoid(clip(logit, -15, 15))",
                        "week": week,
                        "occurrence_probability": round(occurrence, 6)
                        if occurrence is not None
                        else None,
                        "plausibility_band": band,
                        "threshold_applied": threshold,
                        "range_model_used": prior is not None,
                    },
                )
            )
            if len(detections) >= self._max_per_window:
                break
        return detections

    def _band_for(self, occurrence: float | None) -> tuple[str, float]:
        if occurrence is None:
            # With no range model there is no plausibility information, so apply
            # the in-range bar uniformly rather than inventing a prior.
            return "unfiltered", self._thresholds["in_range"]
        if occurrence >= self._common_prior:
            return "in_range", self._thresholds["in_range"]
        if occurrence >= self._range_threshold:
            return "uncommon", self._thresholds["uncommon"]
        return "out_of_range", self._thresholds["out_of_range"]

    async def health(self) -> DetectorHealth:
        if self._interpreter is None:
            missing = self.missing_assets()
            return DetectorHealth(
                available=False,
                state="unavailable",
                detail=f"model assets missing: {', '.join(missing)}" if missing else "not initialised",
            )
        detail = f"{len(self._labels)} labels, week {self._week}"
        if self._range is not None:
            detail += f", {self._species_in_range} species plausible here this week"
        else:
            detail += ", range model off (no station coordinates)"
        if self._suppressed_out_of_range:
            detail += f", {self._suppressed_out_of_range} suppressed as implausible"
        return DetectorHealth(available=True, state="ok", detail=detail)

    async def shutdown(self) -> None:
        self._interpreter = None
        self._range = None
