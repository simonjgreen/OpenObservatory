"""FastAPI control plane and the live WebSocket the debug UI runs on.

Three live channels, deliberately separate:

``/api/v1/live``
    Spectrogram columns (binary), telemetry and pipeline events (JSON). Every
    viewer sees identical data because the server computes it once.

``/api/v1/live/audio``
    Raw int16 PCM for the "GO LIVE" listen button over a WebSocket, decoded by
    an AudioWorklet on the client. Kept apart so that opening it costs nothing
    until someone actually presses the button, and so a listener that falls
    behind loses audio without disturbing the visual feed.

``/api/v1/live/audio.wav``
    The same audio, as a chunked WAV stream for a plain ``<audio>`` element.
    This is the fallback for browsers where Web Audio produces no output at
    all — observed on a laptop with an insecure-context page (so
    ``AudioContext``/``AudioWorklet`` cannot be used properly) and a 44.1 kHz
    hardware default: an oscillator through ``context.destination`` was
    silent, but a WAV through a plain media element was audible on the same
    machine. See ``docs/architecture/GAP_REPORT.md`` — this was the original
    proposal before the WebSocket path replaced it for latency; latency does
    not matter if nothing plays.

``/api/v1/live/tune`` (POST)
    Retunes the shared ultrasonic heterodyne oscillator without touching the
    audio.wav stream -- the control path the WAV channel is otherwise missing,
    since it has no socket to carry a `tune` frame. See the docstring on
    ``post_live_tune`` below.

Default binding is LAN-only and anonymous read is enabled for this debug slice —
recorded honestly in ``docs/operations`` rather than implied to be secure. The
authentication foundation is Milestone 4 work.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import dataclasses
import io
import os
import struct
import time
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import history as history_queries
from .. import models as model_registry
from ..audio.probe import enumerate_capture_devices, probe_supported_rates, system_report
from ..config import Settings, get_settings
from ..db import models as orm
from ..db.session import create_all, get_session, init_engine
from ..display import detection_flags, display_title
from ..events import EventType
from ..station import Station
from .metrics import PrometheusExporter

log = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"


class ReviewIn(BaseModel):
    """Body for `POST /api/v1/detections/{id}/review` — the minimal review
    workflow (confirm/reject a detection, optionally with a note)."""

    status: str = Field(pattern="^(confirmed|rejected)$")
    note: str = Field(default="", max_length=2000)


class LiveClient:
    """One connected browser, with a single task owning the socket.

    Every frame — control JSON and binary spectrogram alike — is queued here and
    written by one writer. That is not tidiness, it is a correctness requirement: a
    Starlette WebSocket cannot take concurrent sends, and the previous design had
    two writers (a task per broadcast frame, plus the event pump writing JSON).
    On loopback the sends completed too fast to ever overlap, so it looked perfect;
    over Wi-Fi they did overlap, the send raised, and the socket was dropped from
    the fan-out — leaving JSON flowing while the spectrogram silently died after a
    frame or two. That is what "pausing and stuttering" was.
    """

    def __init__(self, socket: WebSocket, *, maxsize: int = 96) -> None:
        self.socket = socket
        self._pending: deque[tuple[bool, Any]] = deque()
        self._maxsize = maxsize
        self._wake = asyncio.Event()
        self.closed = False
        self.sent = 0
        self.dropped = 0

    def offer(self, payload: Any, *, binary: bool) -> None:
        """Queue a frame. Never blocks, never raises, never awaits."""
        if self.closed:
            return
        if len(self._pending) >= self._maxsize:
            # Shed the oldest *binary* frame: a viewer who cannot keep up should
            # lose spectrogram history, not the status and detection frames that
            # tell them what is going on.
            for index, (is_binary, _) in enumerate(self._pending):
                if is_binary:
                    del self._pending[index]
                    self.dropped += 1
                    break
            else:
                self.dropped += 1
                return
        self._pending.append((binary, payload))
        self._wake.set()

    async def run(self) -> None:
        """The only place this socket is ever written."""
        while not self.closed:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            is_binary, payload = self._pending.popleft()
            if is_binary:
                await self.socket.send_bytes(payload)
            else:
                await self.socket.send_json(payload)
            self.sent += 1

    @property
    def queue_depth(self) -> int:
        return len(self._pending)

    def close(self) -> None:
        self.closed = True
        self._pending.clear()
        self._wake.set()


class LiveHub:
    """Fan-out of live frames to connected browsers.

    Snapshot-on-connect matters here: a viewer opening the page mid-flight would
    otherwise stare at an empty canvas for a minute, which looks exactly like a
    broken pipeline.
    """

    def __init__(self) -> None:
        self._clients: set[LiveClient] = set()
        self.frames_sent = 0
        self.dropped = 0

    def add(self, client: LiveClient) -> None:
        self._clients.add(client)

    def discard(self, client: LiveClient) -> None:
        self._clients.discard(client)
        client.close()

    @property
    def count(self) -> int:
        return len(self._clients)

    def broadcast_binary(self, payload: bytes) -> None:
        """Hand a frame to every client. Synchronous, so the capture path is not
        charged for a task creation per frame per viewer."""
        for client in self._clients:
            client.offer(payload, binary=True)
        self.frames_sent += len(self._clients)

    def broadcast_json(self, payload: dict[str, Any]) -> None:
        for client in self._clients:
            client.offer(payload, binary=False)

    def stats(self) -> dict[str, Any]:
        return {
            "sockets": len(self._clients),
            "frames_sent": self.frames_sent,
            "queued": [c.queue_depth for c in self._clients],
            "dropped": sum(c.dropped for c in self._clients),
        }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_directories()
    init_engine(settings)
    create_all()

    station = Station(settings)
    hub = LiveHub()
    exporter = PrometheusExporter(station)
    station.set_spectrogram_sink(lambda columns: hub.broadcast_binary(columns.to_binary()))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await station.start()
        try:
            yield
        finally:
            await station.stop()

    app = FastAPI(
        title="Open Observatory",
        version="0.3.0",
        description=(
            "Local-first passive acoustic observatory. This build exposes the "
            "Milestone 0-3 pipeline and its real-time debug surface."
        ),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.station = station
    app.state.hub = hub

    # -- station and health --------------------------------------------

    @app.get(f"{API_PREFIX}/station")
    def get_station() -> dict[str, Any]:
        return station.status_snapshot()

    @app.get(f"{API_PREFIX}/health")
    def get_health() -> JSONResponse:
        snapshot = station.status_snapshot()
        capture = snapshot["capture"]
        problems: list[str] = []
        if capture["state"] != "capturing":
            problems.append(f"capture state is {capture['state']}: {capture['detail']}")
        if not capture["is_live_hardware"]:
            problems.append("capturing from a synthetic/replay source, not the microphone")
        if capture["block_age_s"] is not None and capture["block_age_s"] > 2.0:
            problems.append(f"no audio block for {capture['block_age_s']}s")
        for detector in snapshot["detectors"]:
            if detector["state"] in ("error", "degraded"):
                problems.append(f"detector {detector['plugin_id']}: {detector['state']}")
        if settings.clips_require_mount and not os.path.ismount(settings.clip_dir):
            problems.append(
                f"clip storage {settings.clip_dir} is not a mount point: evidence would "
                "be written to the system disk, which competes with capture for I/O"
            )
        status = "ok" if not problems else ("degraded" if capture["state"] == "capturing" else "critical")
        return JSONResponse(
            {
                "status": status,
                "problems": problems,
                "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "capture": capture,
            },
            status_code=200 if status != "critical" else 503,
        )

    @app.get(f"{API_PREFIX}/system")
    def get_system() -> dict[str, Any]:
        return {"host": system_report(), "process": exporter.process_stats()}

    @app.get("/metrics", response_class=PlainTextResponse)
    def get_metrics() -> Response:
        if not settings.metrics_enabled:
            raise HTTPException(status_code=404, detail="metrics disabled")
        body, content_type = exporter.render()
        return Response(content=body, media_type=content_type)

    # -- audio devices --------------------------------------------------

    @app.get(f"{API_PREFIX}/audio/devices")
    def get_audio_devices() -> dict[str, Any]:
        devices = [device.to_dict() for device in enumerate_capture_devices()]
        return {
            "devices": devices,
            "selected_device_key": settings.audio_device,
            "active": {
                "device_key": station.stream.device_key if station.stream else None,
                "sample_rate": station.stream.fmt.sample_rate if station.stream else None,
                "source_kind": str(station.stream.source_kind) if station.stream else None,
            },
        }

    @app.post(f"{API_PREFIX}/audio/probe")
    def post_audio_probe(device_key: str | None = None) -> dict[str, Any]:
        """Probe rates by opening the hardware. Only safe while not capturing it."""
        from ..audio.probe import find_device

        device = find_device(device_key or settings.audio_device)
        if device is None:
            raise HTTPException(status_code=404, detail="no matching capture device")
        active = station.stream is not None and station.stream.device_key == device.stable_device_key
        rates: dict[int, str] = {}
        if not active:
            rates = probe_supported_rates(device, settings.preferred_sample_rates)
        return {
            "device": device.to_dict(),
            "probe_skipped_because_in_use": active,
            "rate_support": {str(rate): state for rate, state in rates.items()},
        }

    @app.get(f"{API_PREFIX}/audiomoth")
    def get_audiomoth() -> dict[str, Any]:
        """AudioMoth firmware identity over USB HID, when reachable.

        Only answers while the device is in USB/OFF (configuration) mode; in
        DEFAULT it is a USB audio device with no HID interface, which is the
        normal running state and not an error.
        """
        from ..hardware.audiomoth_hid import AudioMothHid, AudioMothHidError, find_hidraw_devices

        paths = find_hidraw_devices()
        if not paths:
            return {
                "hid_available": False,
                "note": (
                    "No AudioMoth HID interface. Expected while the side switch is in "
                    "DEFAULT or CUSTOM, which is the streaming position."
                ),
            }
        try:
            with AudioMothHid(paths[0]) as device:
                identity = device.identify()
        except AudioMothHidError as exc:
            return {"hid_available": True, "error": str(exc)}
        return {
            "hid_available": True,
            "hidraw_path": identity.hidraw_path,
            "firmware_version": ".".join(str(part) for part in identity.firmware_version),
            "firmware_description": identity.firmware_description,
            "device_uid": identity.device_uid,
            "supports_serial_bootloader": identity.supports_serial_bootloader,
        }

    # -- streams and gaps ----------------------------------------------

    @app.get(f"{API_PREFIX}/streams")
    def list_streams(
        limit: int = Query(20, ge=1, le=200), session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        rows = session.scalars(
            select(orm.AudioStream).order_by(orm.AudioStream.start_utc.desc()).limit(limit)
        ).all()
        return {
            "streams": [
                {
                    "id": str(row.id),
                    "source_kind": row.source_kind,
                    "start_utc": _iso(row.start_utc),
                    "end_utc": _iso(row.end_utc),
                    "sample_rate": row.sample_rate,
                    "sample_format": row.sample_format,
                    "channels": row.channels,
                    "frame_count": row.frame_count,
                    "discontinuity_count": row.discontinuity_count,
                    "end_reason": row.end_reason,
                    "detail": row.detail,
                }
                for row in rows
            ]
        }

    @app.get(f"{API_PREFIX}/gaps")
    def list_gaps(
        limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        rows = session.scalars(
            select(orm.CaptureGap).order_by(orm.CaptureGap.start_utc.desc()).limit(limit)
        ).all()
        return {
            "gaps": [
                {
                    "id": str(row.id),
                    "stream_id": str(row.stream_id),
                    "start_utc": _iso(row.start_utc),
                    "estimated_missing_frames": row.estimated_missing_frames,
                    "reason": row.reason,
                    "detail": row.detail,
                }
                for row in rows
            ]
        }

    # -- detectors and licences ----------------------------------------

    @app.get(f"{API_PREFIX}/detectors")
    async def list_detectors() -> dict[str, Any]:
        health = []
        for worker in station.workers:
            snapshot = worker.snapshot()
            # DetectorHealth is a slotted frozen dataclass, so it has no __dict__.
            snapshot["health"] = dataclasses.asdict(await worker.health())
            health.append(snapshot)
        return {"detectors": health}

    @app.get(f"{API_PREFIX}/models")
    def list_models() -> dict[str, Any]:
        return {
            "model_dir": str(model_registry.DEFAULT_MODEL_DIR),
            "assets": model_registry.licence_summary(),
            "note": (
                "Model assets are not bundled with this software (ADR-006). Their "
                "licences differ from the code's and are listed per asset."
            ),
        }

    # -- detections -----------------------------------------------------

    @app.get(f"{API_PREFIX}/detections")
    def list_detections(
        limit: int = Query(100, ge=1, le=500),
        since: datetime | None = None,
        until: datetime | None = None,
        #: A named window such as `last-night`, resolved in the station's timezone.
        #: Ignored when `since` is given explicitly.
        window: str | None = None,
        group: str | None = None,
        plugin_id: str | None = None,
        identified_only: bool = False,
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        #: A detection persists honestly whatever stream produced it (station.py's
        #: synthetic fallback is itself correct behaviour), but a browsing view must
        #: not present a test scene as an observed bird. Off by default; explicit
        #: opt-in keeps the data reachable for diagnostics.
        include_synthetic: bool = False,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        resolved: history_queries.Range | None = None
        if since is None and window:
            resolved = history_queries.resolve_named_range(window, settings.timezone)
            since, until = resolved.start, resolved.end

        query = select(orm.Detection, orm.AudioStream.source_kind).outerjoin(
            orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id
        )
        if since is not None:
            query = query.where(orm.Detection.event_start_utc >= since)
        if until is not None:
            query = query.where(orm.Detection.event_start_utc < until)
        if identified_only:
            query = query.where(
                orm.Detection.taxonomic_group.in_(history_queries.IDENTIFIED_GROUPS)
            )
        if group:
            query = query.where(orm.Detection.taxonomic_group == group)
        if min_score > 0:
            query = query.where(orm.Detection.score >= min_score)
        if plugin_id:
            query = query.join(orm.Detector).where(orm.Detector.plugin_id == plugin_id)

        excluded_count = 0
        if not include_synthetic:
            # Same predicates, source condition flipped, counted before the limit is
            # applied — otherwise the count would describe the page, not the window.
            excluded_count = (
                session.execute(
                    select(func.count()).select_from(
                        query.where(
                            history_queries.is_not_live(orm.AudioStream.source_kind)
                        ).subquery()
                    )
                ).scalar_one()
                or 0
            )
            query = query.where(history_queries.is_live(orm.AudioStream.source_kind))

        query = query.order_by(orm.Detection.event_start_utc.desc()).limit(limit)
        rows = session.execute(query).all()
        return {
            "detections": [
                _detection_payload(detection, source_kind=source_kind)
                for detection, source_kind in rows
            ],
            "range": resolved.to_dict() if resolved else None,
            # So the client can tell "that is all of them" from "that is the first
            # page", which changes what an apparently quiet night means.
            "truncated": len(rows) >= limit,
            "include_synthetic": include_synthetic,
            "excluded_synthetic_count": excluded_count,
        }

    @app.get(f"{API_PREFIX}/detections/export")
    def export_detections(
        format: str = Query("csv", pattern="^(csv|json)$"),
        limit: int = Query(5000, ge=1, le=20000),
        since: datetime | None = None,
        until: datetime | None = None,
        window: str | None = None,
        group: str | None = None,
        plugin_id: str | None = None,
        identified_only: bool = False,
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        include_synthetic: bool = False,
        session: Session = Depends(get_session),
    ) -> Response:
        """CSV/JSON export for the operator UI's history view (Milestone 4
        acceptance criteria) and offline analysis. Shares `list_detections`'s
        filters exactly — same `window`/`since`/`until`/`group`/`plugin_id`/
        `min_score`/`include_synthetic` semantics, including the honest
        default of excluding synthetic-source detections — so "export what
        I'm looking at" is true rather than approximate. A higher `limit`
        default than the list endpoint (5000 vs 500): an export is a
        deliberate one-off request, not a page a UI paints repeatedly.
        """
        resolved: history_queries.Range | None = None
        if since is None and window:
            resolved = history_queries.resolve_named_range(window, settings.timezone)
            since, until = resolved.start, resolved.end

        query = select(orm.Detection, orm.AudioStream.source_kind).outerjoin(
            orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id
        )
        if since is not None:
            query = query.where(orm.Detection.event_start_utc >= since)
        if until is not None:
            query = query.where(orm.Detection.event_start_utc < until)
        if identified_only:
            query = query.where(
                orm.Detection.taxonomic_group.in_(history_queries.IDENTIFIED_GROUPS)
            )
        if group:
            query = query.where(orm.Detection.taxonomic_group == group)
        if min_score > 0:
            query = query.where(orm.Detection.score >= min_score)
        if plugin_id:
            query = query.join(orm.Detector).where(orm.Detector.plugin_id == plugin_id)
        if not include_synthetic:
            query = query.where(history_queries.is_live(orm.AudioStream.source_kind))
        query = query.order_by(orm.Detection.event_start_utc.desc()).limit(limit)

        rows = session.execute(query).all()
        records = [_detection_payload(detection, source_kind=source_kind) for detection, source_kind in rows]
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

        if format == "json":
            return JSONResponse(
                {"detections": records, "count": len(records), "exported_at": _iso(datetime.now(UTC))},
                headers={
                    "Content-Disposition": f'attachment; filename="detections-{stamp}.json"'
                },
            )

        # CSV: flattened to the fields a spreadsheet-literate operator wants.
        # Score is exported as a bare number with the same column header used
        # elsewhere ("score", never "confidence" or "probability") — the
        # honesty rule (BirdNET scores are not calibrated probabilities)
        # applies to a CSV column exactly as much as it applies to a screen.
        fieldnames = [
            "id",
            "event_start_utc",
            "event_end_utc",
            "duration_s",
            "taxonomic_group",
            "display_name",
            "common_name",
            "scientific_name",
            "score",
            "calibrated_probability",
            "peak_frequency_hz",
            "detector_plugin_id",
            "detector_calibrated",
            "source_kind",
            "is_live_source",
        ]

        def render() -> Iterator[str]:
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            yield buffer.getvalue()
            for record in records:
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow(
                    {
                        **record,
                        "detector_plugin_id": record["detector"]["plugin_id"],
                        "detector_calibrated": record["detector"]["calibrated"],
                    }
                )
                yield buffer.getvalue()

        return StreamingResponse(
            render(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="detections-{stamp}.csv"'},
        )

    @app.get(f"{API_PREFIX}/detections/{{detection_id}}")
    def get_detection(
        detection_id: str,
        include_synthetic: bool = False,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        row = session.execute(
            select(orm.Detection, orm.AudioStream.source_kind)
            .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
            .where(orm.Detection.id == _uuid(detection_id))
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="detection not found")
        detection, source_kind = row
        if not include_synthetic and source_kind != history_queries.LIVE_SOURCE_KIND:
            # Not a 200-with-a-flag: the detail view is reached by ID (from a list
            # that already excludes this by default), so the honest answer to "why
            # can't I find it" is the same 404 the list would have implied, plus the
            # reason, rather than a payload the caller did not ask to see.
            raise HTTPException(
                status_code=404,
                detail=(
                    "detection not found in the default (live-only) view; it was "
                    f"captured from a {source_kind or 'unknown'} stream, not the "
                    "microphone. Retry with include_synthetic=true to see it."
                ),
            )
        return _detection_payload(detection, include_native=True, source_kind=source_kind)

    @app.post(f"{API_PREFIX}/detections/{{detection_id}}/review")
    def post_detection_review(
        detection_id: str,
        body: ReviewIn,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Record a human review of one detection. Append-only, matching
        `orm.Review`'s own docstring ("current status is derived from the
        latest valid review") — this always inserts a new row rather than
        mutating one, so the review history for a detection is never lost,
        only superseded. The minimal Milestone 4 review workflow: confirm or
        reject, plus an optional free-text note. Correcting a taxon
        (`corrected_taxon_id`) is left for a future pass; this endpoint
        always writes it as `None`.
        """
        detection = session.get(orm.Detection, _uuid(detection_id))
        if detection is None:
            raise HTTPException(status_code=404, detail="detection not found")
        previous = session.execute(
            select(orm.Review)
            .where(orm.Review.detection_id == detection.id)
            .order_by(orm.Review.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        review = orm.Review(
            detection_id=detection.id,
            actor="local",
            status=body.status,
            note=body.note or "",
            supersedes_review_id=previous.id if previous else None,
        )
        session.add(review)
        session.commit()
        return {
            "id": str(review.id),
            "detection_id": detection_id,
            "status": review.status,
            "note": review.note,
            "created_at": _iso(review.created_at),
        }

    @app.get(f"{API_PREFIX}/detections/{{detection_id}}/review")
    def get_detection_review(
        detection_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """The latest review for a detection, or `null` if none exists yet."""
        latest = session.execute(
            select(orm.Review)
            .where(orm.Review.detection_id == _uuid(detection_id))
            .order_by(orm.Review.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return {"review": None}
        return {
            "review": {
                "id": str(latest.id),
                "status": latest.status,
                "note": latest.note,
                "created_at": _iso(latest.created_at),
            }
        }

    @app.get(f"{API_PREFIX}/taxa/activity")
    def taxa_activity(
        hours: int = Query(24, ge=1, le=168),
        include_synthetic: bool = False,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(hours=hours)
        query = (
            select(
                orm.Detection.taxonomic_group,
                orm.Detection.common_name,
                orm.Detection.scientific_name,
                orm.Detection.detector_label,
                func.count().label("detections"),
                func.max(orm.Detection.score).label("best_score"),
                func.max(orm.Detection.event_start_utc).label("last_seen"),
            )
            .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
            .where(orm.Detection.event_start_utc >= since)
        )

        excluded_count = 0
        if not include_synthetic:
            excluded_count = (
                session.execute(
                    select(func.count(orm.Detection.id))
                    .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
                    .where(
                        orm.Detection.event_start_utc >= since,
                        history_queries.is_not_live(orm.AudioStream.source_kind),
                    )
                ).scalar_one()
                or 0
            )
            query = query.where(history_queries.is_live(orm.AudioStream.source_kind))

        rows = session.execute(
            query.group_by(
                orm.Detection.taxonomic_group,
                orm.Detection.common_name,
                orm.Detection.scientific_name,
                orm.Detection.detector_label,
            ).order_by(func.count().desc())
        ).all()
        entries = []
        for row in rows:
            # Aggregated across many detections, so there is no single
            # peak_frequency_hz/native_result to derive a hint from; display_title
            # still gives the uniform name-fallback chain.
            display_name, title_hint = display_title(
                common_name=row.common_name,
                scientific_name=row.scientific_name,
                label=row.detector_label,
                plugin_id=None,
                taxonomic_group=row.taxonomic_group,
                peak_frequency_hz=None,
                native_result=None,
            )
            entries.append(
                {
                    "taxonomic_group": row.taxonomic_group,
                    "common_name": row.common_name,
                    "scientific_name": row.scientific_name,
                    "label": row.detector_label,
                    "display_name": display_name,
                    "title_hint": title_hint,
                    "detections": row.detections,
                    "best_score": round(row.best_score, 4),
                    "last_seen_utc": _iso(row.last_seen),
                }
            )
        return {
            "since_utc": _iso(since),
            "hours": hours,
            "entries": entries,
            "include_synthetic": include_synthetic,
            "excluded_synthetic_count": excluded_count,
        }

    @app.get(f"{API_PREFIX}/history")
    def get_history(
        window: str = "last-night",
        since: datetime | None = None,
        until: datetime | None = None,
        bucket_seconds: int | None = Query(None, ge=10, le=86400),
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        include_unidentified: bool = True,
        include_synthetic: bool = False,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Everything needed to browse a past window without shipping every row.

        A night holds on the order of a hundred thousand activity detections, so the
        timeline and the species list are both aggregated in SQL. Capture coverage is
        included because an empty night is otherwise ambiguous — nothing called, or
        nothing was listening — and those mean very different things.

        Synthetic/replay detections are excluded from the timeline and species views
        by default (LIVE_SOURCE_KIND): they are real rows, honestly produced by the
        detector fallback path, but not evidence of an animal. `coverage` is
        unaffected — it already reports `seconds_from_microphone` separately from
        total coverage and is not a wildlife view.
        """
        resolved = (
            history_queries.Range(since, until or datetime.now(UTC), "custom")
            if since is not None
            else history_queries.resolve_named_range(window, settings.timezone)
        )
        return {
            "range": resolved.to_dict(),
            "timezone": settings.timezone,
            "include_synthetic": include_synthetic,
            "timeline": history_queries.timeline(
                session,
                resolved,
                bucket_seconds=bucket_seconds,
                min_score=min_score,
                include_unidentified=include_unidentified,
                include_synthetic=include_synthetic,
            ),
            "species": history_queries.species_summary(
                session,
                resolved,
                min_score=min_score,
                include_unidentified=False,
                include_synthetic=include_synthetic,
            ),
            "unidentified": history_queries.species_summary(
                session,
                resolved,
                min_score=min_score,
                include_unidentified=True,
                limit=5,
                include_synthetic=include_synthetic,
            )
            if include_unidentified
            else [],
            "coverage": history_queries.coverage(session, resolved),
        }

    @app.get(f"{API_PREFIX}/history/windows")
    def get_history_windows() -> dict[str, Any]:
        """The named windows the UI offers, resolved now for this station."""
        names = ("last-hour", "last-night", "dawn-chorus", "today", "yesterday", "last-24h")
        return {
            "timezone": settings.timezone,
            "windows": [
                {"name": name, **history_queries.resolve_named_range(name, settings.timezone).to_dict()}
                for name in names
            ],
        }

    @app.get(f"{API_PREFIX}/media/{{asset_id}}")
    def get_media(asset_id: str, session: Session = Depends(get_session)) -> FileResponse:
        row = session.get(orm.MediaAsset, _uuid(asset_id))
        if row is None:
            raise HTTPException(status_code=404, detail="media asset not found")
        path = Path(row.storage_uri)
        # Never serve a path that escaped the configured clip directory, whatever
        # the database says (technical spec §13).
        try:
            path.resolve().relative_to(settings.clip_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="asset outside clip directory") from exc
        if not path.exists():
            raise HTTPException(status_code=410, detail="asset file no longer present")
        return FileResponse(path, media_type=row.mime_type, filename=path.name)

    # -- debug surface --------------------------------------------------

    @app.get(f"{API_PREFIX}/debug/pipeline")
    def debug_pipeline() -> dict[str, Any]:
        return {
            "station": station.status_snapshot(),
            "live_hub": hub.stats(),
            "recent_events": station.bus.recent(120),
        }

    @app.get(f"{API_PREFIX}/debug/levels")
    def debug_levels(seconds: int = Query(300, ge=10, le=900)) -> dict[str, Any]:
        return {"seconds": seconds, "samples": station.level_history(seconds)}

    @app.get(f"{API_PREFIX}/debug/events")
    def debug_events(limit: int = Query(200, ge=1, le=800)) -> dict[str, Any]:
        return {"events": station.bus.recent(limit)}

    # -- live channels --------------------------------------------------

    @app.websocket(f"{API_PREFIX}/live")
    async def live_socket(socket: WebSocket) -> None:
        await socket.accept()
        client = LiveClient(socket)
        subscription = station.bus.subscribe(maxsize=256, label="live-ws")
        writer = asyncio.create_task(client.run(), name="live-writer")
        pump: asyncio.Task[None] | None = None
        try:
            client.offer(
                {
                    "type": "hello",
                    "server_utc": time.time(),
                    "station": station.status_snapshot(),
                    "spectrograms": [
                        encoder.describe() for encoder in station.spectrograms.values()
                    ],
                    "recent_detections": [
                        record.to_dict() for record in station.recent_detections[-40:]
                    ],
                    "recent_events": station.bus.recent(60),
                },
                binary=False,
            )
            # Backfill each channel so the canvas is populated at once rather than
            # showing a minute of blank, which looks exactly like a broken pipeline.
            #
            # Genuinely capped, unlike the first attempt: that computed a column
            # count from a 60 s window, which at a 24 ms hop is 2500 — more than the
            # 2400 the history holds, so the cap never bound and every client still
            # got the full ~770 kB burst.
            for encoder in station.spectrograms.values():
                history = encoder.history_frame(
                    max_columns=max(1, int(settings.spectrogram_backfill_s / encoder.hop_s))
                )
                if history is not None:
                    client.offer(history.to_binary(), binary=True)

            # Only now join the fan-out, so live frames queue behind the backfill
            # and the client never has to reorder.
            hub.add(client)
            pump = asyncio.create_task(_pump_events(client, subscription), name="live-pump")

            while True:
                # Client messages are only keep-alive pings today; reading also gives
                # prompt disconnect detection.
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("live_socket.error")
        finally:
            hub.discard(client)
            subscription.close()
            for task in (pump, writer):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def _pump_events(client: LiveClient, subscription: Any) -> None:
        """Feed events and periodic status into the client's single write queue."""
        status_due = time.monotonic()
        while not client.closed:
            try:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=0.5)
                if event.get("event_type") == "_bus.closed":
                    return
                client.offer({"type": "event", "event": event}, binary=False)
            except TimeoutError:
                pass
            if time.monotonic() >= status_due:
                status_due = time.monotonic() + 2.0
                client.offer(
                    {"type": "status", "station": station.status_snapshot()}, binary=False
                )

    @app.websocket(f"{API_PREFIX}/live/audio")
    async def live_audio_socket(
        socket: WebSocket,
        channel: str = Query("audible", pattern="^(audible|ultrasonic)$"),
        tune_hz: float | None = Query(None),
    ) -> None:
        """The GO LIVE listen channel. ``?channel=ultrasonic`` selects the live
        heterodyne monitor instead of the default 48 kHz audible mix.

        ADR-012: exactly one task performs every send on this socket. The send
        loop below owns it entirely; a concurrent reader loop only *receives*
        (keep-alive pings on the audible channel, retune requests on the
        ultrasonic one) and never touches ``socket.send*``, so the single-writer
        invariant that the visual channel already relies on holds here too.
        """
        await socket.accept()
        ultrasonic = channel == "ultrasonic"
        broadcaster = station.live_audio_ultrasonic if ultrasonic else station.live_audio
        listener = broadcaster.add_listener(label=f"browser:{channel}")

        if ultrasonic and tune_hz is not None:
            station.set_ultrasonic_tune_hz(tune_hz)

        available = not ultrasonic or station.heterodyne is not None
        hello: dict[str, Any] = {
            "type": "audio-hello",
            "channel": channel,
            "sample_rate": broadcaster.sample_rate,
            "chunk_frames": broadcaster.chunk_frames,
            "chunk_ms": broadcaster.chunk_ms,
            "encoding": "pcm_s16le_mono",
            "available": available,
        }
        if ultrasonic:
            if station.heterodyne is not None:
                hello["tune_hz"] = station.heterodyne.tune_hz
                hello["bandwidth_hz"] = station.heterodyne.bandwidth_hz
            else:
                hello["reason"] = station.heterodyne_unavailable_reason

        reader: asyncio.Task[None] | None = None
        try:
            await socket.send_json(hello)
            if not available:
                # Nothing this connection can ever stream; say so and stop,
                # rather than accepting a socket that will sit silent forever.
                return
            # Anything queued while the handshake completed is already stale; a
            # live feed should start at now, not at whatever accumulated during
            # setup.
            listener.drain()

            if ultrasonic:
                reader = asyncio.create_task(
                    _pump_tune_requests(socket, station), name="live-audio-tune-reader"
                )

            while True:
                payload = await listener.queue.get()
                if payload is None:
                    return
                await socket.send_bytes(payload)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("live_audio_socket.error", channel=channel)
        finally:
            broadcaster.remove_listener(listener)
            if reader is not None:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader

    async def _pump_tune_requests(socket: WebSocket, station: Station) -> None:
        """Read-only loop: applies retune requests without ever writing to the
        socket, so it cannot violate the single-writer rule above.

        Frame: ``{"type": "tune", "tune_hz": 42000}``. Anything else, or a
        message that fails to parse, is ignored rather than closing the
        connection over a stray keep-alive ping.
        """
        try:
            while True:
                try:
                    message = await socket.receive_json()
                except (WebSocketDisconnect, asyncio.CancelledError):
                    raise
                except Exception:
                    # Malformed frame (or a plain keep-alive text ping): skip
                    # it rather than tearing down the whole reader over one
                    # bad message.
                    continue
                if not isinstance(message, dict) or message.get("type") != "tune":
                    continue
                try:
                    hz = float(message["tune_hz"])
                except (KeyError, TypeError, ValueError):
                    continue
                station.set_ultrasonic_tune_hz(hz)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("live_audio_socket.tune_reader_error")

    @app.get(f"{API_PREFIX}/live/audio.wav")
    async def live_audio_wav(
        request: Request,
        channel: str = Query("audible", pattern="^(audible|ultrasonic)$"),
        tune_hz: float | None = Query(None),
    ) -> Response:
        """Chunked-WAV fallback for the GO LIVE button.

        Mirrors ``/live/audio``'s plumbing exactly: the same broadcaster, the
        same bounded per-listener queue and drop policy, and the same "no
        heterodyne work with zero listeners" invariant — a listener is only
        ever attached once the channel is confirmed available. The one
        difference is the transport: continuous 16-bit PCM behind a WAV header,
        so a plain ``<audio>`` element can decode it without Web Audio.
        """
        ultrasonic = channel == "ultrasonic"
        broadcaster = station.live_audio_ultrasonic if ultrasonic else station.live_audio

        if ultrasonic and tune_hz is not None:
            station.set_ultrasonic_tune_hz(tune_hz)

        available = not ultrasonic or station.heterodyne is not None
        if not available:
            raise HTTPException(
                status_code=503,
                detail=station.heterodyne_unavailable_reason or "ultrasonic channel unavailable",
            )

        headers = {
            "Cache-Control": "no-store",
            "X-Live-Sample-Rate": str(broadcaster.sample_rate),
        }
        if ultrasonic and station.heterodyne is not None:
            headers["X-Live-Tune-Hz"] = str(station.heterodyne.tune_hz)
            headers["X-Live-Bandwidth-Hz"] = str(station.heterodyne.bandwidth_hz)

        listener = broadcaster.add_listener(label=f"http-wav:{channel}")
        # Anything queued before this request starts actually consuming is
        # stale, exactly as for the WebSocket's handshake delay.
        listener.drain()

        async def body() -> AsyncIterator[bytes]:
            try:
                yield _wav_stream_header(sample_rate=broadcaster.sample_rate)
                while True:
                    # A client that has gone away should release its listener
                    # promptly rather than waiting for the queue to notice —
                    # for the ultrasonic channel that listener is the only
                    # thing keeping the heterodyne running.
                    if await request.is_disconnected():
                        return
                    try:
                        payload = await asyncio.wait_for(listener.queue.get(), timeout=1.0)
                    except TimeoutError:
                        continue
                    if payload is None:
                        return
                    yield payload
            finally:
                broadcaster.remove_listener(listener)

        return StreamingResponse(body(), media_type="audio/wav", headers=headers)

    @app.post(f"{API_PREFIX}/live/tune")
    def post_live_tune(tune_hz: float = Query(...)) -> dict[str, Any]:
        """Retune the live ultrasonic heterodyne monitor in place.

        The chunked-WAV listen channel (ADR-019) has no socket to carry the
        WebSocket's ``{"type": "tune", ...}`` frame back to the server, so a
        `<audio>` client sweeping the dial has no in-place retune the way the
        old WebSocket client did -- until now. This is that control path: a
        tiny, idempotent HTTP call the client can fire on every slider tick
        (throttled client-side) without touching the audio stream at all.

        There is exactly one heterodyne oscillator per station (see
        ``Station.set_ultrasonic_tune_hz``), shared by every ultrasonic
        listener regardless of transport, so there is no "which stream" to
        target -- exactly the same "last request wins for everyone" behaviour
        the WebSocket's tune frame already has. Safe to call with no listener
        connected at all (a no-op landing value is still returned) and safe to
        call when the ultrasonic channel is unavailable for this station's
        native rate (``available`` is false and ``reason`` explains why).
        """
        applied = station.set_ultrasonic_tune_hz(tune_hz)
        heterodyne = station.heterodyne
        return {
            "tune_hz": applied,
            "bandwidth_hz": heterodyne.bandwidth_hz if heterodyne is not None else None,
            "available": heterodyne is not None,
            "reason": None if heterodyne is not None else station.heterodyne_unavailable_reason,
        }

    # -- static UI ------------------------------------------------------

    if settings.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(settings.web_dist), html=True), name="web")
    else:

        @app.get("/")
        def missing_ui() -> PlainTextResponse:
            return PlainTextResponse(
                "Debug UI is not built. Run 'npm ci && npm run build' in web/, "
                f"which writes to {settings.web_dist}.\n"
                f"The API is available under {API_PREFIX}/.",
                status_code=200,
            )

    @app.middleware("http")
    async def _no_store(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        path = request.url.path
        # The bundle filenames are content-hashed and safe to cache hard, but the
        # document that names them must never be cached: otherwise a redeploy keeps
        # serving the previous build until someone thinks to force-refresh, which is
        # a confusing way to conclude that a fix did not work.
        if path.startswith(API_PREFIX) or path in ("/", "/index.html"):
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        elif "/assets/" in path:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    return app


def _detection_payload(
    row: orm.Detection, *, include_native: bool = False, source_kind: str | None = None
) -> dict[str, Any]:
    display_name, title_hint = display_title(
        common_name=row.common_name,
        scientific_name=row.scientific_name,
        label=row.detector_label,
        plugin_id=row.detector.plugin_id if row.detector else None,
        taxonomic_group=row.taxonomic_group,
        peak_frequency_hz=row.peak_frequency_hz,
        native_result=row.native_result,
    )
    payload: dict[str, Any] = {
        "id": str(row.id),
        "event_start_utc": _iso(row.event_start_utc),
        "event_end_utc": _iso(row.event_end_utc),
        "duration_s": round((row.event_end_utc - row.event_start_utc).total_seconds(), 3),
        "label": row.detector_label,
        "display_name": display_name,
        "title_hint": title_hint,
        "flags": detection_flags(row.native_result),
        "common_name": row.common_name,
        "scientific_name": row.scientific_name,
        "canonical_taxon_id": row.canonical_taxon_id,
        "rank": row.rank,
        "taxonomic_group": row.taxonomic_group,
        "score": round(row.score, 6),
        "calibrated_probability": row.calibrated_probability,
        "peak_frequency_hz": row.peak_frequency_hz,
        "source_start_frame": row.source_start_frame,
        "source_end_frame": row.source_end_frame,
        "stream_id": str(row.stream_id),
        #: Honest even when include_synthetic=true surfaced this row: the caller
        #: asked to see it, not to be told it was real.
        "source_kind": source_kind,
        "is_live_source": source_kind == history_queries.LIVE_SOURCE_KIND,
        "detector": {
            "plugin_id": row.detector.plugin_id,
            "plugin_version": row.detector.plugin_version,
            "model_id": row.detector.model_id,
            "model_version": row.detector.model_version,
            "licence_name": row.detector.licence_name,
            "calibrated": row.detector.calibrated,
        },
        "media": [
            {
                "id": str(link.media_asset_id),
                "role": link.role,
                "kind": link.asset.kind,
                "description": (link.asset.detail or {}).get("description"),
                "detail": link.asset.detail or {},
                "sample_rate": link.asset.sample_rate,
                "byte_length": link.asset.byte_length,
                "sha256": link.asset.sha256,
                "url": f"{API_PREFIX}/media/{link.media_asset_id}",
            }
            for link in row.media
        ],
    }
    if include_native:
        payload["native_result"] = row.native_result
    return payload


#: Conventional placeholder for a WAV chunk size that cannot be known in
#: advance. Every browser and media player treats it as "read until the
#: connection closes" rather than a malformed file.
_WAV_UNKNOWN_SIZE = 0xFFFFFFFF


def _wav_stream_header(*, sample_rate: int, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """A canonical 44-byte PCM WAV header for a stream of unknown, effectively
    endless length.

    ``clips.py`` writes finished, seekable clip files via ``soundfile``, which
    needs the true length up front and has no equivalent for an open-ended
    stream — so this is hand-built rather than reused, matching what
    ``soundfile``/libsndfile itself writes for 16-bit PCM mono except for the
    two size fields, which use ``_WAV_UNKNOWN_SIZE``.
    """
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    return (
        b"RIFF"
        + struct.pack("<I", _WAV_UNKNOWN_SIZE)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH",
            16,  # fmt chunk size
            1,  # PCM
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
        + b"data"
        + struct.pack("<I", _WAV_UNKNOWN_SIZE)
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


def _uuid(value: str) -> Any:
    import uuid as _uuid_module

    try:
        return _uuid_module.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="malformed identifier") from exc


__all__ = ["EventType", "create_app"]
