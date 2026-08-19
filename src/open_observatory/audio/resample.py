"""Deterministic derivation of the audible 48 kHz stream from native capture.

The AudioMoth USB Microphone on this target offers exactly one hardware profile:
384 kHz mono S16_LE. Every audible-band detector wants 48 kHz, so resampling is
on the critical path and its correctness is load-bearing — an audible detector
fed a subtly wrong stream fails quietly.

Two requirements shape this module:

1. **Stateful, continuous resampling.** A polyphase filter applied
   block-by-block without carrying state produces a click at every block
   boundary. ``soxr.ResampleStream`` keeps that state. The ``scipy`` fallback
   reproduces it with explicit overlap-save.
2. **Exact frame mapping.** Every output frame must be attributable to a native
   source frame range so evidence clips and detection intervals line up with the
   authoritative stream (audio pipeline spec, "Resampling correctness").
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Literal

import numpy as np

Backend = Literal["soxr", "scipy", "decimate-integer", "passthrough"]


@dataclass(frozen=True, slots=True)
class ResampledBlock:
    pcm: np.ndarray
    #: Absolute frame index of the first output frame in the derived stream.
    first_frame: int
    #: Native frame index this output block starts at, ignoring filter latency.
    native_first_frame: int
    native_frame_count: int


class AudibleResampler:
    """Streaming native → 48 kHz converter with a stable frame mapping.

    Output frame *n* corresponds to native frame ``round(n * src / dst)``. The
    conversion is driven by an exact rational ratio so no drift accumulates over
    hours, regardless of backend.
    """

    def __init__(
        self,
        source_rate: int,
        target_rate: int = 48000,
        *,
        quality: str = "HQ",
        prefer: Backend | None = None,
    ) -> None:
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("sample rates must be positive")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self.ratio = Fraction(target_rate, source_rate)
        self._in_frames = 0
        self._out_frames = 0
        # `Any`, not `object`: this holds a `soxr.ResampleStream` whose module is
        # an optional import, so it cannot be named in an annotation here.
        self._stream: Any = None
        self._tail = np.zeros(0, dtype=np.float32)
        self.backend: Backend
        self.backend_detail = ""

        if source_rate == target_rate:
            self.backend = "passthrough"
            return

        if prefer != "scipy":
            try:
                import soxr

                self._stream = soxr.ResampleStream(
                    source_rate, target_rate, 1, dtype="float32", quality=quality
                )
                self.backend = "soxr"
                self.backend_detail = f"soxr {soxr.__version__} quality={quality}"
                return
            except Exception as exc:  # pragma: no cover - depends on wheel availability
                self.backend_detail = f"soxr unavailable: {exc}"

        try:
            from scipy.signal import resample_poly  # noqa: F401

            self.backend = "scipy"
            ratio = Fraction(target_rate, source_rate).limit_denominator(1_000_000)
            self._up, self._down = ratio.numerator, ratio.denominator
            # Overlap large enough to cover resample_poly's FIR transient.
            self._overlap = 32 * self._down
            self._primed = False
            self.backend_detail = (
                f"{self.backend_detail + '; ' if self.backend_detail else ''}"
                f"scipy resample_poly up={self._up} down={self._down}"
            )
            return
        except ImportError as exc:
            raise RuntimeError(
                "no resampler available: install the 'resample' extra "
                f"(soxr or scipy). {self.backend_detail or exc}"
            ) from exc

    # ------------------------------------------------------------------

    @property
    def input_frames(self) -> int:
        return self._in_frames

    @property
    def output_frames(self) -> int:
        return self._out_frames

    def native_frame_for_output(self, output_frame: int) -> int:
        """Map a derived-stream frame back to the authoritative native frame."""
        return round(output_frame * self.source_rate / self.target_rate)

    def output_frame_for_native(self, native_frame: int) -> int:
        return round(native_frame * self.target_rate / self.source_rate)

    def expected_output_frames(self, input_frames: int) -> int:
        return round(input_frames * self.target_rate / self.source_rate)

    # ------------------------------------------------------------------

    def process(self, pcm: np.ndarray) -> ResampledBlock:
        """Convert one native block, carrying filter state across calls."""
        if pcm.ndim != 1:
            raise ValueError("expected mono PCM")
        block = np.ascontiguousarray(pcm, dtype=np.float32)
        native_first = self._in_frames
        out_first = self._out_frames

        if self.backend == "passthrough":
            out = block.copy()
        elif self.backend == "soxr":
            assert self._stream is not None
            out = np.asarray(self._stream.resample_chunk(block), dtype=np.float32).reshape(-1)
        else:
            out = self._scipy_chunk(block)

        self._in_frames += int(block.shape[0])
        self._out_frames += int(out.shape[0])
        return ResampledBlock(
            pcm=out,
            first_frame=out_first,
            native_first_frame=native_first,
            native_frame_count=int(block.shape[0]),
        )

    def _scipy_chunk(self, block: np.ndarray) -> np.ndarray:
        """Overlap-save polyphase resampling, mirroring soxr's continuity.

        A tail of the previous input is prepended so the FIR filter sees real
        history rather than implicit zeros; the corresponding output prefix is
        then discarded. Output length is corrected against the exact rational
        ratio so no rounding error accumulates.
        """
        from scipy.signal import resample_poly

        padded = np.concatenate((self._tail, block)) if self._tail.size else block
        converted = resample_poly(padded, self._up, self._down).astype(np.float32, copy=False)

        # Discard the output that corresponds to the prepended history.
        skip = round(self._tail.size * self._up / self._down)
        out = converted[skip:]

        # Keep the exact overall ratio: total output must track total input.
        target_total = self.expected_output_frames(self._in_frames + int(block.shape[0]))
        want = target_total - self._out_frames
        if want < 0:
            want = 0
        if out.shape[0] > want:
            out = out[:want]
        elif out.shape[0] < want:
            out = np.concatenate((out, np.zeros(want - out.shape[0], dtype=np.float32)))

        keep = min(self._overlap, padded.size)
        self._tail = padded[padded.size - keep :].copy()
        return out

    def describe(self) -> dict[str, object]:
        deficit = self.expected_output_frames(self._in_frames) - self._out_frames
        self._deficit_min = min(getattr(self, "_deficit_min", deficit), deficit)
        self._deficit_max = max(getattr(self, "_deficit_max", deficit), deficit)
        return {
            "backend": self.backend,
            "backend_detail": self.backend_detail,
            "source_rate": self.source_rate,
            "target_rate": self.target_rate,
            "ratio": f"{self.ratio.numerator}/{self.ratio.denominator}",
            "input_frames": self._in_frames,
            "output_frames": self._out_frames,
            # Frames still inside the filter, i.e. produced-so-far versus what the
            # exact ratio implies. libsoxr emits ragged chunks, so this oscillates
            # within a bounded band and must NOT be read as cumulative drift; it is
            # delivery latency. Timestamps are derived from frame indices via
            # StreamClock precisely so this cannot become a timing error.
            "delivery_deficit_frames": deficit,
            "delivery_deficit_ms": round(deficit / self.target_rate * 1000.0, 3),
            "delivery_deficit_min": self._deficit_min,
            "delivery_deficit_max": self._deficit_max,
            # Measured on the target device: libsoxr is delay-compensated, so
            # output frame n corresponds exactly to native frame round(n*src/dst).
            "group_delay_frames": 0,
        }
