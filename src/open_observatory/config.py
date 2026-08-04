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

    # ---- capture ----------------------------------------------------------
    source: SourceKind = "auto"
    #: Stable device key, e.g. ``usb-10c4:0002:0100`` or an ALSA card id.
    audio_device: str | None = None
    #: Tried in order; the first that the device accepts is used.
    preferred_sample_rates: tuple[int, ...] = (384000, 250000, 192000, 96000, 48000)
    preferred_formats: tuple[str, ...] = ("S16_LE", "S32_LE")
    capture_channels: int = 1
    capture_block_ms: int = 100
    native_ring_seconds: int = 120
    audible_ring_seconds: int = 120
    #: Reopen backoff bounds after a device disappears.
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

    detector_queue_depth: int = 16

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

    # ---- api --------------------------------------------------------------
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    #: Serve the built debug UI from this directory when it exists.
    web_dist: Path = REPO_ROOT / "web" / "dist"
    log_level: str = "INFO"
    log_json: bool = False

    metrics_enabled: bool = True

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
