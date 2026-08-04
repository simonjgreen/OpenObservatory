"""Exclusive ALSA capture. The only component permitted to open the microphone.

Design notes:

* The device is addressed by ``hw:CARD=<id>`` so card renumbering across
  reboots cannot silently point us at the wrong hardware — and never through
  ``plug``, which would resample behind our back and destroy the ultrasonic band
  we captured at 384 kHz to obtain.
* Reads happen on a worker thread (ALSA's read is blocking) and are handed to the
  event loop one capture block at a time.
* Frame accounting is authoritative. Gaps are estimated from the divergence
  between frames actually read and monotonic elapsed time, then reported as an
  explicit discontinuity rather than absorbed silently.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

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
from .probe import CaptureDevice, find_device

log = structlog.get_logger(__name__)

#: Scale factors from integer PCM to float32 in [-1, 1).
_SCALE = {"S16_LE": 1.0 / 32768.0, "S32_LE": 1.0 / 2147483648.0}
_NUMPY_DTYPE = {"S16_LE": "<i2", "S32_LE": "<i4"}


class AlsaCaptureError(RuntimeError):
    pass


class AlsaSource:
    """Reads fixed-duration mono blocks from a hardware capture device."""

    def __init__(
        self,
        *,
        device_key: str | None = None,
        preferred_rates: tuple[int, ...] = (384000, 192000, 48000),
        preferred_formats: tuple[str, ...] = ("S16_LE", "S32_LE"),
        channels: int = 1,
        block_ms: int = 100,
        #: ALSA period. Smaller periods give finer overrun granularity than the
        #: capture block itself, so several are accumulated per block.
        period_ms: float = 10.0,
    ) -> None:
        self._device_key = device_key
        self._preferred_rates = preferred_rates
        self._preferred_formats = preferred_formats
        self._channels = channels
        self._block_ms = block_ms
        self._period_ms = period_ms

        self.device: CaptureDevice | None = None
        self.info: StreamInfo
        self._pcm: Any = None
        self._sequence = 0
        self._frames_read = 0
        self._block_frames = 0
        self._period_frames = 0
        self._dtype = "<i2"
        self._scale = _SCALE["S16_LE"]
        self._pending_discontinuity: DiscontinuityReason | None = DiscontinuityReason.STREAM_START
        self._pending_missing = 0
        self._closed = False
        self.overrun_count = 0
        self.short_read_count = 0

        # Frame-zero anchor, established from the *first block actually read*.
        # Opening and preparing an ALSA device plus filling its first buffer takes
        # a fixed couple of hundred milliseconds, so anchoring on open() time would
        # put every timestamp that far early and make the gap estimator report a
        # permanent phantom gap of exactly that size.
        self._anchor_monotonic_ns: int | None = None
        # Slow-moving baseline of the frames-behind-wall-clock figure. A USB audio
        # device is clocked by its own crystal, which differs from the host's
        # monotonic clock by tens of ppm; that shows up as this figure drifting
        # steadily. Lost audio shows up as it *stepping*. Only steps are gaps.
        self._deficit_baseline: float | None = None
        self.observed_rate_hz: float | None = None
        self.rate_offset_ppm: float | None = None
        self.missing_frames_total = 0

    # ------------------------------------------------------------------

    async def open(self) -> StreamInfo:
        return await asyncio.to_thread(self._open_blocking)

    def _open_blocking(self) -> StreamInfo:
        try:
            import alsaaudio
        except ImportError as exc:  # pragma: no cover
            raise AlsaCaptureError(
                "pyalsaaudio is not installed; install the 'alsa' extra"
            ) from exc

        device = find_device(self._device_key)
        if device is None:
            raise AlsaCaptureError(
                "no ALSA capture device found"
                + (f" matching {self._device_key!r}" if self._device_key else "")
            )
        self.device = device

        # Prefer a rate the kernel actually advertises; fall back to trying the
        # configured preferences in order.
        advertised = device.advertised_rates
        candidates = [r for r in self._preferred_rates if not advertised or r in advertised]
        if not candidates:
            candidates = list(advertised) or list(self._preferred_rates)

        last_error: Exception | None = None
        for sample_format in self._preferred_formats:
            fmt_const = getattr(alsaaudio, f"PCM_FORMAT_{sample_format}", None)
            if fmt_const is None or sample_format not in _NUMPY_DTYPE:
                continue
            for rate in candidates:
                period_frames = max(64, int(round(rate * self._period_ms / 1000.0)))
                try:
                    pcm = alsaaudio.PCM(
                        alsaaudio.PCM_CAPTURE,
                        alsaaudio.PCM_NORMAL,
                        device=device.alsa_address,
                        channels=self._channels,
                        rate=rate,
                        format=fmt_const,
                        periodsize=period_frames,
                        periods=8,
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                negotiated = pcm.info()
                actual_rate = int(negotiated.get("rate", rate))
                if actual_rate != rate:
                    # ALSA silently substituted a rate: refuse it rather than
                    # record audio whose true bandwidth we cannot state.
                    pcm.close()
                    last_error = AlsaCaptureError(
                        f"device negotiated {actual_rate} Hz when {rate} Hz was requested"
                    )
                    continue
                self._pcm = pcm
                self._configure(rate, sample_format, period_frames, negotiated)
                return self.info
        raise AlsaCaptureError(
            f"could not open {device.alsa_address} at any of {candidates} "
            f"in {list(self._preferred_formats)}: {last_error}"
        )

    def _configure(
        self, rate: int, sample_format: str, period_frames: int, negotiated: dict[str, Any]
    ) -> None:
        assert self.device is not None
        self._dtype = _NUMPY_DTYPE[sample_format]
        self._scale = _SCALE[sample_format]
        self._period_frames = int(negotiated.get("period_size", period_frames) or period_frames)
        self._block_frames = max(
            self._period_frames, int(round(rate * self._block_ms / 1000.0))
        )
        # Round the block to a whole number of periods so reads align.
        periods_per_block = max(1, self._block_frames // self._period_frames)
        self._block_frames = periods_per_block * self._period_frames
        self._frames_read = 0
        self._sequence = 0

        clock = ClockCorrelation.sample()
        self.info = StreamInfo(
            stream_id=uuid.uuid4(),
            source_kind=SourceKind.ALSA,
            device_key=self.device.stable_device_key,
            device_label=self.device.card_name or self.device.card_id,
            fmt=AudioFormat(sample_rate=rate, channels=self._channels, sample_format=sample_format),
            started_monotonic_ns=clock.monotonic_ns,
            clock=clock,
            detail={
                "alsa_address": self.device.alsa_address,
                "by_id_symlink": self.device.by_id_symlink,
                "card_index_at_open": self.device.card_index,
                "period_frames": self._period_frames,
                "block_frames": self._block_frames,
                "periods_per_block": periods_per_block,
                "negotiated": {k: v for k, v in negotiated.items() if isinstance(v, int | str)},
                "advertised_rates": list(self.device.advertised_rates),
                "usb_serial": self.device.usb_serial,
            },
        )
        log.info(
            "capture.opened",
            device=self.device.stable_device_key,
            address=self.device.alsa_address,
            rate=rate,
            sample_format=sample_format,
            block_frames=self._block_frames,
            period_frames=self._period_frames,
        )

    # ------------------------------------------------------------------

    async def read(self) -> CaptureBlock | None:
        if self._closed:
            return None
        return await asyncio.to_thread(self._read_blocking)

    def _read_blocking(self) -> CaptureBlock | None:
        import alsaaudio

        assert self._pcm is not None
        pieces: list[np.ndarray] = []
        collected = 0
        block_start_monotonic: int | None = None
        discontinuity = self._pending_discontinuity
        missing = self._pending_missing
        self._pending_discontinuity = None
        self._pending_missing = 0

        while collected < self._block_frames:
            if self._closed:
                return None
            try:
                length, data = self._pcm.read()
            except alsaaudio.ALSAAudioError as exc:
                message = str(exc)
                if "Input/output error" in message or "EPIPE" in message or "overrun" in message:
                    self.overrun_count += 1
                    discontinuity = DiscontinuityReason.OVERRUN
                    log.warning("capture.overrun", detail=message, count=self.overrun_count)
                    continue
                raise AlsaCaptureError(f"ALSA read failed: {exc}") from exc

            if block_start_monotonic is None:
                block_start_monotonic = time.monotonic_ns()

            if length < 0:
                self.overrun_count += 1
                discontinuity = DiscontinuityReason.OVERRUN
                continue
            if length == 0:
                # No data ready. With PCM_NORMAL this is unusual; treat a run of
                # them as a stalled device rather than spinning.
                self.short_read_count += 1
                if self.short_read_count % 500 == 0:
                    log.warning("capture.short_reads", count=self.short_read_count)
                time.sleep(0.001)
                continue

            samples = np.frombuffer(data, dtype=self._dtype)
            if self._channels > 1:
                usable = samples.shape[0] - (samples.shape[0] % self._channels)
                samples = samples[:usable].reshape(-1, self._channels)[:, 0]
            pieces.append(samples)
            collected += int(samples.shape[0])

        if block_start_monotonic is None:
            block_start_monotonic = time.monotonic_ns()

        raw = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
        pcm = raw.astype(np.float32) * self._scale

        rate = self.info.fmt.sample_rate
        first_frame = self._frames_read
        # The block *ends* at the moment the last period was handed to us, so
        # its start is one block-duration earlier. Using the read-completion
        # time as the start would bias every timestamp by a whole block.
        duration_ns = int(pcm.shape[0]) * NS_PER_S // rate
        monotonic_start = time.monotonic_ns() - duration_ns

        if self._anchor_monotonic_ns is None:
            self._anchor_monotonic_ns = monotonic_start
            self._deficit_baseline = 0.0

        # How far the stream is behind what elapsed monotonic time implies.
        elapsed_ns = monotonic_start - self._anchor_monotonic_ns
        expected_frames = max(0, elapsed_ns * rate // NS_PER_S)
        deficit = float(expected_frames - first_frame)

        if elapsed_ns > NS_PER_S:
            # Frames the device *presented*, which is what its crystal determines.
            # Frames lost to overruns must be added back, or a single dropout would
            # be misreported forever as a large clock offset — measured at -1439 ppm
            # from one overrun on the target, versus a true offset near 0.2 ppm.
            presented = first_frame + self.missing_frames_total
            self.observed_rate_hz = presented * NS_PER_S / elapsed_ns
            self.rate_offset_ppm = (self.observed_rate_hz / rate - 1.0) * 1e6

        assert self._deficit_baseline is not None
        step = deficit - self._deficit_baseline
        if step > self._block_frames and discontinuity is None:
            # A step beyond a whole block is lost audio, not crystal drift.
            discontinuity = DiscontinuityReason.OVERRUN
            missing = int(step)
            self.missing_frames_total += missing
            self._deficit_baseline = deficit
        else:
            # Track the slow component, so genuine crystal offset is absorbed
            # rather than reported forever as a gap.
            self._deficit_baseline += 0.01 * step

        self._frames_read += int(pcm.shape[0])
        sequence = self._sequence
        self._sequence += 1

        return CaptureBlock(
            stream_id=self.info.stream_id,
            sequence=sequence,
            first_frame=first_frame,
            sample_rate=rate,
            pcm=pcm,
            monotonic_start_ns=monotonic_start,
            clock=self.info.clock,
            discontinuity=discontinuity,
            missing_frames=missing,
        )

    async def close(self) -> None:
        self._closed = True
        pcm, self._pcm = self._pcm, None
        if pcm is not None:
            await asyncio.to_thread(pcm.close)
        log.info("capture.closed", frames=self._frames_read, overruns=self.overrun_count)
