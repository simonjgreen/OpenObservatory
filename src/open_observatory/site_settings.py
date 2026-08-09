"""Operator-editable settings, managed through the web UI.

The repository describes a *system*; a deployment describes a *site*
(ADR-047). Everything in this module exists so that the parameters of one
installation -- where the station is, what it is called, how sensitive its
detectors are, what its spectrogram contrast is -- live in untracked runtime
configuration and are editable from the on-device UI.

ADR-048 widens that from the original site-identity whitelist to **every**
field of :class:`~open_observatory.config.Settings`. The goal is a new
operator getting from a freshly imaged Pi to a tuned station without opening
a terminal or a text editor. The audit is not prose: :data:`EDITABLE_SETTINGS`
and :data:`NON_EDITABLE` between them name every field exactly once, and
``tests/test_site_settings.py`` fails if a field is added to ``Settings``
without a decision being recorded here.

Persistence is ``config/runtime.env``, the same gitignored, operator-owned
file the environment-variable path has always read. The web UI is a second
writer of that file, not a second configuration system: a value set through
the UI and a value set by editing the file are indistinguishable at the next
startup, and ``oo config`` prints the merged result either way.

Three tiers, decided here and enforced by the catalogue:

* **live** (``tier="live"``) -- applied to the running process the moment they
  are saved, and persisted so a restart changes nothing. Three mechanisms,
  all of them the same code path a restart would take: fields the station
  reads fresh on every use (it simply reads the mutated ``Settings``);
  fields pushed into a running object by ``tuning.py``; and MQTT, applied by
  restarting the publisher.
* **restart-pinned** (``tier="restart"``) -- persisted immediately and
  *reported* immediately, but deliberately not injected into running
  components. Coordinates are the original case: they bind into the BirdNET
  range filter and the ultrasonic night schedule when detectors start, and
  swapping them under a running range model would change what "plausible"
  means mid-stream without any detector row recording the switch. Capture
  geometry is the larger case: sample rate, block size and ring depth are
  negotiated with the device once, and re-negotiating them means tearing
  down capture -- which charter item 1 forbids as a side effect of a form
  submission. The API says loudly that a restart is pending rather than
  pretending the new value is in force.
* **never browser-editable** -- :data:`NON_EDITABLE`, each with a named
  hazard. The bar is a concrete hazard, not tidiness.

The honesty rule that governs the whole surface: a value the station has
*saved* but is *not yet using* must never be reported as if it were in force.
That is what ``applied`` (``Station.applied_site``) is for -- it records what
the running components were actually built with or last retuned to, and any
daylight between it and the saved value is reported as ``pending_restart``,
for live-tier fields too. A "live" field whose live application did not
happen -- because the detector is not running, or the ultrasonic encoder does
not exist at this sample rate -- is reported as pending, not as applied.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from .config import Settings

Tier = Literal["live", "restart"]


@dataclass(frozen=True)
class SettingCategory:
    """A group of settings, as the UI renders it.

    Served from the API rather than hardcoded in the panel so that adding a
    setting is a one-file change and the ordering cannot drift between the
    two surfaces.
    """

    id: str
    title: str
    description: str
    #: Not rendered as an ordinary form section (the guided first-run flow
    #: owns it, or it is machine state).
    hidden: bool = False


CATEGORIES: tuple[SettingCategory, ...] = (
    SettingCategory(
        "station",
        "Station",
        "Who and where this station is. Coordinates drive species plausibility "
        "filtering and the ultrasonic night schedule; with them unset the "
        "station runs unfiltered and always-on, and says so.",
    ),
    SettingCategory(
        "capture",
        "Capture",
        "The microphone and the shape of the audio taken from it. Capture is "
        "negotiated with the device once at start, so everything here is saved "
        "now and in force after a restart -- a form submission never tears "
        "down a running capture.",
    ),
    SettingCategory(
        "view",
        "Live view",
        "How the spectrograms are drawn. The floor and ceiling are the dB "
        "window mapped onto the colour ramp: raise the floor to push a noisy "
        "background to black, lower the ceiling to bring out quiet detail. "
        "Neither changes what is detected -- only what you see.",
    ),
    SettingCategory(
        "detect-audible",
        "Audible detection",
        "The activity detector and BirdNET. Thresholds are score bars, not "
        "accuracy: raising one makes that band quieter, it does not make the "
        "model better.",
    ),
    SettingCategory(
        "detect-ultrasonic",
        "Ultrasonic detection",
        "The bat-pass detector. If a noisy mounting is producing false passes, "
        "these are the knobs -- raise the SNR bar and the pulses-per-pass "
        "minimum until the false passes stop.",
    ),
    SettingCategory(
        "ultrasonic-audio",
        "Making ultrasound listenable",
        "How an ultrasonic event is rendered into something a human can hear, "
        "and where the live heterodyne monitor is tuned by default.",
    ),
    SettingCategory(
        "clips",
        "Evidence clips",
        "What gets recorded as evidence for a detection, and the hard limits "
        "that stop a pathological night filling the disk.",
    ),
    SettingCategory(
        "retention",
        "Retention",
        "How long clip *bytes* survive, tier by tier. Detection metadata is "
        "never deleted by any of this.",
    ),
    SettingCategory(
        "refinement",
        "Overnight refinement",
        "The separate, CPU-fenced process that re-examines stored ultrasonic "
        "evidence in the quiet hours. Read at the start of each run, so a "
        "change here is in force for tonight without restarting the station.",
    ),
    SettingCategory(
        "display",
        "Counter-top display",
        "The push channel the inside-observer panel listens on.",
    ),
    SettingCategory(
        "mqtt",
        "MQTT / Home Assistant",
        "Optional. Nothing here is required for capture, detection, review or "
        "query -- the station is fully usable with MQTT off.",
    ),
    SettingCategory(
        "advanced",
        "Advanced",
        "Queue depths, logging and the replay/synthetic sources. Correct "
        "defaults; change them when you are diagnosing something specific.",
    ),
    SettingCategory(
        "setup",
        "Setup",
        "State of the guided first-run flow.",
        hidden=True,
    ),
)

CATEGORY_IDS = tuple(category.id for category in CATEGORIES)


@dataclass(frozen=True)
class EditableSetting:
    """One field of :class:`Settings` the web UI may change."""

    name: str
    category: str
    #: "live" or "restart" -- see the module docstring.
    tier: Tier = "live"
    #: Never echoed back to a client; GET reports only whether it is set.
    secret: bool = False
    #: Shown as the form label. Derived from the name when empty.
    label: str = ""
    #: One or two sentences of why an operator would touch this.
    help: str = ""
    #: Rendered next to the input (dB, Hz, s, ...). Derived when None.
    unit: str | None = None
    #: Inclusive numeric bounds, enforced in :func:`coerce_updates`.
    minimum: float | None = None
    maximum: float | None = None
    #: Legitimate but hazardous. The UI must show this before accepting an
    #: edit; the value is still editable. Hiding a dangerous setting does not
    #: make it safe, it makes it a terminal session.
    danger: str = ""
    #: Extra honesty attached to the field (why it is restart-pinned, what
    #: else it interacts with).
    note: str = ""
    #: Overrides the choices inferred from a Literal annotation.
    choices: tuple[str, ...] = ()

    @property
    def restart_required(self) -> bool:
        """Back-compatible spelling of ``tier == "restart"``.

        The API payload and the web panel both grew up around this name; it
        stays as the derived form rather than a second source of truth.
        """
        return self.tier == "restart"


def _e(name: str, category: str, **kwargs: Any) -> EditableSetting:
    return EditableSetting(name=name, category=category, **kwargs)


#: Every field of ``Settings`` an operator may change from a browser, in the
#: order the UI renders them. Grouped by category; within a category, the
#: things an operator reaches for first come first.
EDITABLE_SETTINGS: tuple[EditableSetting, ...] = (
    # ---- station -------------------------------------------------------
    _e("station_name", "station", label="station name"),
    _e(
        "timezone",
        "station",
        label="timezone",
        help="IANA zone name, e.g. Europe/London. Everything is stored in UTC; "
        "this is only how times are presented.",
    ),
    _e(
        "latitude",
        "station",
        tier="restart",
        label="latitude",
        unit="°",
        minimum=-90.0,
        maximum=90.0,
        note=(
            "Bound into the BirdNET range filter and the night schedule when "
            "detectors start; saved now, in force after the next restart."
        ),
    ),
    _e(
        "longitude",
        "station",
        tier="restart",
        label="longitude",
        unit="°",
        minimum=-180.0,
        maximum=180.0,
        note=(
            "Bound into the BirdNET range filter and the night schedule when "
            "detectors start; saved now, in force after the next restart."
        ),
    ),
    _e(
        "clips_require_mount",
        "station",
        label="evidence storage must be its own mount",
        help="Report the station degraded when the clip directory is not a "
        "mount point. Writing clips to the SD card alongside capture caused "
        "measured ALSA overruns; this makes a missing SSD visible instead of "
        "silent. Capture still wins -- the station keeps recording either way.",
    ),
    # ---- capture -------------------------------------------------------
    _e(
        "source",
        "capture",
        tier="restart",
        label="audio source",
        choices=("auto", "alsa", "synthetic"),
        help="'auto' uses the microphone and falls back to a synthetic scene "
        "if it disappears. 'alsa' refuses to fall back. 'synthetic' never "
        "opens the microphone at all.",
        danger="'synthetic' stops this station recording real audio until it is "
        "changed back. Use it only for a demonstration or a test.",
        note="The 'replay' source is not offered here; it needs a file path, "
        "which is not browser-editable (see NON_EDITABLE).",
    ),
    _e(
        "audio_device",
        "capture",
        tier="restart",
        label="capture device key",
        help="Stable device key, e.g. usb-10c4:0002:0100, or an ALSA card id. "
        "Leave empty to take the first suitable device found -- correct for a "
        "station with one microphone.",
        danger="A key that matches no attached device means the station falls "
        "back to a synthetic source and records nothing real. Run "
        "'oo devices' (or read the capture panel) for the exact key first.",
    ),
    _e(
        "preferred_sample_rates",
        "capture",
        tier="restart",
        label="preferred sample rates",
        unit="Hz",
        help="Tried in order; the first the device accepts is used. Rates below "
        "96 kHz disable ultrasonic detection entirely -- there is nothing above "
        "24 kHz to find in a 48 kHz stream.",
    ),
    _e(
        "preferred_formats",
        "capture",
        tier="restart",
        label="preferred sample formats",
        help="Tried in order, e.g. S16_LE, S32_LE.",
    ),
    _e(
        "capture_channels", "capture", tier="restart", label="channels",
        minimum=1, maximum=2,
    ),
    _e(
        "capture_block_ms",
        "capture",
        tier="restart",
        label="capture block",
        unit="ms",
        minimum=10,
        maximum=1000,
        help="How much audio each read returns. Smaller is lower latency and "
        "more syscalls.",
    ),
    _e(
        "capture_buffer_ms",
        "capture",
        tier="restart",
        label="ALSA ring depth",
        unit="ms",
        minimum=50,
        maximum=10000,
        help="The longest stall the capture path can absorb before audio is "
        "lost. Must be comfortably larger than the block size -- it was "
        "effectively shorter than one block until ADR-030 and that cost "
        "recordings.",
    ),
    _e(
        "native_ring_seconds",
        "capture",
        tier="restart",
        label="native ring buffer",
        unit="s",
        minimum=10,
        maximum=600,
        help="Seconds of full-rate audio kept in memory for clip pre-roll. At "
        "384 kHz this is the biggest single memory user in the process.",
    ),
    _e(
        "audible_ring_seconds",
        "capture",
        tier="restart",
        label="audible ring buffer",
        unit="s",
        minimum=10,
        maximum=600,
    ),
    _e(
        "audible_sample_rate",
        "capture",
        tier="restart",
        label="derived audible rate",
        unit="Hz",
        minimum=8000,
        maximum=192000,
        help="The rate BirdNET and the activity detector see. 48000 is what "
        "BirdNET's window contract assumes; changing it is a detector change.",
    ),
    _e(
        "hardware_recheck_s",
        "capture",
        label="recheck for the microphone every",
        unit="s",
        minimum=0,
        maximum=3600,
        help="While running on the synthetic fallback, how often to look for "
        "the real device coming back. 0 disables recovery -- a reattached "
        "microphone then goes unnoticed until a restart.",
    ),
    _e("reopen_backoff_min_s", "capture", label="reopen backoff, minimum",
       unit="s", minimum=0.1, maximum=60),
    _e("reopen_backoff_max_s", "capture", label="reopen backoff, maximum",
       unit="s", minimum=0.1, maximum=3600),
    # ---- live view -----------------------------------------------------
    _e(
        "spectrogram_floor_db",
        "view",
        label="audible floor",
        unit="dB",
        minimum=-200,
        maximum=0,
        help="Level mapped to black on the audible spectrogram. Raise it "
        "towards the ceiling to push a noisy background out of the picture.",
    ),
    _e(
        "spectrogram_ceiling_db",
        "view",
        label="audible ceiling",
        unit="dB",
        minimum=-200,
        maximum=0,
        help="Level mapped to the brightest colour. Lower it to bring out "
        "quiet detail; too low and everything saturates.",
    ),
    _e(
        "ultrasonic_spectrogram_floor_db",
        "view",
        label="ultrasonic floor",
        unit="dB",
        minimum=-200,
        maximum=0,
        help="The ultrasonic channel has its own window because the "
        "AudioMoth's noise floor sits far higher than the audible channel's "
        "(ADR-041). The shipped value sits ~3 dB below the lowest level "
        "measured on a real station, so genuine quiet renders near-black.",
    ),
    _e(
        "ultrasonic_spectrogram_ceiling_db",
        "view",
        label="ultrasonic ceiling",
        unit="dB",
        minimum=-200,
        maximum=0,
        help="The shipped value is the measured bat-band median plus the 36 dB "
        "SNR at which the pass detector's own scoring saturates, so a call the "
        "detector calls 'as strong as it gets' reads as near-white (ADR-041).",
    ),
    _e("spectrogram_fft", "view", tier="restart", label="FFT size",
       minimum=256, maximum=16384),
    _e("spectrogram_hop_ms", "view", tier="restart", label="column hop",
       unit="ms", minimum=1, maximum=1000),
    _e("spectrogram_bins", "view", tier="restart", label="frequency bins",
       minimum=16, maximum=1024),
    _e("spectrogram_min_hz", "view", tier="restart", label="lowest frequency shown",
       unit="Hz", minimum=1, maximum=200000),
    _e("spectrogram_max_hz", "view", tier="restart", label="highest frequency shown",
       unit="Hz", minimum=1, maximum=200000),
    _e(
        "spectrogram_history_columns",
        "view",
        tier="restart",
        label="retained columns",
        minimum=60,
        maximum=20000,
        help="Server-side history so a newly-connected browser sees the recent "
        "past. Costs memory in proportion.",
    ),
    _e("spectrogram_backfill_s", "view", label="history sent on connect",
       unit="s", minimum=0, maximum=600),
    _e(
        "spectrogram_encode_min_viewers",
        "view",
        label="viewers required before encoding",
        minimum=0,
        maximum=64,
        help="The station's steady state is nobody watching, and the two "
        "encoders measured over half the per-block CPU work (ADR-040). Set to "
        "0 to always encode.",
    ),
    _e(
        "spectrogram_keep_audible_warm",
        "view",
        label="keep the audible encoder warm",
        help="Instant history when a browser opens, at about half the saving "
        "ADR-040 bought back.",
    ),
    # ---- audible detection ---------------------------------------------
    _e("activity_enabled", "detect-audible", tier="restart",
       label="activity detector",
       note="Adding or removing a detector rebuilds the detector pipeline; "
            "saved now, in force after the next restart."),
    _e(
        "activity_min_snr_db",
        "detect-audible",
        label="activity SNR threshold",
        unit="dB",
        minimum=0,
        maximum=90,
        help="How far above the tracked noise floor a sound must sit to count "
        "as an event. Calibrated against measured stationary noise reaching "
        "11.9 dB, so the shipped value leaves a 6 dB margin. Raise it if a "
        "noisy mounting is firing the detector constantly.",
    ),
    _e("activity_min_duration_ms", "detect-audible", label="minimum event duration",
       unit="ms", minimum=1, maximum=10000),
    _e(
        "activity_band_hz",
        "detect-audible",
        label="activity band",
        unit="Hz",
        help="Low,high in Hz. Narrow it around the band you care about to "
        "ignore a persistent noise source outside it.",
    ),
    _e("birdnet_enabled", "detect-audible", tier="restart", label="BirdNET",
       note="Adding or removing a detector rebuilds the detector pipeline; "
            "saved now, in force after the next restart."),
    _e(
        "birdnet_min_confidence",
        "detect-audible",
        label="BirdNET minimum confidence",
        minimum=0,
        maximum=1,
        help="The bar below which a raw BirdNET score is not even considered. "
        "Scores are model outputs, not probabilities.",
    ),
    _e(
        "birdnet_plausibility_floor",
        "detect-audible",
        label="plausibility floor",
        minimum=0,
        maximum=1,
        help="Occurrence probability at or below which the range model's "
        "verdict is 'not a candidate at any score' (ADR-032). Measured on a "
        "live station: implausible North American owls sit at 8e-06 to "
        "1.6e-04, a genuine but seasonally-uncommon Tawny Owl at 0.019. The "
        "shipped value sits between the two with margin on both sides. Raising "
        "it much above 0.001 starts rejecting real uncommon species.",
    ),
    _e("birdnet_common_prior", "detect-audible", label="common-species prior",
       minimum=0, maximum=1,
       help="Occurrence probability above which a species counts as a local "
            "regular and is held only to the in-range bar."),
    _e("birdnet_range_threshold", "detect-audible", label="out-of-range prior",
       minimum=0, maximum=1,
       help="Occurrence probability below which a species is treated as out of "
            "range and held to the strictest bar."),
    _e("birdnet_threshold_in_range", "detect-audible",
       label="confidence bar: in range", minimum=0, maximum=1),
    _e("birdnet_threshold_uncommon", "detect-audible",
       label="confidence bar: uncommon", minimum=0, maximum=1),
    _e("birdnet_threshold_out_of_range", "detect-audible",
       label="confidence bar: out of range", minimum=0, maximum=1,
       help="The bar an implausible species must clear. Raise it towards 1.0 "
            "if exotic species keep appearing."),
    _e("birdnet_window_stride_s", "detect-audible", tier="restart",
       label="BirdNET window stride", unit="s", minimum=0.1, maximum=3.0,
       note="Part of the detector's window contract, negotiated with the "
            "segmenter when detectors start."),
    _e("birdnet_use_location_filter", "detect-audible", tier="restart",
       label="use the range model",
       note="Decides which model files are loaded and what the detector "
            "declares about itself; applied when detectors start."),
    # ---- ultrasonic detection -------------------------------------------
    _e("ultrasonic_enabled", "detect-ultrasonic", tier="restart",
       label="ultrasonic pass detector",
       note="Adding or removing a detector rebuilds the detector pipeline; "
            "saved now, in force after the next restart."),
    _e(
        "ultrasonic_min_snr_db",
        "detect-ultrasonic",
        label="pulse SNR threshold",
        unit="dB",
        minimum=0,
        maximum=90,
        help="How far above the tracked band noise floor a pulse must sit. "
        "This is the first knob to raise when a noisy mount (a plant against "
        "a shed, a fan, rain on a roof) is producing false passes.",
    ),
    _e(
        "ultrasonic_min_pulses_per_pass",
        "detect-ultrasonic",
        label="minimum pulses per pass",
        minimum=1,
        maximum=100,
        help="Fewer pulses than this is more likely a stray transient than a "
        "genuine echolocation pass. The second knob to raise against a "
        "periodic mechanical noise, which rarely produces a long train.",
    ),
    _e(
        "ultrasonic_band_hz",
        "detect-ultrasonic",
        label="ultrasonic band",
        unit="Hz",
        help="Low,high in Hz. Below 15 kHz the audible detectors already have "
        "coverage. Raising the low edge is the way to ignore a noise source "
        "that only reaches the bottom of the band.",
    ),
    _e("ultrasonic_min_pulse_ms", "detect-ultrasonic", label="minimum pulse length",
       unit="ms", minimum=0.1, maximum=1000,
       help="Short enough to exclude sustained tones, long enough to exclude "
            "clicks."),
    _e("ultrasonic_max_pulse_ms", "detect-ultrasonic", label="maximum pulse length",
       unit="ms", minimum=0.1, maximum=5000),
    _e("ultrasonic_merge_gap_ms", "detect-ultrasonic", label="pulse merge gap",
       unit="ms", minimum=0, maximum=100,
       help="Onset-to-onset spacing below which two threshold crossings are "
            "one call whose envelope dipped. Kept well under a feeding buzz's "
            "5 ms spacing so a buzz is never merged away."),
    _e("ultrasonic_pass_gap_s", "detect-ultrasonic", label="pass gap",
       unit="s", minimum=0.05, maximum=60,
       help="Pulses closer together than this belong to the same pass."),
    _e("ultrasonic_buzz_max_interval_ms", "detect-ultrasonic",
       label="feeding buzz: maximum interval", unit="ms", minimum=0.1, maximum=1000),
    _e("ultrasonic_buzz_min_pulses", "detect-ultrasonic",
       label="feeding buzz: minimum pulses", minimum=2, maximum=100),
    _e("ultrasonic_buzz_interval_ratio", "detect-ultrasonic",
       label="feeding buzz: interval collapse ratio", minimum=0.01, maximum=1.0,
       help="A buzz is a terminal collapse in inter-pulse interval. Defaults "
            "are a starting point, not a calibrated result."),
    _e(
        "ultrasonic_schedule",
        "detect-ultrasonic",
        tier="restart",
        label="when to run",
        note="The night schedule is built from the coordinates when detectors "
        "start; saved now, in force after the next restart. With coordinates "
        "unset, 'night' still runs continuously rather than silently detecting "
        "nothing overnight.",
    ),
    _e("ultrasonic_schedule_dusk_margin_min", "detect-ultrasonic", tier="restart",
       label="start before dusk", unit="min", minimum=-720, maximum=720),
    _e("ultrasonic_schedule_dawn_margin_min", "detect-ultrasonic", tier="restart",
       label="stop after dawn", unit="min", minimum=-720, maximum=720),
    # ---- making ultrasound listenable ------------------------------------
    _e("ultrasonic_audible_method", "ultrasonic-audio", label="rendering method",
       help="time-expansion preserves every harmonic and the call shape and is "
            "the analysis standard; heterodyne keeps real time and discards "
            "everything outside the tuned band."),
    _e("ultrasonic_time_expansion_factor", "ultrasonic-audio",
       label="time-expansion factor", minimum=0, maximum=100,
       help="0 picks a factor per detection so the call lands near the target "
            "frequency below, which keeps a 25 kHz and a 110 kHz species "
            "equally audible."),
    _e("ultrasonic_target_hz", "ultrasonic-audio", label="target frequency",
       unit="Hz", minimum=100, maximum=20000),
    _e("ultrasonic_highpass_hz", "ultrasonic-audio", label="high-pass before rendering",
       unit="Hz", minimum=0, maximum=200000,
       help="Wind and traffic rumble below this would otherwise be shifted "
            "down into a sub-audible thump that masks the call."),
    _e("ultrasonic_heterodyne_bandwidth_hz", "ultrasonic-audio",
       label="heterodyne bandwidth", unit="Hz", minimum=100, maximum=100000,
       note="Shared with the live heterodyne monitor; a live change applies to "
            "rendered clips immediately and to the next live listen session."),
    _e("ultrasonic_audible_max_s", "ultrasonic-audio", label="maximum rendered length",
       unit="s", minimum=1, maximum=600),
    _e("ultrasonic_audible_min_peak_hz", "ultrasonic-audio",
       label="render anything peaking above", unit="Hz", minimum=1000, maximum=200000),
    _e("ultrasonic_live_tune_hz", "ultrasonic-audio", label="live monitor default tuning",
       unit="Hz", minimum=1000, maximum=200000,
       help="Where 'GO LIVE -> ultrasonic' starts tuned. 45 kHz sits in the "
            "common pipistrelle range. Adjustable per session in the header."),
    # ---- evidence clips ---------------------------------------------------
    _e("clips_enabled", "clips", label="write evidence clips"),
    _e("clip_pre_roll_s", "clips", label="pre-roll", unit="s", minimum=0, maximum=60,
       help="Audio kept from before the detection. Bounded by the native ring "
            "buffer above."),
    _e("clip_post_roll_s", "clips", label="post-roll", unit="s", minimum=0, maximum=60),
    _e("clip_max_s", "clips", label="maximum clip length", unit="s",
       minimum=1, maximum=300),
    _e("clip_min_score", "clips", label="minimum score to clip",
       minimum=0, maximum=1),
    _e(
        "clip_plugins",
        "clips",
        label="detectors that produce clips",
        help="Comma-separated plugin ids. The activity detector is absent "
        "deliberately: it fires several times a second in a live garden, and "
        "clipping it measured 640 GB/day on the target.",
        danger="Adding 'activity-v1' here will fill any disk. The per-minute "
        "and total-size limits below are the only thing between that setting "
        "and a full filesystem.",
    ),
    _e("clip_max_per_minute", "clips", label="clip rate limit", unit="/min",
       minimum=1, maximum=600),
    _e("clip_max_total_gb", "clips", label="clip directory budget", unit="GB",
       minimum=0.1, maximum=100000),
    _e("clip_min_free_gb", "clips", label="stop clipping below free space",
       unit="GB", minimum=0, maximum=100000,
       help="Evidence writing must never be able to threaten the database."),
    _e("clip_retention_days", "clips", label="clip manager retention", unit="days",
       minimum=1, maximum=3650,
       note="The clip manager's own sweep. The tiered policy below is the one "
            "that decides what actually survives."),
    # ---- retention --------------------------------------------------------
    _e("retention_enabled", "retention", label="tiered retention"),
    _e("retention_native_days", "retention", label="keep full-rate audio for",
       unit="days", minimum=0, maximum=3650,
       help="After this, the native WAV is deleted and the audible rendering "
            "survives."),
    _e("retention_audible_only_days", "retention", label="keep every audible clip for",
       unit="days", minimum=0, maximum=3650,
       help="After this, only first-of-species and best-of-species exemplars "
            "keep their audio."),
    _e("retention_exemplar_only_days", "retention", label="keep exemplar clips for",
       unit="days", minimum=0, maximum=3650,
       help="Detection rows -- species, times, scores -- are never deleted by "
            "any tier. Only the bytes age out."),
    _e("retention_watermark_ratio", "retention", label="reclaim above disk usage",
       minimum=0.1, maximum=0.99,
       help="Continuous oldest-first reclaim above this fraction of the clip "
            "filesystem, regardless of tier -- the NVR behaviour."),
    _e("retention_batch_size", "retention", label="assets per sweep",
       minimum=1, maximum=100000),
    _e("retention_batch_budget_s", "retention", label="sweep time budget",
       unit="s", minimum=0.05, maximum=60,
       help="A sweep that would exceed this stops partway and resumes next "
            "tick. Long sweeps starve the capture event loop (ADR-033)."),
    _e("retention_interval_s", "retention", label="sweep interval", unit="s",
       minimum=10, maximum=86400,
       help="Rounded up to the nearest 10 s housekeeping tick. Sweeping every "
            "tick measured 1.6 false capture-gap records a minute."),
    # ---- refinement --------------------------------------------------------
    _e("refinement_enabled", "refinement", label="overnight refinement",
       note="Read by the separate 'oo refine run' process at the start of each "
            "run; the station process ignores it. In force tonight without a "
            "station restart."),
    _e("refinement_window_start_hour_utc", "refinement", label="window start (UTC hour)",
       minimum=0, maximum=23,
       help="Enforced by the runner itself, not only by the systemd timer: the "
            "one thing that must never happen is the classifier landing on the "
            "CPU at dusk when the bats are flying."),
    _e("refinement_window_end_hour_utc", "refinement", label="window end (UTC hour)",
       minimum=0, maximum=23),
    _e("refinement_max_items", "refinement", label="maximum items per pass",
       minimum=1, maximum=1000000),
    _e("refinement_max_seconds", "refinement", label="wall-clock budget per pass",
       unit="s", minimum=1, maximum=86400),
    _e("refinement_trim_s", "refinement", label="seconds classified per clip",
       unit="s", minimum=0.1, maximum=60,
       help="Centred on the loudest sample. Trimming is where the saving "
            "lives: an untrimmed 6 s clip costs four times as much."),
    _e("refinement_min_det_prob", "refinement", label="minimum detection probability",
       minimum=0, maximum=1,
       help="A noise floor, not a truth threshold. The station's own measured "
            "leans sit at 0.20-0.30 and must survive it."),
    _e("refinement_threads", "refinement", label="inference threads",
       minimum=1, maximum=16,
       help="The unit is fenced to two cores, so more threads than that is "
            "context switching, not throughput."),
    _e("refinement_refiner", "refinement", label="refiner"),
    # ---- counter-top display ------------------------------------------------
    _e("display_channel_heartbeat_s", "display", label="heartbeat interval",
       unit="s", minimum=1, maximum=600,
       help="The display allows three misses before it calls the feed stale, "
            "so lengthening this makes a dead station take longer to look dead."),
    _e("display_channel_snapshot_rows", "display", label="rows sent on connect",
       minimum=1, maximum=100),
    _e("display_channel_queue_max", "display", label="queue depth",
       minimum=1, maximum=10000,
       note="Applies to displays that connect after the change."),
    # ---- MQTT ---------------------------------------------------------------
    _e("mqtt_enabled", "mqtt", label="publish to MQTT"),
    _e("mqtt_host", "mqtt", label="broker host"),
    _e("mqtt_port", "mqtt", label="broker port", minimum=1, maximum=65535),
    _e("mqtt_tls", "mqtt", label="TLS"),
    _e("mqtt_tls_insecure", "mqtt", label="skip TLS certificate verification",
       danger="Silently accepting any certificate defeats the point of TLS. "
              "Only for a self-signed broker on a LAN you control."),
    _e("mqtt_username", "mqtt", label="username"),
    _e("mqtt_password", "mqtt", secret=True, label="password"),
    _e("mqtt_client_id", "mqtt", label="client id"),
    _e("mqtt_topic_prefix", "mqtt", label="topic prefix"),
    _e("mqtt_qos", "mqtt", label="QoS", minimum=0, maximum=2),
    _e("mqtt_retain_state", "mqtt", label="retain state topics",
       help="So a client connecting after the fact -- including Home Assistant "
            "on restart -- sees current state. Detections and alerts are never "
            "retained regardless."),
    _e("mqtt_discovery_enabled", "mqtt", label="Home Assistant discovery"),
    _e("mqtt_discovery_prefix", "mqtt", label="discovery prefix"),
    _e("mqtt_publish_unidentified", "mqtt", label="publish unidentified events",
       help="Off by default: unnamed acoustic events were 154 of 200 "
            "consecutive rows sampled on a live station, and they drown the "
            "identifications in Home Assistant. Bat passes are not affected."),
    _e("mqtt_bat_activity_window_s", "mqtt", label="bat activity sensor window",
       unit="s", minimum=1, maximum=86400),
    _e("mqtt_health_publish_interval_s", "mqtt", label="health republish interval",
       unit="s", minimum=1, maximum=3600),
    _e("mqtt_queue_depth", "mqtt", label="publisher queue depth",
       minimum=1, maximum=100000),
    _e("mqtt_reconnect_min_s", "mqtt", label="reconnect backoff, minimum",
       unit="s", minimum=0.1, maximum=600),
    _e("mqtt_reconnect_max_s", "mqtt", label="reconnect backoff, maximum",
       unit="s", minimum=0.1, maximum=3600),
    _e("mqtt_keepalive_s", "mqtt", label="keepalive", unit="s",
       minimum=5, maximum=3600),
    # ---- advanced -----------------------------------------------------------
    _e("detector_queue_depth", "advanced", tier="restart",
       label="live detector queue depth", minimum=1, maximum=10000),
    _e("deferred_enabled", "advanced", tier="restart",
       label="deferred detector queue",
       help="A bounded queue for a detector too slow to run inline. No shipped "
            "plugin declares itself deferred, so this changes nothing on its "
            "own."),
    _e("deferred_queue_depth", "advanced", tier="restart",
       label="deferred queue depth", minimum=1, maximum=1000000),
    _e("deferred_shutdown_drain_timeout_s", "advanced",
       label="deferred drain timeout on shutdown", unit="s",
       minimum=0, maximum=600),
    _e("replay_loop", "advanced", tier="restart", label="loop the replay file"),
    _e("replay_speed", "advanced", tier="restart", label="replay speed",
       minimum=0.01, maximum=100),
    _e("synthetic_scene", "advanced", tier="restart", label="synthetic scene"),
    _e("synthetic_sample_rate", "advanced", tier="restart",
       label="synthetic sample rate", unit="Hz", minimum=8000, maximum=384000),
    _e("log_level", "advanced", tier="restart", label="log level",
       choices=("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"),
       note="Logging is configured once at process start."),
    _e("log_json", "advanced", tier="restart", label="JSON logs",
       note="Logging is configured once at process start."),
    _e("metrics_enabled", "advanced", tier="restart", label="Prometheus /metrics",
       note="The endpoint is mounted when the application is built."),
    # ---- setup (hidden) ------------------------------------------------------
    _e("setup_completed", "setup", label="first-run flow completed"),
)

EDITABLE_BY_NAME: dict[str, EditableSetting] = {e.name: e for e in EDITABLE_SETTINGS}


#: Fields that are deliberately **not** editable from a browser, each with the
#: concrete hazard that earns the exclusion. Tidiness is not on this list;
#: "an operator might get it wrong" is not on this list either -- that is what
#: ``danger`` and validation are for. Only outcomes with no recovery path from
#: the browser, and outcomes that turn the settings form into a different and
#: more powerful tool than a settings form, appear here.
NON_EDITABLE: dict[str, str] = {
    # -- the surface must not be able to weaken the surface --------------
    "auth_enabled": (
        "authentication must not be editable through the surface it protects: "
        "an unauthenticated session could disable the gate, and a "
        "half-configured one could lock every operator out with no recovery "
        "path but SSH. Set OO_AUTH_ENABLED in config/runtime.env."
    ),
    "auth_public_read_paths": "part of the authentication configuration (see auth_enabled).",
    "auth_password_min_length": "part of the authentication configuration (see auth_enabled).",
    "auth_argon2_time_cost": "part of the authentication configuration (see auth_enabled).",
    "auth_argon2_memory_cost_kib": "part of the authentication configuration (see auth_enabled).",
    "auth_argon2_parallelism": "part of the authentication configuration (see auth_enabled).",
    "auth_session_ttl_hours": "part of the authentication configuration (see auth_enabled).",
    "auth_session_cookie_name": "part of the authentication configuration (see auth_enabled).",
    "auth_cookie_secure": "part of the authentication configuration (see auth_enabled).",
    "auth_login_rate_limit_attempts": "part of the authentication configuration (see auth_enabled).",
    "auth_login_rate_limit_window_s": "part of the authentication configuration (see auth_enabled).",
    "auth_bootstrap_username": "part of the authentication configuration (see auth_enabled).",
    # -- lockout ---------------------------------------------------------
    "bind_host": (
        "changing where the API listens, from the API, is a remote-hands "
        "lockout: the next request goes to an address that no longer answers "
        "and there is no way back except SSH."
    ),
    "bind_port": "same lockout as bind_host: the browser cannot follow the station to a new port.",
    # -- shutdown-and-migrate, not a form field ---------------------------
    "data_dir": (
        "repointing storage under a running station orphans the database "
        "mid-write and strands every existing clip; this is a stop, move, "
        "migrate, start operation."
    ),
    "database_dsn": (
        "the same shutdown-and-migrate operation as data_dir, plus a DSN can "
        "carry credentials for a host this station has no business reaching."
    ),
    "runtime_env_path": (
        "this is the settings store itself. Repointing it makes the UI write "
        "to a file the process does not read, which is exactly the "
        "two-configurations-that-disagree failure the whole mechanism exists "
        "to prevent."
    ),
    # -- turns a settings form into an arbitrary-file tool -----------------
    "replay_path": (
        "the replay source plays a file of the operator's choosing into the "
        "live audio stream and the spectrogram. From a browser -- on a station "
        "whose shipped default is anonymous LAN access -- that is an "
        "arbitrary-file-read tool wearing a settings field. The 'replay' "
        "source is likewise not offered in the source picker."
    ),
    "web_dist": (
        "the API serves this directory's contents over HTTP. Pointing it at "
        "an arbitrary path publishes that path to anyone on the LAN."
    ),
    "birdnet_model_dir": (
        "chooses which model binary the station loads. Selecting the file a "
        "process loads and executes is not a settings decision made from a "
        "form; use 'oo models fetch', which records provenance and the "
        "licence acceptance."
    ),
}


class SettingValueError(ValueError):
    """A proposed value failed validation. ``errors`` maps field -> message."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


# ---------------------------------------------------------------------------
# Field introspection
# ---------------------------------------------------------------------------


def _annotation(name: str) -> Any:
    return Settings.model_fields[name].annotation


def _unwrap_optional(annotation: Any) -> Any:
    """``float | None`` -> ``float``; anything else unchanged."""
    origin = typing.get_origin(annotation)
    if origin in (types.UnionType, typing.Union):
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def default_for(name: str) -> Any:
    """The value shipped in ``config.py`` -- the operator's way back to a
    known state, so it travels with every field in the API payload."""
    return Settings.model_fields[name].default


def _is_tuple_field(name: str) -> bool:
    return typing.get_origin(_unwrap_optional(_annotation(name))) is tuple


def field_kind(name: str) -> str:
    """A coarse type for the form to render: bool/int/float/enum/csv/text."""
    annotation = _unwrap_optional(_annotation(name))
    if typing.get_origin(annotation) is Literal:
        return "enum"
    if typing.get_origin(annotation) is tuple:
        return "csv"
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    return "text"


def choices_for(spec: EditableSetting) -> tuple[str, ...]:
    if spec.choices:
        return spec.choices
    annotation = _unwrap_optional(_annotation(spec.name))
    if typing.get_origin(annotation) is Literal:
        return tuple(str(arg) for arg in typing.get_args(annotation))
    return ()


_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_db", "dB"),
    ("_hz", "Hz"),
    ("_ms", "ms"),
    ("_kib", "KiB"),
    ("_gb", "GB"),
    ("_s", "s"),
    ("_days", "days"),
    ("_min", "min"),
)


def unit_for(spec: EditableSetting) -> str | None:
    if spec.unit is not None:
        return spec.unit
    for suffix, unit in _UNIT_SUFFIXES:
        if spec.name.endswith(suffix):
            return unit
    return None


def label_for(spec: EditableSetting) -> str:
    return spec.label or spec.name.replace("_", " ")


def display_value(value: Any) -> Any:
    """JSON-safe rendering of a settings value for the API payload.

    Tuples become the comma-separated form the env file and the form input
    both use, so what an operator reads is what they can type back.
    """
    if isinstance(value, tuple | list):
        return ",".join(str(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def coerce_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate a client's proposed updates against the Settings field types.

    Returns the coerced values. Raises :class:`SettingValueError` naming every
    failing field at once, so a form round-trips one correction pass, not one
    per mistake. Unknown fields are an error, not ignored -- silently dropping
    a key is how a UI bug looks like a saved setting; a field that is
    deliberately not browser-editable gets its recorded reason as the message,
    rather than a bare "unknown field".

    ``null`` or an empty string means **restore the shipped default**. For an
    optional field that is ``None`` (unset), which is what an empty env value
    has always meant; for a required field it is the "put it back the way it
    came" affordance the tuning form needs, since a measured default an
    operator cannot return to is not much of a reference point.

    Cross-field rules that depend on the *merged* result belong to
    :func:`validate_merged`, which knows the current values.
    """
    errors: dict[str, str] = {}
    coerced: dict[str, Any] = {}
    for name, raw in updates.items():
        spec = EDITABLE_BY_NAME.get(name)
        if spec is None:
            reason = NON_EDITABLE.get(name)
            if reason is not None:
                errors[name] = f"not editable from the web UI: {reason}"
            elif name in Settings.model_fields:
                errors[name] = "not an operator-editable setting"
            else:
                errors[name] = "not a setting"
            continue
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            coerced[name] = default_for(name)
            continue
        if _is_tuple_field(name) and isinstance(raw, str):
            raw = _split_sequence(raw)
        try:
            adapter: TypeAdapter[Any] = TypeAdapter(_annotation(name))
            value = adapter.validate_python(raw)
        except ValidationError as exc:
            errors[name] = exc.errors()[0]["msg"]
            continue
        detail = _check_field(spec, value)
        if detail is not None:
            errors[name] = detail
            continue
        coerced[name] = value
    if errors:
        raise SettingValueError(errors)
    return coerced


def _split_sequence(raw: str) -> Any:
    """Accept both spellings a runtime.env file may already hold."""
    stripped = raw.strip()
    if stripped.startswith("["):
        return json.loads(stripped)
    return [part.strip() for part in stripped.split(",") if part.strip()]


def _check_field(spec: EditableSetting, value: Any) -> str | None:
    """Per-field semantics and bounds. ``None`` means the value is acceptable."""
    if value is None:
        return None
    choices = choices_for(spec)
    if choices and str(value) not in choices:
        return f"must be one of: {', '.join(choices)}"
    if spec.minimum is not None or spec.maximum is not None:
        with contextlib.suppress(TypeError, ValueError):
            numeric = float(value)
            unit = unit_for(spec)
            suffix = f" {unit}" if unit else ""
            if spec.minimum is not None and numeric < spec.minimum:
                return f"must be at least {_trim(spec.minimum)}{suffix}"
            if spec.maximum is not None and numeric > spec.maximum:
                return f"must be at most {_trim(spec.maximum)}{suffix}"
    if spec.name == "timezone":
        try:
            ZoneInfo(str(value))
        except Exception:
            return f"{value!r} is not an IANA timezone name (e.g. 'Europe/London')"
    if _is_tuple_field(spec.name):
        if not value:
            return "must list at least one value, comma-separated"
        if spec.name.endswith("_band_hz"):
            low, high = float(value[0]), float(value[1])
            if low >= high:
                return f"low edge ({_trim(low)} Hz) must be below the high edge ({_trim(high)} Hz)"
            if low < 0:
                return "band edges must not be negative"
    if spec.name == "preferred_sample_rates" and any(int(rate) <= 0 for rate in value):
        return "sample rates must be positive"
    return None


def _trim(number: float) -> str:
    return f"{number:g}"


#: Cross-field rules, checked against the *merged* configuration -- the saved
#: values with the proposed updates laid on top. Each is (field names it
#: reports against, predicate, message). A rule fires only when at least one of
#: its fields is being changed, so an operator is never blocked by a
#: pre-existing inconsistency they are not touching.
def _merged(settings: Settings, updates: dict[str, Any]) -> dict[str, Any]:
    return {name: updates.get(name, getattr(settings, name)) for name in Settings.model_fields}


def validate_merged(settings: Settings, updates: dict[str, Any]) -> None:
    """Reject combinations that are individually valid and jointly wrong.

    The bar this enforces, from the charter: a settings write must never leave
    the station in a state where capture cannot start. A floor above a ceiling
    renders a blank spectrogram; a ring buffer shorter than a block silently
    drops audio; a retention ladder out of order deletes the exemplar before
    the copy it was promoted from.
    """
    merged = _merged(settings, updates)
    errors: dict[str, str] = {}

    def rule(names: tuple[str, ...], ok: bool, message: str) -> None:
        if ok or not set(names) & set(updates):
            return
        for name in names:
            errors.setdefault(name, message)

    lat, lon = merged["latitude"], merged["longitude"]
    rule(
        ("latitude", "longitude"),
        (lat is None) == (lon is None),
        "set both coordinates, or clear both -- a lone coordinate is treated as "
        "no location at all",
    )
    rule(
        ("spectrogram_floor_db", "spectrogram_ceiling_db"),
        merged["spectrogram_floor_db"] < merged["spectrogram_ceiling_db"],
        "the audible floor must be below the ceiling, or the spectrogram maps "
        "every level to one colour",
    )
    rule(
        ("ultrasonic_spectrogram_floor_db", "ultrasonic_spectrogram_ceiling_db"),
        merged["ultrasonic_spectrogram_floor_db"]
        < merged["ultrasonic_spectrogram_ceiling_db"],
        "the ultrasonic floor must be below the ceiling, or the spectrogram "
        "maps every level to one colour",
    )
    rule(
        ("spectrogram_min_hz", "spectrogram_max_hz"),
        merged["spectrogram_min_hz"] < merged["spectrogram_max_hz"],
        "the lowest frequency shown must be below the highest",
    )
    rule(
        ("capture_block_ms", "capture_buffer_ms"),
        merged["capture_buffer_ms"] >= 2 * merged["capture_block_ms"],
        "the ALSA ring must hold at least two capture blocks, or a single "
        "scheduling hiccup loses audio (ADR-030)",
    )
    rule(
        ("reopen_backoff_min_s", "reopen_backoff_max_s"),
        merged["reopen_backoff_min_s"] <= merged["reopen_backoff_max_s"],
        "the minimum reopen backoff must not exceed the maximum",
    )
    rule(
        ("mqtt_reconnect_min_s", "mqtt_reconnect_max_s"),
        merged["mqtt_reconnect_min_s"] <= merged["mqtt_reconnect_max_s"],
        "the minimum reconnect backoff must not exceed the maximum",
    )
    rule(
        ("ultrasonic_min_pulse_ms", "ultrasonic_max_pulse_ms"),
        merged["ultrasonic_min_pulse_ms"] < merged["ultrasonic_max_pulse_ms"],
        "the minimum pulse length must be below the maximum",
    )
    rule(
        ("clip_pre_roll_s", "clip_post_roll_s", "clip_max_s"),
        merged["clip_pre_roll_s"] + merged["clip_post_roll_s"] <= merged["clip_max_s"],
        "pre-roll plus post-roll must fit inside the maximum clip length, or "
        "every clip is truncated to something shorter than it claims",
    )
    rule(
        ("clip_pre_roll_s", "native_ring_seconds"),
        merged["clip_pre_roll_s"] <= merged["native_ring_seconds"],
        "pre-roll cannot exceed the native ring buffer -- the audio before the "
        "detection no longer exists to be written",
    )
    rule(
        ("retention_native_days", "retention_audible_only_days"),
        merged["retention_native_days"] <= merged["retention_audible_only_days"],
        "the native tier must expire no later than the audible tier",
    )
    rule(
        ("retention_audible_only_days", "retention_exemplar_only_days"),
        merged["retention_audible_only_days"] <= merged["retention_exemplar_only_days"],
        "the audible tier must expire no later than the exemplar tier",
    )
    rule(
        ("refinement_window_start_hour_utc", "refinement_window_end_hour_utc"),
        merged["refinement_window_start_hour_utc"]
        != merged["refinement_window_end_hour_utc"],
        "the refinement window is half-open [start, end); equal hours would "
        "make it either empty or the whole day, and neither is what anyone "
        "means",
    )
    rule(
        ("birdnet_range_threshold", "birdnet_common_prior"),
        merged["birdnet_range_threshold"] <= merged["birdnet_common_prior"],
        "the out-of-range prior must not exceed the common-species prior, or "
        "the 'uncommon' band inverts",
    )
    rule(
        ("birdnet_plausibility_floor", "birdnet_range_threshold"),
        merged["birdnet_plausibility_floor"] <= merged["birdnet_range_threshold"],
        "the plausibility floor must sit at or below the out-of-range prior; "
        "above it, the out-of-range band can never be reached at all",
    )
    rule(
        ("preferred_sample_rates",),
        bool(merged["preferred_sample_rates"]),
        "at least one sample rate must be offered, or capture has nothing to "
        "negotiate and cannot start",
    )
    rule(
        ("preferred_formats",),
        bool(merged["preferred_formats"]),
        "at least one sample format must be offered, or capture has nothing to "
        "negotiate and cannot start",
    )
    if errors:
        raise SettingValueError(errors)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def to_env_value(value: Any) -> str | None:
    """Render a coerced value as the string ``runtime.env`` stores.

    ``None`` means the key is removed from the file entirely, falling back to
    the shipped default -- an absent key is honest about being unset, where an
    empty ``OO_LATITUDE=`` used to crash startup (see config.py's
    ``_empty_env_is_unset``). Sequences are written comma-separated, which is
    what ``Settings``' own ``_split_csv``/``_split_band`` validators read back.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple | list):
        return ",".join(str(item) for item in value)
    return str(value)


class RuntimeEnvStore:
    """Reads and rewrites ``config/runtime.env`` without owning it.

    The file is operator state: hand-written comments and settings outside
    the UI catalogue must survive a UI save byte-for-byte. Only lines whose
    key is being updated are touched; new keys are appended under a marked
    section. Writes are atomic (tempfile + rename in the same directory) so
    a crash mid-save leaves the old file, not half a file.
    """

    MANAGED_MARK = "# --- written by the web UI settings page ---"

    def __init__(self, path: Path) -> None:
        self.path = path

    def apply(self, updates: dict[str, Any]) -> None:
        env_updates = {f"OO_{name.upper()}": to_env_value(value) for name, value in updates.items()}
        lines = (
            self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        )
        remaining = dict(env_updates)
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            key = stripped.split("=", 1)[0].strip() if "=" in stripped else None
            if key in remaining and not stripped.startswith("#"):
                value = remaining.pop(key)
                if value is not None:
                    out.append(f"{key}={value}")
                # None: drop the line -- absent key means shipped default.
                continue
            out.append(line)
        additions = [f"{key}={value}" for key, value in remaining.items() if value is not None]
        if additions:
            if self.MANAGED_MARK not in out:
                out.extend(["", self.MANAGED_MARK])
            out.extend(additions)
        self._write_atomic("\n".join(out) + "\n")

    def _write_atomic(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".runtime.env.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            # runtime.env can hold credentials; never world-readable.
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


# ---------------------------------------------------------------------------
# API payloads
# ---------------------------------------------------------------------------


def describe_settings(
    settings: Settings, *, applied_site: dict[str, Any] | None
) -> dict[str, Any]:
    """The GET /api/v1/settings payload.

    ``applied_site`` is what the running pipeline was actually built with or
    last retuned to (``Station.applied_site``), so a saved-but-not-yet-live
    value is reported as exactly that instead of quietly looking live. It
    covers live-tier fields too: a live field whose application did not
    happen -- no ultrasonic encoder at this sample rate, a detector that is
    switched off -- is pending, and saying otherwise would be the honesty
    constraint violated on the settings surface itself.
    """
    fields = []
    pending: list[str] = []
    for spec in EDITABLE_SETTINGS:
        value = getattr(settings, spec.name)
        entry: dict[str, Any] = {
            "name": spec.name,
            "category": spec.category,
            "tier": spec.tier,
            "kind": field_kind(spec.name),
            "label": label_for(spec),
            "help": spec.help or None,
            "unit": unit_for(spec),
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "choices": list(choices_for(spec)),
            "danger": spec.danger or None,
            "secret": spec.secret,
            "restart_required": spec.restart_required,
            "note": spec.note or None,
            "default": display_value(default_for(spec.name)),
        }
        if spec.secret:
            entry["value"] = None
            entry["is_set"] = bool(value)
        else:
            entry["value"] = display_value(value)
        if (
            applied_site is not None
            and spec.name in applied_site
            and applied_site[spec.name] != value
        ):
            entry["pending_restart"] = True
            pending.append(spec.name)
        fields.append(entry)
    return {
        "fields": fields,
        "categories": [
            {
                "id": category.id,
                "title": category.title,
                "description": category.description,
                "hidden": category.hidden,
            }
            for category in CATEGORIES
        ],
        "non_editable": [
            {"name": name, "reason": reason} for name, reason in sorted(NON_EDITABLE.items())
        ],
        "pending_restart": pending,
        "location_configured": settings.latitude is not None and settings.longitude is not None,
    }


@dataclass(frozen=True)
class SetupStep:
    """One item in the guided first-run flow."""

    id: str
    title: str
    detail: str
    done: bool
    #: The station is fully usable without this one.
    optional: bool = False
    #: Settings the step edits, in the order the form should show them.
    fields: tuple[str, ...] = field(default_factory=tuple)


def describe_setup(settings: Settings, *, capture: dict[str, Any]) -> dict[str, Any]:
    """The GET /api/v1/setup payload: what a person needs on day one.

    Deliberately short. This is "the station guides rather than fails" -- four
    questions a new operator can answer in a minute -- not the commissioning
    wizard of Milestone 7. Each step reports what it knows, including when the
    answer is "your microphone is not the one recording right now", because a
    first-run flow that says "all done" over a synthetic fallback source would
    be the most expensive lie this surface could tell.
    """
    live_hardware = bool(capture.get("is_live_hardware"))
    device = capture.get("device") or {}
    device_name = device.get("name") or device.get("key") or capture.get("detail") or ""
    rate = device.get("sample_rate") or capture.get("sample_rate")

    if live_hardware:
        mic_detail = f"Recording from {device_name or 'the attached device'}"
        if rate:
            mic_detail += f" at {int(rate)} Hz"
            if int(rate) < 96000:
                mic_detail += " — below 96 kHz, so ultrasonic detection is unavailable"
        mic_detail += "."
    else:
        mic_detail = (
            "No microphone is being recorded from: the station is running on a "
            f"synthetic or replay source ({capture.get('detail') or capture.get('state')}). "
            "Check the device is attached and, if it needs naming, set the capture "
            "device key."
        )

    steps = (
        SetupStep(
            id="location",
            title="Where is this station?",
            detail=(
                "Coordinates switch on species plausibility filtering and the "
                "ultrasonic night schedule. Until they are set the station runs "
                "unfiltered and always-on — which is honest, but noisier than it "
                "needs to be."
            ),
            done=settings.latitude is not None and settings.longitude is not None,
            fields=("latitude", "longitude"),
        ),
        SetupStep(
            id="timezone",
            title="What time is it here?",
            detail=(
                "Everything is stored in UTC. This is only how times are shown to "
                "you — and, once coordinates are set, how dusk and dawn are named."
            ),
            done=settings.timezone != "UTC",
            optional=True,
            fields=("station_name", "timezone"),
        ),
        SetupStep(
            id="microphone",
            title="Is the microphone working?",
            detail=mic_detail,
            done=live_hardware,
            fields=("audio_device", "preferred_sample_rates", "source"),
        ),
        SetupStep(
            id="mqtt",
            title="Publish to Home Assistant?",
            detail=(
                "Entirely optional. Capture, detection, review and query never "
                "need it, and leaving it off means no new network traffic and no "
                "new failure mode."
            ),
            done=settings.mqtt_enabled,
            optional=True,
            fields=("mqtt_enabled", "mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password"),
        ),
    )
    required = [step for step in steps if not step.optional]
    return {
        "completed": settings.setup_completed,
        "required_outstanding": [step.id for step in required if not step.done],
        "steps": [
            {
                "id": step.id,
                "title": step.title,
                "detail": step.detail,
                "done": step.done,
                "optional": step.optional,
                "fields": list(step.fields),
            }
            for step in steps
        ],
    }
