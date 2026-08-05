"""FastAPI control plane and the live WebSocket the debug UI runs on.

Two live channels, deliberately separate:

``/api/v1/live``
    Spectrogram columns (binary), telemetry and pipeline events (JSON). Every
    viewer sees identical data because the server computes it once.

``/api/v1/live/audio``
    Raw int16 PCM for the "GO LIVE" listen button. Kept apart so that opening it
    costs nothing until someone actually presses the button, and so a listener
    that falls behind loses audio without disturbing the visual feed.

Default binding is LAN-only and anonymous read is enabled for this debug slice —
recorded honestly in ``docs/operations`` rather than implied to be secure. The
authentication foundation is Milestone 4 work.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models as model_registry
from ..audio.probe import enumerate_capture_devices, probe_supported_rates, system_report
from ..config import Settings, get_settings
from ..db import models as orm
from ..db.session import create_all, get_session, init_engine
from ..events import EventType
from ..station import Station
from .metrics import PrometheusExporter

log = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"


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
        group: str | None = None,
        plugin_id: str | None = None,
        min_score: float = Query(0.0, ge=0.0, le=1.0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        query = select(orm.Detection).order_by(orm.Detection.event_start_utc.desc())
        if since is not None:
            query = query.where(orm.Detection.event_start_utc >= since)
        if group:
            query = query.where(orm.Detection.taxonomic_group == group)
        if min_score > 0:
            query = query.where(orm.Detection.score >= min_score)
        if plugin_id:
            query = query.join(orm.Detector).where(orm.Detector.plugin_id == plugin_id)
        rows = session.scalars(query.limit(limit)).all()
        return {"detections": [_detection_payload(row) for row in rows]}

    @app.get(f"{API_PREFIX}/detections/{{detection_id}}")
    def get_detection(detection_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        row = session.get(orm.Detection, _uuid(detection_id))
        if row is None:
            raise HTTPException(status_code=404, detail="detection not found")
        return _detection_payload(row, include_native=True)

    @app.get(f"{API_PREFIX}/taxa/activity")
    def taxa_activity(
        hours: int = Query(24, ge=1, le=168), session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(hours=hours)
        rows = session.execute(
            select(
                orm.Detection.taxonomic_group,
                orm.Detection.common_name,
                orm.Detection.scientific_name,
                orm.Detection.detector_label,
                func.count().label("detections"),
                func.max(orm.Detection.score).label("best_score"),
                func.max(orm.Detection.event_start_utc).label("last_seen"),
            )
            .where(orm.Detection.event_start_utc >= since)
            .group_by(
                orm.Detection.taxonomic_group,
                orm.Detection.common_name,
                orm.Detection.scientific_name,
                orm.Detection.detector_label,
            )
            .order_by(func.count().desc())
        ).all()
        return {
            "since_utc": _iso(since),
            "hours": hours,
            "entries": [
                {
                    "taxonomic_group": row.taxonomic_group,
                    "common_name": row.common_name,
                    "scientific_name": row.scientific_name,
                    "label": row.detector_label,
                    "display_name": row.common_name or row.detector_label or "unknown",
                    "detections": row.detections,
                    "best_score": round(row.best_score, 4),
                    "last_seen_utc": _iso(row.last_seen),
                }
                for row in rows
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
    async def live_audio_socket(socket: WebSocket) -> None:
        await socket.accept()
        listener = station.live_audio.add_listener(label="browser")
        try:
            await socket.send_json(
                {
                    "type": "audio-hello",
                    "sample_rate": station.live_audio.sample_rate,
                    "chunk_frames": station.live_audio.chunk_frames,
                    "chunk_ms": station.live_audio.chunk_ms,
                    "encoding": "pcm_s16le_mono",
                }
            )
            # Anything queued while the handshake completed is already stale; a live
            # feed should start at now, not at whatever accumulated during setup.
            listener.drain()
            while True:
                payload = await listener.queue.get()
                if payload is None:
                    return
                await socket.send_bytes(payload)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("live_audio_socket.error")
        finally:
            station.live_audio.remove_listener(listener)

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


def _detection_payload(row: orm.Detection, *, include_native: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(row.id),
        "event_start_utc": _iso(row.event_start_utc),
        "event_end_utc": _iso(row.event_end_utc),
        "duration_s": round((row.event_end_utc - row.event_start_utc).total_seconds(), 3),
        "label": row.detector_label,
        "display_name": row.common_name or row.detector_label or "unknown",
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
