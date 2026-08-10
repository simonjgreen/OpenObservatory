"""``oo`` — the operator command line.

Every diagnostic the technical spec asks for before trusting the hardware lives
here, so the answer to "what did the device actually negotiate?" is a recorded
command output rather than an assumption.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import structlog
import typer
from rich.console import Console
from rich.table import Table

from . import models as model_registry
from .config import Settings, get_settings, set_settings

app = typer.Typer(help="Open Observatory operator CLI", no_args_is_help=True)
audio_app = typer.Typer(help="Audio device diagnostics")
models_app = typer.Typer(help="Model asset acquisition (ADR-006)")
moth_app = typer.Typer(help="AudioMoth firmware and configuration over USB HID")
history_app = typer.Typer(help="Capture history and coverage diagnostics")
clips_app = typer.Typer(help="Evidence clip storage and retention (ADR-026)")
detections_app = typer.Typer(help="Detection review and repair")
refine_app = typer.Typer(help="The refinement runner (charter item 5, ADR-045)")
app.add_typer(audio_app, name="audio")
app.add_typer(models_app, name="models")
app.add_typer(moth_app, name="audiomoth")
app.add_typer(history_app, name="history")
app.add_typer(clips_app, name="clips")
app.add_typer(detections_app, name="detections")
app.add_typer(refine_app, name="refine")

console = Console()
console_err = Console(stderr=True)


def notice(markup: str, *, json_out: bool = False) -> None:
    """Advisory text for a human, kept off a `--json` document.

    Anything printed to stdout *after* `emit_json` lands inside the same stream
    a caller is parsing, and turns a valid document into "Extra data" at the
    line where the advice begins. That is not hypothetical: the dry-run notice
    on `detections reconcile-plausibility` did exactly this to a 1,485-line
    report, and the JSON above it was perfectly well-formed.

    So under `--json` the advice goes to stderr, where a human still sees it and
    a pipe does not.
    """
    if json_out:
        console_err.print(markup)
    else:
        console.print(markup)


def emit_json(payload: Any) -> None:
    """Write machine-readable JSON to stdout, and nothing else.

    Not `console.print_json`: rich colourises its output and emits ANSI escape
    sequences even when stdout is a pipe, so every `--json` flag in this CLI
    used to produce something `jq` and `json.load` both reject with a parse
    error at column 2. A `--json` option exists to be piped; output that only
    works when a human is looking at it is not machine-readable.
    """
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")
    sys.stdout.flush()


class _StderrLoggerFactory:
    """A structlog logger factory that looks up `sys.stderr` on every write.

    See the comment in `configure_logging` for why binding the stream once is a
    trap. This keeps `cache_logger_on_first_use=True` -- the station logs often
    enough for that to be worth having -- while making the cached logger
    indifferent to the stream being swapped underneath it.
    """

    def __call__(self, *args: Any) -> Any:
        return structlog.PrintLogger(file=_LiveStderr())  # type: ignore[arg-type]


class _LiveStderr:
    """Forwards to whatever `sys.stderr` is at the moment of the call."""

    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Resolve `sys.stderr` at write time, not at configure time.
        #
        # `PrintLoggerFactory(file=sys.stderr)` binds the stream object once, and
        # `cache_logger_on_first_use` then keeps that bound logger forever. If
        # anything replaces or closes stderr afterwards, every later log call
        # raises `ValueError: I/O operation on closed file` -- and because the
        # logger is cached, the failure outlives whatever did the replacing.
        #
        # Found upgrading typer 0.15 -> 0.27, whose `CliRunner` closes the stream
        # it substitutes: 127 tests failed, none of them alone, all of them after
        # some earlier test had used the runner. A real `oo` process has a stderr
        # that never closes, so this was only ever a test-harness fault -- but a
        # logger that can be permanently poisoned by a stream swap is worth not
        # having, and it blocked seventeen unrelated dependency upgrades.
        logger_factory=_StderrLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ----------------------------------------------------------------------
# audio


@audio_app.command("probe")
def audio_probe(
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
    write: Path | None = typer.Option(
        None, "--write", help="Also write the report to this path"
    ),
    test_rates: bool = typer.Option(
        True, help="Open the hardware to confirm which rates are natively supported"
    ),
) -> None:
    """Enumerate capture devices and record exactly what they support.

    Nothing downstream is allowed to assume the AudioMoth's rate or format; this
    is where those facts come from.
    """
    from .audio.probe import enumerate_capture_devices, find_device, probe_supported_rates, system_report

    settings = get_settings()
    devices = enumerate_capture_devices()
    report: dict[str, Any] = {
        "devices": [device.to_dict() for device in devices],
        "system": system_report(),
        "preferred_sample_rates": list(settings.preferred_sample_rates),
    }

    if test_rates and devices:
        selected = find_device(settings.audio_device)
        if selected is not None:
            support = probe_supported_rates(selected, settings.preferred_sample_rates)
            report["selected_device_key"] = selected.stable_device_key
            report["rate_support"] = {str(rate): state for rate, state in support.items()}

    if json_out:
        emit_json(report)
    else:
        if not devices:
            console.print("[bold red]No ALSA capture device found.[/bold red]")
            console.print(
                "If an AudioMoth is attached, check the side switch is in DEFAULT "
                "(streaming) rather than USB/OFF (configuration)."
            )
        for device in devices:
            table = Table(title=f"{device.card_name}  [{device.stable_device_key}]")
            table.add_column("property")
            table.add_column("value", overflow="fold")
            table.add_row("driver", device.driver)
            table.add_row("alsa address", device.alsa_address)
            table.add_row("card index (unstable)", str(device.card_index))
            table.add_row("by-id symlink", device.by_id_symlink or "—")
            table.add_row(
                "usb", f"{device.usb_vendor_id}:{device.usb_product_id} serial={device.usb_serial}"
            )
            table.add_row("advertised rates", ", ".join(str(r) for r in device.advertised_rates) or "—")
            for index, profile in enumerate(device.profiles):
                table.add_row(
                    f"profile {index}",
                    f"{profile.sample_format} {profile.channels}ch "
                    f"{profile.sample_rates} bits={profile.bits} map={profile.channel_map}",
                )
            for note in device.notes:
                table.add_row("note", note)
            console.print(table)
        if "rate_support" in report:
            table = Table(title="Native rate support (hw: device, no plug resampling)")
            table.add_column("rate")
            table.add_column("state")
            styles = {
                "supported": "[green]supported[/green]",
                "unsupported": "[red]unsupported[/red]",
                "resampled": "[yellow]device substituted another rate[/yellow]",
                "busy": "[cyan]busy — the station is capturing[/cyan]",
            }
            for rate, state in report["rate_support"].items():
                table.add_row(rate, styles.get(state, state))
            console.print(table)
            if any(state == "busy" for state in report["rate_support"].values()):
                console.print(
                    "[dim]Stop the station (sudo systemctl stop open-observatory) to probe "
                    "rates directly; only one process may own the microphone.[/dim]"
                )

    if write:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(json.dumps(report, indent=2) + "\n")
        console.print(f"[dim]written to {write}[/dim]")


@audio_app.command("test-capture")
def audio_test_capture(
    seconds: float = typer.Option(5.0, help="Duration to record"),
    out: Path | None = typer.Option(None, help="Write the captured audio to this WAV"),
) -> None:
    """Capture briefly from the real device and report timing and levels.

    This is the acceptance check for "the microphone works": it reports the frames
    actually delivered against the frames elapsed monotonic time implies, so a
    device that silently drops audio cannot pass.
    """
    from .audio.alsa_source import AlsaSource
    from .audio.contracts import NS_PER_S
    from .audio.levels import measure

    settings = get_settings()
    configure_logging(settings)

    async def run() -> int:
        source = AlsaSource(
            device_key=settings.audio_device,
            preferred_rates=settings.preferred_sample_rates,
            preferred_formats=settings.preferred_formats,
            channels=settings.capture_channels,
            block_ms=settings.capture_block_ms,
        )
        try:
            info = await source.open()
        except Exception as exc:
            console.print(f"[bold red]could not open capture device:[/bold red] {exc}")
            return 1

        rate = info.fmt.sample_rate
        target = int(seconds * rate)
        import numpy as np

        collected: list[Any] = []
        frames = 0
        discontinuities = 0
        began = info.started_monotonic_ns
        first_block_ns: int | None = None
        while frames < target:
            block = await source.read()
            if block is None:
                break
            if first_block_ns is None:
                first_block_ns = block.monotonic_start_ns
            frames += block.frame_count
            if block.discontinuity is not None and block.sequence > 0:
                discontinuities += 1
            collected.append(block.pcm)
        await source.close()

        audio = np.concatenate(collected) if collected else np.zeros(0, dtype="float32")
        elapsed_s = (
            (block.monotonic_end_ns - (first_block_ns or began)) / NS_PER_S if collected else 0.0
        )
        levels = measure(audio, rate)

        table = Table(title="Capture test")
        table.add_column("measurement")
        table.add_column("value")
        table.add_row("device", f"{info.device_label}  [{info.device_key}]")
        table.add_row("negotiated", f"{rate} Hz {info.fmt.sample_format} {info.fmt.channels}ch")
        table.add_row("frames delivered", f"{frames:,}")
        table.add_row("frames expected", f"{int(elapsed_s * rate):,}")
        table.add_row("audio seconds", f"{frames / rate:.4f}")
        table.add_row("wall seconds", f"{elapsed_s:.4f}")
        table.add_row("discontinuities", str(discontinuities))
        table.add_row("overruns reported", str(source.overrun_count))
        table.add_row("rms", f"{levels.rms_dbfs:.1f} dBFS")
        table.add_row("peak", f"{levels.peak_dbfs:.1f} dBFS")
        table.add_row("crest factor", f"{levels.crest_factor_db:.1f} dB")
        table.add_row("clipped samples", f"{levels.clipped_samples:,} ({levels.clipping_ratio:.4%})")
        table.add_row("dc offset", f"{levels.dc_offset:+.6f}")
        console.print(table)

        if levels.silent:
            console.print("[bold red]Signal is silent — check the microphone and gain.[/bold red]")
        elif levels.clipping_ratio > 0.0005:
            console.print(
                "[bold yellow]Input is clipping. Lower the AudioMoth gain "
                "(switch to USB/OFF and use the USB Microphone app).[/bold yellow]"
            )

        if out:
            import soundfile as sf

            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(out), audio, rate, subtype="PCM_16")
            console.print(f"[dim]wrote {out} ({out.stat().st_size:,} bytes)[/dim]")
        return 0

    raise typer.Exit(asyncio.run(run()))


@audio_app.command("resample-check")
def audio_resample_check(
    source_rate: int = typer.Option(384000, help="Native rate to test"),
    target_rate: int = typer.Option(48000, help="Derived audible rate"),
    seconds: float = typer.Option(60.0, help="Synthetic audio duration"),
    block_ms: int = typer.Option(100, help="Capture block size"),
) -> None:
    """Verify the resampler's timing properties against the audio pipeline spec.

    Three separate properties, which are easy to conflate:

    **Group delay** — does output frame n correspond to native frame n*src/dst?
    Measured with an impulse. A non-zero delay would bias every audible detection
    timestamp, so it must be zero or explicitly compensated.

    **Delivery deficit** — how far behind the exact ratio is the *count* of frames
    produced so far? libsoxr emits ragged chunks, so this oscillates within a
    bounded band. Bounded is correct; trending is cumulative drift and a failure.

    **Seam continuity** — does a block boundary introduce a click?
    """
    import numpy as np

    from .audio.resample import AudibleResampler

    block_frames = int(source_rate * block_ms / 1000)
    blocks = int(seconds * source_rate / block_frames)

    # --- group delay, by impulse ------------------------------------------
    delay_converter = AudibleResampler(source_rate, target_rate)
    impulse_at = block_frames * 5 + block_frames // 3
    impulse_out: list[Any] = []
    for index in range(blocks if blocks < 20 else 20):
        chunk = np.zeros(block_frames, dtype=np.float32)
        local = impulse_at - index * block_frames
        if 0 <= local < block_frames:
            chunk[local] = 1.0
        impulse_out.append(delay_converter.process(chunk).pcm)
    impulse_signal = np.concatenate(impulse_out)
    ideal_position = impulse_at * target_rate / source_rate
    if impulse_signal.size and float(np.abs(impulse_signal).max()) > 0:
        actual_position = float(np.argmax(np.abs(impulse_signal)))
        group_delay = actual_position - ideal_position
    else:
        actual_position = float("nan")
        group_delay = float("nan")

    # --- deficit band and seam continuity over a tone ---------------------
    converter = AudibleResampler(source_rate, target_rate)
    tone_hz = 1000.0
    outputs: list[Any] = []
    deficits: list[int] = []
    phase = 0
    for _ in range(blocks):
        t = (phase + np.arange(block_frames)) / source_rate
        outputs.append(converter.process(0.5 * np.sin(2 * np.pi * tone_hz * t)).pcm)
        phase += block_frames
        deficits.append(
            converter.expected_output_frames(converter.input_frames) - converter.output_frames
        )

    derived = np.concatenate(outputs)
    deficit_min, deficit_max = min(deficits), max(deficits)
    # Trend test: compare the mean deficit of the first and last tenth of the run.
    tenth = max(1, len(deficits) // 10)
    trend = float(np.mean(deficits[-tenth:]) - np.mean(deficits[:tenth]))

    diffs = np.abs(np.diff(derived))
    median_step = float(np.median(diffs))
    worst_step = float(diffs.max())

    size = min(1 << 16, derived.shape[0])
    spectrum = np.abs(np.fft.rfft(derived[:size] * np.hanning(size)))
    peak_hz = float(np.fft.rfftfreq(size, 1.0 / target_rate)[int(np.argmax(spectrum))])

    table = Table(title="Resampler check")
    table.add_column("measurement")
    table.add_column("value")
    table.add_row("backend", f"{converter.backend} ({converter.backend_detail})")
    table.add_row("ratio", f"{converter.ratio.numerator}/{converter.ratio.denominator}")
    table.add_row("audio duration", f"{converter.input_frames / source_rate:.1f} s")
    table.add_row("input frames", f"{converter.input_frames:,}")
    table.add_row("output frames", f"{derived.shape[0]:,}")
    table.add_row("expected by exact ratio", f"{converter.expected_output_frames(converter.input_frames):,}")
    table.add_row(
        "group delay",
        f"{group_delay:+.2f} output frames ({group_delay / target_rate * 1000:+.4f} ms)",
    )
    table.add_row(
        "delivery deficit band",
        f"{deficit_min} to {deficit_max} frames "
        f"({deficit_min / target_rate * 1000:.2f}-{deficit_max / target_rate * 1000:.2f} ms)",
    )
    table.add_row(
        "deficit trend (last-first decile)",
        f"{trend:+.1f} frames — "
        + (
            "[green]bounded[/green]"
            if abs(trend) < (deficit_max - deficit_min) + 8
            else "[red]TRENDING[/red]"
        ),
    )
    table.add_row("tone in", f"{tone_hz:.1f} Hz")
    table.add_row("tone out (peak bin)", f"{peak_hz:.1f} Hz")
    table.add_row("worst / median sample step", f"{worst_step / max(median_step, 1e-9):.2f}x")
    table.add_row(
        "seam continuity",
        "[green]no discontinuity[/green]"
        if worst_step < median_step * 25
        else f"[red]suspicious jump: {worst_step:.4f}[/red]",
    )
    console.print(table)

    failures: list[str] = []
    if not (abs(group_delay) <= 1.0):
        failures.append(
            f"group delay of {group_delay:+.2f} output frames would bias every "
            "audible detection timestamp"
        )
    if abs(trend) >= (deficit_max - deficit_min) + 8:
        failures.append(
            f"delivery deficit is trending by {trend:+.1f} frames, which is cumulative drift"
        )
    if worst_step >= median_step * 25:
        failures.append("a block seam introduced a discontinuity")
    if abs(peak_hz - tone_hz) > target_rate / size * 2:
        failures.append(f"tone moved from {tone_hz} Hz to {peak_hz:.1f} Hz")

    if failures:
        for failure in failures:
            console.print(f"[bold red]FAIL:[/bold red] {failure}")
        raise typer.Exit(1)
    console.print(
        "[green]Timing is sound: zero group delay, bounded delivery latency, "
        "continuous across seams.[/green]"
    )


@audio_app.command("window-dump")
def audio_window_dump(
    source: Path | None = typer.Option(
        None, "--source", help="WAV file to replay through the pipeline; omit for a synthetic scene"
    ),
    scene: str = typer.Option(
        "impulse", help="Synthetic scene when --source is omitted (see `oo audio window-dump --help`)"
    ),
    stream_kind: str = typer.Option(
        "audible48", "--stream-kind", help="'native' or 'audible48' -- which derived stream to segment"
    ),
    sample_rate: int = typer.Option(384000, help="Native rate to assume for --source, or to synthesise"),
    duration_s: float = typer.Option(3.0, help="WindowSpec.duration_s"),
    stride_s: float = typer.Option(3.0, help="WindowSpec.stride_s"),
    seconds: float = typer.Option(12.0, help="How much audio to feed through the segmenter"),
    block_ms: int = typer.Option(100, help="Capture block size, matching real ALSA block pacing"),
    index: int = typer.Option(0, "--index", help="Which completed window to show in full detail"),
    gap_at_s: float | None = typer.Option(
        None, "--gap-at-s", help="Inject a capture gap once this many seconds of native audio have been read"
    ),
    gap_frames: int = typer.Option(
        0, help="Native frames to drop when --gap-at-s fires, simulating lost audio"
    ),
    write_wav: Path | None = typer.Option(
        None, "--write-wav", help="Write the detailed window's actual PCM to this WAV file"
    ),
    timezone: str | None = typer.Option(
        None, help="IANA zone for local-time rendering; defaults to the configured station timezone"
    ),
    summary_only: bool = typer.Option(
        False, "--summary-only", help="Skip the full detail block for --index"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
) -> None:
    """Inspect one window exactly as the segmenter cut it, with ground truth.

    This is Milestone 2's window inspection CLI. It runs the *real* production
    classes -- ``StreamClock``, ``AudibleResampler``, ``StreamSegmenter`` and a
    ``RingBuffer`` -- over a replayed WAV file or a synthetic scene, the same way
    ``station.py`` drives them from live capture. It does not attach to a running
    station: the native ring buffer is in-process memory owned by whichever
    process holds the microphone (technical spec, "one process owns the
    microphone"), and a second process cannot read it without either perturbing
    capture or adding a new IPC surface, which is out of scope here. So this
    command is read-only with respect to the live capture path in the strongest
    sense: it never touches it. What it inspects is stored/replay audio, run
    through the identical segmenter code a live stream would use.

    Frames address the audio, never wall-clock time: every window is reported by
    its actual ``start_frame``/``end_frame`` (and, for the audible stream, the
    native frame range it maps back to), with UTC first and a local-time
    rendering derived from it -- never the other way around.

    Ground truth, not a summary that could itself be lying: each window's frame
    count comes from the actual shape of its ``pcm`` array, not from
    ``WindowSpec`` arithmetic, and is independently cross-checked against a
    second read of the same frame range from a fresh ``RingBuffer`` fed the same
    derived audio. A mismatch is reported, not silently trusted.
    """
    import hashlib
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    import numpy as np

    from .audio.contracts import NS_PER_S, AudioWindow, StreamClock, WindowSpec
    from .audio.replay_source import ReplaySource, SyntheticSource
    from .audio.resample import AudibleResampler
    from .audio.ring import RingBuffer
    from .segmenter import WindowRouter

    if stream_kind not in ("native", "audible48"):
        console.print("[bold red]--stream-kind must be 'native' or 'audible48'[/bold red]")
        raise typer.Exit(2)

    settings = get_settings()
    configure_logging(settings)  # keep structlog on stderr so --json stdout stays clean
    try:
        tz = ZoneInfo(timezone or settings.timezone)
    except Exception as exc:
        console.print(f"[bold red]bad timezone:[/bold red] {exc}")
        raise typer.Exit(2) from None

    def _utc(ns: int) -> datetime:
        return datetime.fromtimestamp(ns / NS_PER_S, tz=UTC)

    def _iso(ns: int) -> str:
        return _utc(ns).isoformat().replace("+00:00", "Z")

    def _local(ns: int) -> str:
        return _utc(ns).astimezone(tz).isoformat()

    async def run() -> dict[str, Any]:
        capture_source: ReplaySource | SyntheticSource
        if source is not None:
            capture_source = ReplaySource(source, block_ms=block_ms, mode="accelerated", loop=False)
        else:
            capture_source = SyntheticSource(
                scene=scene, sample_rate=sample_rate, block_ms=block_ms, mode="accelerated"
            )
        info = await capture_source.open()
        native_rate = info.fmt.sample_rate
        output_rate = settings.audible_sample_rate if stream_kind == "audible48" else native_rate

        window_spec = WindowSpec(
            stream_kind=stream_kind,  # type: ignore[arg-type]
            sample_rate=output_rate,
            duration_s=duration_s,
            stride_s=stride_s,
        )
        router = WindowRouter(native_rate=native_rate, stream_id=info.stream_id)
        router.register(window_spec, "window-dump", sample_rate=output_rate)
        resampler = (
            AudibleResampler(native_rate, output_rate) if stream_kind == "audible48" else None
        )

        # An independent read path for the ground-truth cross-check: the ring
        # is fed the same derived audio the segmenter sees, but through its own
        # accounting, so a segmenter bug and a ring bug would have to agree to
        # go unnoticed.
        ring_capacity_s = max(seconds * 1.5, duration_s * 4, 5.0)
        truth_ring = RingBuffer(output_rate, ring_capacity_s)

        clock: StreamClock | None = None
        windows: list[AudioWindow] = []
        native_frames_seen = 0
        gap_injected = False
        target_native_frames = int(seconds * native_rate)

        while native_frames_seen < target_native_frames:
            if (
                gap_at_s is not None
                and not gap_injected
                and native_frames_seen >= int(gap_at_s * native_rate)
            ):
                capture_source.inject_gap(gap_frames)
                gap_injected = True

            block = await capture_source.read()
            if block is None:
                break
            native_frames_seen += block.frame_count

            if clock is None:
                offset_ns = block.first_frame * NS_PER_S // block.sample_rate
                clock = StreamClock(
                    utc_ns_at_frame_zero=block.utc_start_ns - offset_ns,
                    monotonic_ns_at_frame_zero=block.monotonic_start_ns - offset_ns,
                )

            discontinuous = block.discontinuity is not None and block.sequence > 0

            if stream_kind == "native":
                out_pcm, out_first_frame = block.pcm, block.first_frame
            else:
                assert resampler is not None
                derived = resampler.process(block.pcm)
                out_pcm, out_first_frame = derived.pcm, derived.first_frame

            if out_pcm.size == 0:
                continue

            out_utc = clock.utc_ns(out_first_frame, output_rate)
            out_monotonic = clock.monotonic_ns(out_first_frame, output_rate)
            truth_ring.append(out_first_frame, out_pcm, out_monotonic)

            def _collect(
                window: AudioWindow, _consumers: list[str], _sink: list[AudioWindow] = windows
            ) -> None:
                _sink.append(window)

            router.push(
                stream_kind,  # type: ignore[arg-type]
                out_pcm,
                out_first_frame,
                out_utc,
                out_monotonic,
                discontinuous=discontinuous,
                on_window=_collect,
            )

        await capture_source.close()

        segmenter_snapshot = router.snapshot()

        report: dict[str, Any] = {
            "stream": {
                "stream_id": str(info.stream_id),
                "source_kind": str(info.source_kind),
                "device_label": info.device_label,
                "native_sample_rate": native_rate,
                "stream_kind": stream_kind,
                "output_sample_rate": output_rate,
            },
            "clock": {
                "utc_ns_at_frame_zero": clock.utc_ns_at_frame_zero if clock else None,
                "utc_at_frame_zero": _iso(clock.utc_ns_at_frame_zero) if clock else None,
            },
            "window_spec": {
                "duration_s": duration_s,
                "stride_s": stride_s,
                "expected_frame_count": window_spec.frame_count,
            },
            "gap_injected": {"at_s": gap_at_s, "frames": gap_frames} if gap_injected else None,
            "segmenter": segmenter_snapshot[0] if segmenter_snapshot else None,
            "native_frames_fed": native_frames_seen,
            "windows": [],
        }

        for i, window in enumerate(windows):
            actual_frames = int(window.pcm.shape[0])
            truth = truth_ring.extract(window.start_frame, window.end_frame)
            ring_match = truth is not None and np.array_equal(truth, window.pcm)
            entry: dict[str, Any] = {
                "index": i,
                "window_id": str(window.window_id),
                "start_frame": window.start_frame,
                "end_frame": window.end_frame,
                "native_start_frame": window.native_start_frame,
                "native_end_frame": window.native_end_frame,
                "actual_frame_count": actual_frames,
                "expected_frame_count": window_spec.frame_count,
                "duration_s_actual": round(actual_frames / window.sample_rate, 6),
                "utc_start": _iso(window.utc_start_ns),
                "utc_end": _iso(window.utc_end_ns),
                "local_start": _local(window.utc_start_ns),
                "local_end": _local(window.utc_end_ns),
                "ring_cross_check": "match" if ring_match else "MISMATCH-or-unavailable",
            }
            if i == index:
                pcm = window.pcm
                peak = float(np.max(np.abs(pcm))) if pcm.size else 0.0
                rms = float(np.sqrt(np.mean(np.square(pcm)))) if pcm.size else 0.0
                entry["detail"] = {
                    "peak": round(peak, 6),
                    "rms": round(rms, 6),
                    "sha256": hashlib.sha256(np.ascontiguousarray(pcm).tobytes()).hexdigest(),
                    "first_samples": [round(float(x), 6) for x in pcm[:8]],
                    "last_samples": [round(float(x), 6) for x in pcm[-8:]],
                }
                if write_wav is not None:
                    import soundfile as sf

                    write_wav.parent.mkdir(parents=True, exist_ok=True)
                    sf.write(str(write_wav), pcm, window.sample_rate, subtype="FLOAT")
                    entry["written_to"] = str(write_wav)
            report["windows"].append(entry)

        return report

    report = asyncio.run(run())

    if json_out:
        emit_json(report)
        if not report["windows"]:
            raise typer.Exit(1)
        return

    console.print(
        f"[bold]{report['stream']['source_kind']}[/bold] "
        f"{report['stream']['device_label']}  stream_id={report['stream']['stream_id']}"
    )
    console.print(
        f"native {report['stream']['native_sample_rate']} Hz -> "
        f"segmenting [bold]{stream_kind}[/bold] @ {report['stream']['output_sample_rate']} Hz, "
        f"window {duration_s}s / stride {stride_s}s"
    )
    if clock_row := report["clock"]["utc_at_frame_zero"]:
        console.print(f"stream clock anchor: frame 0 = {clock_row} UTC")
    if report["gap_injected"]:
        console.print(
            f"[yellow]injected a {report['gap_injected']['frames']}-frame gap at "
            f"{report['gap_injected']['at_s']}s of native audio[/yellow]"
        )

    if not report["windows"]:
        console.print(
            f"[bold red]No window completed.[/bold red] {report['native_frames_fed']:,} native "
            f"frames were fed through, but that is less than one window's worth. "
            "Increase --seconds or shorten --duration-s."
        )
        raise typer.Exit(1)

    table = Table(title="Windows emitted")
    for col in (
        "idx", "start_frame", "end_frame", "native_start", "native_end",
        "frames (actual/expected)", "utc_start", "local_start", "ring check",
    ):
        table.add_column(col)
    for entry in report["windows"]:
        table.add_row(
            str(entry["index"]),
            str(entry["start_frame"]),
            str(entry["end_frame"]),
            str(entry["native_start_frame"]),
            str(entry["native_end_frame"]),
            f"{entry['actual_frame_count']}/{entry['expected_frame_count']}",
            entry["utc_start"],
            entry["local_start"],
            "[green]match[/green]" if entry["ring_cross_check"] == "match" else "[red]MISMATCH[/red]",
        )
    console.print(table)

    if report["segmenter"] is not None:
        seg = report["segmenter"]
        console.print(
            "segmenter view: "
            f"buffered_frames={seg['buffered_frames']} ({seg['buffered_s']}s tail not yet a window), "
            f"windows_emitted={seg['windows_emitted']}, resets={seg['resets']}"
        )

    if index < 0 or index >= len(report["windows"]):
        console.print(
            f"[bold red]--index {index} is out of range (0..{len(report['windows']) - 1})[/bold red]"
        )
        raise typer.Exit(1)

    if not summary_only:
        detail = report["windows"][index]["detail"]
        table = Table(title=f"Window {index} detail")
        table.add_column("field")
        table.add_column("value", overflow="fold")
        table.add_row("window_id", report["windows"][index]["window_id"])
        table.add_row("actual frame count", str(report["windows"][index]["actual_frame_count"]))
        table.add_row("duration (actual)", f"{report['windows'][index]['duration_s_actual']} s")
        table.add_row("peak", str(detail["peak"]))
        table.add_row("rms", str(detail["rms"]))
        table.add_row("sha256", detail["sha256"])
        table.add_row("first 8 samples", str(detail["first_samples"]))
        table.add_row("last 8 samples", str(detail["last_samples"]))
        table.add_row(
            "ring cross-check",
            "[green]match — independent read agrees[/green]"
            if report["windows"][index]["ring_cross_check"] == "match"
            else "[red]MISMATCH — do not trust this window[/red]",
        )
        console.print(table)
        if "written_to" in report["windows"][index]:
            console.print(f"[dim]wrote {report['windows'][index]['written_to']}[/dim]")


# ----------------------------------------------------------------------
# models


@models_app.command("status")
def models_status() -> None:
    """Show which model assets are installed and verified."""
    table = Table(title="Model assets")
    table.add_column("file")
    table.add_column("licence")
    table.add_column("state")
    table.add_column("size")
    for entry in model_registry.status():
        if not entry.present:
            state = "[yellow]not installed[/yellow]"
        elif entry.ok:
            state = "[green]verified[/green]"
        else:
            state = "[red]checksum mismatch[/red]"
        table.add_row(
            entry.asset.filename,
            entry.asset.licence,
            state,
            f"{entry.size_bytes:,}" if entry.size_bytes else "—",
        )
    console.print(table)
    console.print(
        "[dim]Model assets are not bundled with this software (ADR-006). "
        "Their licences differ from the code's.[/dim]"
    )


@models_app.command("fetch")
def models_fetch(
    force: bool = typer.Option(False, help="Re-download even if the checksum already matches"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the licence confirmation"),
) -> None:
    """Download and verify model assets listed in models/manifest.tsv."""
    settings = get_settings()
    configure_logging(settings)
    assets = model_registry.load_manifest()

    console.print("[bold]The following third-party model assets will be downloaded:[/bold]")
    for asset in assets:
        console.print(f"  • {asset.filename}  [cyan]{asset.licence}[/cyan]")
        console.print(f"    {asset.url}")
    console.print(
        "\nThese licences are not the same as this software's. "
        "CC BY-NC-SA 4.0 in particular prohibits commercial use."
    )
    if not yes and not typer.confirm("Continue?", default=True):
        raise typer.Exit(1)

    try:
        results = model_registry.fetch(force=force)
    except RuntimeError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(1) from exc
    verified = sum(1 for entry in results if entry.ok)
    console.print(f"[green]{verified}/{len(results)} assets installed and verified.[/green]")


# ----------------------------------------------------------------------
# audiomoth


@moth_app.command("info")
def moth_info() -> None:
    """Read firmware identity over USB HID (needs the switch in USB/OFF)."""
    from .hardware.audiomoth_hid import AudioMothHid, AudioMothHidError, find_hidraw_devices

    paths = find_hidraw_devices()
    if not paths:
        console.print("[yellow]No AudioMoth HID interface found.[/yellow]")
        console.print(
            "This is expected while the side switch is in DEFAULT or CUSTOM — in those "
            "positions the device is a USB audio source with no HID interface.\n"
            "Move the switch to USB/OFF to configure it."
        )
        raise typer.Exit(1)
    try:
        with AudioMothHid(paths[0]) as device:
            identity = device.identify()
    except AudioMothHidError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(1) from exc

    table = Table(title="AudioMoth")
    table.add_column("property")
    table.add_column("value")
    table.add_row("hidraw", identity.hidraw_path)
    table.add_row("firmware", ".".join(str(p) for p in identity.firmware_version))
    table.add_row("description", identity.firmware_description)
    table.add_row("device uid", identity.device_uid)
    table.add_row("serial bootloader", "yes" if identity.supports_serial_bootloader else "no")
    console.print(table)
    if "USB-Microphone" not in identity.firmware_description:
        console.print(
            "[bold yellow]This is not USB-Microphone firmware. The device cannot act as "
            "a live microphone until AudioMoth-USB-Microphone is flashed.[/bold yellow]"
        )


# ----------------------------------------------------------------------
# history / coverage repair


@history_app.command("reconcile-streams")
def history_reconcile_streams(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Write the corrections. Without this flag nothing is changed.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when applying"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
    ratio_threshold: float = typer.Option(
        0.9,
        help=(
            "A stream is suspect if frame_count implies less than this fraction "
            "of its claimed wall-clock span."
        ),
    ),
) -> None:
    """Find and correct `audio_stream` rows whose claim disagrees with their own frames.

    ADR-024: a stream row can claim `start_utc`/`end_utc` far apart while its
    `frame_count` shows only a fraction of that span was actually captured -- the
    live database's worst case claimed 32 hours and delivered 2.79. Capture
    coverage is computed from these rows, so an uncorrected one makes the
    coverage bar lie.

    This command never writes anything unless `--apply` is given, and even then
    the original `end_utc`/`end_reason` are preserved under `detail.reconciliation`
    on the row rather than overwritten silently, so the correction is auditable.
    It only ever touches rows that already have an `end_utc` -- a row still open
    (`end_utc IS NULL`) may belong to a station running right now, and this
    command has no way to know; those are `Station._close_orphaned_streams`'s
    job, at that process's own next startup.

    Only run this against a database no station process is actively writing to
    the *same rows* for, i.e. not while capture is mid-stream on the row being
    corrected -- closed rows are safe at any time.
    """
    from .db.session import ensure_schema_at_head, init_engine, session_scope
    from .history import apply_stream_reconciliation, find_suspect_streams

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    ensure_schema_at_head()

    with session_scope() as session:
        suspects = find_suspect_streams(session, ratio_threshold=ratio_threshold)

        if json_out:
            emit_json([item.to_dict() for item in suspects])
        elif not suspects:
            console.print("[green]No suspect stream rows found.[/green]")
        else:
            table = Table(title=f"{len(suspects)} suspect stream row(s)")
            table.add_column("stream_id")
            table.add_column("source")
            table.add_column("claimed")
            table.add_column("frame-derived")
            table.add_column("proposed end_utc")
            for item in suspects:
                table.add_row(
                    str(item.stream_id),
                    item.source_kind,
                    f"{item.claimed_seconds / 3600:.2f}h",
                    f"{item.frame_derived_seconds / 3600:.2f}h",
                    item.proposed_end_utc.isoformat(),
                )
            console.print(table)
            for item in suspects:
                console.print(f"  • [dim]{item.stream_id}[/dim]: {item.reason}")

        if not suspects:
            return

        if not apply:
            notice(
                "\n[yellow]Dry run only -- nothing was changed.[/yellow] "
                "Re-run with --apply to correct these rows.",
                json_out=json_out,
            )
            return

        if not yes and not typer.confirm(
            f"Apply {len(suspects)} correction(s) to the database now?", default=False
        ):
            notice("[yellow]Aborted; nothing was changed.[/yellow]", json_out=json_out)
            raise typer.Exit(1)

        for item in suspects:
            apply_stream_reconciliation(session, item)
        console.print(f"[green]Corrected {len(suspects)} row(s).[/green]")


@detections_app.command("reconcile-plausibility")
def detections_reconcile_plausibility(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Write the flags. Without this flag nothing is changed.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when applying"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
    limit: int = typer.Option(
        5000, help="Maximum stored BirdNET detections to re-evaluate, most recent first"
    ),
) -> None:
    """Re-evaluate stored BirdNET detections against the current range model (ADR-032).

    Historical fix for the two defects documented in
    `docs/detectors/DETECTOR_STRATEGY.md`'s "Known limitation" section and
    `HANDOVER.md` section 6.3 item 0: a near-zero occurrence prior that used to be
    overruled by an uncalibrated score, and a missing prior that used to get the
    *easiest* confidence bar instead of the strictest one. `detectors/birdnet.py`
    now applies the fixed logic to every new detection; this command re-checks
    detections already in the database against the *current* range model,
    `birdnet_plausibility_floor` and band thresholds, and reports which ones would
    not be admitted today.

    This command never writes anything unless `--apply` is given, and even then it
    never deletes a row or overwrites its `native_result` -- the finding is added
    under a new `native_result.plausibility_review` key, preserving the original
    detector output verbatim, so the correction is auditable (the same shape as
    `oo history reconcile-streams`'s `detail.reconciliation`).

    Since ADR-044 this flag has teeth, and `--apply` takes effect immediately
    with no restart: a flagged row is kept and marked `withdrawn` by
    `/api/v1/detections`, dropped from `/api/v1/history`'s species list and
    `/api/v1/taxa/activity` (both of which report `excluded_withdrawn_count`),
    and shown by neither the MQTT publisher nor the ESP32 counter-top display. Read the
    dry-run output, and preferably `--json` it to a file, before applying.

    Requires station coordinates (`latitude`/`longitude`) and the BirdNET model
    assets (`oo models fetch`) to be present, since the range model itself has to
    be re-run to recompute an occurrence probability for each stored detection.
    """
    from . import models as model_registry
    from .db.session import ensure_schema_at_head, init_engine, session_scope
    from .detectors.base import DetectorUnavailable
    from .plausibility_repair import apply_plausibility_flag, find_implausible_detections

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    ensure_schema_at_head()

    if settings.latitude is None or settings.longitude is None:
        # stderr unconditionally: a failure message on stdout corrupts a --json
        # caller's document exactly as the dry-run notice did, and an error is
        # the case where a caller most needs the stream to still parse.
        console_err.print(
            "[red]No station coordinates configured -- cannot re-evaluate against the "
            "range model.[/red]"
        )
        raise typer.Exit(1)

    model_dir = settings.birdnet_model_dir or model_registry.DEFAULT_MODEL_DIR

    with session_scope() as session:
        try:
            findings = find_implausible_detections(
                session,
                model_dir=model_dir,
                latitude=settings.latitude,
                longitude=settings.longitude,
                plausibility_floor=settings.birdnet_plausibility_floor,
                limit=limit,
            )
        except DetectorUnavailable as exc:
            console_err.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        if json_out:
            emit_json([item.to_dict() for item in findings])
        elif not findings:
            console.print("[green]No implausible detections found.[/green]")
        else:
            table = Table(title=f"{len(findings)} implausible detection(s)")
            table.add_column("common_name")
            table.add_column("event_start_utc")
            table.add_column("score", justify="right")
            table.add_column("stored band")
            table.add_column("recomputed band")
            for item in findings:
                table.add_row(
                    item.common_name or "",
                    item.event_start_utc.isoformat(),
                    f"{item.score:.3f}",
                    item.stored_band or "",
                    item.recomputed_band,
                )
            console.print(table)
            for item in findings:
                console.print(f"  • [dim]{item.detection_id}[/dim]: {item.reason}")

        if not findings:
            return

        if not apply:
            notice(
                "\n[yellow]Dry run only -- nothing was changed.[/yellow] "
                "Re-run with --apply to flag these rows.",
                json_out=json_out,
            )
            return

        if not yes and not typer.confirm(
            f"Flag {len(findings)} detection(s) as implausible now?", default=False
        ):
            notice("[yellow]Aborted; nothing was changed.[/yellow]", json_out=json_out)
            raise typer.Exit(1)

        for item in findings:
            apply_plausibility_flag(session, item)
        console.print(
            f"[green]Flagged {len(findings)} row(s).[/green] Rows are kept, not deleted; "
            "the original claim is preserved under native_result, and the finding is "
            "recorded under native_result.plausibility_review. These rows are now "
            "marked withdrawn by the API and are no longer shown on MQTT or the "
            "counter-top display (ADR-044); no restart is needed."
        )


@detections_app.command("reconcile-taxonomy")
def detections_reconcile_taxonomy(
    apply: bool = typer.Option(
        False, "--apply", help="Write the corrections. Without this flag nothing is changed."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when applying"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
    limit: int = typer.Option(100000, help="Maximum stored BirdNET detections to examine"),
) -> None:
    """Stop stored sound categories claiming to be birds at species rank (ADR-049).

    Eleven of BirdNET GLOBAL 6K's output classes are sounds, not species --
    "Engine", "Human vocal", "Dog", "Siren" and seven more. Until ADR-049 the
    adapter stamped `rank="species"` and `taxonomic_group="bird"` onto all of
    them, and the normaliser then minted a `canonical_taxon_id` such as
    `sci:engine` from a scientific field that is not a binomial. New detections
    are written correctly now; this command corrects the ones already stored.

    It sets `rank` to NULL, `taxonomic_group` to `acoustic_event`, and clears
    `scientific_name` and `canonical_taxon_id`. It does **not** touch
    `common_name` -- "Engine" is an honest description of what was heard and is
    exactly the signal an operator wants -- it does not touch the score, the
    timestamps or the evidence, and it never deletes a row. The previous values
    are preserved verbatim under `native_result.taxonomy_review`, so what the
    system used to believe stays on the record and a second run is a no-op.

    Unlike `reconcile-plausibility`, this does not skip human-reviewed rows:
    the review workflow has no field in which a human could have endorsed a
    rank or a taxonomic group, so skipping would leave the false claim standing
    on precisely the rows somebody looked at. No `Review` row is touched.
    """
    from .db.session import ensure_schema_at_head, init_engine, session_scope
    from .taxonomy_repair import apply_taxonomy_correction, find_mislabelled_taxonomy

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    ensure_schema_at_head()

    with session_scope() as session:
        findings = find_mislabelled_taxonomy(session, limit=limit)

        if json_out:
            emit_json([item.to_dict() for item in findings])
        elif not findings:
            console.print("[green]No detections are recording a sound as a species.[/green]")
        else:
            table = Table(title=f"{len(findings)} non-taxonomic detection(s) stored as species")
            table.add_column("common_name")
            table.add_column("kind")
            table.add_column("event_start_utc")
            table.add_column("score", justify="right")
            table.add_column("stored rank")
            table.add_column("stored group")
            for item in findings[:200]:
                table.add_row(
                    item.common_name or "",
                    item.sound_kind,
                    item.event_start_utc.isoformat(),
                    f"{item.score:.3f}",
                    str(item.original_rank),
                    item.original_taxonomic_group,
                )
            console.print(table)
            if len(findings) > 200:
                console.print(f"  [dim]... and {len(findings) - 200} more[/dim]")

        if not findings:
            return

        if not apply:
            notice(
                "\n[yellow]Dry run only -- nothing was changed.[/yellow] "
                "Re-run with --apply to correct these rows.",
                json_out=json_out,
            )
            return

        if not yes and not typer.confirm(
            f"Correct the taxonomic fields on {len(findings)} detection(s) now?",
            default=False,
        ):
            notice("[yellow]Aborted; nothing was changed.[/yellow]", json_out=json_out)
            raise typer.Exit(1)

        for item in findings:
            apply_taxonomy_correction(session, item)
        console.print(
            f"[green]Corrected {len(findings)} row(s).[/green] No row was deleted and no "
            "common name was changed; the original rank, group, scientific name and "
            "taxon id are preserved under native_result.taxonomy_review. These rows no "
            "longer appear in the species tallies or in the taxon search."
        )


# ----------------------------------------------------------------------
# system / serve


@clips_app.command("purge-human-audio")
def clips_purge_human_audio(
    apply: bool = typer.Option(
        False, "--apply", help="Delete the files. Without this flag nothing is deleted."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when applying"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
) -> None:
    """Delete evidence clips of human speech, keeping the detection rows (ADR-049).

    The charter's privacy constraint is about people who never consented to a
    microphone in a garden. `clip_human_audio` (off by default) stops new clips
    of BirdNET's three human sound classes from being written at all; this
    command deals with whatever a station accumulated before that default
    existed. Measured on the live station on 2026-08-09: 24 "Human vocal"
    detections holding 48 assets and 125 MB.

    Detection rows are never touched: "somebody was talking in the garden at
    18:55" stays in the record, the recording of them talking does not. The
    `media_asset` rows survive too, marked `reclaimed_at` with reason
    `privacy_human_audio` -- the same shape the retention sweeper uses when a
    clip ages out (ADR-026), so `/api/v1/media/{id}` keeps answering 410 rather
    than 500.

    Dry-run by default. Selection is by detector label, so it works on rows
    written before ADR-049 as well as after.
    """
    from .db.session import ensure_schema_at_head, init_engine, session_scope
    from .privacy import purge_human_audio

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    ensure_schema_at_head()

    report = purge_human_audio(session_scope, dry_run=True)

    if json_out and not apply:
        emit_json(report.to_dict())
    elif not report.items:
        console.print("[green]No human-audio evidence clips are stored.[/green]")
    else:
        table = Table(
            title=(
                f"{len(report.items)} human-audio clip(s) across "
                f"{report.detections} detection(s)"
            )
        )
        table.add_column("event_start_utc")
        table.add_column("common_name")
        table.add_column("kind")
        table.add_column("MB", justify="right")
        table.add_column("on disk")
        for item in report.items[:200]:
            table.add_row(
                item.event_start_utc.isoformat(),
                item.common_name or "",
                item.kind,
                f"{item.bytes / 1024**2:.1f}",
                "yes" if item.existed_on_disk else "missing",
            )
        console.print(table)
        total_mb = sum(item.bytes for item in report.items) / 1024**2
        console.print(f"  [dim]{total_mb:.1f} MB total[/dim]")

    if not report.items:
        return

    if not apply:
        notice(
            "\n[yellow]Dry run only -- nothing was deleted.[/yellow] "
            "Re-run with --apply to delete these files. Detection rows are kept "
            "either way.",
            json_out=json_out,
        )
        return

    if not yes and not typer.confirm(
        f"Permanently delete {len(report.items)} clip file(s) of human sound?",
        default=False,
    ):
        notice("[yellow]Aborted; nothing was deleted.[/yellow]", json_out=json_out)
        raise typer.Exit(1)

    applied = purge_human_audio(session_scope, dry_run=False)
    if json_out:
        emit_json(applied.to_dict())
    else:
        console.print(
            f"[green]Deleted {applied.deleted} clip(s), reclaiming "
            f"{applied.bytes_reclaimed / 1024**2:.1f} MB.[/green] "
            f"{applied.already_missing} were already gone from disk; "
            f"{applied.failed} could not be removed. Detection rows are unchanged."
        )


@clips_app.command("reconcile-missing")
def clips_reconcile_missing(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Mark the rows reclaimed. Without this flag nothing is changed.",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when applying"
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable output"),
    limit: int = typer.Option(
        200000, help="Maximum live media_asset rows to examine, oldest first"
    ),
) -> None:
    """Reconcile `media_asset` rows that claim a clip the disk does not have (ADR-057).

    A row with `reclaimed_at` unset is the database asserting that evidence
    exists. Measured on the live station 2026-08-10: 8,067 of 48,941 such rows
    (16.5%, 20.59 GB) named files that were not there, every one created before
    2026-08-05T18:44:35Z. `ClipManager.enforce_retention` -- the pre-ADR-026
    filesystem sweep -- had unlinked them oldest-first to stay inside a 20 GB
    budget and never touched the database; its own logs account for the whole
    loss (8,166 files, 20.84 GB, between 2026-08-05 and 2026-08-08). None of
    those clips survive anywhere, including `data/clips.sdcard-backup`, whose
    oldest file postdates the boundary by nine minutes.

    The consequences are not cosmetic: the storage panel over-reports clips and
    bytes, retention believes it can reclaim space that does not exist, the API
    offers a play button that 404s or 410s, and the refinement runner (ADR-045)
    picks candidates oldest-first from exactly this population and reports every
    one `unavailable`.

    **What --apply changes.** For each row: `reclaimed_at` is set,
    `reclaim_reason` is set to `missing`, and what the row used to claim is
    preserved verbatim under `detail.missing_reconciliation`. `missing` is not
    one of the retention tiers on purpose -- a clip aged out by policy and a
    clip that vanished are different facts, and only one of them is something
    the system chose. No file is deleted (there is nothing to delete), no
    `media_asset` row is deleted, and no `detection` row is touched: "this
    happened, and we no longer have the audio" stays in the record.

    Dry-run by default. Running it twice is a no-op.
    """
    from .db.session import ensure_schema_at_head, init_engine, session_scope
    from .media_repair import apply_missing_reconciliation, find_missing_assets

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    ensure_schema_at_head()

    with session_scope() as session:
        report = find_missing_assets(session, limit=limit)
        findings = report.findings

        if json_out:
            emit_json(report.to_dict())
        elif not findings:
            console.print(
                f"[green]All {report.scanned} live media_asset row(s) name a file "
                "that is on disk.[/green]"
            )
        else:
            payload = report.to_dict()
            table = Table(
                title=(
                    f"{report.missing} of {report.scanned} live row(s) claim a missing "
                    f"file ({report.missing_bytes / 1024**3:.2f} GB)"
                )
            )
            table.add_column("created_at")
            table.add_column("kind")
            table.add_column("common_name")
            table.add_column("MB", justify="right")
            table.add_column("held", justify="center")
            for item in findings[:200]:
                table.add_row(
                    item.created_at.isoformat(),
                    item.kind,
                    item.common_name or "",
                    f"{item.byte_length / 1024**2:.1f}",
                    "yes" if item.held_for_review else "",
                )
            console.print(table)
            if len(findings) > 200:
                console.print(f"  [dim]... and {len(findings) - 200} more[/dim]")
            console.print(f"  by kind: {payload['by_kind']}")
            console.print(f"  by created day: {payload['by_created_day']}")
            held = int(payload["held_for_review"])  # type: ignore[call-overload]
            if held:
                console.print(
                    f"  [yellow]{held} of these are held for human review — the hold "
                    "cannot be satisfied, the audio is gone[/yellow]"
                )

        if not findings:
            return

        if not apply:
            notice(
                "\n[yellow]Dry run only -- nothing was changed.[/yellow] "
                "Re-run with --apply to mark these rows reclaimed with reason "
                "'missing'. No file is deleted either way.",
                json_out=json_out,
            )
            return

        if not yes and not typer.confirm(
            f"Mark {len(findings)} row(s) as missing evidence now?", default=False
        ):
            notice("[yellow]Aborted; nothing was changed.[/yellow]", json_out=json_out)
            raise typer.Exit(1)

        for item in findings:
            apply_missing_reconciliation(session, item)
        console.print(
            f"[green]Reconciled {len(findings)} row(s), "
            f"{report.missing_bytes / 1024**3:.2f} GB of phantom evidence.[/green] "
            "Rows are kept, not deleted; the original claim is preserved under "
            "detail.missing_reconciliation and the reason is 'missing', not a "
            "retention tier. Storage figures and the retention budget are correct "
            "from the next read; no restart is needed."
        )


@clips_app.command("retention")
def clips_retention(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be deleted; delete nothing"
    ),
    limit: int = typer.Option(
        50, help="Maximum decisions to print (the sweep itself is separately batch-bounded)"
    ),
) -> None:
    """Run one tiered retention sweep and report what it did (or would do).

    This deletes clip *files* irreversibly when not run with ``--dry-run``.
    Detection metadata is never touched by this command, whichever mode it
    runs in (ADR-026). Always run ``--dry-run`` first against a station you
    care about.
    """
    from .db.session import ensure_schema_at_head, init_engine, session_scope
    from .retention import RetentionSweeper

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    ensure_schema_at_head()

    sweeper = RetentionSweeper(
        clip_dir=settings.clip_dir,
        session_factory=session_scope,
        native_days=settings.retention_native_days,
        audible_only_days=settings.retention_audible_only_days,
        exemplar_only_days=settings.retention_exemplar_only_days,
        watermark_ratio=settings.retention_watermark_ratio,
        batch_size=settings.retention_batch_size,
        batch_budget_s=settings.retention_batch_budget_s,
    )
    report = sweeper.sweep(dry_run=dry_run)

    if dry_run:
        console.print("[bold yellow]DRY RUN[/bold yellow] — nothing was deleted\n")

    table = Table(title="Retention sweep")
    table.add_column("tier")
    table.add_column("files", justify="right")
    table.add_column("bytes", justify="right")
    for tier in ("native", "exemplar_only", "expired", "watermark"):
        table.add_row(
            tier,
            str(report.tier_counts.get(tier, 0)),
            f"{report.tier_bytes.get(tier, 0):,}",
        )
    console.print(table)
    console.print(
        f"exemplar detections (first/best-of-species, exempt through the "
        f"{settings.retention_audible_only_days}-{settings.retention_exemplar_only_days}d tier): "
        f"{report.exemplar_detections}"
    )
    if report.already_missing:
        console.print(
            f"[yellow]{report.already_missing} asset(s) were already gone from disk "
            "(row present, file missing) — marked reclaimed without further I/O[/yellow]"
        )
    console.print(
        f"disk used: {report.disk_used_ratio_before:.1%}"
        f" -> {report.disk_used_ratio_after:.1%}"
        if report.disk_used_ratio_before is not None and report.disk_used_ratio_after is not None
        else "disk used: unknown"
    )
    console.print(f"duration: {report.duration_s:.3f}s  complete: {report.complete}")

    if report.decisions:
        detail = Table(title=f"Decisions (showing up to {limit})")
        detail.add_column("tier")
        detail.add_column("kind")
        detail.add_column("bytes", justify="right")
        detail.add_column("on disk")
        detail.add_column("reason")
        for decision in report.decisions[:limit]:
            detail.add_row(
                decision.tier,
                decision.kind,
                str(decision.bytes),
                "yes" if decision.existed_on_disk else "no",
                decision.reason,
            )
        console.print(detail)


# ----------------------------------------------------------------------
# refinement (charter item 5, ADR-045)


def _build_refiner(settings: Settings) -> Any:
    from .refinement.batdetect2 import BatDetect2Refiner

    # One entry today. Kept as an explicit mapping rather than an import-by-name
    # so an unknown value fails at startup with a readable error rather than at
    # 01:00 with an ImportError in the journal.
    if settings.refinement_refiner == "batdetect2-cascade":
        return BatDetect2Refiner(
            trim_s=settings.refinement_trim_s,
            min_det_prob=settings.refinement_min_det_prob,
            threads=settings.refinement_threads,
        )
    raise typer.BadParameter(f"unknown refiner {settings.refinement_refiner!r}")


@refine_app.command("run")
def refine_run(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Classify and report; write no refinement rows"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Run outside the configured quiet window. Do not use this on a station "
        "that is capturing bats right now — that is the whole point of the window.",
    ),
    limit: int | None = typer.Option(None, help="Override the per-run item budget"),
    json_out: bool = typer.Option(False, "--json", help="Print the report as JSON"),
) -> None:
    """Run one refinement pass over stored evidence.

    **This is not run by the station process and must never be.** It is started
    by ``open-observatory-refine.timer`` in its own systemd unit, fenced to
    ``AllowedCPUs=2-3``/``Nice=19``/``MemoryMax=1G``, in the measured quiet
    window. A 0.30 s retention sweep inside the capture process cost ~1.9 false
    capture gaps a minute (ADR-033); a BatDetect2 pass is 2.1 s of inference.

    What this command can and cannot do to the record: the shipped refiner has
    ``propose`` authority only. It never edits a detection's species, score or
    ``native_result`` — it writes an append-only ``refinement`` row carrying the
    original claim verbatim plus what it suggests instead, and stamps the
    detection with the fact that refinement ran, at what version, with what
    outcome. Accepting a proposal is a human act via the review workflow. See
    ADR-045 for why that ceiling is where it is.
    """
    from .db.session import create_all, init_engine, session_scope
    from .refinement.runner import RefinementRunner, write_health_event

    settings = get_settings()
    configure_logging(settings)
    if not settings.refinement_enabled:
        console.print("[yellow]refinement_enabled is false; nothing to do.[/yellow]")
        raise typer.Exit(0)

    init_engine(settings)
    create_all()

    runner = RefinementRunner(
        _build_refiner(settings),
        session_factory=session_scope,
        max_items=limit if limit is not None else settings.refinement_max_items,
        max_seconds=settings.refinement_max_seconds,
        quiet_window_start_hour=settings.refinement_window_start_hour_utc,
        quiet_window_end_hour=settings.refinement_window_end_hour_utc,
    )
    report = runner.run(dry_run=dry_run, force=force)

    if not dry_run:
        with session_scope() as session:
            write_health_event(session, report)

    if json_out:
        emit_json({**report.to_dict(), "proposals": report.proposals})
        return

    if report.skipped_reason:
        console.print(f"[yellow]Skipped:[/yellow] {report.skipped_reason}")
        return

    if dry_run:
        console.print("[bold yellow]DRY RUN[/bold yellow] — no refinement rows were written\n")

    table = Table(title=f"Refinement pass — {report.refiner_version_label}")
    table.add_column("outcome")
    table.add_column("count", justify="right")
    for outcome, count in sorted(report.outcomes.items()):
        table.add_row(outcome, str(count))
    console.print(table)
    console.print(
        f"considered {report.candidates_considered}, examined {report.examined} "
        f"in {report.duration_s:.1f}s "
        f"({report.inference_s:.1f}s classifying {report.audio_s:.1f}s of audio"
        + (f", {report.realtime_factor}x realtime" if report.realtime_factor else "")
        + f")  complete: {report.complete}"
    )
    if report.proposals:
        detail = Table(title=f"Proposals for human review ({len(report.proposals)})")
        detail.add_column("when (UTC)")
        detail.add_column("our peak Hz", justify="right")
        detail.add_column("proposed species")
        detail.add_column("det_prob", justify="right")
        for item in report.proposals[:50]:
            peak = item.get("our_peak_frequency_hz")
            detail.add_row(
                str(item["event_start_utc"])[11:19],
                f"{peak:,.0f}" if peak else "-",
                str(item.get("proposed_scientific_name") or "-"),
                f"{item['proposed_score']:.2f}" if item.get("proposed_score") else "-",
            )
        console.print(detail)
        console.print(
            "[yellow]These are proposals, not identifications.[/yellow] Nothing above has "
            "changed any record. Compare each species against this station's own measured "
            "peak frequency before accepting one — a confident BatDetect2 answer has already "
            "contradicted that measurement once (HANDOVER.md §6.3 item 6)."
        )


@refine_app.command("status")
def refine_status(
    limit: int = typer.Option(20, help="Unresolved proposals to list"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """What the refiner has done, and what is waiting for a human ear.

    Also answers the question the charter's retention safeguard depends on:
    *how many events has the refiner never examined?* A pure age rule would
    delete those having never looked at them once.
    """
    from sqlalchemy import func, select

    from .db import models as orm
    from .db.session import create_all, init_engine, session_scope
    from .refinement.contracts import EXAMINED_OUTCOMES, RefinementOutcome

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    create_all()

    with session_scope() as session:
        by_outcome: dict[str | None, int] = {
            row[0]: row[1]
            for row in session.execute(
                select(orm.Detection.refinement_outcome, func.count())
                .group_by(orm.Detection.refinement_outcome)
                .where(orm.Detection.taxonomic_group == "bat")
            ).all()
        }
        never_examined = sum(
            count
            for outcome, count in by_outcome.items()
            if outcome is None or outcome not in {str(item) for item in EXAMINED_OUTCOMES}
        )
        last_run = session.execute(
            select(orm.HealthEvent)
            .where(orm.HealthEvent.service == "refinement")
            .order_by(orm.HealthEvent.start_utc.desc())
            .limit(1)
        ).scalar_one_or_none()
        pending = (
            session.execute(
                select(orm.Refinement)
                .where(orm.Refinement.outcome == str(RefinementOutcome.PROPOSED))
                .where(orm.Refinement.resolved_at.is_(None))
                .order_by(orm.Refinement.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

        counts: dict[str, int] = {
            (key or "never_refined"): value for key, value in by_outcome.items()
        }
        last_run_at = last_run.start_utc.isoformat() if last_run else None
        proposals: list[dict[str, Any]] = [
            {
                "detection_id": str(row.detection_id),
                "created_at": row.created_at.isoformat(),
                "original": row.original_common_name,
                "proposed": row.proposed_scientific_name,
                "score": row.proposed_score,
                "reason": row.reason,
            }
            for row in pending
        ]
        payload: dict[str, Any] = {
            "bat_detections_by_refinement_outcome": counts,
            "bat_detections_never_examined": never_examined,
            "last_run": last_run.detail if last_run else None,
            "last_run_at": last_run_at,
            "unresolved_proposals": proposals,
        }

    if json_out:
        emit_json(payload)
        return

    console.print(f"last refinement run: {last_run_at or '[red]never[/red]'}")
    table = Table(title="Bat detections by refinement outcome")
    table.add_column("outcome")
    table.add_column("detections", justify="right")
    for key, value in sorted(counts.items()):
        table.add_row(str(key), str(value))
    console.print(table)
    console.print(
        f"[bold]{never_examined}[/bold] bat detection(s) have never been examined by a "
        "refiner. Retention currently deletes on age alone and would remove their "
        "evidence without ever having looked at it — see ADR-045."
    )
    if proposals:
        detail = Table(title="Unresolved proposals (awaiting a human ear)")
        detail.add_column("when")
        detail.add_column("original")
        detail.add_column("proposed")
        detail.add_column("det_prob", justify="right")
        for row in proposals:
            detail.add_row(
                str(row["created_at"])[:19],
                str(row["original"] or "-"),
                str(row["proposed"] or "-"),
                f"{row['score']:.2f}" if row["score"] else "-",
            )
        console.print(detail)


@app.command("system-report")
def system_report_command(json_out: bool = typer.Option(False, "--json")) -> None:
    """Host facts worth recording alongside a diagnostic."""
    from .audio.probe import system_report

    report = system_report()
    if json_out:
        emit_json(report)
        return
    for key, value in report.items():
        console.print(f"[bold]{key}[/bold]: {value}")


@app.command("serve")
def serve(
    host: str | None = typer.Option(None, help="Bind address"),
    port: int | None = typer.Option(None, help="Bind port"),
    source: str | None = typer.Option(
        None, help="Override the capture source: auto, alsa, replay or synthetic"
    ),
    reload: bool = typer.Option(False, help="Reload on source changes (development only)"),
) -> None:
    """Run the station: capture, detectors, API and the debug UI."""
    import uvicorn

    settings = get_settings()
    if source:
        settings = settings.model_copy(update={"source": source})
        set_settings(settings)
    configure_logging(settings)

    from .api.app import create_app

    console.print(
        f"[bold green]Open Observatory[/bold green] on "
        f"http://{host or settings.bind_host}:{port or settings.bind_port}  "
        f"(source={settings.source})"
    )
    uvicorn.run(
        create_app(settings) if not reload else "open_observatory.api.app:create_app",
        factory=reload,
        host=host or settings.bind_host,
        port=port or settings.bind_port,
        log_level=settings.log_level.lower(),
        ws_max_size=8 * 1024 * 1024,
    )


@app.command("config")
def show_config() -> None:
    """Print effective configuration, with the resolved database DSN."""
    settings = get_settings()
    payload = settings.model_dump(mode="json")
    payload["resolved_database_dsn"] = settings.resolved_database_dsn
    emit_json(payload)


if __name__ == "__main__":
    app()
