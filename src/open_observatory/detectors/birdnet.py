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

ADR-032: a species the range model puts at or near zero for this location and
week (below ``plausibility_floor``) is suppressed outright, at any score --
BirdNET scores are not calibrated probabilities, so no score is strong enough
evidence to overrule "essentially impossible here". A species the range model
is loaded but silent about (``occurrence is None`` with the model present)
gets the strictest bar, not the easiest one -- that is not the same situation
as no range model being loaded at all, and `_band_for` takes both facts as
separate arguments so it can tell them apart. See
``docs/detectors/DETECTOR_STRATEGY.md``'s "Known limitation" section for the
measured owls this was built to fix.

Inference uses ``ai_edge_litert`` (the maintained successor to
``tflite-runtime``, which has no cp312 aarch64 wheel), falling back to
``tflite_runtime`` where that is what is installed.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
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
from . import birdnet_classes
from .base import DetectorContext, DetectorUnavailable
from .near_miss import NearMissLedger

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
            from tflite_runtime.interpreter import Interpreter
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


def band_for(
    occurrence: float | None,
    *,
    range_model_loaded: bool,
    plausibility_floor: float,
    common_prior: float,
    range_threshold: float,
    threshold_in_range: float,
    threshold_uncommon: float,
    threshold_out_of_range: float,
    non_taxonomic: bool = False,
) -> tuple[str, float]:
    """Sort a candidate into a plausibility band and its confidence bar (ADR-032).

    A free function, not just a method, so both :class:`BirdNetDetector` (the
    live path) and ``oo detections reconcile-plausibility`` (the historical
    repair CLI, ``cli.py``) apply exactly one definition of "implausible" --
    the repair command has no ``BirdNetDetector`` instance to call a method
    on, since it only needs the range model, not the classifier.

    ``range_model_loaded`` and ``occurrence`` carry genuinely different
    information and must not be collapsed into one. ``range_model_loaded``
    says whether a prior exists to consult at all; ``occurrence`` is what that
    prior said for this specific species, once it exists. The pre-ADR-032
    logic took only ``occurrence`` and treated ``None`` as "no range model"
    unconditionally, which meant a range model that was loaded but silent
    about one species was indistinguishable from a range model that was never
    loaded at all -- and got the *easiest* threshold as a result (defect (b)
    in DETECTOR_STRATEGY.md's "Known limitation" section).

    ``non_taxonomic`` (ADR-049) is checked before any of that, and is a
    statement about the *question*, not about the answer. Eleven of BirdNET's
    output classes are sound categories rather than species
    (``birdnet_classes.NON_TAXONOMIC_LABELS``), and the range model has no
    meaningful prior for them: asked about "Engine" it returns 4e-06 at this
    station, which is not "engines are essentially absent from this garden",
    it is "a car is not a taxon with a distribution". Feeding that number to
    the plausibility floor would suppress a correct detection of a passing car
    at any score, and — measured on the live database on 2026-08-09 — would
    have withdrawn 91 of the 114 rows the repair pass proposed to flag. So
    these classes are exempted from the prior entirely and judged on score
    alone, at the ordinary in-range bar.
    """
    if non_taxonomic:
        # No prior is *available* here, as distinct from `no_prior` below,
        # where a prior exists in principle and this model happens to be
        # silent. The strictest-bar reasoning that justifies `no_prior` does
        # not transfer: it is a hedge against a species we cannot place, and
        # there is no species to place.
        return birdnet_classes.NON_TAXONOMIC_BAND, threshold_in_range
    if not range_model_loaded:
        # No range model at all: there is no plausibility information to act
        # on, so apply the in-range bar uniformly rather than inventing a
        # prior. This is the pre-ADR-032 behaviour, preserved.
        return "unfiltered", threshold_in_range
    if occurrence is None:
        # The range model is loaded but has nothing to say about this
        # species. That is not an endorsement, so it must not receive the
        # easiest bar -- give it the strictest one available instead.
        return "no_prior", threshold_out_of_range
    if occurrence <= plausibility_floor:
        # The range model is saying "essentially impossible here this week".
        # BirdNET scores are not calibrated probabilities, so no score is
        # evidence strong enough to overrule that -- this is a different
        # operation from raising the bar (defect (a)); nothing clears an
        # infinite bar.
        return "implausible", math.inf
    if occurrence >= common_prior:
        return "in_range", threshold_in_range
    if occurrence >= range_threshold:
        return "uncommon", threshold_uncommon
    return "out_of_range", threshold_out_of_range


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
        assert self._probabilities is not None  # set on first use above
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
        #: Occurrence probability at or below which a species is suppressed
        #: outright, at any score -- see the module docstring's "Known
        #: limitation" write-up in DETECTOR_STRATEGY.md and ADR-032. Measured
        #: on the live station: implausible North American owls sit at
        #: 8e-06-1e-05; a genuine, seasonally-uncommon Tawny Owl sits at
        #: 0.019253. The default (5e-4) sits two orders of magnitude below the
        #: Tawny Owl and well above the owls, with margin on both sides.
        plausibility_floor: float = 0.0005,
        max_per_window: int = 5,
        #: ADR-052. How many individual rejected candidates to keep in the
        #: in-memory near-miss ring. 0 keeps the per-band histograms and the
        #: per-species tally but stops recording individual rows.
        near_miss_ring: int = 200,
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
        self._plausibility_floor = plausibility_floor
        self._thresholds = {
            "in_range": threshold_in_range,
            "uncommon": threshold_uncommon,
            "out_of_range": threshold_out_of_range,
        }
        self._max_per_window = max_per_window

        # `Any`: this is whichever `Interpreter` the optional import found
        # (ai-edge-litert or tflite-runtime), neither of which can be named
        # in an annotation on a machine where the other one is installed.
        self._interpreter: Any = None
        self._input_index = 0
        self._output_index = 0
        self._expected_samples = int(3.0 * sample_rate)
        self._labels: list[str] = []
        self._parsed: list[tuple[str | None, str]] = []
        self._range: _RangeModel | None = None
        self._timezone: tzinfo = UTC
        self._windows = 0
        #: Candidates that fell below the strict ``out_of_range`` bar. Kept
        #: separate from ``_suppressed_uncommon`` -- the old single counter
        #: conflated the two, so it was not a count of out-of-range species
        #: (DETECTOR_STRATEGY.md "Also" note).
        self._suppressed_out_of_range = 0
        #: Candidates that fell below the ``uncommon`` bar.
        self._suppressed_uncommon = 0
        #: Candidates rejected outright because the range model puts them at
        #: or below ``plausibility_floor`` for this location and week -- no
        #: score admits these (defect (a)).
        self._suppressed_implausible_prior = 0
        #: Candidates the range model is loaded but silent about, so they
        #: faced the strict bar rather than the easy one (defect (b)).
        self._suppressed_no_prior = 0
        #: Candidates admitted as sound categories rather than species
        #: (ADR-049). Not a suppression count -- these were *kept*, and the
        #: counter exists so an operator can see how much of a noisy site's
        #: BirdNET output is traffic and machinery rather than birds.
        self._non_taxonomic = 0
        self._species_in_range = 0
        self._week = 0
        #: ADR-052. What was proposed and refused, so a human can tune these
        #: bars on evidence rather than on four totals. Always constructed:
        #: the counting part costs a handful of integer increments per
        #: candidate (measured at ~1.4 us per rejection, against BirdNET's own
        #: 72 ms per window on the live Pi), so there is no version of this
        #: worth switching off by default -- a diagnostic you must remember to
        #: enable before the thing you want to diagnose happens is not there
        #: when you need it. The ring depth is the operator's knob.
        self._near_misses = NearMissLedger(capacity=near_miss_ring)

    # ------------------------------------------------------------------

    def missing_assets(self) -> list[str]:
        return [
            name
            for name in (CLASSIFIER_FILE, RANGE_FILE, LABELS_FILE)
            if not (self.model_dir / name).exists()
        ]

    def retune(
        self,
        *,
        min_confidence: float | None = None,
        plausibility_floor: float | None = None,
        common_prior: float | None = None,
        range_threshold: float | None = None,
        threshold_in_range: float | None = None,
        threshold_uncommon: float | None = None,
        threshold_out_of_range: float | None = None,
        max_per_window: int | None = None,
        near_miss_ring: int | None = None,
    ) -> None:
        """Change the confidence and plausibility bars on a running detector.

        None of these touch the interpreter, the labels, the range model or the
        cached per-week occurrence vector -- they are applied *after* inference,
        when a raw score is turned into (or refused as) a detection. So this is
        a genuinely live change: the next window is scored by the old model and
        judged by the new bars, which is exactly the intent.

        ``use_location_filter`` is deliberately absent. Turning the range model
        on or off changes which *model files* are loaded and what the detector
        declares about itself, and is restart-pinned in ``site_settings.py``.
        """
        if min_confidence is not None:
            self._min_confidence = float(min_confidence)
        if plausibility_floor is not None:
            self._plausibility_floor = float(plausibility_floor)
        if common_prior is not None:
            self._common_prior = float(common_prior)
        if range_threshold is not None:
            self._range_threshold = float(range_threshold)
        if threshold_in_range is not None:
            self._thresholds["in_range"] = float(threshold_in_range)
        if threshold_uncommon is not None:
            self._thresholds["uncommon"] = float(threshold_uncommon)
        if threshold_out_of_range is not None:
            self._thresholds["out_of_range"] = float(threshold_out_of_range)
        if max_per_window is not None:
            self._max_per_window = int(max_per_window)
        if near_miss_ring is not None:
            # Rebinds a bounded deque; keeps the cumulative histograms and the
            # species tally, which is what an operator retuning mid-session
            # wants -- the comparison across a threshold change is the point.
            self._near_misses.resize(int(near_miss_ring))

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
        details_in = self._interpreter.get_input_details()[0]
        details_out = self._interpreter.get_output_details()[0]
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

        # Declared `tzinfo` at its first assignment: the happy path below stores
        # a `ZoneInfo` and only the fallback stores `datetime.UTC`, so inferring
        # the attribute's type from the fallback made the real assignment the error.
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

        self._interpreter.set_tensor(
            self._input_index, samples.reshape(1, -1)
        )
        self._interpreter.invoke()
        logits = self._interpreter.get_tensor(self._output_index)[0]
        # BirdNET emits logits; a sigmoid gives the conventional "confidence".
        confidences = 1.0 / (1.0 + np.exp(-np.clip(logits, -15.0, 15.0)))
        self._windows += 1

        local_time = datetime.fromtimestamp(window.utc_start_ns / 1e9, self._timezone)
        week = birdnet_week(local_time)
        self._week = week
        prior: np.ndarray | None = None
        range_model_loaded = self._range is not None
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
            scientific, common = self._parsed[index]
            # ADR-049. Eleven output classes are sound categories, not species.
            # Decided from the label, before the range model is consulted at
            # all, because the prior for such a class is not a weak signal --
            # it is a category error.
            sound_kind = birdnet_classes.kind_of(scientific)
            non_taxonomic = sound_kind is not None
            occurrence: float | None = None
            if prior is not None and not non_taxonomic:
                raw = float(prior[index])
                # A loaded range model that is silent (NaN) about one species
                # is not the same as no range model at all -- both are
                # "missing", so both funnel through the same _band_for branch.
                occurrence = None if math.isnan(raw) else raw
            band, threshold = self._band_for(
                occurrence,
                range_model_loaded=range_model_loaded,
                non_taxonomic=non_taxonomic,
            )
            if confidence < threshold:
                self._count_suppressed(band)
                # ADR-052. Every rejection, in every band -- deliberately
                # wider than `_count_suppressed`, which by ADR-032's design
                # counts only the plausibility bands. A candidate at 0.54
                # against the 0.55 `in_range` bar is counted nowhere else, and
                # it is the single most common thing an operator is actually
                # asking about when they can hear a bird and see nothing.
                self._near_misses.record_rejected(
                    at_ns=window.utc_start_ns,
                    label_index=int(index),
                    common_name=common,
                    scientific_name=None if non_taxonomic else scientific,
                    score=confidence,
                    occurrence=occurrence,
                    band=band,
                    threshold=threshold,
                )
                continue
            self._near_misses.record_admitted(
                band=band, label_index=int(index), score=confidence
            )
            native_result: dict[str, object] = {
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
                "range_model_used": prior is not None and not non_taxonomic,
            }
            if non_taxonomic:
                # Recorded on the row so a consumer, an export or a later
                # repair pass can tell "this was never a species claim" from
                # "this is a species claim we have not yet judged", without
                # having to hold the label catalogue itself.
                native_result["sound_kind"] = sound_kind
                self._non_taxonomic += 1
            detections.append(
                NativeDetection(
                    offset_start_s=0.0,
                    offset_end_s=self._expected_samples / window.sample_rate,
                    score=confidence,
                    label=self._labels[index],
                    common_name=common,
                    # A sound category has no binomial and no rank. The
                    # scientific field of "Engine_Engine" is the word "Engine",
                    # which is not a name for anything; persisting it as
                    # `scientific_name` is what let `_canonical_taxon_id` mint
                    # `sci:engine`. The common name is kept: "Engine" is an
                    # honest description of what was heard, and the operator's
                    # history view is better for having it.
                    scientific_name=None if non_taxonomic else scientific,
                    rank=None if non_taxonomic else "species",
                    taxonomic_group=(
                        birdnet_classes.NON_TAXONOMIC_GROUP if non_taxonomic else "bird"
                    ),
                    calibrated_probability=None,
                    native_result=native_result,
                )
            )
            if len(detections) >= self._max_per_window:
                break
        return detections

    def _band_for(
        self,
        occurrence: float | None,
        *,
        range_model_loaded: bool,
        non_taxonomic: bool = False,
    ) -> tuple[str, float]:
        return band_for(
            occurrence,
            range_model_loaded=range_model_loaded,
            non_taxonomic=non_taxonomic,
            plausibility_floor=self._plausibility_floor,
            common_prior=self._common_prior,
            range_threshold=self._range_threshold,
            threshold_in_range=self._thresholds["in_range"],
            threshold_uncommon=self._thresholds["uncommon"],
            threshold_out_of_range=self._thresholds["out_of_range"],
        )

    def _count_suppressed(self, band: str) -> None:
        if band == "implausible":
            self._suppressed_implausible_prior += 1
        elif band == "no_prior":
            self._suppressed_no_prior += 1
        elif band == "uncommon":
            self._suppressed_uncommon += 1
        elif band == "out_of_range":
            self._suppressed_out_of_range += 1
        # "in_range" / "unfiltered" candidates that fail their (low) bar are
        # ordinary low-confidence rejections, not a plausibility judgement --
        # not counted here, matching the old counter's scope.

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
        # Split by reason, not a single misleading total (DETECTOR_STRATEGY.md
        # "Also" note: the old counter included ``uncommon`` candidates and
        # was not a count of suppressed out-of-range species).
        if self._suppressed_implausible_prior:
            detail += f", {self._suppressed_implausible_prior} suppressed (near-zero prior)"
        if self._suppressed_no_prior:
            detail += f", {self._suppressed_no_prior} suppressed (no prior for species)"
        if self._suppressed_out_of_range:
            detail += f", {self._suppressed_out_of_range} rejected (out-of-range bar not cleared)"
        if self._suppressed_uncommon:
            detail += f", {self._suppressed_uncommon} rejected (uncommon bar not cleared)"
        if self._non_taxonomic:
            detail += f", {self._non_taxonomic} non-biological sounds (not species claims)"
        return DetectorHealth(available=True, state="ok", detail=detail)

    def plausibility_snapshot(self) -> dict[str, int]:
        """Per-reason suppression counts, for `api/metrics.py`'s Prometheus export.

        Deliberately a plain method rather than a `DetectorPlugin` protocol
        member (see `detectors/deferred.py`'s duck-typing precedent for
        `self.deferred`): the other two shipped detectors have no equivalent
        concept and should not need to grow a no-op stub to keep conforming.

        Deliberately still only suppressions: `api/metrics.py` labels every key
        of this dict as a `reason` on `oo_birdnet_suppressed_total`, so a
        non-suppression count added here would be exported as a suppression.
        The ADR-049 non-biological count has its own accessor and its own
        metric for exactly that reason.
        """
        return {
            "suppressed_implausible_prior": self._suppressed_implausible_prior,
            "suppressed_no_prior": self._suppressed_no_prior,
            "suppressed_out_of_range": self._suppressed_out_of_range,
            "suppressed_uncommon": self._suppressed_uncommon,
        }

    def near_miss_snapshot(
        self, *, limit: int = 50, species_limit: int = 40
    ) -> dict[str, object]:
        """ADR-052: what was proposed and refused, for `GET
        /api/v1/detectors/near-misses` and the diagnostic UI.

        Duck-typed for the same reason `plausibility_snapshot` is (see its
        docstring): only a detector with plausibility bands has anything to
        say here, and the other two shipped detectors should not grow a no-op
        stub to keep conforming.

        The bars in force are passed in from *this* instance rather than read
        from `Settings`, so the payload's thresholds are the ones that
        actually judged the candidates it is showing. A settings write and a
        detector retune are two steps (ADR-048), and reporting the saved value
        next to rejections made under the old one would be exactly the
        saved-vs-in-force dishonesty that ADR forbids.
        """
        thresholds = {
            "in_range": self._thresholds["in_range"],
            "unfiltered": self._thresholds["in_range"],
            "non_biological": self._thresholds["in_range"],
            "uncommon": self._thresholds["uncommon"],
            "out_of_range": self._thresholds["out_of_range"],
            "no_prior": self._thresholds["out_of_range"],
            "implausible": math.inf,
        }
        snapshot = self._near_misses.snapshot(
            thresholds=thresholds, limit=limit, species_limit=species_limit
        )
        snapshot["min_confidence"] = self._min_confidence
        snapshot["plausibility_floor"] = self._plausibility_floor
        snapshot["week"] = self._week
        snapshot["windows_analysed"] = self._windows
        snapshot["range_model_loaded"] = self._range is not None
        # Stated on the payload rather than left to a reader's assumption:
        # nothing below `min_confidence` is ever a candidate, so the
        # histogram's lowest bins are structurally empty and are not evidence
        # that the model is quiet down there.
        snapshot["note"] = (
            "Candidates only. A raw score below min_confidence "
            f"({self._min_confidence}) is never proposed and appears nowhere here. "
            "Scores are model outputs, not probabilities. No audio is retained "
            "for a rejected candidate and nothing here is persisted."
        )
        return snapshot

    def non_taxonomic_admitted(self) -> int:
        """Candidates admitted as sound categories rather than species (ADR-049)."""
        return self._non_taxonomic

    async def shutdown(self) -> None:
        self._interpreter = None
        self._range = None


def load_range_model_for_repair(
    model_dir: Path, latitude: float, longitude: float, *, threads: int = 1
) -> tuple[list[str], list[tuple[str | None, str]], _RangeModel]:
    """Load labels and the range model only, for the ``oo detections

    reconcile-plausibility`` repair CLI (``cli.py``). Deliberately skips the
    classifier: the repair command re-evaluates *stored* detections against
    the current range model and floor, it does not re-run inference, so the
    much larger classifier weights are never needed. Raises
    :class:`DetectorUnavailable` if either the range model or the labels file
    is missing -- the same ADR-006 degrade-not-crash contract
    :meth:`BirdNetDetector.initialise` uses.
    """
    missing = [name for name in (RANGE_FILE, LABELS_FILE) if not (model_dir / name).exists()]
    if missing:
        raise DetectorUnavailable(
            f"BirdNET model assets not installed: {', '.join(missing)} missing from "
            f"{model_dir}. Run 'oo models fetch' to download them."
        )
    labels = [
        line
        for line in (model_dir / LABELS_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    parsed = [parse_label(entry) for entry in labels]
    range_model = _RangeModel(model_dir / RANGE_FILE, latitude, longitude, threads)
    return labels, parsed, range_model
