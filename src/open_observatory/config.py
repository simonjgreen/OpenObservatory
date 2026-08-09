"""Runtime configuration.

Every knob is settable through the environment with an ``OO_`` prefix, matching
``config/example.env``. Nothing here reads the network and nothing here has a
cloud default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    #: `NoDecode`: see `auth_public_read_paths` below for why -- this is the
    #: field that bug was originally found against, whenever it is actually
    #: set (see `config/example.env`).
    preferred_sample_rates: Annotated[tuple[int, ...], NoDecode] = (
        384000,
        250000,
        192000,
        96000,
        48000,
    )
    preferred_formats: Annotated[tuple[str, ...], NoDecode] = ("S16_LE", "S32_LE")
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
    #: Live viewers required before the spectrogram encoders run at all
    #: (ADR-040). The station's steady state is no browser connected -- the wall
    #: display is the first-class surface -- and the two encoders measured
    #: 0.0554 of a core on the target against a whole-hot-path 0.1067, so more
    #: than half the per-block work was being done for nobody. Set to 0 for the
    #: pre-ADR-040 behaviour of always encoding, which is what a station whose
    #: operator really does keep a browser open all day should do.
    spectrogram_encode_min_viewers: int = 1
    #: Keep the audible encoder running even with nobody watching, so a browser
    #: opening gets instant history on the channel most viewers look at first.
    #: Off by default: on the target it is not the cheap one it was assumed to
    #: be (0.0261 of a core against the ultrasonic channel's 0.0293), so it buys
    #: back half the history for 47% of the saving.
    spectrogram_keep_audible_warm: bool = False

    # ---- inside-observer push channel (ADR-038) ---------------------------
    #: Seconds between heartbeat frames on `/api/v1/display`. Also what the
    #: display uses to decide the feed has gone stale (it allows three misses),
    #: so lengthening this makes a dead station take longer to look dead.
    display_channel_heartbeat_s: float = 10.0
    #: Detection rows fetched once, on connect, so a display joining mid-day is
    #: not blank. Six is what the 240x320 panel renders; the query reads a
    #: multiple of it and collapses repeats down to this.
    display_channel_snapshot_rows: int = 6
    #: Frames a display may fall behind by before the oldest detection is shed.
    #: Bounded like every other queue here: capture always wins, and this channel
    #: must never be able to apply back-pressure to anything upstream.
    display_channel_queue_max: int = 64

    # ---- detectors --------------------------------------------------------
    activity_enabled: bool = True
    #: Calibrated against measured noise; see detectors/activity.py.
    activity_min_snr_db: float = 18.0
    activity_min_duration_ms: float = 60.0
    #: `NoDecode`: same pre-existing pydantic-settings bug as
    #: `preferred_sample_rates`, found while auditing for it -- not
    #: previously reported to crash because nothing in this repository or
    #: `config/example.env` sets it from the environment yet.
    activity_band_hz: Annotated[tuple[float, float], NoDecode] = (1200.0, 11000.0)

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

    # ---- refinement runner (charter item 5, ADR-042) -----------------------
    #: The refiner runs in its **own process**, started by
    #: ``deploy/open-observatory-refine.timer`` -- never inside the station.
    #: ADR-033 is the whole reason: a 0.30 s retention sweep in a dedicated
    #: thread starved the capture event loop for 55-150 ms and cost ~1.9 false
    #: capture gaps a minute, and a BatDetect2 pass is 2.1 s of inference. These
    #: settings are read by ``oo refine run``; the station process ignores them.
    refinement_enabled: bool = True
    #: Which refiner to run. ``batdetect2-cascade`` classifies stored ultrasonic
    #: evidence clips and may only ever *propose* -- see ADR-042 and
    #: ``refinement/batdetect2.py`` for the measured accuracy evidence behind
    #: that ceiling.
    refinement_refiner: Literal["batdetect2-cascade"] = "batdetect2-cascade"
    #: Measured quiet window, in **UTC** hours, half-open ``[start, end)``.
    #: Enforced by ``RefinementRunner.run`` itself and not only by the timer: a
    #: unit file is one ``systemctl start`` away from being bypassed, and the
    #: one thing that must never happen is the classifier landing on the CPU at
    #: dusk when the bats are actually flying.
    refinement_window_start_hour_utc: int = 1
    refinement_window_end_hour_utc: int = 3
    #: Ceilings on one pass. The wall-clock budget is the real bound: at the
    #: measured 2.1 s per pass, a busy night's 1015 passes is ~36 minutes, which
    #: fits the two-hour window with room for a backlog. A pass that runs out of
    #: budget stops cleanly and reports ``complete: false``; the next night
    #: resumes with the oldest unrefined events.
    refinement_max_items: int = 1200
    refinement_max_seconds: float = 5400.0
    #: Seconds of clip classified, centred on the loudest sample. Trimming is
    #: where the cascade's saving lives: an untrimmed 6 s evidence clip is mostly
    #: pre-roll silence and costs four times as much (ADR-017, 2026-08-05).
    refinement_trim_s: float = 1.5
    #: Noise floor, **not** a truth threshold: it exists to stop one pass
    #: emitting a dozen near-zero species rows. The station's own measured leans
    #: sit at 0.20-0.30 and must survive it, because a low-confidence lean is
    #: exactly what a human ear should arbitrate.
    refinement_min_det_prob: float = 0.05
    #: Inference threads. The unit is fenced to two cores (``AllowedCPUs=2-3``),
    #: so more threads than that is context switching, not throughput.
    refinement_threads: int = 2

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
    #: `NoDecode`: see `auth_public_read_paths` below for why -- this is one
    #: of the three fields `config/example.env` warns an operator away from
    #: setting because of it.
    clip_plugins: Annotated[tuple[str, ...], NoDecode] = (
        "birdnet-v2.4",
        "ultrasonic-pass-v1",
    )
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
    #: `NoDecode`: same pre-existing pydantic-settings bug as
    #: `preferred_sample_rates`, found while auditing for it -- not
    #: previously reported to crash because nothing in this repository or
    #: `config/example.env` sets it from the environment yet.
    ultrasonic_band_hz: Annotated[tuple[float, float], NoDecode] = (15000.0, 125000.0)
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

    # ---- authentication (Milestone 4, closes ADR-015) ----------------------
    #: Off by default so an upgrade never locks an operator out of their own
    #: station unattended -- turning this on is a deliberate operator action,
    #: not a side effect of pulling new code. See ADR-034. While off, the
    #: station behaves exactly as it always has: anonymous read AND write on
    #: the LAN. `/api/v1/health` reports this loudly (``auth.enabled: false``)
    #: and a warning is logged once at startup so it is not silently the
    #: case.
    auth_enabled: bool = False
    #: GET-only paths that stay reachable with no credential even when
    #: ``auth_enabled`` is true. Exists for exactly one reason: the ESP32
    #: wall display (``firmware/inside-observer``) polls these two paths
    #: every ``pollSeconds`` with no way to carry a credential, and it cannot
    #: be reflashed as part of closing this ADR (see ADR-034). `/api/v1/health`
    #: and `/metrics` are separately hardcoded as always-public in `api/app.py`
    #: -- `deploy.sh`'s health check and a Prometheus scraper have no login
    #: flow either -- so this list only needs to cover what is left.
    #: `Annotated[..., NoDecode]`: pydantic-settings otherwise tries to
    #: JSON-decode every tuple-typed field's raw env/dotenv string *before*
    #: this class's own `_split_csv` validator ever runs, and a plain
    #: comma-separated value like `/a,/b` is not valid JSON -- discovered
    #: while adding this field: the same failure (`SettingsError` at
    #: startup) also existed for `preferred_sample_rates`, `preferred_formats`
    #: and `clip_plugins` whenever they were actually set via the environment
    #: (see `config/example.env`'s former warning), and for `activity_band_hz`
    #: / `ultrasonic_band_hz`, found by auditing every other tuple-typed
    #: field for the same bug. All five now carry the same `NoDecode`
    #: annotation and are covered by `tests/test_config.py`.
    #:
    #: `/api/v1/display` is the push channel of ADR-038. It is not a GET, so the
    #: HTTP gate never sees it; the WebSocket handler consults this same list so
    #: the display's two transports are exempt or not exempt together, rather
    #: than the fallback working and the primary path silently not.
    auth_public_read_paths: Annotated[tuple[str, ...], NoDecode] = (
        "/api/v1/detections",
        "/api/v1/display",
    )
    #: Minimum password length enforced at account creation and password
    #: change. NIST 800-63B's floor; no composition rules on top of it, which
    #: is also 800-63B guidance -- composition rules push people toward
    #: predictable substitutions, length does not.
    auth_password_min_length: int = 12
    #: Argon2id cost parameters, pinned exactly like every other dependency
    #: in this project rather than left at whatever `argon2-cffi` ships as
    #: its default this release. These match that library's own recommended
    #: defaults as of the pinned version (OWASP guidance for Argon2id: >=2
    #: iterations, >=19 MiB memory, parallelism matched to available cores)
    #: -- copied explicitly, not inherited silently, so a future library
    #: upgrade cannot change station security posture without the diff
    #: showing it.
    auth_argon2_time_cost: int = 3
    auth_argon2_memory_cost_kib: int = 65536
    auth_argon2_parallelism: int = 4
    #: Session cookie lifetime. Long-lived deliberately: this is a local
    #: appliance an operator glances at, not a banking site.
    auth_session_ttl_hours: float = 24.0 * 14
    auth_session_cookie_name: str = "oo_session"
    #: `Secure` requires HTTPS or the browser silently refuses to ever send
    #: the cookie back -- and this station is served over plain HTTP on a LAN
    #: by default (no TLS component exists anywhere in this codebase).
    #: Defaulting `Secure=true` here would not make the cookie safer; it
    #: would make login appear to succeed and then silently never
    #: authenticate any subsequent request, which is worse than being honest
    #: about the gap. See ADR-034. Set true only once a reverse proxy or
    #: similar terminates TLS in front of this station.
    auth_cookie_secure: bool = False
    #: Login attempts allowed per client IP inside the window below before
    #: `/api/v1/auth/login` starts returning 429. Deliberately coarse
    #: (in-process, per-worker, reset on restart) -- see ADR-034 for why a
    #: heavier limiter was not built for a single-operator LAN appliance.
    auth_login_rate_limit_attempts: int = 5
    auth_login_rate_limit_window_s: float = 60.0
    #: Username the first-run bootstrap account is created under when
    #: `auth_enabled` is true and no user exists yet. The password is never
    #: this or any other fixed value -- see `auth.bootstrap_admin_if_needed`.
    auth_bootstrap_username: str = "operator"

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

    @field_validator(
        "preferred_sample_rates",
        "preferred_formats",
        "clip_plugins",
        "auth_public_read_paths",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        # `NoDecode` (see the field definitions above) means pydantic-settings
        # no longer JSON-decodes this field's raw string for us, so a value
        # that was already a JSON list in someone's existing `runtime.env` --
        # which worked before these fields carried `NoDecode` -- has to be
        # handled here too, not just the comma-separated form the docs show.
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return tuple(part.strip() for part in stripped.split(",") if part.strip())
        return value

    @field_validator("activity_band_hz", "ultrasonic_band_hz", mode="before")
    @classmethod
    def _split_band(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            low, _, high = stripped.partition(",")
            return (float(low.strip()), float(high.strip()))
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
