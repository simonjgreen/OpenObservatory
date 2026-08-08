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
app.add_typer(audio_app, name="audio")
app.add_typer(models_app, name="models")
app.add_typer(moth_app, name="audiomoth")
app.add_typer(history_app, name="history")

console = Console()


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
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
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
        console.print_json(json.dumps(report))
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
    from .db.session import create_all, init_engine, session_scope
    from .history import apply_stream_reconciliation, find_suspect_streams

    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    create_all()

    with session_scope() as session:
        suspects = find_suspect_streams(session, ratio_threshold=ratio_threshold)

        if json_out:
            console.print_json(json.dumps([item.to_dict() for item in suspects]))
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
            console.print(
                "\n[yellow]Dry run only -- nothing was changed.[/yellow] "
                "Re-run with --apply to correct these rows."
            )
            return

        if not yes and not typer.confirm(
            f"Apply {len(suspects)} correction(s) to the database now?", default=False
        ):
            console.print("[yellow]Aborted; nothing was changed.[/yellow]")
            raise typer.Exit(1)

        for item in suspects:
            apply_stream_reconciliation(session, item)
        console.print(f"[green]Corrected {len(suspects)} row(s).[/green]")


# ----------------------------------------------------------------------
# system / serve


@app.command("system-report")
def system_report_command(json_out: bool = typer.Option(False, "--json")) -> None:
    """Host facts worth recording alongside a diagnostic."""
    from .audio.probe import system_report

    report = system_report()
    if json_out:
        console.print_json(json.dumps(report))
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
    console.print_json(json.dumps(payload, default=str))


if __name__ == "__main__":
    app()
