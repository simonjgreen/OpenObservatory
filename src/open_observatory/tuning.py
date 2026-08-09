"""Where a live-tier setting has to be *pushed* to take effect.

Most live-tier settings need nothing: the station reads them fresh from the
``Settings`` object each time it uses them, so mutating that object is the
whole of "applied". This module is the rest -- the settings whose value was
copied into a long-lived object at construction time (a spectrogram encoder,
a detector plugin, the clip manager, the retention sweeper) and therefore has
to be handed to that object explicitly.

Keeping the map here rather than in ``station.py`` is what makes the claim
testable: ``tests/test_tuning.py`` asserts every entry names a real setting,
a real target and a parameter the target actually accepts, so a rename cannot
quietly turn a "live" setting into one that saves and does nothing -- which is
exactly the dishonesty ADR-048's tier system exists to prevent.

Nothing here restarts, stalls or reconfigures capture. Each target either
takes a plain attribute assignment or a ``retune()`` call that rebinds
thresholds read per-window; none touches a device, a thread pool or a queue.
Charter item 1 holds: no settings write can cost a frame of audio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TargetKind = Literal["clips", "retention", "spectrogram", "detector"]


@dataclass(frozen=True)
class LiveTarget:
    """How one setting reaches the running object that uses it."""

    kind: TargetKind
    #: Attribute name (clips/retention) or ``retune()`` keyword
    #: (spectrogram/detector).
    parameter: str
    #: Spectrogram channel name or detector plugin id.
    owner: str = ""
    #: Applied value = ``factor * saved value`` -- for the two settings stored
    #: in gigabytes and used in bytes.
    scale: float = 1.0


#: setting name -> where it has to be pushed. A live-tier setting absent from
#: this map is one the station re-reads from ``Settings`` on every use.
LIVE_TARGETS: dict[str, LiveTarget] = {
    # -- spectrogram contrast (ADR-041) -------------------------------------
    "spectrogram_floor_db": LiveTarget("spectrogram", "floor_db", "audible"),
    "spectrogram_ceiling_db": LiveTarget("spectrogram", "ceiling_db", "audible"),
    "ultrasonic_spectrogram_floor_db": LiveTarget("spectrogram", "floor_db", "ultrasonic"),
    "ultrasonic_spectrogram_ceiling_db": LiveTarget("spectrogram", "ceiling_db", "ultrasonic"),
    # -- activity detector ---------------------------------------------------
    "activity_band_hz": LiveTarget("detector", "band_hz", "activity-v1"),
    "activity_min_snr_db": LiveTarget("detector", "min_snr_db", "activity-v1"),
    "activity_min_duration_ms": LiveTarget("detector", "min_duration_ms", "activity-v1"),
    # -- BirdNET bars --------------------------------------------------------
    "birdnet_min_confidence": LiveTarget("detector", "min_confidence", "birdnet-v2.4"),
    "birdnet_plausibility_floor": LiveTarget("detector", "plausibility_floor", "birdnet-v2.4"),
    "birdnet_common_prior": LiveTarget("detector", "common_prior", "birdnet-v2.4"),
    "birdnet_range_threshold": LiveTarget("detector", "range_threshold", "birdnet-v2.4"),
    "birdnet_threshold_in_range": LiveTarget("detector", "threshold_in_range", "birdnet-v2.4"),
    "birdnet_threshold_uncommon": LiveTarget("detector", "threshold_uncommon", "birdnet-v2.4"),
    "birdnet_threshold_out_of_range": LiveTarget(
        "detector", "threshold_out_of_range", "birdnet-v2.4"
    ),
    # -- ultrasonic pass detector -------------------------------------------
    "ultrasonic_band_hz": LiveTarget("detector", "band_hz", "ultrasonic-pass-v1"),
    "ultrasonic_min_snr_db": LiveTarget("detector", "min_snr_db", "ultrasonic-pass-v1"),
    "ultrasonic_min_pulse_ms": LiveTarget("detector", "min_pulse_ms", "ultrasonic-pass-v1"),
    "ultrasonic_max_pulse_ms": LiveTarget("detector", "max_pulse_ms", "ultrasonic-pass-v1"),
    "ultrasonic_merge_gap_ms": LiveTarget("detector", "merge_gap_ms", "ultrasonic-pass-v1"),
    "ultrasonic_pass_gap_s": LiveTarget("detector", "pass_gap_s", "ultrasonic-pass-v1"),
    "ultrasonic_min_pulses_per_pass": LiveTarget(
        "detector", "min_pulses_per_pass", "ultrasonic-pass-v1"
    ),
    "ultrasonic_buzz_max_interval_ms": LiveTarget(
        "detector", "buzz_max_interval_ms", "ultrasonic-pass-v1"
    ),
    "ultrasonic_buzz_min_pulses": LiveTarget("detector", "buzz_min_pulses", "ultrasonic-pass-v1"),
    "ultrasonic_buzz_interval_ratio": LiveTarget(
        "detector", "buzz_interval_ratio", "ultrasonic-pass-v1"
    ),
    # -- clip manager --------------------------------------------------------
    "clip_pre_roll_s": LiveTarget("clips", "pre_roll_s"),
    "clip_post_roll_s": LiveTarget("clips", "post_roll_s"),
    "clip_max_s": LiveTarget("clips", "max_duration_s"),
    "clip_min_score": LiveTarget("clips", "min_score"),
    "clip_retention_days": LiveTarget("clips", "retention_days"),
    "clip_plugins": LiveTarget("clips", "clip_plugins"),
    "clip_max_per_minute": LiveTarget("clips", "max_per_minute"),
    "clip_max_total_gb": LiveTarget("clips", "max_total_bytes", scale=1024**3),
    "clip_min_free_gb": LiveTarget("clips", "min_free_bytes", scale=1024**3),
    "ultrasonic_audible_method": LiveTarget("clips", "ultrasonic_audible_method"),
    "ultrasonic_time_expansion_factor": LiveTarget("clips", "ultrasonic_time_expansion_factor"),
    "ultrasonic_target_hz": LiveTarget("clips", "ultrasonic_target_hz"),
    "ultrasonic_highpass_hz": LiveTarget("clips", "ultrasonic_highpass_hz"),
    "ultrasonic_heterodyne_bandwidth_hz": LiveTarget(
        "clips", "ultrasonic_heterodyne_bandwidth_hz"
    ),
    "ultrasonic_audible_max_s": LiveTarget("clips", "ultrasonic_audible_max_s"),
    "ultrasonic_audible_min_peak_hz": LiveTarget("clips", "ultrasonic_audible_min_peak_hz"),
    # -- retention sweeper ---------------------------------------------------
    "retention_native_days": LiveTarget("retention", "native_days"),
    "retention_audible_only_days": LiveTarget("retention", "audible_only_days"),
    "retention_exemplar_only_days": LiveTarget("retention", "exemplar_only_days"),
    "retention_watermark_ratio": LiveTarget("retention", "watermark_ratio"),
    "retention_batch_size": LiveTarget("retention", "batch_size"),
    "retention_batch_budget_s": LiveTarget("retention", "batch_budget_s"),
}

#: Bound when the process starts -- logging is configured once, the metrics
#: endpoint is mounted once, and the replay/synthetic sources and the deferred
#: queue are constructed from these. Recorded at ``Station.start`` so that
#: editing one is reported as pending rather than silently doing nothing.
PINNED_AT_PROCESS_START: tuple[str, ...] = (
    "log_level",
    "log_json",
    "metrics_enabled",
    "deferred_enabled",
    "deferred_queue_depth",
    "replay_loop",
    "replay_speed",
    "synthetic_scene",
    "synthetic_sample_rate",
)

#: Settings the running pipeline binds once and never re-reads, tracked in
#: ``Station.applied_site`` so any daylight between saved and applied is
#: reported. Coordinates are the original members (ADR-047); the rest are the
#: restart-pinned fields whose value a running component genuinely holds.
PINNED_AT_DETECTOR_START: tuple[str, ...] = (
    "latitude",
    "longitude",
    "activity_enabled",
    "birdnet_enabled",
    "birdnet_use_location_filter",
    "birdnet_window_stride_s",
    "ultrasonic_enabled",
    "ultrasonic_schedule",
    "ultrasonic_schedule_dusk_margin_min",
    "ultrasonic_schedule_dawn_margin_min",
    "detector_queue_depth",
)

#: The same, for the capture path and the spectrogram geometry. Recorded when
#: capture negotiates with the device, so "saved, awaiting restart" is a fact
#: about this process rather than a guess.
PINNED_AT_CAPTURE_START: tuple[str, ...] = (
    "source",
    "audio_device",
    "preferred_sample_rates",
    "preferred_formats",
    "capture_channels",
    "capture_block_ms",
    "capture_buffer_ms",
    "native_ring_seconds",
    "audible_ring_seconds",
    "audible_sample_rate",
    "spectrogram_fft",
    "spectrogram_hop_ms",
    "spectrogram_bins",
    "spectrogram_min_hz",
    "spectrogram_max_hz",
    "spectrogram_history_columns",
)
