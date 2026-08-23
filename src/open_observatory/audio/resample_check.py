"""The resampler timing check, computed in constant memory.

This is the measurement behind ``oo audio resample-check``: the audio pipeline
spec's "Resampling correctness" list, run over synthetic PCM. It lives here
rather than in `cli.py` so the streaming statistics can be tested directly
against a naive reference implementation.

**Why it streams.** The first implementation accumulated every output block and
concatenated them, then took ``np.diff`` of the result. At the five minutes it
had ever been run for that costs about 300 MB and nobody noticed. At the one
hour the spec actually asks for (`docs/audio/AUDIO_PIPELINE.md`, "no cumulative
timestamp drift over one hour of generated PCM") it peaks at **~2.8 GB**, on a
target device with 7.8 GiB that is simultaneously running capture at 384 kHz.
`oo audio resample-check` is a documented target-device smoke test
(`docs/delivery/MILESTONE_STATUS.md`), so anyone running it at full duration
hits that, and an OOM there kills capture — which `CLAUDE.md` ranks above
closing this gate. Everything below is therefore computed in one pass with
bounded state.

Four properties are measured, and they are easy to conflate:

**Group delay** — does output frame *n* correspond to native frame
``n*src/dst``? Measured with an impulse, over at most 20 blocks.

**Delivery deficit** — how far behind the exact ratio is the *count* of frames
produced so far? libsoxr emits ragged chunks, so this oscillates within a
bounded band. Bounded is correct; trending is cumulative drift and a failure.

**Seam continuity** — does a block boundary introduce a click? The diff series
is taken across block boundaries, exactly as it was when the whole run was
concatenated first.

**Tone identity** — a 1 kHz tone in must still be a 1 kHz tone out.

The one statistic that cannot be computed exactly in bounded memory is the
*median* absolute sample step, so it is computed from a fixed histogram — see
`_StepStatistics`. Everything else (max, counts, deficit band, trend, group
delay, spectrum peak) is exact and identical to the accumulating version.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

#: Absolute sample steps are bounded by 2.0 for audio in [-1, 1]. Anything at or
#: above that lands in the top bin; the exact maximum is tracked separately and
#: is what the threshold is actually applied to, so clipping here cannot mask a
#: discontinuity.
_STEP_RANGE = 2.0
#: 2^20 bins over that range: 1.9e-6 per bin, so the median is recovered to
#: better than 1e-5 relative on a signal whose median step is ~0.065. The
#: counter costs 8 MB, which is the entire memory budget of this statistic.
_STEP_BINS = 1 << 20
#: Samples buffered before folding into the histogram. Amortises the bincount
#: allocation over ~440 blocks instead of paying it per block.
_STEP_FLUSH = 1 << 21


class _StepStatistics:
    """Exact max and near-exact median of |diff| over a stream of blocks.

    The diff is taken *across* block boundaries by carrying the previous block's
    last sample, so a click at a seam is seen exactly as it would be if the whole
    run had been concatenated first — which is the entire point of the statistic.
    """

    def __init__(self) -> None:
        self._counts = np.zeros(_STEP_BINS, dtype=np.int64)
        self._pending: list[np.ndarray] = []
        self._pending_size = 0
        self._max = 0.0
        self._count = 0
        self._last: float | None = None

    def push(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        if self._last is None:
            joined = block
        else:
            joined = np.concatenate((np.asarray([self._last], dtype=block.dtype), block))
        self._last = float(block[-1])
        if joined.size < 2:
            return
        steps = np.abs(np.diff(joined))
        self._max = max(self._max, float(steps.max()))
        self._count += int(steps.size)
        self._pending.append(steps)
        self._pending_size += int(steps.size)
        if self._pending_size >= _STEP_FLUSH:
            self._flush()

    def _flush(self) -> None:
        if not self._pending:
            return
        steps = np.concatenate(self._pending) if len(self._pending) > 1 else self._pending[0]
        self._pending = []
        self._pending_size = 0
        scaled = steps * (_STEP_BINS / _STEP_RANGE)
        index = np.clip(scaled.astype(np.int64), 0, _STEP_BINS - 1)
        self._counts += np.bincount(index, minlength=_STEP_BINS)

    @property
    def worst(self) -> float:
        return self._max

    @property
    def median(self) -> float:
        """Bin-centre estimate of the median absolute step.

        Lower-midpoint convention: the bin holding the ``n//2``-th ordered value.
        `numpy.median` averages the two middle values on an even count, so this
        can differ from it by up to half a bin (9.5e-7) plus that averaging —
        five orders below the 25x threshold it feeds.
        """
        self._flush()
        if self._count == 0:
            return 0.0
        cumulative = np.cumsum(self._counts)
        target = self._count // 2
        bin_index = int(np.searchsorted(cumulative, target + 1))
        bin_index = min(bin_index, _STEP_BINS - 1)
        return (bin_index + 0.5) * (_STEP_RANGE / _STEP_BINS)


@dataclass(frozen=True)
class ResamplerCheck:
    """Everything the check measured, plus the verdicts it reached."""

    backend: str
    backend_detail: str
    ratio: str
    source_rate: int
    target_rate: int
    block_ms: int
    block_frames: int
    blocks: int
    requested_seconds: float
    audio_seconds: float
    input_frames: int
    output_frames: int
    expected_output_frames: int
    group_delay_frames: float
    group_delay_ms: float
    deficit_min: int
    deficit_max: int
    deficit_trend: float
    deficit_trend_limit: float
    median_step: float
    worst_step: float
    worst_step_ratio: float
    seam_limit_ratio: float
    tone_hz: float
    peak_hz: float
    tone_tolerance_hz: float
    fft_size: int
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def measure_resampler(
    source_rate: int = 384000,
    target_rate: int = 48000,
    seconds: float = 60.0,
    block_ms: int = 100,
    *,
    tone_hz: float = 1000.0,
) -> ResamplerCheck:
    """Run the check over synthetic PCM in constant memory.

    Peak resident memory is independent of ``seconds``: the histogram (8 MB),
    one block of input and output, and the first ``fft_size`` output frames.
    """
    from .resample import AudibleResampler

    block_frames = int(source_rate * block_ms / 1000)
    blocks = int(seconds * source_rate / block_frames)

    # --- group delay, by impulse ------------------------------------------
    # Bounded at 20 blocks by construction, so this term never grew with the
    # run length even in the accumulating version.
    delay_converter = AudibleResampler(source_rate, target_rate)
    impulse_at = block_frames * 5 + block_frames // 3
    impulse_out: list[np.ndarray] = []
    for index in range(blocks if blocks < 20 else 20):
        chunk = np.zeros(block_frames, dtype=np.float32)
        local = impulse_at - index * block_frames
        if 0 <= local < block_frames:
            chunk[local] = 1.0
        impulse_out.append(delay_converter.process(chunk).pcm)
    impulse_signal = np.concatenate(impulse_out) if impulse_out else np.zeros(0, dtype=np.float32)
    ideal_position = impulse_at * target_rate / source_rate
    if impulse_signal.size and float(np.abs(impulse_signal).max()) > 0:
        actual_position = float(np.argmax(np.abs(impulse_signal)))
        group_delay = actual_position - ideal_position
    else:
        group_delay = float("nan")
    del impulse_out, impulse_signal

    # --- deficit band, trend, seams and tone identity, in one pass --------
    converter = AudibleResampler(source_rate, target_rate)
    steps = _StepStatistics()
    deficits: list[int] = []
    fft_size = 1 << 16
    head = np.zeros(fft_size, dtype=np.float32)
    head_filled = 0
    output_frames = 0
    phase = 0

    for _ in range(blocks):
        t = (phase + np.arange(block_frames)) / source_rate
        produced = converter.process(0.5 * np.sin(2 * np.pi * tone_hz * t)).pcm
        phase += block_frames
        deficits.append(
            converter.expected_output_frames(converter.input_frames) - converter.output_frames
        )
        output_frames += int(produced.shape[0])
        steps.push(produced)
        if head_filled < fft_size and produced.size:
            take = min(fft_size - head_filled, int(produced.shape[0]))
            head[head_filled : head_filled + take] = produced[:take]
            head_filled += take

    deficit_min = min(deficits) if deficits else 0
    deficit_max = max(deficits) if deficits else 0
    tenth = max(1, len(deficits) // 10)
    trend = (
        float(np.mean(deficits[-tenth:]) - np.mean(deficits[:tenth])) if deficits else 0.0
    )

    median_step = steps.median
    worst_step = steps.worst

    size = min(fft_size, head_filled)
    if size:
        spectrum = np.abs(np.fft.rfft(head[:size] * np.hanning(size)))
        peak_hz = float(np.fft.rfftfreq(size, 1.0 / target_rate)[int(np.argmax(spectrum))])
    else:
        peak_hz = float("nan")

    # --- verdicts ---------------------------------------------------------
    # Thresholds are unchanged from the original implementation; their
    # derivation is recorded in ADR-069.
    trend_limit = float((deficit_max - deficit_min) + 8)
    seam_limit_ratio = 25.0
    tone_tolerance_hz = target_rate / size * 2 if size else float("inf")

    failures: list[str] = []
    if not (abs(group_delay) <= 1.0):
        failures.append(
            f"group delay of {group_delay:+.2f} output frames would bias every "
            "audible detection timestamp"
        )
    if abs(trend) >= trend_limit:
        failures.append(
            f"delivery deficit is trending by {trend:+.1f} frames, which is cumulative drift"
        )
    if worst_step >= median_step * seam_limit_ratio:
        failures.append("a block seam introduced a discontinuity")
    if abs(peak_hz - tone_hz) > tone_tolerance_hz:
        failures.append(f"tone moved from {tone_hz} Hz to {peak_hz:.1f} Hz")

    return ResamplerCheck(
        backend=converter.backend,
        backend_detail=converter.backend_detail,
        ratio=f"{converter.ratio.numerator}/{converter.ratio.denominator}",
        source_rate=source_rate,
        target_rate=target_rate,
        block_ms=block_ms,
        block_frames=block_frames,
        blocks=blocks,
        requested_seconds=float(seconds),
        audio_seconds=converter.input_frames / source_rate,
        input_frames=converter.input_frames,
        output_frames=output_frames,
        expected_output_frames=converter.expected_output_frames(converter.input_frames),
        group_delay_frames=group_delay,
        group_delay_ms=group_delay / target_rate * 1000,
        deficit_min=deficit_min,
        deficit_max=deficit_max,
        deficit_trend=trend,
        deficit_trend_limit=trend_limit,
        median_step=median_step,
        worst_step=worst_step,
        worst_step_ratio=worst_step / max(median_step, 1e-9),
        seam_limit_ratio=seam_limit_ratio,
        tone_hz=tone_hz,
        peak_hz=peak_hz,
        tone_tolerance_hz=tone_tolerance_hz,
        fft_size=size,
        failures=failures,
    )
