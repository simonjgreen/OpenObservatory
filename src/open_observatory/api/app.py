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
from typing import Any, cast
from zoneinfo import ZoneInfo

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import display_channel as display_state
from .. import firmware_store, plausibility
from .. import history as history_queries
from .. import models as model_registry
from .. import review as review_queries
from ..audio.probe import enumerate_capture_devices, probe_supported_rates, system_report
from ..auth import AuthError, AuthService, Principal
from ..config import Settings, get_settings
from ..db import models as orm
from ..db.session import ensure_schema_at_head, get_session, init_engine, session_scope
from ..display import detection_flags, display_title
from ..events import EventType
from ..mqtt import MqttPublisher
from ..pause import PauseError, available_presets
from ..site_settings import (
    EDITABLE_BY_NAME,
    RuntimeEnvStore,
    SettingValueError,
    coerce_updates,
    describe_settings,
    describe_setup,
    validate_merged,
)
from ..station import Station
from .metrics import PrometheusExporter

log = structlog.get_logger(__name__)

API_PREFIX = "/api/v1"

#: Reachable with no credential regardless of `auth_enabled` or the
#: configurable `auth_public_read_paths` allow-list -- these are not operator
#: choices. `/metrics` is excluded from the auth gate separately, below,
#: because it never matches the `API_PREFIX` the gate checks.
#:
#: `/health`: `deploy/deploy.sh` polls this after every restart with no
#: credential and no login flow; requiring auth here would make every future
#: deploy hang or fail (see ADR-034).
#: `/auth/login`: reaching it is the only way out of being logged out.
#: `/auth/logout`: idempotent and self-service (it can only ever act on the
#: credential the caller already presents), kept reachable so a stale or
#: expired session can always be cleared client-side.
_ALWAYS_PUBLIC_PATHS = frozenset(
    {f"{API_PREFIX}/health", f"{API_PREFIX}/auth/login", f"{API_PREFIX}/auth/logout"}
)


class ReviewIn(BaseModel):
    """Body for `POST /api/v1/detections/{id}/review`.

    Four review actions (ADR-043):

    * ``confirmed`` / ``rejected`` — the original Milestone 4 workflow.
    * ``corrected`` — the original identification was wrong, and
      ``corrected_taxon_id`` says what it actually was. Required only for
      this status; the id must be one `GET /api/v1/taxa/search` would return
      (see that endpoint and ``review.resolve_taxon``).
    * ``held`` — no verdict, but keep the evidence: exempts the detection
      from the retention sweeper's age-based tiers (``retention.py``).
    """

    status: str = Field(pattern="^(confirmed|rejected|corrected|held)$")
    note: str = Field(default="", max_length=2000)
    corrected_taxon_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check_correction(self) -> ReviewIn:
        if self.status == "corrected" and not self.corrected_taxon_id:
            raise ValueError("corrected_taxon_id is required when status is 'corrected'")
        if self.status != "corrected" and self.corrected_taxon_id:
            raise ValueError("corrected_taxon_id is only valid when status is 'corrected'")
        return self


class PauseIn(BaseModel):
    """Body for `POST /api/v1/pause` (ADR-055).

    A preset key, never a raw number of seconds. "until-midnight" cannot be
    expressed as a duration -- it depends on the station's configured zone and
    on the time of day it is pressed -- so the station resolves all of them and
    the browser resolves none, which also means one place decides what "an
    hour" means rather than two that can disagree.
    """

    preset: str = Field(min_length=1, max_length=40)


class LoginIn(BaseModel):
    """Body for `POST /api/v1/auth/login`. Never logged -- see `auth.py`."""

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=400)


class PasswordChangeIn(BaseModel):
    """Body for `POST /api/v1/auth/password`. Requires the current password,
    not just a valid session, so a hijacked-but-unlocked browser tab cannot
    lock the real operator out by changing it unchallenged."""

    current_password: str = Field(min_length=1, max_length=400)
    new_password: str = Field(min_length=1, max_length=400)


class ApiTokenCreateIn(BaseModel):
    """Body for `POST /api/v1/auth/tokens`."""

    name: str = Field(min_length=1, max_length=120)


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
    ensure_schema_at_head()

    station = Station(settings)
    hub = LiveHub()
    #: Connected inside-observer displays (ADR-038). A plain set rather than a
    #: hub: this channel has no broadcast -- every client is filtered by its own
    #: threshold, so each gets its own frames.
    display_clients: set[display_state.DisplayClient] = set()
    exporter = PrometheusExporter(station)
    auth_service = AuthService(settings)
    station.set_spectrogram_sink(lambda columns: hub.broadcast_binary(columns.to_binary()))
    # ADR-040: the hub's socket count is the station's only way to know whether
    # the spectrograms are being drawn for anyone. Deliberately the *live* hub
    # and not the display clients -- the counter-top display has no canvas and would
    # never make encoding worth doing.
    station.set_spectrogram_consumer_count(lambda: hub.count)
    # Milestone 6 (ADR-025): subscribes to station.bus, the same seam every other
    # consumer uses. Off by default (mqtt_enabled=False) and never awaited from the
    # capture path -- see mqtt/publisher.py's module docstring for the full set of
    # guarantees (bounded queue, no shared thread pool, bounded reconnect backoff).
    mqtt_publisher = MqttPublisher(
        settings,
        station.bus,
        station_id_provider=lambda: str(station.station_id) if station.station_id else None,
        health_provider=lambda: _health_payload(),
        capture_status_provider=lambda: station.status_snapshot()["capture"],
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.auth_enabled:
            with session_scope() as db:
                generated_password = auth_service.bootstrap_admin_if_needed(db)
            if generated_password is not None:
                # Printed, not just logged: this is the only time the
                # operator ever sees it, and structlog's configured level
                # (or a non-interactive log shipper) should not be able to
                # swallow it. Still goes through structlog too, at WARNING,
                # so it lands in whatever log the rest of this session does.
                print(
                    "\n"
                    "==================================================================\n"
                    f"  Open Observatory: created account '{settings.auth_bootstrap_username}'\n"
                    f"  Generated password (shown once): {generated_password}\n"
                    "  You will be required to change it on first login.\n"
                    "==================================================================\n",
                    # `flush=True`: stdout is block-buffered (not a tty) under
                    # uvicorn/systemd, so without this the banner can sit in an
                    # ~8 KiB buffer indefinitely -- observed while testing this
                    # feature, where it simply never appeared in the captured
                    # log until the process later wrote enough other output to
                    # flush the buffer. A one-time secret must be visible the
                    # moment it is printed, not eventually.
                    flush=True,
                )
        else:
            log.warning(
                "auth.disabled",
                note=(
                    "auth_enabled is false: the API and UI are reachable with no "
                    "credential from anything on this network. Set OO_AUTH_ENABLED=true "
                    "to close this. See ADR-015/ADR-034."
                ),
            )
        await station.start()
        await mqtt_publisher.start()
        try:
            yield
        finally:
            await mqtt_publisher.stop()
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
    app.state.auth_service = auth_service

    # -- authentication ---------------------------------------------------

    def _bearer_token(request: Request) -> str | None:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return None

    def _resolve_principal(request: Request, session: Session) -> Principal | None:
        """Session cookie or bearer token, in that order. A request carrying
        both is unusual (only a hand-crafted client would do it) but the
        cookie -- the browser's own credential -- takes precedence so a
        stray `Authorization` header from some other tool cannot silently
        override who the browser itself is logged in as."""
        cookie = request.cookies.get(settings.auth_session_cookie_name)
        if cookie:
            principal = auth_service.resolve_session(session, cookie)
            if principal is not None:
                return principal
        token = _bearer_token(request)
        if token:
            return auth_service.resolve_api_token(session, token)
        return None

    def get_optional_principal(
        request: Request, session: Session = Depends(get_session)
    ) -> Principal | None:
        return _resolve_principal(request, session)

    def require_principal(
        principal: Principal | None = Depends(get_optional_principal),
    ) -> Principal:
        """For endpoints that manage *your own account* (password, tokens):
        these always require proof of identity, independent of whether
        `auth_enabled` is currently gating the rest of the API -- an
        operator testing the feature before flipping the switch should not
        be able to silently manage a token store as nobody in particular."""
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return principal

    @app.middleware("http")
    async def _enforce_auth(request: Request, call_next: Any) -> Response:
        """The blanket gate. Off entirely while `auth_enabled` is false, so
        an operator who has not turned this on sees literally no behaviour
        change (ADR-034) -- not even a cookie is inspected.

        `_ALWAYS_PUBLIC_PATHS` and, for GET only, `auth_public_read_paths`
        (default: the ESP32 counter-top display's `/api/v1/detections` poll) are
        exempt. Everything else under `API_PREFIX` needs a valid session or
        API token. Static UI assets and `/metrics` never match `API_PREFIX`
        and are untouched by this function.
        """
        path = request.url.path
        if (
            settings.auth_enabled
            and path.startswith(API_PREFIX)
            and path not in _ALWAYS_PUBLIC_PATHS
            and not (request.method == "GET" and path in settings.auth_public_read_paths)
        ):
            with session_scope() as session:
                principal = _resolve_principal(request, session)
            if principal is None:
                log.info("auth.request_rejected", path=path, method=request.method)
                return JSONResponse({"detail": "authentication required"}, status_code=401)
            request.state.principal = principal
        return await call_next(request)

    @app.post(f"{API_PREFIX}/auth/login")
    def post_auth_login(
        body: LoginIn, request: Request, response: Response, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        client_key = request.client.host if request.client else "unknown"
        auth_service.login_limiter.sweep()
        allowed, retry_after = auth_service.login_limiter.allow(client_key)
        if not allowed:
            auth_service.metrics.login_rate_limited.inc()
            log.warning("auth.login_rate_limited", client=client_key)
            raise HTTPException(
                status_code=429,
                detail="too many login attempts; try again shortly",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )
        try:
            # .username/.password are never logged -- only the client key and
            # the outcome are, on both the success and failure paths below.
            user = auth_service.authenticate(session, username=body.username, password=body.password)
        except AuthError:
            auth_service.metrics.login_failure.inc()
            log.warning("auth.login_failed", client=client_key)
            raise HTTPException(status_code=401, detail="invalid username or password") from None
        auth_service.login_limiter.reset(client_key)
        auth_service.metrics.login_success.inc()
        token, row = auth_service.create_session(
            session, user=user, user_agent=request.headers.get("user-agent", "")
        )
        log.info("auth.login_succeeded", client=client_key, username=user.username)
        response.set_cookie(
            settings.auth_session_cookie_name,
            token,
            max_age=int(settings.auth_session_ttl_hours * 3600),
            httponly=True,
            samesite="lax",
            secure=settings.auth_cookie_secure,
            path="/",
        )
        return {
            "username": user.username,
            "must_change_password": user.must_change_password,
            "expires_utc": _iso(row.expires_at),
        }

    @app.post(f"{API_PREFIX}/auth/logout")
    def post_auth_logout(
        request: Request, response: Response, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        cookie = request.cookies.get(settings.auth_session_cookie_name)
        if cookie:
            auth_service.revoke_session(session, cookie)
        response.delete_cookie(settings.auth_session_cookie_name, path="/")
        return {"logged_out": True}

    @app.get(f"{API_PREFIX}/auth/me")
    def get_auth_me(principal: Principal | None = Depends(get_optional_principal)) -> dict[str, Any]:
        """Always reachable directly (never in `_ALWAYS_PUBLIC_PATHS`, but
        also never gated: when `auth_enabled` is true and there is no valid
        credential, `_enforce_auth` above already returned 401 before this
        body ever runs). The UI's login-vs-app decision is entirely: did
        this request succeed."""
        if principal is None:
            return {"authenticated": False, "auth_enabled": settings.auth_enabled}
        return {
            "authenticated": True,
            "auth_enabled": settings.auth_enabled,
            "username": principal.username,
            "method": principal.method,
            "must_change_password": principal.must_change_password,
        }

    @app.post(f"{API_PREFIX}/auth/password")
    def post_auth_password(
        body: PasswordChangeIn,
        principal: Principal = Depends(require_principal),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.get(orm.User, principal.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="account not found")
        try:
            auth_service.change_password(
                session,
                user=user,
                current_password=body.current_password,
                new_password=body.new_password,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"changed": True}

    @app.get(f"{API_PREFIX}/auth/tokens")
    def list_auth_tokens(
        principal: Principal = Depends(require_principal),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.get(orm.User, principal.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="account not found")
        return {
            "tokens": [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "token_prefix": row.token_prefix,
                    "created_at": _iso(row.created_at),
                    "last_used_at": _iso(row.last_used_at),
                    "revoked": row.revoked_at is not None,
                }
                for row in auth_service.list_api_tokens(session, user=user)
            ]
        }

    @app.post(f"{API_PREFIX}/auth/tokens")
    def create_auth_token(
        body: ApiTokenCreateIn,
        principal: Principal = Depends(require_principal),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.get(orm.User, principal.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="account not found")
        token, row = auth_service.create_api_token(session, user=user, name=body.name)
        log.info("auth.token_created", username=user.username, name=row.name, token_id=str(row.id))
        return {
            "id": str(row.id),
            "name": row.name,
            #: Shown exactly once. Neither this value nor its hash's plaintext
            #: origin is ever logged or persisted anywhere else.
            "token": token,
            "created_at": _iso(row.created_at),
        }

    @app.delete(f"{API_PREFIX}/auth/tokens/{{token_id}}")
    def revoke_auth_token(
        token_id: str,
        principal: Principal = Depends(require_principal),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        user = session.get(orm.User, principal.user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="account not found")
        ok = auth_service.revoke_api_token(session, user=user, token_id=_uuid(token_id))
        if not ok:
            raise HTTPException(status_code=404, detail="token not found")
        log.info("auth.token_revoked", username=user.username, token_id=token_id)
        return {"revoked": True}

    # -- station and health --------------------------------------------

    @app.get(f"{API_PREFIX}/station")
    def get_station() -> dict[str, Any]:
        snapshot = station.status_snapshot()
        # Every other queue in this system reports its depth and its drops here;
        # so does this one. `mean_frame_bytes` is the number the ADR-038 budget
        # is actually judged against, measured on the wire rather than asserted.
        snapshot["display_channel"] = {
            "clients": len(display_clients),
            "per_client": [client.stats() for client in display_clients],
        }
        return snapshot

    # -- inside-observer firmware (ADR-050) ---------------------------------
    #
    # The station holds one image; the display fetches it over the connection
    # it already has. The whole point is that the ESP32 never goes back on a
    # cable, so everything here is written to fail *safe* rather than to
    # succeed often: a refused upload, a withdrawn offer and a display that
    # ignores a frame all leave a working display on the shelf.

    firmware = firmware_store.FirmwareStore(settings.firmware_dir)
    FIRMWARE_IMAGE_PATH = f"{API_PREFIX}/firmware/image"

    def _firmware_payload() -> dict[str, Any]:
        release = firmware.current()
        return {
            "published": release.as_dict() if release is not None else None,
            "image_path": FIRMWARE_IMAGE_PATH if release is not None else None,
            "offer_on_connect": settings.display_ota_offer_on_connect,
            #: So the UI can say "this will not fit" in the same words the
            #: display would, rather than discovering it at upload time.
            "app_slot_bytes": firmware_store.APP_SLOT_BYTES,
            "displays": [
                {
                    "firmware_version": client.firmware_version,
                    # Honest about the third state: a display that predates
                    # ADR-050 reports no version at all, and "unknown" is not
                    # the same claim as "out of date".
                    "up_to_date": (
                        None
                        if release is None or not client.firmware_version
                        else firmware_store.compare_versions(
                            client.firmware_version, release.version
                        )
                        >= 0
                    ),
                    "frames_sent": client.sent,
                }
                for client in display_clients
            ],
        }

    @app.get(f"{API_PREFIX}/firmware")
    def get_firmware() -> dict[str, Any]:
        return _firmware_payload()

    @app.post(f"{API_PREFIX}/firmware")
    async def publish_firmware(
        request: Request,
        version: str = Query(..., min_length=1, max_length=15),
        notes: str = Query("", max_length=500),
    ) -> dict[str, Any]:
        """Store one image and start offering it.

        The body is the raw `.bin`, not a multipart form: this endpoint has
        exactly one file and no other fields, and `application/octet-stream`
        avoids adding a multipart parser to the dependency set for it.

        The image is validated as an ESP32 application for *this* chip before
        anything is written (`firmware_store.validate_image`). That check is
        about the likely mistake rather than about security -- uploading
        `firmware.elf`, or a whole-flash backup, or an ESP32-S3 build, each
        produces a file that downloads and verifies perfectly and then does not
        boot. A display that does not boot is the car journey this feature
        exists to remove.
        """
        payload = await request.body()
        try:
            release = await asyncio.to_thread(
                firmware.publish, payload, version=version, notes=notes
            )
        except firmware_store.FirmwareError as exc:
            raise HTTPException(status_code=422, detail={"errors": {"image": str(exc)}}) from exc
        log.info(
            "firmware.published",
            version=release.version,
            sha256=release.sha256,
            size_bytes=release.size_bytes,
        )
        return _firmware_payload()

    @app.delete(f"{API_PREFIX}/firmware")
    async def withdraw_firmware() -> dict[str, Any]:
        """Stop offering. Never touches a display that already installed it.

        There is no "roll the fleet back" button and there deliberately is not
        one: rollback is the *display's* decision, taken against evidence only
        it has (can I reach the station?), and a station that could push a
        downgrade could also push a downgrade to a build that cannot be
        upgraded again.
        """
        had = await asyncio.to_thread(firmware.withdraw)
        log.info("firmware.withdrawn", was_published=had)
        return _firmware_payload()

    @app.get(FIRMWARE_IMAGE_PATH)
    def get_firmware_image() -> FileResponse:
        release = firmware.current()
        if release is None:
            raise HTTPException(status_code=404, detail="no firmware image is published")
        # Content-Length must be exact: the display compares it against the
        # size in the offer and refuses a mismatch rather than writing an
        # image of unknown length into a boot slot.
        return FileResponse(
            firmware.image_path,
            media_type="application/octet-stream",
            filename=f"inside-observer-{release.version}.bin",
        )

    def _offer_firmware_to(client: display_state.DisplayClient) -> bool:
        """Queue an update frame if this display should have one. Never blocks."""
        release = firmware.current()
        if release is None or not firmware_store.should_offer(
            release, client.firmware_version
        ):
            return False
        client.offer(
            display_state.update_frame(
                version=release.version,
                sha256=release.sha256,
                size_bytes=release.size_bytes,
                path=FIRMWARE_IMAGE_PATH,
            )
        )
        return True

    @app.post(f"{API_PREFIX}/firmware/rollout")
    def rollout_firmware() -> dict[str, Any]:
        """Tell every connected display that is behind about the published image.

        Push rather than poll, and push *and* the connect check rather than
        either alone, because they catch different displays: this catches one
        that has been connected for a week and would otherwise never ask, and
        the connect check catches one that was unplugged or rebooting while
        this ran. Neither costs a byte when the versions already agree.

        Offering is not installing. The display refuses anything not strictly
        newer, refuses a digest it cannot use, and waits until nobody is
        looking at it and nothing is happening in the garden. This endpoint
        returns how many were *told*, which is the only thing the station
        knows.
        """
        release = firmware.current()
        if release is None:
            raise HTTPException(status_code=409, detail="no firmware image is published")
        offered = sum(1 for client in display_clients if _offer_firmware_to(client))
        log.info(
            "firmware.rollout",
            version=release.version,
            connected=len(display_clients),
            offered=offered,
        )
        return {"offered": offered, "connected": len(display_clients), **_firmware_payload()}

    # -- site settings (ADR: site parameters are runtime state) -------------
    #
    # The settings page's persistence is config/runtime.env -- the same
    # gitignored file the environment path reads -- so the UI and hand-editing
    # are two writers of one configuration, not two configurations.
    env_store = RuntimeEnvStore(settings.runtime_env_path)

    @app.get(f"{API_PREFIX}/settings")
    def get_site_settings() -> dict[str, Any]:
        return describe_settings(settings, applied_site=station.applied_site)

    @app.get(f"{API_PREFIX}/setup")
    def get_setup() -> dict[str, Any]:
        """The guided first-run flow's state.

        A station with no configuration should guide rather than fail, so this
        answers the four questions a person actually has on day one -- where am
        I, what time is it here, is my microphone working, do I want MQTT --
        and reads the *live* capture state for the microphone answer rather
        than a stored flag, because "configured" and "working" are different
        claims and only one of them is worth making.
        """
        return describe_setup(settings, capture=station.status_snapshot()["capture"])

    @app.put(f"{API_PREFIX}/settings")
    async def put_site_settings(payload: dict[str, Any]) -> dict[str, Any]:
        """Save operator-editable settings.

        Validate, persist (runtime.env, atomically), then apply live where that
        is safe: identity fields mutate the running settings object and
        re-upsert the station row; tuning is pushed into the encoders,
        detectors, clip manager and retention sweeper by
        ``Station.apply_tuning``; MQTT changes restart the publisher so it
        reconnects with the new broker details.

        Restart-pinned fields are persisted and reported, never injected --
        the response and every later GET name them as pending a restart (see
        site_settings.py for why a live coordinate swap under the BirdNET range
        model, or a live capture re-negotiation, is the wrong kind of clever).
        A live field whose target object is not running is reported the same
        way, on the same evidence: ``Station.applied_site`` says what is
        actually in use, and anything else is a claim this API will not make.

        Validation runs *before* anything is written, and covers cross-field
        rules against the merged configuration, so a rejected request leaves
        both the file and the process exactly as they were.
        """
        try:
            updates = coerce_updates(payload)
            validate_merged(settings, updates)
        except SettingValueError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc

        changed = {
            name: value for name, value in updates.items() if getattr(settings, name) != value
        }
        await asyncio.to_thread(env_store.apply, changed)
        for name, value in changed.items():
            setattr(settings, name, value)

        if {"station_name", "timezone", "latitude", "longitude"} & changed.keys():
            await station.apply_site_identity()
        # Live tuning. Cheap and synchronous by construction -- attribute
        # rebinds and threshold swaps, never a device or a thread -- so it does
        # not go near the capture path or an executor.
        live_changed = [
            name for name in changed if not EDITABLE_BY_NAME[name].restart_required
        ]
        if live_changed:
            station.apply_tuning(live_changed)
        mqtt_changed = [name for name in changed if name.startswith("mqtt_")]
        if mqtt_changed:
            # Stop/start rather than poking fields into a live client: the
            # publisher reads its settings at start, and this is the same code
            # path a process restart would take -- no second reconfigure path
            # to test or to drift.
            await mqtt_publisher.stop()
            await mqtt_publisher.start()

        log.info(
            "settings.updated",
            fields=sorted(changed),
            # Values are logged only for non-secret fields.
            values={
                k: v for k, v in changed.items() if not EDITABLE_BY_NAME[k].secret
            },
        )
        result = describe_settings(settings, applied_site=station.applied_site)
        result["saved"] = sorted(changed)
        return result

    # -- operator pause (ADR-055) ----------------------------------------

    def _pause_payload() -> dict[str, Any]:
        """The whole state of the pause control, for every client that draws it.

        Includes the menu as well as the state, so the split button is built
        from one request rather than from a settings fetch plus a state fetch
        that can disagree about what it is offering.
        """
        payload = station.pause.snapshot()
        payload["presets"] = [
            preset.to_dict() for preset in available_presets(settings.pause_presets)
        ]
        payload["default_preset"] = settings.pause_default_preset
        payload["timezone"] = settings.timezone
        payload["banner"] = station.pause.banner(settings.timezone)
        return payload

    @app.get(f"{API_PREFIX}/pause")
    def get_pause() -> dict[str, Any]:
        return _pause_payload()

    @app.post(f"{API_PREFIX}/pause")
    async def post_pause(body: PauseIn, request: Request) -> dict[str, Any]:
        """Stop recording for a chosen duration.

        Deliberately an endpoint of its own rather than a field on
        `PUT /api/v1/settings`. A setting describes how the station behaves
        indefinitely; this is an action with a deadline, it is taken repeatedly,
        it is not persisted to `runtime.env`, and it must be reachable in one
        request from a control on the main page. Wiring it through the settings
        writer would also mean a privacy action waiting on a file write.

        Pressing it while already paused replaces the deadline -- see
        `PauseController.start`.

        Any *known* preset is accepted, not only the ones `pause_presets`
        currently lists. The setting decides what the menu offers; refusing a
        key that a browser tab loaded ten minutes ago would turn a settings edit
        into a failed privacy action at the moment it is least welcome.
        """
        principal = getattr(request.state, "principal", None)
        actor = getattr(principal, "username", None) or "operator"
        try:
            # The database write inside is one INSERT; the in-memory deadline is
            # set first, so the gates close whether or not it succeeds.
            state = await asyncio.to_thread(station.pause.start, body.preset, actor=actor)
        except PauseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # Tell the live UI at once rather than at the next 2 s status frame: the
        # operator has just pressed a privacy control and every open browser
        # should show it on, immediately.
        station.bus.emit(
            EventType.HEALTH_EVENT,
            {
                "service": "station",
                "severity": "info",
                "event_type": "pause.started",
                "detail": state,
            },
            station_id=station.station_id,
        )
        log.warning("pause.requested", preset=body.preset, actor=actor)
        return _pause_payload()

    @app.delete(f"{API_PREFIX}/pause")
    async def delete_pause(request: Request) -> dict[str, Any]:
        """Resume now. Idempotent: resuming a station that is not paused is fine.

        One click, by requirement. There is no confirmation step: the failure
        mode of resuming a second too early is a detection nobody wanted, and
        the failure mode of a fiddly resume is an operator who leaves the pause
        running all night.
        """
        principal = getattr(request.state, "principal", None)
        actor = getattr(principal, "username", None) or "operator"
        state = await asyncio.to_thread(station.pause.resume, actor=actor)
        station.bus.emit(
            EventType.HEALTH_EVENT,
            {
                "service": "station",
                "severity": "info",
                "event_type": "pause.ended",
                "detail": state,
            },
            station_id=station.station_id,
        )
        return _pause_payload()

    def _refuse_if_paused(channel: str) -> str:
        """The message a live listener is given while the station is paused.

        Empty when it may listen. Live listening is refused because a pause
        anyone with the URL can listen straight through is not a pause -- the
        garden is exactly as audible to a browser as it was before, and the
        operator has been told otherwise.
        """
        if not station.pause.active:
            return ""
        banner = station.pause.banner(settings.timezone)
        log.info("live_audio.refused_paused", channel=channel)
        return banner or "live listening is unavailable while the station is paused"

    def _health_payload() -> dict[str, Any]:
        """Shared by GET /health and the MQTT publisher's periodic health sensor,
        so `binary_sensor.<station>_station_healthy` in Home Assistant means
        exactly what the API endpoint means -- including synthetic-source
        degradation -- rather than a second, drifting notion of "healthy"."""
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
        retention = snapshot["retention"]
        storage = snapshot["storage"]
        if (
            settings.retention_enabled
            and storage["disk_used_ratio"] is not None
            and storage["disk_used_ratio"] > settings.retention_watermark_ratio
            and not retention["last_sweep_complete"]
        ):
            problems.append(
                f"disk usage {storage['disk_used_ratio']:.0%} exceeds the "
                f"{settings.retention_watermark_ratio:.0%} watermark and the retention "
                "sweep is not keeping up"
            )
        # ADR-061: the watermark tier's one deliberate refusal. It will not
        # delete a `kept` recording to get under the watermark -- that is the
        # design, not a bug -- so if disk is still over the line with kept
        # bytes sitting on it, silence here would look exactly like a station
        # quietly handling it. It is not: unless an operator frees space (or
        # clears a keep) by hand, the disk fills and clip writes start
        # failing. Capture itself is unaffected either way.
        watermark_blocked_by_kept = int(retention.get("watermark_blocked_by_kept") or 0)
        if (
            settings.retention_enabled
            and storage["disk_used_ratio"] is not None
            and storage["disk_used_ratio"] > settings.retention_watermark_ratio
            and watermark_blocked_by_kept > 0
        ):
            problems.append(
                f"disk usage {storage['disk_used_ratio']:.0%} exceeds the "
                f"{settings.retention_watermark_ratio:.0%} watermark and "
                f"{watermark_blocked_by_kept:,} bytes of it are operator-kept recordings "
                "the retention sweep will not delete: free space by hand or the disk "
                "will fill and clip writes will start failing"
            )
        # ADR-034: visible rather than silent. `auth_enabled: false` is the
        # shipped default and is never itself a `problems` entry -- an
        # operator who has not opted in should see a status of exactly what
        # they always saw, not a station that reports itself degraded out of
        # the box. An operator who *has* opted in but is locked out (no
        # active account reachable at all, e.g. every user disabled) is a
        # real misconfiguration and is surfaced as one.
        auth_info: dict[str, Any] = {"enabled": settings.auth_enabled}
        if settings.auth_enabled:
            with session_scope() as auth_session:
                active_users = auth_service.active_user_count(auth_session)
            auth_info["active_users"] = active_users
            if active_users == 0:
                problems.append(
                    "auth_enabled is true but no active user accounts exist; the "
                    "station is locked out of its own API. Restart with "
                    "OO_AUTH_ENABLED=false to regain access, or restore a user row."
                )

        # Notes are honest disclosures that do not make the station unhealthy.
        # An unset location is a correct first-run state -- but it must be
        # said, because its silent consequence (no plausibility filtering, no
        # night scheduling) is exactly the kind of omission the charter's
        # honesty constraint forbids.
        notes: list[str] = []
        if settings.latitude is None or settings.longitude is None:
            notes.append(
                "no station location configured: BirdNET runs without range-based "
                "plausibility filtering and the ultrasonic night schedule stays "
                "always-on. Set coordinates in the web UI settings page."
            )
        # First-run guidance, not a fault. UTC is the shipped default because
        # it is the only zone that is not somebody's local assumption, but a
        # station still on it is showing an operator times that are probably
        # not theirs, and saying nothing about that is the same omission the
        # unset-location note exists to avoid. Silenced once the operator has
        # been through (or dismissed) the guided flow, so an operator who
        # genuinely wants UTC is not nagged forever.
        if not settings.setup_completed and settings.timezone == "UTC":
            notes.append(
                "timezone is still UTC (the shipped default): times in the UI and on "
                "the counter-top display are UTC, not local. Set your IANA zone in the "
                "web UI settings page."
            )
        # ADR-057. A note, not a `problems` entry: the station is capturing,
        # detecting and serving correctly -- what is wrong is its *account* of
        # what evidence it holds, and degrading the health signal (and with it
        # `binary_sensor.<station>_station_healthy` in Home Assistant) for a
        # bookkeeping error would train an operator to ignore it. It is still
        # said out loud, because "8,067 clips" meaning "8,067 rows, 16.5% of
        # which have no file" is exactly the honesty failure the charter names:
        # a number shown to a human must mean what its label says.
        missing_audit = retention.get("missing_audit") or {}
        known_missing = int(retention.get("known_missing") or 0)
        if known_missing:
            exact = bool(missing_audit.get("passes_completed"))
            scope = (
                f"{known_missing} of {missing_audit.get('last_pass_scanned')} evidence rows"
                if exact
                else f"{known_missing} evidence rows (audit still on its first pass)"
            )
            notes.append(
                f"{scope} claim a clip file that is not on disk: storage figures "
                "over-report by that much and the play button on those detections "
                "returns 410. Run `oo clips reconcile-missing` to see them and "
                "`--apply` to mark them reclaimed. No clip is deleted either way."
            )
        pending = station.site_pending_restart()
        if pending:
            notes.append(
                f"settings saved but not yet in force (restart required): {', '.join(pending)}"
            )
        # ADR-055. A note, never a `problems` entry: an operator pause is a
        # deliberate act, not a fault, and putting it in `problems` would flip
        # `binary_sensor.<station>_station_healthy` in Home Assistant and make
        # every alerting rule in the house treat a birthday party as an
        # outage. It is still said loudly -- a station reporting `status: ok`
        # while recording nothing would be exactly the kind of quiet omission
        # the honesty constraint forbids.
        pause_state = station.pause.snapshot()
        pause_state["banner"] = station.pause.banner(settings.timezone)
        if pause_state["active"]:
            notes.append(
                "paused by the operator until "
                f"{pause_state['ends_utc']}: detections, evidence clips, "
                "publishing and live listening are suppressed. Capture itself is "
                "still running, so the device stays open (ADR-055)."
            )

        # A station that has heard nothing for a sustained period is critical
        # whatever `capture.state` claims. `state` is what the capture task was
        # asked to do; silence is what it achieved, and only one of those is an
        # observation. See HANDOVER §1e.
        deaf_for = capture["block_age_s"]
        deaf = deaf_for is not None and deaf_for > settings.capture_silence_critical_s
        status = (
            "ok"
            if not problems
            else ("degraded" if capture["state"] == "capturing" and not deaf else "critical")
        )
        return {
            "status": status,
            "problems": problems,
            "notes": notes,
            "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "capture": capture,
            #: Read by `display_channel.health_state`, which is what puts the
            #: pause banner on the counter-top display.
            "pause": pause_state,
            "storage": {
                "disk_used_ratio": storage["disk_used_ratio"],
                "watermark_ratio": settings.retention_watermark_ratio,
                "retention_sweep_keeping_up": retention["last_sweep_complete"],
                "retention_last_sweep_at": retention["last_sweep_at"],
                # ADR-057. Reported next to the disk figures because that is
                # what it corrects: rows counted as stored evidence that are
                # not on the disk this ratio measures.
                "rows_claiming_missing_files": known_missing,
                "missing_file_audit": missing_audit,
            },
            "mqtt": mqtt_publisher.snapshot(),
            "auth": auth_info,
        }

    @app.get(f"{API_PREFIX}/health")
    def get_health() -> JSONResponse:
        payload = _health_payload()
        return JSONResponse(payload, status_code=200 if payload["status"] != "critical" else 503)

    @app.get(f"{API_PREFIX}/retention/status")
    def get_retention_status(session: Session = Depends(get_session)) -> dict[str, Any]:
        """What the tiered retention policy (ADR-026) is holding, per tier.

        Counted in SQL from `media_asset`, **not** by walking the clip tree.
        A census of the ~17k assets on the SSD would be sustained disk I/O on
        every poll of this panel, and sustained disk I/O contending with the
        ALSA read is the documented cause of two separate capture-overrun
        incidents in this project. `created_at` is indexed and `byte_length`
        is already stored, so the aggregate below costs nothing capture cares
        about.

        Rows with `reclaimed_at` set are excluded: the clip is gone, the
        detection row deliberately survives it, and counting it here would
        report bytes that are no longer on disk.

        **`reclaimed_at IS NULL` is a claim, not a measurement (ADR-057).**
        On the live station 8,067 rows (16.5%, 20.59 GB) passed that filter
        while naming files that had been unlinked without their rows being
        marked, so every figure below was over-reported by that much and
        `eligible_for_deletion` promised space no deletion could recover. The
        fix is to reconcile those rows (`oo clips reconcile-missing`), and
        this endpoint's job is to stop the over-report being invisible in the
        meantime: `missing_files` carries what the rolling audit knows, and
        `eligible_for_deletion.bytes_verified_present` is the reclaimable
        figure with the known-missing bytes taken out. Both are labelled by
        how they were arrived at, because a sampled bound and a census are
        different claims.
        """
        sweeper = station.retention
        now = datetime.now(UTC)

        def bucket(older_than_days: float | None, younger_than_days: float) -> dict[str, int]:
            stmt = select(
                func.count(orm.MediaAsset.id), func.coalesce(func.sum(orm.MediaAsset.byte_length), 0)
            ).where(
                orm.MediaAsset.reclaimed_at.is_(None),
                orm.MediaAsset.created_at > now - timedelta(days=younger_than_days),
            )
            if older_than_days is not None:
                stmt = stmt.where(orm.MediaAsset.created_at <= now - timedelta(days=older_than_days))
            clips, byte_total = session.execute(stmt).one()
            return {"clips": int(clips), "bytes": int(byte_total)}

        native_d = sweeper.native_days
        audible_d = sweeper.audible_only_days
        # ADR-061: past `audible_d`, nothing distinguishes further age bands --
        # only a `kept` detection's clip survives, and it survives forever, not
        # to some later cutoff. What used to be a separate "kept only" tier
        # (a fixed 30-90 day band, back when a 90-day tier deleted anything not
        # kept) is therefore this same overdue bucket, not a distinct one.
        overdue = select(
            func.count(orm.MediaAsset.id), func.coalesce(func.sum(orm.MediaAsset.byte_length), 0)
        ).where(
            orm.MediaAsset.reclaimed_at.is_(None),
            orm.MediaAsset.created_at <= now - timedelta(days=audible_d),
        )
        overdue_clips, overdue_bytes = session.execute(overdue).one()

        return {
            "tiers": [
                {"name": "native + audible", "age_days_max": native_d, **bucket(None, native_d)},
                {"name": "audible only", "age_days_max": audible_d, **bucket(native_d, audible_d)},
            ],
            # Past the last tier and not yet reclaimed. Non-zero here is normal
            # between sweeps, not a fault; persistently non-zero means the sweep
            # is not keeping up, which `/health` reports by name.
            "eligible_for_deletion": {
                "clips": int(overdue_clips),
                "bytes": int(overdue_bytes),
                # ADR-057: what deleting all of that would actually free.
                # `bytes` counts what the rows claim; this takes out the bytes
                # the audit has confirmed are not on disk. Clamped at zero
                # because the known-missing figure spans every age band, not
                # just the overdue one, so it can exceed this bucket -- and a
                # negative "space you will get back" would be a worse lie than
                # the one being corrected.
                "bytes_verified_present": max(
                    0, int(overdue_bytes) - sweeper.known_missing_bytes
                ),
            },
            # ADR-057. Present unconditionally, including as all-zeroes, so a
            # consumer can tell "checked, nothing missing" from "this station
            # does not report it".
            "missing_files": {
                **sweeper.audit_snapshot(),
                "clips": sweeper.known_missing,
                "bytes": sweeper.known_missing_bytes,
                # False until a pass has completed: before that the figures
                # are a partial sample and a floor, not a count of the table.
                "exact": bool(sweeper.audit_passes),
            },
            "disk_reclaim_threshold": sweeper.watermark_ratio,
            # ADR-061: 0 unless the last sweep found disk over the watermark
            # with nothing left to reclaim but kept evidence -- see
            # `RetentionSweeper._watermark_reclaim` and `/health`, which
            # escalates this into a named problem.
            "watermark_blocked_by_kept": sweeper.last_watermark_blocked_by_kept,
            # ADR-061: how long the last sweep spent before its first tier
            # guard, and which tier guards (if any) it never reached. A
            # sweep that reaches every guard but deletes nothing is healthy;
            # one whose preamble alone eats the budget looks identical in
            # every other figure on this page, which is exactly how the
            # original nine-day failure hid.
            "last_preamble_s": sweeper.last_preamble_s,
            "last_tiers_skipped": list(sweeper.last_tiers_skipped),
            "last_run_utc": sweeper.last_sweep_at.isoformat() if sweeper.last_sweep_at else None,
            # The sweeper deletes for real when enabled; `--dry-run` is a CLI
            # affordance, never a server mode, so this is always False here.
            "dry_run": False,
            "enabled": settings.retention_enabled,
        }

    @app.get(f"{API_PREFIX}/system")
    def get_system() -> dict[str, Any]:
        return {"host": system_report(), "process": exporter.process_stats()}

    @app.get("/metrics", response_class=PlainTextResponse)
    def get_metrics() -> Response:
        if not settings.metrics_enabled:
            raise HTTPException(status_code=404, detail="metrics disabled")
        body, content_type = exporter.render()
        mqtt_body, _ = mqtt_publisher.render_metrics()
        auth_body, _ = auth_service.metrics.render()
        return Response(content=body + b"\n" + mqtt_body + b"\n" + auth_body, media_type=content_type)

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

    @app.get(f"{API_PREFIX}/detectors/near-misses")
    def list_near_misses(
        limit: int = Query(50, ge=0, le=500),
        species_limit: int = Query(40, ge=0, le=500),
    ) -> dict[str, Any]:
        """ADR-052. What the detectors proposed and then refused.

        The counters under `GET /api/v1/detectors` say how many candidates
        were suppressed. They cannot say *which*, at what score, against which
        bar -- so an operator watching 152 suppressions in an hour has no way
        to tell 152 correct rejections of North American owls from 152
        wrongly-binned garden birds, and cannot choose a threshold on
        evidence. This is that evidence.

        Duck-typed on `near_miss_snapshot`, matching `plausibility_snapshot`
        in `api/metrics.py`: a detector with no plausibility bands simply does
        not appear, rather than being made to carry an empty stub.

        Metadata only. No audio is retained for a rejected candidate and
        nothing here is persisted -- it is an in-memory diagnostic that dies
        with the process (the charter's privacy constraint, and ADR-049's
        decision not to write clips for human sound, are both untouched).
        """
        detectors = []
        for worker in station.workers:
            snapshot_fn = getattr(worker.plugin, "near_miss_snapshot", None)
            if not callable(snapshot_fn):
                continue
            payload = snapshot_fn(limit=limit, species_limit=species_limit)
            payload["plugin_id"] = worker.plugin_id
            detectors.append(payload)
        return {"detectors": detectors}

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
        review_map = review_queries.latest_reviews_by_detection(
            session, (detection.id for detection, _ in rows)
        )
        return {
            "detections": [
                _detection_payload(
                    detection, source_kind=source_kind, review=review_map.get(detection.id)
                )
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
            # Same pre-existing InstrumentedAttribute/ColumnElement mypy gap as
            # list_detections above -- cast rather than adding a new instance
            # of it, since this call site is new.
            query = query.where(history_queries.is_live(cast(Any, orm.AudioStream.source_kind)))
        query = query.order_by(orm.Detection.event_start_utc.desc()).limit(limit)

        rows = session.execute(query).all()
        review_map = review_queries.latest_reviews_by_detection(
            session, (detection.id for detection, _ in rows)
        )
        records = [
            _detection_payload(detection, source_kind=source_kind, review=review_map.get(detection.id))
            for detection, source_kind in rows
        ]
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
            # A human correction never overwrites the columns above -- the
            # original claim stays exactly as the detector wrote it. These
            # four are the annotation layered on top (ADR-043): empty unless
            # a reviewer corrected this row.
            "identification_source",
            "effective_common_name",
            "effective_scientific_name",
            "review_status",
            "reviewed_by",
            "reviewed_at",
            "score",
            "calibrated_probability",
            # A CSV opened in a spreadsheet is exactly the surface where a
            # withdrawn owl would be read as an observation and then cited, so
            # the marker travels with the export (ADR-044) rather than being a
            # property only the JSON API knows about.
            "withdrawn",
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
                review = record.get("review") or {}
                writer.writerow(
                    {
                        **record,
                        "detector_plugin_id": record["detector"]["plugin_id"],
                        "detector_calibrated": record["detector"]["calibrated"],
                        "review_status": review.get("status", ""),
                        "reviewed_by": review.get("actor", ""),
                        "reviewed_at": review.get("created_at", ""),
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
        review = review_queries.latest_review(session, detection.id)
        return _detection_payload(
            detection, include_native=True, source_kind=source_kind, review=review
        )

    @app.post(f"{API_PREFIX}/detections/{{detection_id}}/review")
    def post_detection_review(
        detection_id: str,
        body: ReviewIn,
        session: Session = Depends(get_session),
        principal: Principal | None = Depends(get_optional_principal),
    ) -> dict[str, Any]:
        """Record a human review of one detection (ADR-043). Append-only,
        matching `orm.Review`'s own docstring ("current status is derived
        from the latest valid review") — this always inserts a new row
        rather than mutating one, so the review history for a detection is
        never lost, only superseded. `Detection.common_name` /
        `scientific_name` etc. are never touched: the original machine
        claim stays visible and attributable, and a correction is a new,
        clearly-attributed row layered on top of it, not an edit to it.

        `actor` is the logged-in operator's username when auth is enabled
        and a session/token was presented, else `"local"` — same anonymous
        default the rest of this debug-slice API uses when auth is off
        (ADR-034).
        """
        detection = session.get(orm.Detection, _uuid(detection_id))
        if detection is None:
            raise HTTPException(status_code=404, detail="detection not found")
        previous = review_queries.latest_review(session, detection.id)

        corrected_common_name: str | None = None
        corrected_scientific_name: str | None = None
        if body.status == review_queries.CORRECTED_STATUS:
            target = review_queries.resolve_taxon(session, cast(str, body.corrected_taxon_id))
            if target is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"unknown taxon_id {body.corrected_taxon_id!r}: this station has "
                        f"never identified it itself. Search {API_PREFIX}/taxa/search for "
                        "a valid taxon_id."
                    ),
                )
            corrected_common_name = target.common_name
            corrected_scientific_name = target.scientific_name

        review = orm.Review(
            detection_id=detection.id,
            actor=principal.username if principal else "local",
            status=body.status,
            note=body.note or "",
            corrected_taxon_id=(
                body.corrected_taxon_id if body.status == review_queries.CORRECTED_STATUS else None
            ),
            corrected_common_name=corrected_common_name,
            corrected_scientific_name=corrected_scientific_name,
            supersedes_review_id=previous.id if previous else None,
        )
        session.add(review)
        session.commit()
        return _review_payload(review)

    @app.get(f"{API_PREFIX}/detections/{{detection_id}}/review")
    def get_detection_review(
        detection_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """The latest review for a detection, or `null` if none exists yet."""
        latest = review_queries.latest_review(session, _uuid(detection_id))
        if latest is None:
            return {"review": None}
        return {"review": _review_payload(latest)}

    @app.put(f"{API_PREFIX}/detections/{{detection_id}}/keep")
    def keep_detection(
        detection_id: str,
        session: Session = Depends(get_session),
        principal: Principal | None = Depends(get_optional_principal),
    ) -> dict[str, Any]:
        """Mark a detection kept forever (ADR-061): Tasks 1-4 already made
        `kept_at IS NOT NULL` exempt on every retention tier and the
        watermark, so this route is the entire remaining gap between "the
        mechanism exists" and "an operator can use it". Idempotent --
        re-keeping an already-kept detection just refreshes `kept_at`/
        `kept_by` to the latest actor and time, matching the review
        endpoints' append-is-cheap posture rather than rejecting a repeat
        click.

        `kept_by` follows the same actor rule `post_detection_review` uses
        (ADR-043): the logged-in operator's username when auth is enabled
        and a session/token was presented, else the fixed string
        `"operator"` -- distinct from review's `"local"` because this is an
        explicit, named operator action rather than the anonymous
        debug-slice default.
        """
        detection = session.get(orm.Detection, _uuid(detection_id))
        if detection is None:
            raise HTTPException(status_code=404, detail="detection not found")
        detection.kept_at = datetime.now(UTC)
        detection.kept_by = principal.username if principal else "operator"
        session.commit()
        session.refresh(detection)
        return _detection_payload(detection)

    @app.delete(f"{API_PREFIX}/detections/{{detection_id}}/keep")
    def unkeep_detection(
        detection_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Clear the keep flag. Age, the 90-day expiry and disk pressure never
        clear it (ADR-061) -- only this, an explicit human action."""
        detection = session.get(orm.Detection, _uuid(detection_id))
        if detection is None:
            raise HTTPException(status_code=404, detail="detection not found")
        detection.kept_at = None
        detection.kept_by = None
        session.commit()
        session.refresh(detection)
        return _detection_payload(detection)

    @app.get(f"{API_PREFIX}/taxa/search")
    def search_taxa(
        q: str = Query(..., min_length=1, max_length=120),
        limit: int = Query(20, ge=1, le=100),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        """Taxon lookup for the review drawer's correction control (ADR-043).

        Matches `q` against common or scientific name, case-insensitively,
        among species this station has *itself* already identified — see
        `review.search_taxa`'s docstring for why this deliberately does not
        reach for a bundled or fetched taxonomy database.
        """
        return {"taxa": review_queries.search_taxa(session, q, limit=limit), "source": "station_history"}

    @app.get(f"{API_PREFIX}/taxa/activity")
    def taxa_activity(
        hours: int = Query(24, ge=1, le=168),
        include_synthetic: bool = False,
        include_withdrawn: bool = False,
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

        # Same reasoning as history.species_summary (ADR-044): this groups by
        # species and has no row to mark, so a withdrawn claim would come back
        # as an ordinary "seen in the last 24 h" entry.
        excluded_withdrawn = 0
        if not include_withdrawn:
            excluded_withdrawn = (
                session.execute(
                    select(func.count(orm.Detection.id))
                    .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
                    .where(
                        orm.Detection.event_start_utc >= since,
                        history_queries.is_withdrawn(),
                    )
                ).scalar_one()
                or 0
            )
            query = query.where(history_queries.is_not_withdrawn())

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
            "include_withdrawn": include_withdrawn,
            "excluded_withdrawn_count": excluded_withdrawn,
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
            # Withdrawn rows (ADR-044) are excluded from `species`/`unidentified`
            # -- an aggregate that names a species has nowhere to put a marker --
            # but *not* from `timeline`, which counts detections rather than
            # naming anything. Reported here so the exclusion is never silent.
            "excluded_withdrawn_count": history_queries.excluded_withdrawn_count(
                session, resolved, min_score=min_score
            ),
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
        """The named windows the UI offers, resolved now for this station.

        Deliberately not the whole grammar `history.resolve_range` understands
        (ADR-056 added relative and calendar ranges: `last-30d`, `2026-07`,
        `2026-W32`, …). This list is what the dashboard puts on screen, and a
        window is only offered once the station can answer it quickly enough to
        be worth tapping.
        """
        names = (
            "last-hour", "last-night", "dawn-chorus", "today", "yesterday", "last-24h", "last-7d",
        )
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

    def _ws_principal(socket: WebSocket) -> Principal | None:
        """Cookie or bearer token off a WebSocket handshake. A browser sends
        its cookies on the upgrade request automatically (same-origin, like
        any other same-origin fetch), so a logged-in tab needs no code of
        its own to carry the session onto these sockets."""
        with session_scope() as session:
            cookie = socket.cookies.get(settings.auth_session_cookie_name)
            if cookie:
                principal = auth_service.resolve_session(session, cookie)
                if principal is not None:
                    return principal
            header = socket.headers.get("authorization", "")
            if header.lower().startswith("bearer "):
                return auth_service.resolve_api_token(session, header[7:].strip())
        return None

    @app.websocket(f"{API_PREFIX}/live")
    async def live_socket(socket: WebSocket) -> None:
        await socket.accept()
        if settings.auth_enabled and _ws_principal(socket) is None:
            log.info("auth.ws_rejected", path=API_PREFIX + "/live")
            await socket.send_json({"type": "error", "detail": "authentication required"})
            await socket.close(code=4401)
            return
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
                    "spectrograms": station.describe_spectrograms(),
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
        if settings.auth_enabled and _ws_principal(socket) is None:
            log.info("auth.ws_rejected", path=API_PREFIX + "/live/audio")
            await socket.send_json({"type": "error", "detail": "authentication required"})
            await socket.close(code=4401)
            return
        # ADR-055, before a listener is attached. Refused rather than served
        # silence: a socket that connects and then plays nothing is
        # indistinguishable from a broken station, and the operator needs to be
        # able to tell those apart. The `available: false` shape is the one
        # this channel already uses for "there is nothing here to stream",
        # so the existing client renders the reason without a change.
        refusal = _refuse_if_paused(channel)
        if refusal:
            await socket.send_json(
                {
                    "type": "audio-hello",
                    "channel": channel,
                    "available": False,
                    "paused": True,
                    "reason": refusal,
                    "sample_rate": station.live_audio.sample_rate,
                    "chunk_frames": station.live_audio.chunk_frames,
                    "chunk_ms": station.live_audio.chunk_ms,
                    "encoding": "pcm_s16le_mono",
                }
            )
            return

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
                try:
                    payload = await asyncio.wait_for(listener.queue.get(), timeout=1.0)
                except TimeoutError:
                    # A pause that begins while somebody is already listening
                    # has to end their stream too, or "paused" means "paused
                    # for people who were not quick enough". The station stops
                    # publishing at the same instant (`_handle_block`), so this
                    # loop simply stops receiving; the timeout is what turns
                    # that silence into a decision instead of a hang.
                    if station.pause.active:
                        return
                    continue
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
        # ADR-055. 503 with the pause's own wording: `audio.ts` reads `detail`
        # off a non-OK probe and shows it on the listen control, so the operator
        # is told why rather than left with a silent player. Refused before any
        # listener is attached, so a paused station cannot be made to run the
        # heterodyne for a listener it is not going to serve.
        refusal = _refuse_if_paused(channel)
        if refusal:
            raise HTTPException(status_code=503, detail=refusal)

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
                    # A pause that starts mid-stream ends this response too.
                    # The station has already stopped publishing, so without
                    # this the listener would sit on an open, silent connection
                    # -- which is exactly what a broken station looks like.
                    if station.pause.active:
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

    # -- inside-observer push channel (ADR-038) --------------------------

    #: `_health_payload()` costs a full `status_snapshot()` (~18 ms measured on
    #: the Pi). One display asking for it every heartbeat would be cheap; several
    #: asking independently would not, and neither would a future one that
    #: reconnects in a loop. Cached briefly and shared, so the cost is bounded by
    #: the clock rather than by the number of clients.
    display_health_cache: dict[str, Any] = {"at": 0.0, "value": ("L", "")}

    def _display_health() -> tuple[str, str]:
        now = time.monotonic()
        if now - display_health_cache["at"] > 5.0:
            display_health_cache["value"] = display_state.health_state(_health_payload())
            display_health_cache["at"] = now
        value = display_health_cache["value"]
        assert isinstance(value, tuple)
        return value

    def _display_day_key(moment: datetime | None = None) -> str:
        """Today, in the station's configured zone. UTC internally; local only
        for the one presentation decision this channel makes, which is where a
        day ends -- the display has no timezone database of its own."""
        moment = moment or datetime.now(UTC)
        return moment.astimezone(ZoneInfo(settings.timezone)).date().isoformat()

    def _display_snapshot(filt: display_state.DisplayFilter) -> tuple[list[dict[str, Any]], set[str]]:
        """The rows a display needs on connect, plus today's species names.

        Two small, column-limited queries, run once per connection rather than
        every 20 s. Deliberately does not go through `_detection_payload`: that
        builds the ~1.8 kB record (media checksums, detector metadata, UUIDs)
        this whole channel exists to stop sending.
        """
        # A dominant species can fill the feed, so read a multiple of the rows
        # and collapse. Bounded so a pathological threshold cannot turn the
        # connect handshake into a large query.
        fetch = min(200, max(filt.rows * 12, filt.rows))
        groups = ["bird", "bat"] if filt.show_bats else ["bird"]
        with session_scope() as session:
            query = (
                select(
                    orm.Detection.event_start_utc,
                    orm.Detection.common_name,
                    orm.Detection.scientific_name,
                    orm.Detection.taxonomic_group,
                    orm.Detection.score,
                    orm.Detection.peak_frequency_hz,
                )
                .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
                .where(orm.Detection.taxonomic_group.in_(groups))
                .where(history_queries.is_live(orm.AudioStream.source_kind))
                # ADR-044. Filtered in SQL rather than by fetching
                # `native_result` and testing it in Python: this query's narrow
                # column list is the whole point of ADR-038's connect snapshot,
                # and the ~1.8 kB blob is exactly what it exists to not read.
                .where(history_queries.is_not_withdrawn())
                .where(
                    (orm.Detection.taxonomic_group == display_state.BAT_GROUP)
                    | (orm.Detection.score >= filt.min_score)
                )
                .order_by(orm.Detection.event_start_utc.desc())
                .limit(fetch)
            )
            rows = session.execute(query).all()

            today = history_queries.resolve_named_range("today", settings.timezone)
            species_rows = session.execute(
                select(orm.Detection.scientific_name, orm.Detection.common_name)
                .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
                .where(orm.Detection.event_start_utc >= today.start)
                .where(orm.Detection.event_start_utc < today.end)
                .where(orm.Detection.taxonomic_group == "bird")
                .where(orm.Detection.score >= filt.min_score)
                .where(history_queries.is_live(orm.AudioStream.source_kind))
                # The footer count has to agree with the feed above it.
                .where(history_queries.is_not_withdrawn())
                .distinct()
            ).all()

        items = [
            item
            for item in (
                display_state.wire_item(
                    {
                        "event_start_utc": row.event_start_utc,
                        "common_name": row.common_name,
                        "scientific_name": row.scientific_name,
                        "taxonomic_group": row.taxonomic_group,
                        "score": row.score,
                        "peak_frequency_hz": row.peak_frequency_hz,
                    },
                    filt,
                )
                for row in rows
            )
            if item is not None
        ]
        names = {
            str(row.scientific_name)
            for row in species_rows
            if display_state.is_taxonomic_name(row.scientific_name, row.common_name)
        }
        return display_state.collapse_runs(items, filt.rows), names

    @app.websocket(f"{API_PREFIX}/display")
    async def display_socket(
        socket: WebSocket,
        min_score: float = Query(0.75, ge=0.0, le=1.0),
        bats: bool = Query(True),
        rows: int = Query(0, ge=0, le=12),
        #: The firmware version on the glass (ADR-050). Absent from builds
        #: before that, which have no update path; reported as unknown rather
        #: than assumed to be old.
        fw: str = Query("", max_length=15),
    ) -> None:
        """The inside observer's feed: detections only, a few dozen bytes each.

        Deliberately *not* a mode of `/api/v1/live`. That socket's whole
        vocabulary is wrong for this client -- binary spectrogram columns an
        ESP32 cannot use, a hello frame carrying forty full detection records and
        sixty events, a 2 s full-station status -- so "filtering" it would mean
        replacing every frame it sends while still paying ADR-012's warning that
        any change to that channel must be re-measured from a real browser over
        real Wi-Fi. A separate endpoint leaves the debug UI's transport untouched
        and lets this one be exactly as small as it needs to be.

        ADR-012's single-writer rule holds here as it does there: `client.run()`
        is the only code that writes this socket. The pump and the receive loop
        never touch it.
        """
        await socket.accept()
        # Same allow-list the display's HTTP poll already uses: an ESP32 with no
        # keyboard cannot log in, and ADR-034's public-read exemption exists for
        # exactly this client. Not exempt by default is not an option that leaves
        # the display working.
        if (
            settings.auth_enabled
            and f"{API_PREFIX}/display" not in settings.auth_public_read_paths
            and _ws_principal(socket) is None
        ):
            log.info("auth.ws_rejected", path=API_PREFIX + "/display")
            await socket.close(code=4401)
            return

        filt = display_state.DisplayFilter(
            min_score=min_score,
            show_bats=bats,
            rows=rows or settings.display_channel_snapshot_rows,
        )
        heartbeat_s = max(1, int(settings.display_channel_heartbeat_s))
        client = display_state.DisplayClient(
            socket,
            maxsize=settings.display_channel_queue_max,
            firmware_version=fw or None,
        )
        display_clients.add(client)
        # Only detections. Subscribing to the whole bus would queue capture.levels
        # at ~1 Hz and every window event for a client that renders none of them.
        subscription = station.bus.subscribe(
            [EventType.DETECTION_CREATED], maxsize=128, label="display-ws"
        )
        writer = asyncio.create_task(client.run(), name="display-writer")
        pump: asyncio.Task[None] | None = None
        try:
            # One short DB read, off the event loop. Once per connection, not
            # once per 20 s -- a display connects and then stays connected for
            # days, so this is the only query it ever costs the station.
            items, names = await asyncio.to_thread(_display_snapshot, filt)
            tracker = display_state.SpeciesToday(day_key=_display_day_key(), names=names)
            state, detail = _display_health()
            client.offer(
                display_state.hello_frame(
                    now=int(time.time()),
                    state=state,
                    detail=detail,
                    species_today=tracker.count,
                    items=items,
                    heartbeat_s=heartbeat_s,
                )
            )
            log.info(
                "display_channel.connected",
                rows=len(items),
                species_today=tracker.count,
                min_score=filt.min_score,
                bats=filt.show_bats,
                firmware_version=client.firmware_version,
            )
            # The version check on connect (ADR-050). Queued after the hello so
            # the screen is populated before anything asks it to go blank, and
            # only when the display named a version older than the published
            # one -- there is no frame at all in the ordinary case.
            if settings.display_ota_offer_on_connect and _offer_firmware_to(client):
                log.info(
                    "firmware.offered_on_connect",
                    display_version=client.firmware_version,
                )
            pump = asyncio.create_task(
                _pump_display(client, subscription, filt, tracker, heartbeat_s),
                name="display-pump",
            )
            while True:
                # Nothing the display sends is acted on; reading is how a
                # disconnect is noticed promptly.
                await socket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("display_socket.error")
        finally:
            log.info("display_channel.disconnected", **client.stats())
            display_clients.discard(client)
            client.close()
            subscription.close()
            for task in (pump, writer):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

    async def _pump_display(
        client: display_state.DisplayClient,
        subscription: Any,
        filt: display_state.DisplayFilter,
        tracker: display_state.SpeciesToday,
        heartbeat_s: int,
    ) -> None:
        """Turn detection events into wire frames, and keep the heartbeat going.

        Never writes the socket -- it only ever calls `client.offer`, which is
        synchronous, bounded and non-blocking.
        """
        next_beat = time.monotonic() + heartbeat_s
        last_sent_count = tracker.count
        while not client.closed:
            timeout = max(0.05, next_beat - time.monotonic())
            try:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=timeout)
            except TimeoutError:
                event = None
            if event is not None:
                if event.get("event_type") == "_bus.closed":
                    return
                state, _ = _display_health()
                detection = event.get("data") or {}
                # ADR-020: while the station is not on the real microphone its
                # detections are records of a test scene, not observations of the
                # garden. The banner already says so; the feed must not quietly
                # keep filling with them.
                if state == "L":
                    item = display_state.wire_item(detection, filt)
                    if item is not None:
                        tracker.observe(_display_day_key(), detection)
                        moved = tracker.count if tracker.count != last_sent_count else None
                        last_sent_count = tracker.count
                        client.offer(display_state.detection_frame(item, species_today=moved))
            if time.monotonic() >= next_beat:
                next_beat = time.monotonic() + heartbeat_s
                state, detail = _display_health()
                # Rolls the count over at local midnight even on a silent night.
                day = _display_day_key()
                if day != tracker.day_key:
                    tracker.day_key, tracker.names = day, set()
                last_sent_count = tracker.count
                client.offer(
                    display_state.status_frame(
                        now=int(time.time()),
                        state=state,
                        detail=detail,
                        species_today=tracker.count,
                    )
                )

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


def _review_payload(review: orm.Review) -> dict[str, Any]:
    """The full shape of one review, shared by the POST and GET review
    endpoints and by `_detection_payload`'s embedded summary -- one place
    defines what a client is told about a review."""
    return {
        "id": str(review.id),
        "detection_id": str(review.detection_id),
        "status": review.status,
        "note": review.note,
        "actor": review.actor,
        "corrected_taxon_id": review.corrected_taxon_id,
        "corrected_common_name": review.corrected_common_name,
        "corrected_scientific_name": review.corrected_scientific_name,
        "created_at": _iso(review.created_at),
    }


def _detection_payload(
    row: orm.Detection,
    *,
    include_native: bool = False,
    source_kind: str | None = None,
    review: orm.Review | None = None,
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
        #: ADR-044. The row is *kept* and answered with, not hidden: the charter's
        #: item 5 says the prior verdict stays visible and attributable, and a
        #: record the system got wrong is evidence about the system. What changes
        #: is that no client can now render it as a live claim by accident --
        #: `withdrawn` is a top-level boolean on every detection payload (present
        #: and false on the overwhelming majority), and `withdrawal` carries the
        #: reviewer's recomputed prior, threshold, reason and timestamp verbatim.
        "withdrawn": plausibility.is_withdrawn(row.native_result),
        "withdrawal": plausibility.withdrawal(row.native_result),
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
    # A human correction is the highest-quality information this system ever
    # holds about an event (ADR-043). `common_name`/`scientific_name` above
    # stay exactly as the detector wrote them -- the original claim is never
    # edited -- and a correction is surfaced alongside it, not instead of it:
    # `review` carries the full annotation (who, when, what changed, and to
    # what), and `identification_source`/`effective_common_name`/
    # `effective_scientific_name` are what a consumer that just wants "the
    # best available name" should read.
    is_corrected = review is not None and review.status == review_queries.CORRECTED_STATUS
    payload["review"] = _review_payload(review) if review is not None else None
    payload["identification_source"] = "human" if is_corrected else "model"
    payload["effective_common_name"] = (
        review.corrected_common_name if is_corrected and review is not None else row.common_name
    )
    payload["effective_scientific_name"] = (
        review.corrected_scientific_name
        if is_corrected and review is not None
        else row.scientific_name
    )
    #: ADR-061. `kept_at`/`kept_by` are the operator-facing half of the keep
    #: flag Tasks 1-4 built the retention exemption for -- present on every
    #: detection payload (usually both null) so the drawer, list and history
    #: views can all render current keep state without a second fetch.
    payload["kept_at"] = _iso(row.kept_at)
    payload["kept_by"] = row.kept_by
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
