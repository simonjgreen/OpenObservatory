"""Runtime configuration.

Every knob is settable through the environment with an ``OO_`` prefix, matching
``config/example.env``. Nothing here reads the network and nothing here has a
cloud default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SourceKind = Literal["auto", "alsa", "replay", "synthetic"]

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OO_",
        env_file=(REPO_ROOT / "config" / "runtime.env", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- station identity -------------------------------------------------
    station_name: str = "Garden Observatory"
    timezone: str = "Europe/London"
    latitude: float | None = None
    longitude: float | None = None

    # ---- storage ----------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"
    database_dsn: str = ""
    clip_retention_days: int = 30
    #: True when evidence clips live on their own device (a USB SSD), so health can
    #: say so when the mount is missing. Writing clips to the SD card alongside
    #: capture is what caused ALSA overruns on 2026-08-08; falling back to it
    #: silently would reintroduce that without anyone noticing. Capture still wins:
    #: the station keeps recording, it just reports itself degraded.
    clips_require_mount: bool = False

    # ---- capture ----------------------------------------------------------
    source: SourceKind = "auto"
    #: Stable device key, e.g. ``usb-10c4:0002:0100`` or an ALSA card id.
    audio_device: str | None = None
    #: Tried in order; the first that the device accepts is used.
    preferred_sample_rates: tuple[int, ...] = (384000, 250000, 192000, 96000, 48000)
    preferred_formats: tuple[str, ...] = ("S16_LE", "S32_LE")
    capture_channels: int = 1
    capture_block_ms: int = 100
    #: Depth of the kernel-side ALSA ring, and so the longest stall the capture
    #: path can absorb before audio is lost. This was effectively 80 ms — shorter
    #: than one 100 ms block — until 2026-08-08; see ADR-030. Deeper costs a few
    #: hundred kilobytes and no latency, since a read still returns as soon as a
    #: block's worth of frames exists.
    capture_buffer_ms: float = 500.0
    native_ring_seconds: int = 120
    audible_ring_seconds: int = 120
    #: Reopen backoff bounds after a device disappears.
    #: While running on the *fallback* synthetic source, how often to look for the
    #: real device coming back. Without this the station degrades gracefully and
    #: then never recovers: the synthetic source never ends, so the capture
    #: supervisor never rebuilds and a reattached microphone goes unnoticed until
    #: someone restarts the service. Measured on 2026-08-08, that cost a day of
    #: recording after the AudioMoth's mode switch was moved.
    hardware_recheck_s: float = 30.0
    reopen_backoff_min_s: float = 1.0
    reopen_backoff_max_s: float = 30.0

    # ---- derived audible stream ------------------------------------------
    audible_sample_rate: int = 48000

    # ---- replay / synthetic ----------------------------------------------
    replay_path: Path | None = None
    replay_loop: bool = True
    replay_speed: float = 1.0
    synthetic_scene: str = "dawn-chorus"
    synthetic_sample_rate: int = 48000

    # ---- spectrogram / live view -----------------------------------------
    spectrogram_fft: int = 2048
    spectrogram_hop_ms: float = 24.0
    spectrogram_bins: int = 192
    spectrogram_min_hz: float = 80.0
    spectrogram_max_hz: float = 15000.0
    spectrogram_floor_db: float = -95.0
    spectrogram_ceiling_db: float = -15.0
    #: Columns retained server-side so a newly-connected browser sees history.
    spectrogram_history_columns: int = 2400
    #: Seconds of history pushed to a client on connect. Matches the UI's default
    #: view; the full retained history is ~770 kB across both channels and sending
    #: all of it in one burst delayed the audio consumer on the same event loop.
    spectrogram_backfill_s: float = 30.0

    # ---- detectors --------------------------------------------------------
    activity_enabled: bool = True
    #: Calibrated against measured noise; see detectors/activity.py.
    activity_min_snr_db: float = 18.0
    activity_min_duration_ms: float = 60.0
    activity_band_hz: tuple[float, float] = (1200.0, 11000.0)

    birdnet_enabled: bool = True
    birdnet_model_dir: Path | None = None
    birdnet_min_confidence: float = 0.12
    birdnet_window_stride_s: float = 1.5
    #: Optional latitude/longitude/week species filtering, when the model supports it.
    birdnet_use_location_filter: bool = False
    #: ADR-032. Occurrence probability at or below which the range model's
    #: verdict is treated as "not a candidate at any score", not just a higher
    #: bar -- BirdNET scores are not calibrated probabilities, so no score can
    #: overrule a near-zero prior. Measured on the live station: implausible
    #: North American owls (Flammulated Owl, Great Horned Owl) sit at
    #: 8e-06-1.6e-04; a genuine, seasonally-uncommon Tawny Owl sits at
    #: 0.019253. The default sits comfortably between the two, with margin on
    #: both sides -- it must keep the Tawny Owl and reject the owls.
    birdnet_plausibility_floor: float = 0.0005

    detector_queue_depth: int = 16

    # ---- deferred-mode detector queue --------------------------------------
    #: DETECTOR_STRATEGY.md: "use a bounded deferred-night queue and process
    #: windows after capture" for a detector too slow to run inline. This is a
    #: general capability of DetectorWorker (detectors/deferred.py), not tied to
    #: any specific model — no plugin ships enabled by default, since BatDetect2
    #: remains evaluated, not adopted (ADR-017). Off unless a future adapter
    #: both declares itself deferred and this is turned on.
    deferred_enabled: bool = False
    #: Much larger than ``detector_queue_depth``: this queue is sized to hold a
    #: backlog across a capture session, not a few seconds of live windows.
    deferred_queue_depth: int = 512
    #: Bounded shutdown drain: give the queue this long to finish naturally,
    #: then abandon (and release the lease for) whatever remains. A window
    #: already handed to the analysis thread cannot be interrupted mid-call, so
    #: this bounds when new items stop being started, not total shutdown time.
    deferred_shutdown_drain_timeout_s: float = 5.0

    # ---- evidence clips ---------------------------------------------------
    clips_enabled: bool = True
    clip_pre_roll_s: float = 3.0
    clip_post_roll_s: float = 3.0
    clip_max_s: float = 12.0
    clip_min_score: float = 0.25
    #: Only these detectors produce evidence clips. The activity detector fires on
    #: every energy blip — several times a second in a live garden — and a 7-second
    #: clip of 384 kHz mono is about 5 MB, so clipping it wrote 640 GB/day when
    #: first measured on the target. Clips are evidence for identifications, not a
    #: continuous archive, which the audio pipeline spec rules out by default.
    clip_plugins: tuple[str, ...] = ("birdnet-v2.4", "ultrasonic-pass-v1")
    #: Hard rate limit across all detectors, so a pathological night cannot fill
    #: the disk however the per-plugin rules are configured.
    clip_max_per_minute: int = 20
    #: Total budget for the clip directory. Oldest clips are deleted first.
    clip_max_total_gb: float = 20.0
    #: Stop writing clips entirely when the filesystem has less than this free,
    #: so evidence writing can never threaten the database (technical spec §12).
    clip_min_free_gb: float = 5.0

    # ---- storage retention (NVR-style tiering) -----------------------------
    #: Detection metadata (species, timestamps, scores, capture coverage) is
    #: never subject to this policy and is kept forever -- only the clip
    #: *bytes* age out. See ADR-026 and `retention.py`.
    retention_enabled: bool = True
    #: Age at which the native, full-rate WAV is deleted. The audible
    #: rendering (playback derivative / audible_ultrasonic) survives past this
    #: point -- only ``evidence_native`` assets are affected.
    retention_native_days: int = 7
    #: Age at which everything except the first-ever and best-of-species clip
    #: is deleted. Below this age, every detection keeps its audible clip;
    #: above it, only exemplar detections do.
    retention_audible_only_days: int = 30
    #: Age at which even the exemplar clips are deleted. Detection rows are
    #: never deleted by this or any other tier.
    retention_exemplar_only_days: int = 90
    #: Continuous oldest-first reclaim kicks in above this fraction of the
    #: clip filesystem used, regardless of tier or exemplar status -- the
    #: NVR "overwrite oldest before the disk fills" behaviour the operator
    #: asked for.
    retention_watermark_ratio: float = 0.85
    #: Bounded work per `RetentionSweeper.sweep()` call, so a large backlog
    #: drains over many housekeeping ticks instead of one long stall.
    retention_batch_size: int = 200
    #: Wall-clock budget per sweep call. A sweep that would exceed this stops
    #: partway through and picks up where it left off next tick.
    retention_batch_budget_s: float = 1.5
    #: How often the sweep runs, in seconds, rounded up to the nearest 10 s
    #: housekeeping tick. Measured on the live station 2026-08-08: sweeping on
    #: every tick cost 1.6 false `capture.gap` records per minute, because a
    #: ~0.30 s sweep in the evidence thread starves the event loop for 55-150 ms
    #: and the loop still has to issue and consume every capture read (ADR-033).
    #: Deletion is not urgent -- the watermark reclaim has days of headroom at
    #: any plausible fill rate -- so the sweep is paced instead.
    retention_interval_s: float = 300.0

    # ---- making ultrasound listenable ------------------------------------
    #: How to render an ultrasonic event into something a human can hear.
    #:
    #: ``time-expansion`` — replay slowed by a factor, so a 45 kHz call becomes a
    #:   4.5 kHz one. Preserves every harmonic and the exact call shape, which is
    #:   what makes it the standard for analysis. Duration is multiplied.
    #: ``heterodyne`` — mix against a local oscillator tuned near the call, as a
    #:   handheld bat detector does. Keeps real-time duration; discards everything
    #:   outside the tuned band, so it is for listening, not measurement.
    #: ``both`` — write one of each.
    #: ``none`` — disable.
    ultrasonic_audible_method: Literal["time-expansion", "heterodyne", "both", "none"] = "both"
    #: Time-expansion factor. 0 chooses one per detection so the call lands near
    #: ``ultrasonic_target_hz``, which keeps a 25 kHz and a 110 kHz species equally
    #: audible instead of favouring whichever the fixed factor happened to suit.
    ultrasonic_time_expansion_factor: float = 0.0
    ultrasonic_target_hz: float = 4000.0
    #: Everything below this is removed first. Wind and traffic rumble would
    #: otherwise be shifted down into a sub-audible thump that masks the call.
    ultrasonic_highpass_hz: float = 12000.0
    #: Bandwidth kept either side of the tuned frequency when heterodyning.
    ultrasonic_heterodyne_bandwidth_hz: float = 5000.0
    #: Cap on the *expanded* clip length, since expansion multiplies duration.
    ultrasonic_audible_max_s: float = 60.0
    #: Render anything peaking above this. Set at 15 kHz rather than the 24 kHz
    #: Nyquist of the playback derivative because content between the two is
    #: technically present in that file and yet effectively inaudible — most adults
    #: cannot hear 18 kHz. Bush-crickets and the lower bat calls both live there.
    ultrasonic_audible_min_peak_hz: float = 15000.0

    # ---- live heterodyne monitoring ---------------------------------------
    #: Default tuning frequency for the live "GO LIVE -> ultrasonic" listen
    #: channel (`heterodyne_stream.StreamingHeterodyne`), independent of
    #: ``ultrasonic_target_hz`` above: that setting picks where a *rendered
    #: clip's* time-expansion lands in the audible band, not where a live
    #: monitor is tuned. 45 kHz sits in the common pipistrelle range, a
    #: reasonable default for a UK garden station; the UI makes it adjustable
    #: because a bat detector tuned to the wrong band hears nothing useful.
    ultrasonic_live_tune_hz: float = 45000.0
    #: Bandwidth kept either side of the live tuning frequency. Shares
    #: ``ultrasonic_heterodyne_bandwidth_hz`` with the clip renderer
    #: deliberately — both describe the same "how selective is the mix"
    #: question, just for a live oscillator instead of a fixed one.

    # ---- ultrasonic pass detector -------------------------------------------
    #: Off switch. The detector needs a native stream at 96 kHz or above anyway,
    #: but this lets an operator disable it without touching capture config.
    ultrasonic_enabled: bool = True
    #: Matches UltrasonicDetector's own default band; below 15 kHz is where the
    #: audible activity/BirdNET detectors already have coverage.
    ultrasonic_band_hz: tuple[float, float] = (15000.0, 125000.0)
    #: Matches UltrasonicDetector's own default; see detectors/ultrasonic.py.
    ultrasonic_min_snr_db: float = 12.0
    #: Matches UltrasonicDetector's own default pulse-duration bounds — short
    #: enough to exclude sustained tones, long enough to exclude clicks.
    ultrasonic_min_pulse_ms: float = 1.5
    ultrasonic_max_pulse_ms: float = 40.0
    #: Onset-to-onset spacing below which two threshold crossings are treated as
    #: fragments of one echolocation call: a sweep's envelope dips mid-call and
    #: arrives as several crossings. Measured onset to onset so a buzz's short,
    #: fast calls are never merged; kept well under their 5 ms spacing.
    ultrasonic_merge_gap_ms: float = 2.0
    #: Matches UltrasonicDetector's own default; pulses closer together than this
    #: belong to the same pass.
    ultrasonic_pass_gap_s: float = 1.5
    #: Matches UltrasonicDetector's own default; fewer pulses than this is more
    #: likely a stray transient than a genuine echolocation pass.
    ultrasonic_min_pulses_per_pass: int = 3
    #: A feeding buzz is a terminal collapse in inter-pulse interval as a bat
    #: closes on prey; these three thresholds describe that collapse. Defaults
    #: are a starting point pending the false-positive review in Milestone 5
    #: item 6, not a calibrated result.
    ultrasonic_buzz_max_interval_ms: float = 12.0
    ultrasonic_buzz_min_pulses: int = 5
    ultrasonic_buzz_interval_ratio: float = 0.4
    #: ``always`` runs the detector continuously, so upgrading an existing
    #: station changes nothing until this is set to ``night``. See schedule.py:
    #: with coordinates unset, ``night`` still runs continuously rather than
    #: silently detecting nothing overnight.
    ultrasonic_schedule: Literal["always", "night"] = "always"
    ultrasonic_schedule_dusk_margin_min: float = 30.0
    ultrasonic_schedule_dawn_margin_min: float = 30.0

    # ---- api --------------------------------------------------------------
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    #: Serve the built debug UI from this directory when it exists.
    web_dist: Path = REPO_ROOT / "web" / "dist"
    log_level: str = "INFO"
    log_json: bool = False

    metrics_enabled: bool = True

    # ---- MQTT / Home Assistant (Milestone 6) -------------------------------
    #: Off by default: an operator who upgrades an existing station gets no new
    #: network traffic, no new failure mode, until they opt in. Never required
    #: for core capture/detection/review/query per the operating brief.
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    #: Skip broker certificate verification. Only meaningful with mqtt_tls;
    #: for a self-signed cert on a LAN broker during setup. Off by default —
    #: silently accepting any certificate defeats the point of TLS.
    mqtt_tls_insecure: bool = False
    #: Never hardcoded, never committed: set via config/runtime.env, which is
    #: gitignored, same as every other secret in this project.
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_client_id: str = "open-observatory"
    #: `{prefix}/{station_id}/...` is the actual topic root; see mqtt/publisher.py.
    mqtt_topic_prefix: str = "openobservatory"
    mqtt_qos: int = 1
    #: State topics (availability, capture, health, discovery configs) are
    #: retained so a client connecting after the fact — including HA itself
    #: on restart — sees current state without waiting for the next event.
    #: `detection` and `alert` are never retained regardless of this flag.
    mqtt_retain_state: bool = True
    mqtt_discovery_enabled: bool = True
    #: HA's own default; matches an operator's HA instance with no configuration.
    mqtt_discovery_prefix: str = "homeassistant"
    #: Bounded per-subscriber queue on the EventBus (events.py), same drop-
    #: oldest-and-count policy as every other consumer. Sized larger than the
    #: live detector queues because MQTT is not a live view — a broker outage
    #: of a few minutes should not lose a night's detections outright.
    mqtt_queue_depth: int = 256
    mqtt_reconnect_min_s: float = 1.0
    mqtt_reconnect_max_s: float = 60.0
    mqtt_keepalive_s: int = 30
    #: How often the health/capture/metric sensors are (re)published even
    #: without a triggering event, so a stale broker session is visibly stale
    #: rather than silently frozen on old retained values.
    mqtt_health_publish_interval_s: float = 15.0
    #: `binary_sensor.<station>_bat_activity` is "on" for this long after the
    #: most recent bat pass, then reverts to "off" on its own.
    mqtt_bat_activity_window_s: float = 900.0
    #: Publish detections that name nothing -- `activity-v1`'s "acoustic event"
    #: and anything else with no species and no taxonomic group.
    #:
    #: Off by default, matching the debug UI, which has hidden unidentified
    #: events by default since Milestone 2 for the same reason: they are the
    #: overwhelming majority (154 of 200 consecutive rows sampled on the live
    #: station, 2026-08-08) and they drown the identifications in Home
    #: Assistant, where every message becomes a state change and an entity
    #: history entry.
    #:
    #: Bat passes are NOT affected: `ultrasonic-pass-v1` names a pass rather
    #: than a species, which is a positive claim about what happened, not an
    #: absence of one.
    mqtt_publish_unidentified: bool = False

    @field_validator("mqtt_qos")
    @classmethod
    def _validate_qos(cls, value: int) -> int:
        if value not in (0, 1, 2):
            raise ValueError(f"mqtt_qos must be 0, 1 or 2, got {value}")
        return value

    @field_validator("preferred_sample_rates", "preferred_formats", "clip_plugins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("activity_band_hz", mode="before")
    @classmethod
    def _split_band(cls, value: object) -> object:
        if isinstance(value, str):
            low, _, high = value.partition(",")
            return (float(low), float(high))
        return value

    @property
    def resolved_database_dsn(self) -> str:
        if self.database_dsn:
            return self.database_dsn
        # ADR-007: SQLite is the developer/debug default.
        return f"sqlite+pysqlite:///{self.data_dir / 'openobservatory.sqlite'}"

    @property
    def clip_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def transient_dir(self) -> Path:
        return self.data_dir / "transient"

    @property
    def fixtures_dir(self) -> Path:
        return REPO_ROOT / "tests" / "fixtures"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.clip_dir, self.transient_dir):
            path.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Override the process-wide settings. Used by tests and the CLI."""
    global _settings
    _settings = settings
