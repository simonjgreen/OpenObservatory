"""Prometheus exposition.

Metrics are generated from the station's live snapshot rather than being
incremented at call sites. The station already maintains every counter the
observability section of the technical spec asks for, and reading them once per
scrape keeps instrumentation out of the capture hot path entirely.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import psutil
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest


class PrometheusExporter:
    def __init__(self, station: Any) -> None:
        self.station = station
        self._process = psutil.Process(os.getpid())
        self._registry = CollectorRegistry()
        self._gauges: dict[str, Gauge] = {}
        self._started = time.time()

    # ------------------------------------------------------------------

    def _gauge(self, name: str, documentation: str, labels: tuple[str, ...] = ()) -> Gauge:
        if name not in self._gauges:
            self._gauges[name] = Gauge(
                name, documentation, list(labels), registry=self._registry
            )
        return self._gauges[name]

    def _set(self, name: str, doc: str, value: float | None, **labels: str) -> None:
        if value is None:
            return
        gauge = self._gauge(name, doc, tuple(labels))
        (gauge.labels(**labels) if labels else gauge).set(float(value))

    def render(self) -> tuple[bytes, str]:
        snapshot = self.station.status_snapshot()
        capture = snapshot["capture"]

        self._set("oo_up", "1 when the station process is running", 1.0)
        self._set(
            "oo_capture_state",
            "1 when capture is running from any source",
            1.0 if capture["state"] == "capturing" else 0.0,
        )
        self._set(
            "oo_capture_live_hardware",
            "1 when capturing from the real microphone rather than a synthetic source",
            1.0 if capture["is_live_hardware"] else 0.0,
        )
        self._set(
            "oo_capture_frames_total",
            "Frames captured in the current stream",
            capture["frames"],
        )
        self._set("oo_capture_blocks_total", "Capture blocks read", capture["blocks"])
        self._set(
            "oo_capture_discontinuities_total",
            "Capture discontinuities observed",
            capture["discontinuities"],
        )
        self._set(
            "oo_capture_missing_frames_total",
            "Estimated frames lost to overruns and device resets",
            capture["estimated_missing_frames"],
        )
        self._set(
            "oo_capture_continuity_ratio",
            "Frames captured divided by frames expected from elapsed monotonic time",
            capture["continuity_ratio"],
        )
        self._set(
            "oo_capture_block_age_seconds",
            "Age of the most recent capture block",
            capture["block_age_s"],
        )
        self._set(
            "oo_capture_sample_rate_hz", "Negotiated capture sample rate", capture["sample_rate"]
        )
        self._set(
            "oo_capture_stream_restarts_total", "Stream reopen count", capture["stream_restarts"]
        )
        self._set(
            "oo_capture_hot_path_cpu_ratio",
            "Seconds of CPU in the per-block hot path per second of audio",
            capture["hot_path_cpu_ratio"],
        )

        levels = snapshot["levels"]
        for name, sample in (("native", levels["native"]), ("audible", levels["audible"])):
            if not sample:
                continue
            self._set(
                "oo_audio_rms_dbfs",
                "Audio RMS in dBFS (uncalibrated)",
                sample["rms_dbfs"],
                stream=name,
            )
            self._set(
                "oo_audio_peak_dbfs",
                "Audio peak in dBFS (uncalibrated)",
                sample["peak_dbfs"],
                stream=name,
            )
            self._set(
                "oo_audio_clipping_ratio",
                "Fraction of samples at or beyond full scale",
                sample["clipping_ratio"],
                stream=name,
            )

        for name, ring in snapshot["rings"].items():
            if not ring:
                continue
            self._set(
                "oo_ring_fill_ratio", "Ring buffer occupancy", ring["fill_ratio"], ring=name
            )
            self._set(
                "oo_ring_held_seconds", "Seconds of audio retained", ring["held_seconds"], ring=name
            )
            self._set(
                "oo_ring_extraction_misses_total",
                "Evidence extractions that could not be served",
                ring["extraction_misses"],
                ring=name,
            )
            self._set(
                "oo_ring_bytes", "Approximate ring buffer memory", ring["estimated_bytes"], ring=name
            )

        if snapshot["resampler"]:
            resampler = snapshot["resampler"]
            self._set(
                "oo_resampler_delivery_deficit_frames",
                "Frames still inside the resampler: bounded delivery latency, not drift",
                resampler["delivery_deficit_frames"],
            )
            self._set(
                "oo_resampler_group_delay_frames",
                "Offset between an output frame and the native frame it maps to",
                resampler["group_delay_frames"],
            )

        for detector in snapshot["detectors"]:
            plugin = detector["plugin_id"]
            self._set(
                "oo_detector_available",
                "1 when a detector is running",
                1.0 if detector["state"] in ("ok", "degraded") else 0.0,
                plugin_id=plugin,
            )
            self._set(
                "oo_detector_queue_depth", "Windows queued", detector["queue_depth"], plugin_id=plugin
            )
            self._set(
                "oo_detector_windows_analysed_total",
                "Windows analysed",
                detector["windows_analysed"],
                plugin_id=plugin,
            )
            self._set(
                "oo_detector_windows_dropped_total",
                "Windows dropped for a full queue or exceeded delivery deadline",
                detector["windows_dropped_queue_full"] + detector["windows_dropped_stale"],
                plugin_id=plugin,
            )
            self._set(
                "oo_detector_detections_total",
                "Native detections emitted",
                detector["detections_emitted"],
                plugin_id=plugin,
            )
            self._set(
                "oo_detector_failures_total",
                "Analysis failures",
                detector["failures"],
                plugin_id=plugin,
            )
            self._set(
                "oo_detector_lag_seconds",
                "now - window_end for the last analysed window",
                detector["lag_s"],
                plugin_id=plugin,
            )
            self._set(
                "oo_detector_runtime_p95_ms",
                "p95 analysis runtime",
                detector["p95_runtime_ms"],
                plugin_id=plugin,
            )

            deferred = detector.get("deferred")
            self._set(
                "oo_detector_deferred_mode",
                "1 when a detector runs off the live path through a bounded deferred queue",
                1.0 if deferred else 0.0,
                plugin_id=plugin,
            )
            if deferred:
                self._set(
                    "oo_detector_deferred_oldest_queued_age_seconds",
                    "Age of the longest-waiting window still in the deferred queue",
                    deferred["oldest_queued_age_s"],
                    plugin_id=plugin,
                )
                self._set(
                    "oo_detector_deferred_items_abandoned_shutdown_total",
                    "Deferred windows abandoned, lease released without analysis, at shutdown",
                    deferred["items_abandoned_on_shutdown"],
                    plugin_id=plugin,
                )

        clips = snapshot["clips"]
        self._set("oo_clips_written_total", "Evidence clips written", clips["written"])
        self._set(
            "oo_clips_failed_total",
            "Clip writes that failed",
            clips["failed_io"] + clips["failed_not_in_ring"],
        )
        self._set(
            "oo_clips_bytes_total", "Bytes of evidence written this run", clips["bytes_written"]
        )

        storage = snapshot["storage"]
        self._set(
            "oo_storage_free_bytes", "Free space on the clip filesystem", storage["disk_free_bytes"]
        )
        self._set(
            "oo_storage_clip_bytes", "Bytes currently held in clips", storage["clip_bytes"]
        )
        self._set(
            "oo_storage_used_ratio",
            "Fraction of the clip filesystem in use",
            storage["disk_used_ratio"],
        )

        retention = snapshot["retention"]
        self._set(
            "oo_retention_watermark_ratio",
            "Disk-used fraction above which oldest-first reclaim runs regardless of tier",
            retention["watermark_ratio"],
        )
        self._set(
            "oo_retention_sweep_duration_seconds",
            "Wall-clock duration of the last retention sweep",
            retention["last_sweep_duration_s"],
        )
        self._set(
            "oo_retention_sweep_complete",
            "1 when the last sweep finished within its batch budget with no backlog left",
            1.0 if retention["last_sweep_complete"] else 0.0,
        )
        last_sweep_at = self.station.retention.last_sweep_at if self.station.retention else None
        self._set(
            "oo_retention_last_sweep_timestamp_seconds",
            "Unix timestamp of the last retention sweep",
            last_sweep_at.timestamp() if last_sweep_at else None,
        )
        self._set(
            "oo_retention_exemplar_detections",
            "Detections currently exempt from the 30-90 day cull as first- or best-of-species",
            retention["exemplar_detections"],
        )
        for tier in ("native", "exemplar_only", "expired", "watermark"):
            self._set(
                "oo_retention_files_deleted_total",
                "Clip files deleted by the retention sweeper",
                retention["totals"].get(f"{tier}_deleted", 0),
                tier=tier,
            )
            self._set(
                "oo_retention_bytes_reclaimed_total",
                "Clip bytes reclaimed by the retention sweeper",
                retention["totals"].get(f"{tier}_bytes", 0),
                tier=tier,
            )

        self._set(
            "oo_live_audio_listeners",
            "Browsers listening to live audible audio",
            snapshot["live_audio"]["listeners"],
        )
        self._set(
            "oo_live_audio_ultrasonic_listeners",
            "Browsers listening to the live heterodyne channel",
            snapshot["live_audio_ultrasonic"]["listeners"],
        )
        self._set(
            "oo_bus_published_total",
            "Events published on the internal bus",
            snapshot["bus"]["published"],
        )
        self._set(
            "oo_bus_dropped_total",
            "Events dropped by slow subscribers",
            sum(entry["dropped"] for entry in snapshot["bus"]["per_subscriber"]),
        )

        persistence = snapshot["persistence"]
        self._set(
            "oo_detections_persisted_total",
            "Detections written to the database",
            persistence["written"],
        )
        self._set(
            "oo_detections_persist_dropped_total",
            "Detections dropped before persistence",
            persistence["dropped"],
        )
        self._set(
            "oo_detections_persist_failures_total",
            "Database write failures",
            persistence["failures"],
        )

        stats = self.process_stats()
        self._set("oo_process_cpu_percent", "Process CPU usage", stats["cpu_percent"])
        self._set(
            "oo_process_memory_rss_bytes", "Process resident memory", stats["memory_rss_bytes"]
        )
        self._set("oo_process_threads", "Process thread count", stats["threads"])
        self._set("oo_host_load1", "Host one-minute load average", stats["load1"])
        self._set(
            "oo_host_cpu_temperature_celsius", "SoC temperature", stats["cpu_temperature_c"]
        )

        return generate_latest(self._registry), CONTENT_TYPE_LATEST

    # ------------------------------------------------------------------

    def process_stats(self) -> dict[str, Any]:
        temperature = None
        raw = Path("/sys/class/thermal/thermal_zone0/temp")
        if raw.exists():
            try:
                temperature = int(raw.read_text().strip()) / 1000.0
            except (OSError, ValueError):
                temperature = None
        with self._process.oneshot():
            memory = self._process.memory_info()
            return {
                "pid": self._process.pid,
                "uptime_s": round(time.time() - self._started, 1),
                "cpu_percent": self._process.cpu_percent(interval=None),
                "memory_rss_bytes": memory.rss,
                "threads": self._process.num_threads(),
                "load1": os.getloadavg()[0],
                "cpu_count": psutil.cpu_count(),
                "cpu_temperature_c": temperature,
                "host_memory_available_bytes": psutil.virtual_memory().available,
            }
