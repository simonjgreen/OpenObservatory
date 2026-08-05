"""Ultrasonic bat-pass detector, running on the native high-rate stream.

This exists because the derived 48 kHz stream is band-limited to 24 kHz and
essentially every UK bat echolocates between 20 and 120 kHz — so on the audible
stream there is nothing to find, and a detector that pretended otherwise would be
lying. The AudioMoth on this station captures at 384 kHz, giving a 192 kHz
Nyquist, which covers every UK species.

The method is the band-energy/pulse-train approach a hardware heterodyne detector
uses: find short high-SNR pulses in the ultrasonic band, measure each pulse's
peak frequency and duration, and group pulse trains into "passes". That produces
real, useful activity data — passes per night, peak-frequency distribution —
without a PyTorch install on a Pi.

**It does not identify species.** Peak frequency yields at best a coarse group
hint, which is reported as a hint and stored as such. BatDetect2 would be a
drop-in replacement for :meth:`_summarise` once benchmarked on this hardware
(Milestone 5); this detector is deliberately not called a classifier.

Derived from the ultrasonic detector prototyped in the OutdoorAcousticEvents
project, reworked onto this project's window contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import numpy as np

from ..audio.contracts import (
    NS_PER_S,
    AudioWindow,
    DetectorHealth,
    DetectorMetadata,
    NativeDetection,
    WindowSpec,
)
from .base import DetectorContext, DetectorUnavailable

# Note: no import of schedule.py, even under TYPE_CHECKING — it may not exist
# yet or may be mid-edit by another agent. `schedule` is accepted duck-typed
# via _ScheduleLike below; any object with a matching is_active works,
# including the real NightSchedule once it lands.

PLUGIN_VERSION = "1.0.0"


@runtime_checkable
class _ScheduleLike(Protocol):
    """Anything with an ``is_active(now: datetime) -> bool`` method.

    Deliberately duck-typed rather than importing ``NightSchedule`` directly,
    so this detector does not depend on that module's existence or shape.
    """

    def is_active(self, now: datetime) -> bool: ...


@dataclass(frozen=True, slots=True)
class _Band:
    low: float
    high: float
    #: The existing, longer group string. Unchanged in meaning; several
    #: callers already depend on its wording.
    group: str
    #: Short display name for a candidate title, e.g. "common pipistrelle".
    short_name: str
    #: Set only where the band is genuinely confusable with something else.
    ambiguity: str | None = None


#: Coarse peak-frequency groups for UK species. A hint on top of a pass, never
#: an identification — several of these bands overlap between species. The
#: 17-26 kHz range is split so the bush-cricket ambiguity note attaches only
#: to the lower half, where it actually applies.
FREQUENCY_HINTS: tuple[_Band, ...] = (
    _Band(
        17_000,
        21_000,
        "Nyctalus / Eptesicus group (noctule, serotine)",
        "noctule / serotine",
        "may be a bush-cricket",
    ),
    _Band(
        21_000,
        26_000,
        "Nyctalus / Eptesicus group (noctule, serotine)",
        "noctule / serotine",
    ),
    _Band(26_000, 38_000, "Myotis / Barbastelle group", "Myotis / barbastelle"),
    _Band(
        38_000,
        50_000,
        "Pipistrellus pipistrellus group (common pipistrelle)",
        "common pipistrelle",
    ),
    _Band(
        50_000,
        62_000,
        "Pipistrellus pygmaeus group (soprano pipistrelle)",
        "soprano pipistrelle",
    ),
    _Band(
        62_000,
        90_000,
        "Rhinolophus ferrumequinum / high-frequency group",
        "greater horseshoe",
    ),
    _Band(
        90_000,
        130_000,
        "Rhinolophus hipposideros group (lesser horseshoe)",
        "lesser horseshoe",
    ),
)


def frequency_hint(hz: float) -> str | None:
    for band in FREQUENCY_HINTS:
        if band.low <= hz < band.high:
            return band.group
    return None


def frequency_candidate(hz: float) -> tuple[str | None, str | None]:
    """Return (short_candidate_name, ambiguity_note); either may be None."""
    for band in FREQUENCY_HINTS:
        if band.low <= hz < band.high:
            return band.short_name, band.ambiguity
    return None, None


def _interpolated_peak_hz(
    column: np.ndarray, peak_bin: int, band_freqs: np.ndarray
) -> float:
    """Estimate a pulse's peak frequency to better than one FFT bin.

    The pulse-detection FFT is short by design — it has to resolve a 1.5 ms call —
    which at 384 kHz leaves 3 kHz bins. Reporting the bin centre quantises every
    peak to a multiple of 3 kHz, and the frequency bands used to suggest a
    candidate species have edges that fall *between* bins: 38 kHz, the boundary
    between the Myotis and common pipistrelle groups, sits between the 36 and
    39 kHz bins. A bat calling at 37.5 kHz would be assigned to either group
    depending on noise.

    Fitting a parabola through the peak bin and its two neighbours in the log
    domain recovers the true maximum to a fraction of a bin, which is the standard
    correction for this and costs three array lookups. It does not make the
    measurement precise enough to identify a species — nothing here does — but it
    stops the band assignment being decided by quantisation.
    """
    last = column.shape[0] - 1
    if peak_bin <= 0 or peak_bin >= last:
        return float(band_freqs[peak_bin])
    alpha, beta, gamma = (
        float(np.log(column[peak_bin - 1] + 1e-20)),
        float(np.log(column[peak_bin] + 1e-20)),
        float(np.log(column[peak_bin + 1] + 1e-20)),
    )
    denominator = alpha - 2.0 * beta + gamma
    if denominator == 0.0:
        return float(band_freqs[peak_bin])
    # Offset in bins, bounded to the neighbouring half-bins: a parabola fitted to
    # noise can otherwise place the vertex arbitrarily far away.
    delta = 0.5 * (alpha - gamma) / denominator
    delta = max(-0.5, min(0.5, delta))
    bin_width = float(band_freqs[1] - band_freqs[0]) if band_freqs.shape[0] > 1 else 0.0
    return float(band_freqs[peak_bin] + delta * bin_width)


@dataclass(frozen=True, slots=True)
class Pulse:
    offset_s: float
    duration_s: float
    peak_hz: float
    snr_db: float


class UltrasonicDetector:
    """Detects bat passes as trains of short ultrasonic pulses."""

    metadata = DetectorMetadata(
        plugin_id="ultrasonic-pass-v1",
        plugin_version=PLUGIN_VERSION,
        model_id="band-energy-pulse-train",
        model_version=PLUGIN_VERSION,
        model_sha256=None,
        taxonomy_version=None,
        licence_name="Apache-2.0",
        licence_url=None,
        claim=(
            "Detects trains of short ultrasonic pulses consistent with bat "
            "echolocation and reports pulse count, timing and peak frequency. "
            "Peak frequency gives a coarse group hint only; this is not a "
            "species identification."
        ),
        resource_class="moderate",
        calibrated=False,
        external_network="none",
    )

    #: Below this native rate there is no ultrasonic band to inspect.
    MIN_SAMPLE_RATE = 96_000

    def __init__(
        self,
        *,
        native_sample_rate: int,
        window_s: float = 2.0,
        stride_s: float = 2.0,
        band_hz: tuple[float, float] = (15_000.0, 125_000.0),
        min_snr_db: float = 12.0,
        min_pulse_ms: float = 1.5,
        max_pulse_ms: float = 40.0,
        merge_gap_ms: float = 2.0,
        pass_gap_s: float = 1.5,
        min_pulses_per_pass: int = 3,
        buzz_max_interval_ms: float = 12.0,
        buzz_min_pulses: int = 5,
        buzz_interval_ratio: float = 0.4,
        schedule: _ScheduleLike | None = None,
    ) -> None:
        self.native_sample_rate = native_sample_rate
        self.window_spec = WindowSpec(
            stream_kind="native",
            sample_rate=native_sample_rate,
            duration_s=window_s,
            stride_s=stride_s,
            # Bats are a night-time, lower-priority load; tolerate more lag than
            # the audible path without dropping work.
            max_delivery_latency_s=60.0,
            priority=50,
        )
        self._band = band_hz
        self._min_snr_db = min_snr_db
        self._min_pulse_s = min_pulse_ms / 1000.0
        self._max_pulse_s = max_pulse_ms / 1000.0
        self._merge_gap_s = merge_gap_ms / 1000.0
        self._pass_gap_s = pass_gap_s
        self._min_pulses = min_pulses_per_pass
        self._buzz_max_interval_ms = buzz_max_interval_ms
        self._buzz_min_pulses = buzz_min_pulses
        self._buzz_interval_ratio = buzz_interval_ratio
        self._schedule = schedule
        self._noise_floor_db: float | None = None
        self.pulses_found = 0
        self.passes_found = 0
        self._blocks = 0
        self._gated_windows = 0

    async def initialise(self, context: DetectorContext) -> None:
        if self.native_sample_rate < self.MIN_SAMPLE_RATE:
            raise DetectorUnavailable(
                f"native stream is {self.native_sample_rate} Hz; ultrasonic detection "
                f"needs at least {self.MIN_SAMPLE_RATE} Hz to see above 24 kHz"
            )
        self._noise_floor_db = None

    async def analyse(self, window: AudioWindow) -> list[NativeDetection]:
        if self._schedule is not None:
            # Use the window's own UTC time, not wall-clock now, so replayed
            # audio is gated the same way it would have been live, and so this
            # early return precedes any FFT work.
            window_utc = datetime.fromtimestamp(window.utc_start_ns / NS_PER_S, tz=UTC)
            if not self._schedule.is_active(window_utc):
                self._gated_windows += 1
                return []
        pcm = np.asarray(window.pcm, dtype=np.float32)
        rate = window.sample_rate
        self._blocks += 1
        pulses = self._find_pulses(pcm, rate)
        self.pulses_found += len(pulses)
        if len(pulses) < self._min_pulses:
            return []

        # Split the pulse list into passes at gaps longer than pass_gap_s.
        detections: list[NativeDetection] = []
        group: list[Pulse] = []
        for pulse in pulses:
            if group and pulse.offset_s - group[-1].offset_s > self._pass_gap_s:
                if len(group) >= self._min_pulses:
                    detections.append(self._summarise(group))
                group = []
            group.append(pulse)
        if len(group) >= self._min_pulses:
            detections.append(self._summarise(group))
        self.passes_found += len(detections)
        return detections

    def _find_pulses(self, pcm: np.ndarray, rate: int) -> list[Pulse]:
        # ~0.5 ms time resolution, enough to resolve individual echolocation calls.
        nperseg = max(64, 1 << int(np.log2(max(64, rate // 2000))))
        hop = nperseg // 2
        if pcm.shape[0] < nperseg:
            return []
        count = 1 + (pcm.shape[0] - nperseg) // hop
        indices = np.arange(nperseg)[None, :] + hop * np.arange(count)[:, None]
        frames = pcm[indices] * np.hanning(nperseg).astype(np.float32)
        power = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).T
        freqs = np.fft.rfftfreq(nperseg, 1.0 / rate)
        times = (np.arange(count) * hop + nperseg / 2) / rate

        mask = (freqs >= self._band[0]) & (freqs <= min(self._band[1], rate / 2 - 1))
        if not mask.any():
            return []
        band_power = power[mask, :]
        band_freqs = freqs[mask]
        energy_db = 10.0 * np.log10(band_power.sum(axis=0) + 1e-20)

        block_floor = float(np.median(energy_db))
        floor = (
            block_floor
            if self._noise_floor_db is None
            else 0.9 * self._noise_floor_db + 0.1 * block_floor
        )
        self._noise_floor_db = floor

        above = energy_db > floor + self._min_snr_db
        bin_dt = float(times[1] - times[0]) if times.size > 1 else hop / rate

        pulses: list[Pulse] = []
        start: int | None = None
        for index, flag in enumerate(above):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                pulses.append(
                    self._make_pulse(
                        start, index, times, bin_dt, band_power, band_freqs, energy_db, floor
                    )
                )
                start = None
        if start is not None:
            pulses.append(
                self._make_pulse(
                    start, len(above), times, bin_dt, band_power, band_freqs, energy_db, floor
                )
            )
        pulses = self._merge_fragments(pulses)
        return [p for p in pulses if self._min_pulse_s <= p.duration_s <= self._max_pulse_s]

    def _merge_fragments(self, pulses: list[Pulse]) -> list[Pulse]:
        """Rejoin threshold crossings that belong to one echolocation call.

        A bat call is a single frequency sweep lasting a few milliseconds, and its
        envelope is not smooth: the band energy can dip below threshold partway
        through, so one call arrives here as several "pulses" a millisecond or two
        apart. Left alone that inflates the pulse count, and because a pass needs
        only ``min_pulses_per_pass`` crossings, the fragments of a *single* call can
        manufacture a bat pass on their own. It also destroys the interval series
        that feeding-buzz detection depends on, which measures call-to-call timing,
        not within-call timing.

        The threshold is measured **onset to onset**, not edge to edge, and that
        distinction matters: during a feeding buzz the calls shorten as they speed
        up, so an edge-to-edge gap conflates "two calls close together" with "one
        long call", and a buzz whose 4 ms calls arrive every 5 ms would be merged
        into a single blob — hiding precisely what buzz detection exists to find.
        Onset spacing is unaffected by call duration.

        The default is deliberately conservative. Fragments of one sweep have onsets
        a millisecond or two apart; the calls of a feeding buzz are 5 ms apart or
        more. Merging below 2 ms leaves a wide margin against the fastest buzz.
        A merged pulse is also never allowed to exceed ``max_pulse_ms``, since
        beyond that it is no longer a plausible single call.
        """
        if self._merge_gap_s <= 0.0 or not pulses:
            return pulses
        merged: list[Pulse] = [pulses[0]]
        # Measured from the previous *fragment's* onset, not from the start of the
        # group being accumulated: a call breaking into three pieces would
        # otherwise stop merging once the group grew past the threshold.
        last_onset = pulses[0].offset_s
        for pulse in pulses[1:]:
            previous = merged[-1]
            onset_gap = pulse.offset_s - last_onset
            span = (pulse.offset_s + pulse.duration_s) - previous.offset_s
            last_onset = pulse.offset_s
            if onset_gap < self._merge_gap_s and span <= self._max_pulse_s:
                merged[-1] = Pulse(
                    offset_s=previous.offset_s,
                    duration_s=(pulse.offset_s + pulse.duration_s) - previous.offset_s,
                    # Keep the louder fragment's peak: the quiet tail of a sweep is a
                    # worse estimate of the call's frequency than its strongest part.
                    peak_hz=(
                        previous.peak_hz if previous.snr_db >= pulse.snr_db else pulse.peak_hz
                    ),
                    snr_db=max(previous.snr_db, pulse.snr_db),
                )
            else:
                merged.append(pulse)
        return merged

    @staticmethod
    def _make_pulse(
        start: int,
        end: int,
        times: np.ndarray,
        bin_dt: float,
        band_power: np.ndarray,
        band_freqs: np.ndarray,
        energy_db: np.ndarray,
        floor: float,
    ) -> Pulse:
        segment = band_power[:, start:end]
        peak_bin, peak_col = (
            int(v) for v in np.unravel_index(int(np.argmax(segment)), segment.shape)
        )
        return Pulse(
            offset_s=float(times[start]),
            duration_s=(end - start) * bin_dt,
            peak_hz=_interpolated_peak_hz(segment[:, peak_col], peak_bin, band_freqs),
            snr_db=float(energy_db[start:end].max() - floor),
        )

    def _find_buzz(
        self, intervals_ms: np.ndarray, overall_median_ms: float
    ) -> tuple[int, int] | None:
        """Return (start, end) pulse indices [start, end) of the buzz run, if any.

        A run is a maximal stretch of consecutive intervals each below
        ``buzz_max_interval_ms``. It qualifies as a buzz only if it is at
        least ``buzz_min_pulses`` intervals long AND its own median is below
        ``buzz_interval_ratio`` of the whole train's median — the ratio test
        is what rules out a bat that simply called fast throughout the pass,
        rather than collapsing into a terminal buzz. Among qualifying runs,
        the one with the lowest (strongest) median interval wins.
        """
        n = intervals_ms.shape[0]
        best: tuple[int, int] | None = None
        best_median = np.inf
        i = 0
        while i < n:
            if intervals_ms[i] < self._buzz_max_interval_ms:
                j = i
                while j < n and intervals_ms[j] < self._buzz_max_interval_ms:
                    j += 1
                run_len = j - i
                if run_len >= self._buzz_min_pulses:
                    run_median = float(np.median(intervals_ms[i:j]))
                    if run_median < self._buzz_interval_ratio * overall_median_ms and (
                        run_median < best_median
                    ):
                        best = (i, j)
                        best_median = run_median
                i = j
            else:
                i += 1
        return best

    def _summarise(self, pulses: list[Pulse]) -> NativeDetection:
        peaks = np.array([p.peak_hz for p in pulses])
        starts = np.array([p.offset_s for p in pulses])
        intervals = np.diff(starts)
        median_peak = float(np.median(peaks))
        peak_snr = max(p.snr_db for p in pulses)
        hint = frequency_hint(median_peak)
        candidate_name, candidate_ambiguity = frequency_candidate(median_peak)

        min_interval_ms = round(float(intervals.min()) * 1000.0, 2) if intervals.size else None

        has_buzz = False
        buzz_offset_s: float | None = None
        buzz_min_interval_ms: float | None = None
        buzz_pulse_count: int | None = None
        if intervals.size and intervals.size >= self._buzz_min_pulses:
            intervals_ms = intervals * 1000.0
            overall_median_ms = float(np.median(intervals_ms))
            run = self._find_buzz(intervals_ms, overall_median_ms)
            if run is not None:
                start_idx, end_idx = run
                has_buzz = True
                buzz_offset_s = float(pulses[start_idx].offset_s - pulses[0].offset_s)
                buzz_min_interval_ms = round(float(intervals_ms[start_idx:end_idx].min()), 2)
                buzz_pulse_count = end_idx - start_idx + 1

        return NativeDetection(
            offset_start_s=float(starts[0]),
            offset_end_s=float(starts[-1] + pulses[-1].duration_s),
            # Score rises with both pulse count and SNR: a coherent train of many
            # pulses is stronger evidence than one loud click.
            score=float(
                np.clip(
                    0.4 * min(1.0, len(pulses) / 8.0) + 0.6 * min(1.0, (peak_snr - 12.0) / 24.0),
                    0.0,
                    1.0,
                )
            ),
            label="bat pass",
            rank=None,
            taxonomic_group="bat",
            peak_frequency_hz=median_peak,
            native_result={
                "detector": "ultrasonic-pass-v1",
                "pulse_count": len(pulses),
                "median_peak_hz": round(median_peak, 1),
                "min_peak_hz": round(float(peaks.min()), 1),
                "max_peak_hz": round(float(peaks.max()), 1),
                "median_interval_ms": round(float(np.median(intervals)) * 1000.0, 1)
                if intervals.size
                else None,
                "mean_pulse_duration_ms": round(
                    float(np.mean([p.duration_s for p in pulses])) * 1000.0, 2
                ),
                "peak_snr_db": round(float(peak_snr), 1),
                "frequency_group_hint": hint,
                "hint_is_not_identification": True,
                "candidate_name": candidate_name,
                "candidate_ambiguity": candidate_ambiguity,
                "min_interval_ms": min_interval_ms,
                "has_feeding_buzz": has_buzz,
                "buzz_offset_s": buzz_offset_s,
                "buzz_min_interval_ms": buzz_min_interval_ms,
                "buzz_pulse_count": buzz_pulse_count,
                "score_definition": (
                    "0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db - 12)/24)"
                ),
            },
        )

    async def health(self) -> DetectorHealth:
        detail = (
            f"{self.pulses_found} pulses, {self.passes_found} passes over "
            f"{self._blocks} windows; noise floor "
            f"{self._noise_floor_db:.1f} dB"
            if self._noise_floor_db is not None
            else "awaiting audio"
        )
        # A gated detector must be visibly off rather than silently absent: a
        # station reporting nothing all night looks identical to a quiet night,
        # and telling those apart is the whole point of the coverage bar.
        if self._schedule is not None:
            state_fn = getattr(self._schedule, "state", None)
            if callable(state_fn):
                schedule_state = state_fn(datetime.now(UTC))
                active = schedule_state.get("active", True)
                reason = schedule_state.get("reason", "")
                detail += f"; schedule {'active' if active else 'gated'} ({reason})"
                dusk, dawn = schedule_state.get("dusk_utc"), schedule_state.get("dawn_utc")
                if dusk and dawn:
                    detail += f", tonight {dusk[11:16]}Z to {dawn[11:16]}Z"
            if self._gated_windows:
                detail += f"; {self._gated_windows} windows gated by schedule"
        return DetectorHealth(
            available=True,
            state="ok",
            detail=detail,
        )

    async def shutdown(self) -> None:
        self._noise_floor_db = None
