"""Replay and synthetic capture sources.

The audio pipeline spec makes this mandatory, not optional: every downstream
component must run against fixtures through the same capture-block contract, so
development and tests never depend on a live microphone. It is also the honest
way to test gap handling — you cannot ask a real device to drop frames on cue.

Three replay modes are supported:

``realtime``
    Blocks are emitted at wall-clock pace, as a device would.
``accelerated``
    As fast as the consumer can take them, with timestamps that still advance at
    the stream's true rate. Useful for a one-hour drift test in seconds.
``step``
    Emits only when :meth:`ReplaySource.step` is called. Deterministic for tests.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
import structlog

from .contracts import (
    NS_PER_S,
    AudioFormat,
    CaptureBlock,
    ClockCorrelation,
    DiscontinuityReason,
    SourceKind,
    StreamInfo,
)

log = structlog.get_logger(__name__)

ReplayMode = Literal["realtime", "accelerated", "step"]


class _PacedSource:
    """Shared timing, frame accounting and gap injection for generated audio."""

    def __init__(
        self,
        *,
        sample_rate: int,
        block_frames: int,
        mode: ReplayMode,
        speed: float,
        source_kind: SourceKind,
        device_key: str,
        device_label: str,
        detail: dict[str, object],
    ) -> None:
        self._sample_rate = sample_rate
        self._block_frames = block_frames
        self._mode = mode
        self._speed = max(0.01, speed)
        self._source_kind = source_kind
        self._device_key = device_key
        self._device_label = device_label
        self._detail = detail

        self._sequence = 0
        self._frames_emitted = 0
        self._closed = False
        self._step_gate: asyncio.Event | None = None
        self._wall_start_ns = 0
        self._pending_discontinuity: DiscontinuityReason | None = DiscontinuityReason.STREAM_START
        self._pending_missing = 0
        #: Frames to skip before the next block, simulating a capture gap.
        self._inject_gap_frames = 0

    async def open(self) -> StreamInfo:
        clock = ClockCorrelation.sample()
        self._wall_start_ns = clock.monotonic_ns
        self.info = StreamInfo(
            stream_id=uuid.uuid4(),
            source_kind=self._source_kind,
            device_key=self._device_key,
            device_label=self._device_label,
            fmt=AudioFormat(
                sample_rate=self._sample_rate, channels=1, sample_format="FLOAT_LE"
            ),
            started_monotonic_ns=clock.monotonic_ns,
            clock=clock,
            detail={**self._detail, "mode": self._mode, "speed": self._speed,
                    "block_frames": self._block_frames},
        )
        if self._mode == "step":
            self._step_gate = asyncio.Event()
        log.info(
            "replay.opened",
            kind=self._source_kind,
            rate=self._sample_rate,
            mode=self._mode,
            label=self._device_label,
        )
        return self.info

    def inject_gap(self, frames: int, reason: DiscontinuityReason = DiscontinuityReason.OVERRUN) -> None:
        """Skip ``frames`` of source audio before the next block."""
        self._inject_gap_frames += max(0, frames)
        self._pending_discontinuity = reason

    async def step(self, blocks: int = 1) -> None:
        """Release blocks in ``step`` mode."""
        if self._step_gate is None:
            raise RuntimeError("step() requires mode='step'")
        for _ in range(blocks):
            self._step_gate.set()
            await asyncio.sleep(0)

    async def _pace(self) -> None:
        if self._closed:
            return
        if self._mode == "step":
            assert self._step_gate is not None
            await self._step_gate.wait()
            self._step_gate.clear()
            return
        if self._mode == "accelerated":
            await asyncio.sleep(0)
            return
        # realtime: sleep until this block's audio would have finished arriving.
        due_ns = self._wall_start_ns + int(
            (self._frames_emitted + self._block_frames) * NS_PER_S / self._sample_rate / self._speed
        )
        delay = (due_ns - time.monotonic_ns()) / NS_PER_S
        if delay > 0:
            await asyncio.sleep(delay)

    def _wrap(self, pcm: np.ndarray) -> CaptureBlock:
        discontinuity = self._pending_discontinuity
        missing = self._pending_missing or self._inject_gap_frames
        self._pending_discontinuity = None
        self._pending_missing = 0
        self._inject_gap_frames = 0

        first_frame = self._frames_emitted + missing
        # Timestamps advance at the stream's true rate even when replaying fast,
        # so downstream drift checks are meaningful in every mode.
        monotonic_start = self._wall_start_ns + int(first_frame * NS_PER_S / self._sample_rate)
        self._frames_emitted = first_frame + int(pcm.shape[0])
        sequence = self._sequence
        self._sequence += 1
        return CaptureBlock(
            stream_id=self.info.stream_id,
            sequence=sequence,
            first_frame=first_frame,
            sample_rate=self._sample_rate,
            pcm=np.ascontiguousarray(pcm, dtype=np.float32),
            monotonic_start_ns=monotonic_start,
            clock=self.info.clock,
            discontinuity=discontinuity,
            missing_frames=missing,
        )

    async def close(self) -> None:
        self._closed = True
        if self._step_gate is not None:
            self._step_gate.set()


class ReplaySource(_PacedSource):
    """Streams a WAV fixture through the capture-block contract."""

    def __init__(
        self,
        path: Path,
        *,
        block_ms: int = 100,
        mode: ReplayMode = "realtime",
        speed: float = 1.0,
        loop: bool = True,
        resample_to: int | None = None,
    ) -> None:
        import soundfile as sf

        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        with sf.SoundFile(str(self.path)) as handle:
            self._file_rate = handle.samplerate
            self._file_frames = len(handle)
            self._file_channels = handle.channels
            subtype = handle.subtype

        rate = resample_to or self._file_rate
        self._resample_to = resample_to
        self._loop = loop
        self._cursor = 0
        self._audio: np.ndarray | None = None

        super().__init__(
            sample_rate=rate,
            block_frames=max(1, round(rate * block_ms / 1000.0)),
            mode=mode,
            speed=speed,
            source_kind=SourceKind.REPLAY,
            device_key=f"replay-{self.path.name}",
            device_label=f"replay:{self.path.name}",
            detail={
                "path": str(self.path),
                "file_sample_rate": self._file_rate,
                "file_frames": self._file_frames,
                "file_channels": self._file_channels,
                "file_subtype": subtype,
                "loop": loop,
            },
        )

    async def open(self) -> StreamInfo:
        import soundfile as sf

        data, rate = sf.read(str(self.path), dtype="float32", always_2d=True)
        mono = data[:, 0] if data.shape[1] else data.reshape(-1)
        if self._resample_to and self._resample_to != rate:
            from .resample import AudibleResampler

            converter = AudibleResampler(rate, self._resample_to)
            mono = converter.process(mono).pcm
        self._audio = np.ascontiguousarray(mono, dtype=np.float32)
        self._cursor = 0
        return await super().open()

    async def read(self) -> CaptureBlock | None:
        if self._closed or self._audio is None:
            return None
        await self._pace()
        if self._closed:
            return None

        total = int(self._audio.shape[0])
        if self._cursor >= total:
            if not self._loop:
                return None
            self._cursor = 0
            self._pending_discontinuity = DiscontinuityReason.REPLAY_WRAP

        take = min(self._block_frames, total - self._cursor)
        chunk = self._audio[self._cursor : self._cursor + take]
        self._cursor += take
        if take < self._block_frames and self._loop:
            # Straddle the loop point so block sizes stay uniform.
            remainder = self._block_frames - take
            chunk = np.concatenate((chunk, self._audio[:remainder]))
            self._cursor = remainder
            self._pending_discontinuity = DiscontinuityReason.REPLAY_WRAP
        return self._wrap(chunk)


class SyntheticSource(_PacedSource):
    """Generates test audio with known content at an arbitrary sample rate.

    Scenes exist to exercise specific pipeline properties:

    ``silence``      level telemetry and zero-signal warnings
    ``tone``         resampler correctness against a known frequency
    ``sweep``        aliasing above the audible Nyquist after downsampling
    ``impulse``      capture-to-window timing alignment (one click per second)
    ``dawn-chorus``  chirps in the bird band, so the activity detector fires
    ``bat-pass``     ultrasonic pulse trains, only audible above 96 kHz
    """

    SCENES = ("silence", "tone", "sweep", "impulse", "dawn-chorus", "bat-pass", "mixed")

    def __init__(
        self,
        *,
        scene: str = "dawn-chorus",
        sample_rate: int = 48000,
        block_ms: int = 100,
        mode: ReplayMode = "realtime",
        speed: float = 1.0,
        tone_hz: float = 1000.0,
        noise_level: float = 0.002,
        seed: int = 20260804,
    ) -> None:
        if scene not in self.SCENES:
            raise ValueError(f"unknown scene {scene!r}; expected one of {self.SCENES}")
        self.scene = scene
        self._tone_hz = tone_hz
        self._noise_level = noise_level
        self._rng = np.random.default_rng(seed)
        super().__init__(
            sample_rate=sample_rate,
            block_frames=max(1, round(sample_rate * block_ms / 1000.0)),
            mode=mode,
            speed=speed,
            source_kind=SourceKind.SYNTHETIC,
            device_key=f"synthetic-{scene}",
            device_label=f"synthetic:{scene}",
            detail={"scene": scene, "tone_hz": tone_hz, "noise_level": noise_level, "seed": seed},
        )

    async def read(self) -> CaptureBlock | None:
        if self._closed:
            return None
        await self._pace()
        if self._closed:
            return None
        start = self._frames_emitted
        t = (start + np.arange(self._block_frames, dtype=np.float64)) / self._sample_rate
        pcm = self._render(t)
        return self._wrap(pcm.astype(np.float32))

    def _render(self, t: np.ndarray) -> np.ndarray:
        nyquist = self._sample_rate / 2.0
        # Annotated because numpy 2.5 types `Generator.normal` as `float |
        # ndarray` -- the scalar overload applies only when `size` is None,
        # which it never is here.
        noise: np.ndarray = np.asarray(
            self._rng.normal(0.0, self._noise_level, t.shape[0])
        )

        if self.scene == "silence":
            return noise * 0.1
        if self.scene == "tone":
            return 0.25 * np.sin(2 * np.pi * self._tone_hz * t) + noise
        if self.scene == "sweep":
            # Logarithmic sweep over a 10 s period, spanning the whole band so a
            # downsampled copy can be checked for aliasing.
            period = 10.0
            phase = (t % period) / period
            freq = 40.0 * (nyquist * 0.98 / 40.0) ** phase
            return 0.2 * np.sin(2 * np.pi * freq * t) + noise
        if self.scene == "impulse":
            out = noise.copy()
            # One sample-accurate click at each whole second.
            first = math.ceil(t[0])
            for second in range(first, int(t[-1]) + 1):
                index = round((second - t[0]) * self._sample_rate)
                if 0 <= index < out.shape[0]:
                    out[index] += 0.9
            return out
        if self.scene == "dawn-chorus":
            return self._chorus(t) + noise
        if self.scene == "bat-pass":
            return self._bat(t, nyquist) + noise
        return self._chorus(t) + self._bat(t, nyquist) + noise

    def _chorus(self, t: np.ndarray) -> np.ndarray:
        """Frequency-modulated chirps in the passerine band."""
        out = np.zeros(t.shape[0])
        # A repeating 6 s pattern of three distinct "voices".
        voices = (
            (0.4, 0.35, 3200.0, 5600.0, 0.28),
            (2.1, 0.18, 2100.0, 2600.0, 0.22),
            (3.6, 0.5, 4200.0, 7400.0, 0.2),
        )
        cycle = 6.0
        for offset, duration, f0, f1, amplitude in voices:
            local = (t % cycle) - offset
            active = (local >= 0) & (local < duration)
            if not active.any():
                continue
            frac = np.clip(local / duration, 0.0, 1.0)
            freq = f0 + (f1 - f0) * frac
            envelope = np.sin(np.pi * np.clip(frac, 0.0, 1.0)) ** 1.5
            # Integrate the instantaneous frequency for a phase-continuous chirp.
            out += np.where(active, amplitude * envelope * np.sin(2 * np.pi * freq * local), 0.0)
        return out

    def _bat(self, t: np.ndarray, nyquist: float) -> np.ndarray:
        """Downward-sweeping echolocation pulses at ~45 kHz.

        Deliberately above 24 kHz so it is absent from the derived 48 kHz stream
        and present only in the native one. Returns silence when the configured
        rate cannot represent it, which is itself a useful test.
        """
        if nyquist < 50_000:
            return np.zeros(t.shape[0])
        out = np.zeros(t.shape[0])
        pulse_ms, interval = 4.0, 0.09
        # An 8-pulse pass every 12 s.
        cycle = 12.0
        for index in range(8):
            begin = 5.0 + index * interval
            local = (t % cycle) - begin
            active = (local >= 0) & (local < pulse_ms / 1000.0)
            if not active.any():
                continue
            frac = np.clip(local / (pulse_ms / 1000.0), 0.0, 1.0)
            freq = 58_000.0 - 16_000.0 * frac
            envelope = np.sin(np.pi * frac) ** 2
            out += np.where(active, 0.5 * envelope * np.sin(2 * np.pi * freq * local), 0.0)
        return out
