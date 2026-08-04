"""Evidence clip extraction.

A detection without audio you can listen to is an assertion, not evidence. Clips
are cut from the native ring buffer at the *authoritative* rate, so a bat pass
keeps its ultrasound and can be reviewed properly; a downsampled playback
derivative is written alongside when the native rate is not directly playable in
a browser (technical spec §7).

Writes are atomic — a temporary file then a rename — so a crash or a full disk can
never leave a half-written WAV that looks like valid evidence.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import structlog

from .audio.ring import RingBuffer

log = structlog.get_logger(__name__)

#: Sample rate for the browser-playable derivative of a high-rate clip.
PLAYBACK_RATE = 48000

#: Beyond this rate, browsers will not reliably decode a WAV, so a playback
#: derivative is always written as well.
BROWSER_SAFE_MAX_RATE = 96000


@dataclass(frozen=True, slots=True)
class ClipAsset:
    asset_id: uuid.UUID
    kind: str
    path: Path
    mime_type: str
    sample_rate: int
    start_frame: int
    end_frame: int
    byte_length: int
    sha256: str
    expires_at: datetime | None
    detail: dict[str, object]

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.sample_rate


@dataclass(slots=True)
class ClipStats:
    requested: int = 0
    written: int = 0
    skipped_low_score: int = 0
    skipped_plugin_not_clipped: int = 0
    skipped_rate_limited: int = 0
    skipped_disk_guard: int = 0
    failed_not_in_ring: int = 0
    failed_io: int = 0
    partial: int = 0
    bytes_written: int = 0
    expired_deleted: int = 0
    budget_deleted: int = 0
    bytes_reclaimed: int = 0


class ClipManager:
    """Extracts, writes and expires evidence clips within a fixed budget.

    Every limit here exists because of a measured failure. Clipping every acoustic
    event on the target device produced 890 MB in two minutes — roughly 640 GB a
    day — which would have filled the disk before morning. Evidence is for
    identifications; a continuous archive is explicitly out of scope.
    """

    def __init__(
        self,
        *,
        clip_dir: Path,
        pre_roll_s: float = 3.0,
        post_roll_s: float = 3.0,
        max_duration_s: float = 12.0,
        min_score: float = 0.25,
        retention_days: int = 30,
        write_playback_derivative: bool = True,
        clip_plugins: tuple[str, ...] = (),
        max_per_minute: int = 20,
        max_total_bytes: int = 20 * 1024**3,
        min_free_bytes: int = 5 * 1024**3,
        ultrasonic_audible_method: str = "both",
        ultrasonic_time_expansion_factor: float = 0.0,
        ultrasonic_target_hz: float = 4000.0,
        ultrasonic_highpass_hz: float = 12000.0,
        ultrasonic_heterodyne_bandwidth_hz: float = 5000.0,
        ultrasonic_audible_max_s: float = 60.0,
        ultrasonic_audible_min_peak_hz: float = 15000.0,
    ) -> None:
        self.clip_dir = Path(clip_dir)
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.pre_roll_s = pre_roll_s
        self.post_roll_s = post_roll_s
        self.max_duration_s = max_duration_s
        self.min_score = min_score
        self.retention_days = retention_days
        self.write_playback_derivative = write_playback_derivative
        self.clip_plugins = frozenset(clip_plugins)
        self.max_per_minute = max_per_minute
        self.max_total_bytes = max_total_bytes
        self.min_free_bytes = min_free_bytes
        self.ultrasonic_audible_method = ultrasonic_audible_method
        self.ultrasonic_time_expansion_factor = ultrasonic_time_expansion_factor
        self.ultrasonic_target_hz = ultrasonic_target_hz
        self.ultrasonic_highpass_hz = ultrasonic_highpass_hz
        self.ultrasonic_heterodyne_bandwidth_hz = ultrasonic_heterodyne_bandwidth_hz
        self.ultrasonic_audible_max_s = ultrasonic_audible_max_s
        self.ultrasonic_audible_min_peak_hz = ultrasonic_audible_min_peak_hz
        self.stats = ClipStats()
        self._recent_writes: deque[float] = deque(maxlen=max(1, max_per_minute * 4))
        self._guard_reason: str | None = None

    # ------------------------------------------------------------------

    def admits(self, plugin_id: str, score: float) -> tuple[bool, str]:
        """Decide whether one detection earns a clip, and say why not."""
        if self.clip_plugins and plugin_id not in self.clip_plugins:
            self.stats.skipped_plugin_not_clipped += 1
            return False, "plugin not in clip_plugins"
        if score < self.min_score:
            self.stats.skipped_low_score += 1
            return False, f"score {score:.3f} below {self.min_score}"

        now = time.monotonic()
        while self._recent_writes and now - self._recent_writes[0] > 60.0:
            self._recent_writes.popleft()
        if len(self._recent_writes) >= self.max_per_minute:
            self.stats.skipped_rate_limited += 1
            return False, f"rate limit of {self.max_per_minute} clips/minute reached"

        usage = shutil.disk_usage(self.clip_dir)
        if usage.free < self.min_free_bytes:
            self.stats.skipped_disk_guard += 1
            self._guard_reason = (
                f"only {usage.free / 1024**3:.1f} GB free, reserve is "
                f"{self.min_free_bytes / 1024**3:.1f} GB"
            )
            return False, self._guard_reason
        self._guard_reason = None
        return True, ""

    def should_write(self, score: float) -> bool:
        return score >= self.min_score

    # ------------------------------------------------------------------

    def enforce_retention(self) -> dict[str, int]:
        """Delete expired clips, then oldest-first until inside the size budget.

        Called from housekeeping rather than on the write path, so a slow
        filesystem walk can never stall capture.
        """
        now = time.time()
        entries: list[tuple[float, int, Path]] = []
        for path in self.clip_dir.rglob("*.wav"):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))

        removed_expired = 0
        reclaimed = 0
        if self.retention_days > 0:
            cutoff = now - self.retention_days * 86400
            surviving: list[tuple[float, int, Path]] = []
            for mtime, size, path in entries:
                if mtime < cutoff:
                    try:
                        path.unlink()
                        removed_expired += 1
                        reclaimed += size
                    except OSError:
                        surviving.append((mtime, size, path))
                else:
                    surviving.append((mtime, size, path))
            entries = surviving

        total = sum(size for _, size, _ in entries)
        removed_budget = 0
        if total > self.max_total_bytes:
            for _mtime, size, path in sorted(entries):
                if total <= self.max_total_bytes:
                    break
                try:
                    path.unlink()
                    removed_budget += 1
                    reclaimed += size
                    total -= size
                except OSError:
                    continue

        # Tidy up any empty day directories and abandoned partial writes.
        for partial in self.clip_dir.rglob("*.partial"):
            with contextlib.suppress(OSError):
                partial.unlink()
        for day in sorted(self.clip_dir.iterdir(), reverse=True):
            if day.is_dir() and not any(day.iterdir()):
                with contextlib.suppress(OSError):
                    day.rmdir()

        self.stats.expired_deleted += removed_expired
        self.stats.budget_deleted += removed_budget
        self.stats.bytes_reclaimed += reclaimed
        if removed_expired or removed_budget:
            log.info(
                "clips.retention",
                expired=removed_expired,
                over_budget=removed_budget,
                reclaimed_bytes=reclaimed,
            )
        return {
            "expired_deleted": removed_expired,
            "budget_deleted": removed_budget,
            "bytes_reclaimed": reclaimed,
        }

    def extract(
        self,
        *,
        ring: RingBuffer,
        detection_id: uuid.UUID,
        stream_id: uuid.UUID,
        event_start_frame: int,
        event_end_frame: int,
        score: float,
        label: str | None,
        event_start_utc: datetime,
        plugin_id: str = "",
        #: Drives the ultrasonic audible derivative: it is what the time-expansion
        #: factor and the heterodyne tuning are chosen from.
        peak_frequency_hz: float | None = None,
    ) -> list[ClipAsset]:
        """Write evidence for one detection. Returns the assets created."""
        self.stats.requested += 1
        admitted, reason = self.admits(plugin_id, score)
        if not admitted:
            log.debug("clip.skipped", plugin=plugin_id, reason=reason)
            return []
        self._recent_writes.append(time.monotonic())

        rate = ring.sample_rate
        pre = int(self.pre_roll_s * rate)
        post = int(self.post_roll_s * rate)
        start = max(0, event_start_frame - pre)
        end = event_end_frame + post
        max_frames = int(self.max_duration_s * rate)
        if end - start > max_frames:
            # Keep the event itself and trim the padding, not the middle.
            centre = (event_start_frame + event_end_frame) // 2
            start = max(0, centre - max_frames // 2)
            end = start + max_frames

        pcm = ring.extract(start, end, allow_partial=True)
        if pcm is None or pcm.size == 0:
            self.stats.failed_not_in_ring += 1
            log.warning(
                "clip.not_in_ring",
                detection=str(detection_id),
                requested=[start, end],
                available=ring.available_range,
            )
            return []
        if int(pcm.shape[0]) != end - start:
            self.stats.partial += 1
            end = start + int(pcm.shape[0])

        stamp = event_start_utc.strftime("%Y%m%dT%H%M%SZ")
        safe_label = _slug(label or "event")
        base = f"{stamp}_{safe_label}_{str(detection_id)[:8]}"
        day_dir = self.clip_dir / event_start_utc.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        assets: list[ClipAsset] = []
        native_kind = "evidence_native" if rate > BROWSER_SAFE_MAX_RATE else "playback"
        try:
            native_asset = self._write_wav(
                day_dir / f"{base}.wav",
                pcm,
                rate,
                kind=native_kind,
                stream_id=stream_id,
                start_frame=start,
                end_frame=end,
                detail={
                    "role": "authoritative",
                    "detection_id": str(detection_id),
                    "label": label,
                    "score": round(score, 6),
                    "pre_roll_s": self.pre_roll_s,
                    "post_roll_s": self.post_roll_s,
                },
            )
        except OSError as exc:
            self.stats.failed_io += 1
            log.error("clip.write_failed", detection=str(detection_id), error=str(exc))
            return []
        assets.append(native_asset)

        if self.write_playback_derivative and rate > BROWSER_SAFE_MAX_RATE:
            try:
                assets.append(
                    self._write_playback(
                        day_dir / f"{base}_playback.wav",
                        pcm,
                        rate,
                        stream_id=stream_id,
                        start_frame=start,
                        end_frame=end,
                        detail={"role": "playback", "detection_id": str(detection_id)},
                    )
                )
            except Exception as exc:
                # A missing derivative is a degraded outcome, not a lost clip.
                log.warning("clip.playback_failed", detection=str(detection_id), error=str(exc))

        # A high-frequency event is inaudible in both of the above: the native clip
        # is at a rate no browser will decode, and while the 48 kHz playback
        # derivative retains everything below 24 kHz, human hearing gives out well
        # before that. Render something that can actually be heard.
        if peak_frequency_hz and peak_frequency_hz >= self.ultrasonic_audible_min_peak_hz:
            try:
                assets.extend(
                    self._write_ultrasonic_audible(
                        day_dir,
                        base,
                        pcm,
                        rate,
                        stream_id=stream_id,
                        start_frame=start,
                        end_frame=end,
                        detection_id=detection_id,
                        peak_frequency_hz=peak_frequency_hz,
                    )
                )
            except Exception as exc:
                log.warning(
                    "clip.ultrasonic_audible_failed",
                    detection=str(detection_id),
                    error=str(exc),
                )

        self.stats.written += len(assets)
        self.stats.bytes_written += sum(asset.byte_length for asset in assets)
        return assets

    # ------------------------------------------------------------------

    def _write_wav(
        self,
        path: Path,
        pcm: np.ndarray,
        sample_rate: int,
        *,
        kind: str,
        stream_id: uuid.UUID,
        start_frame: int,
        end_frame: int,
        detail: dict[str, object],
    ) -> ClipAsset:
        import soundfile as sf

        temporary = path.with_name(path.name + ".partial")
        # Format is stated rather than inferred from the extension, so the atomic
        # temporary name is free to be whatever we like. 16-bit PCM is the
        # AudioMoth's own depth, so nothing is invented.
        sf.write(
            str(temporary),
            np.clip(pcm, -1.0, 1.0),
            sample_rate,
            format="WAV",
            subtype="PCM_16",
        )
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        size = temporary.stat().st_size
        temporary.replace(path)

        expires = (
            datetime.now(UTC) + timedelta(days=self.retention_days)
            if self.retention_days > 0
            else None
        )
        log.info(
            "clip.written",
            path=str(path.name),
            kind=kind,
            sample_rate=sample_rate,
            seconds=round(int(pcm.shape[0]) / sample_rate, 2),
            bytes=size,
        )
        return ClipAsset(
            asset_id=uuid.uuid4(),
            kind=kind,
            path=path,
            mime_type="audio/wav",
            sample_rate=sample_rate,
            start_frame=start_frame,
            end_frame=end_frame,
            byte_length=size,
            sha256=digest,
            expires_at=expires,
            detail=detail,
        )

    def _write_ultrasonic_audible(
        self,
        day_dir: Path,
        base: str,
        pcm: np.ndarray,
        source_rate: int,
        *,
        stream_id: uuid.UUID,
        start_frame: int,
        end_frame: int,
        detection_id: uuid.UUID,
        peak_frequency_hz: float,
    ) -> list[ClipAsset]:
        """Write the audible rendering(s) of an ultrasonic event."""
        from .audio import ultrasound

        renders = ultrasound.render(
            pcm,
            source_rate,
            method=self.ultrasonic_audible_method,
            peak_hz=peak_frequency_hz,
            target_hz=self.ultrasonic_target_hz,
            fixed_factor=self.ultrasonic_time_expansion_factor,
            highpass_hz=self.ultrasonic_highpass_hz,
            bandwidth_hz=self.ultrasonic_heterodyne_bandwidth_hz,
            max_seconds=self.ultrasonic_audible_max_s,
        )

        assets: list[ClipAsset] = []
        for rendered in renders:
            suffix = "te" if rendered.method == "time-expansion" else "het"
            asset = self._write_wav(
                day_dir / f"{base}_{suffix}.wav",
                rendered.pcm,
                rendered.sample_rate,
                kind="audible_ultrasonic",
                stream_id=stream_id,
                start_frame=start_frame,
                end_frame=end_frame,
                detail={
                    "role": rendered.method,
                    "detection_id": str(detection_id),
                    "description": rendered.description,
                    # This derivative is normalised and filtered, so its levels must
                    # not be compared with the authoritative recording's.
                    "authoritative": False,
                    **rendered.detail,
                },
            )
            assets.append(asset)
            log.info(
                "clip.ultrasonic_audible",
                detection=str(detection_id),
                method=rendered.method,
                description=rendered.description,
                seconds=round(rendered.duration_s, 2),
            )
        return assets

    def _write_playback(
        self,
        path: Path,
        pcm: np.ndarray,
        source_rate: int,
        *,
        stream_id: uuid.UUID,
        start_frame: int,
        end_frame: int,
        detail: dict[str, object],
    ) -> ClipAsset:
        from .audio.resample import AudibleResampler

        converter = AudibleResampler(source_rate, PLAYBACK_RATE)
        converted = converter.process(pcm).pcm
        asset = self._write_wav(
            path,
            converted,
            PLAYBACK_RATE,
            kind="playback",
            stream_id=stream_id,
            start_frame=start_frame,
            end_frame=end_frame,
            detail={**detail, "derived_from_rate": source_rate, "resampler": converter.backend},
        )
        return asset

    # ------------------------------------------------------------------

    def disk_usage(self) -> dict[str, object]:
        total = 0
        count = 0
        for path in self.clip_dir.rglob("*.wav"):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                continue
        usage = shutil.disk_usage(self.clip_dir)
        return {
            "clip_dir": str(self.clip_dir),
            "clip_count": count,
            "clip_bytes": total,
            "disk_total_bytes": usage.total,
            "disk_free_bytes": usage.free,
            "disk_used_ratio": round(1.0 - usage.free / usage.total, 4),
        }

    def snapshot(self) -> dict[str, object]:
        stats = self.stats
        return {
            "requested": stats.requested,
            "written": stats.written,
            "skipped_low_score": stats.skipped_low_score,
            "skipped_plugin_not_clipped": stats.skipped_plugin_not_clipped,
            "skipped_rate_limited": stats.skipped_rate_limited,
            "skipped_disk_guard": stats.skipped_disk_guard,
            "failed_not_in_ring": stats.failed_not_in_ring,
            "failed_io": stats.failed_io,
            "partial": stats.partial,
            "bytes_written": stats.bytes_written,
            "expired_deleted": stats.expired_deleted,
            "budget_deleted": stats.budget_deleted,
            "bytes_reclaimed": stats.bytes_reclaimed,
            "writes_last_minute": len(self._recent_writes),
            "policy": {
                "clip_plugins": sorted(self.clip_plugins),
                "min_score": self.min_score,
                "max_per_minute": self.max_per_minute,
                "max_total_gb": round(self.max_total_bytes / 1024**3, 2),
                "min_free_gb": round(self.min_free_bytes / 1024**3, 2),
                "pre_roll_s": self.pre_roll_s,
                "post_roll_s": self.post_roll_s,
                "retention_days": self.retention_days,
            },
            "disk_guard_active": self._guard_reason,
        }


def _slug(value: str, limit: int = 48) -> str:
    """Filesystem-safe name from arbitrary detector output.

    Detector labels come from third-party model files, so they are untrusted
    input for both the filesystem and the UI (technical spec §13).
    """
    keep = [char if char.isalnum() else "-" for char in value.strip().lower()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:limit] or "event"
