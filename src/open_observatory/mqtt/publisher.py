"""The MQTT publisher runtime.

Subscribes to the existing in-process ``EventBus`` (ADR-009) and republishes
station state to MQTT with Home Assistant Discovery (ADR-025). Everything here
follows the same rules as every other consumer of the bus:

* **Capture always wins.** This module never touches the capture or detector
  path directly; it only reads from a bounded ``EventBus`` subscription, which
  already implements the project's drop-oldest-and-count policy
  (``events.py: Subscription.offer``). A slow or absent broker cannot apply
  back-pressure to capture because there is no path back from here to there.
* **No shared thread pool.** ``aiomqtt`` is a native asyncio client — network
  I/O happens on the event loop via the driver's own socket handling, not
  ``asyncio.to_thread``/``run_in_executor``. That sidesteps the trap the brief
  calls out explicitly: the default executor is shared with the ALSA blocking
  read (``alsa_source.py``), and sustained disk/network I/O routed through it
  can starve that read and cause overruns. If a future maintainer is tempted
  to swap in a synchronous MQTT client (e.g. paho directly), it MUST get its
  own executor, exactly like ``ClipManager``'s evidence writer
  (``station.py: self._evidence_executor``) and the deferred detector
  (``detectors/deferred.py``).
* **Graceful degradation.** A broker that is down, unreachable, or rejects the
  configured credentials must never stop capture or detection. Every failure
  in the connect/publish path is caught here, logged, counted, and retried
  with bounded exponential backoff. Nothing raises out of :meth:`start`.
* **Honesty rules survive the wire.** See ``discovery.py``'s module docstring
  and ``_handle_detection`` below for where BirdNET scores are kept
  uncalibrated and bat passes are kept species-free on their way out.

Why ``aiomqtt`` and not raw ``paho-mqtt``: this codebase is asyncio throughout
(FastAPI, the capture loop, every worker in ``station.py``), and ``aiomqtt``
wraps paho's C-derived core in a proper ``async with``/``async for`` API
instead of paho's callback/loop-thread model. Adding paho directly would mean
either running its network loop in its own thread (a second thing, besides
ALSA, needing careful isolation from the default executor) or hand-rolling an
asyncio bridge that ``aiomqtt`` already provides, tested, pinned to an exact
version like every other dependency in ``pyproject.toml``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import aiomqtt
import structlog
from prometheus_client import CollectorRegistry, Gauge, generate_latest

from .. import plausibility
from ..config import Settings
from ..events import EventBus, EventType
from .discovery import BAT_GROUP, BAT_PLUGIN_ID, TopicLayout, build_entities

log = structlog.get_logger(__name__)

#: Bus event types this publisher cares about. Anything else (window.*,
#: capture.levels, ...) would just be dropped unread and waste queue slots.
#:
#: Deliberately excludes a human review or taxon correction (ADR-043):
#: `api/app.py: post_detection_review` writes straight to the database and
#: raises no bus event at all, so there is nothing here to subscribe to even
#: if this set included it. This is a decision, not an oversight -- every
#: Home Assistant entity this publisher creates (`discovery.py`) models "what
#: the station is hearing right now"; a correction typically lands minutes to
#: days after the originating detection, about a clip a listener may already
#: be finished with, and re-publishing it as a fresh MQTT/HA event would
#: misrepresent a retrospective annotation as a new acoustic occurrence. A
#: reviewer's correction is still fully visible -- in the review drawer, in
#: `GET /api/v1/detections`, and in the CSV/JSON export -- just not on this
#: live bus. If a future need for it emerges (e.g. an HA automation that
#: reacts to corrections), the right shape is a distinct event type and
#: topic, not overloading the existing detection topic that already means
#: "this just happened".
_SUBSCRIBED_TYPES = frozenset(
    {
        EventType.DETECTION_CREATED,
        EventType.CAPTURE_STARTED,
        EventType.CAPTURE_STOPPED,
        EventType.HEALTH_EVENT,
    }
)

#: A client factory builds a fresh, not-yet-connected client each connection
#: attempt (aiomqtt clients are not meant to be reused after a failed
#: connect). Tests substitute a fake here to avoid any real network I/O.
ClientFactory = Callable[[], "aiomqtt.Client"]

HealthProvider = Callable[[], dict[str, Any]]
CaptureStatusProvider = Callable[[], dict[str, Any]]
StationIdProvider = Callable[[], str | None]


@dataclass
class MqttStats:
    enabled: bool = False
    connected: bool = False
    connect_attempts: int = 0
    connect_successes: int = 0
    published_total: int = 0
    publish_failures: int = 0
    #: Detections withheld because capture was not live at the time (ADR-020):
    #: MQTT/Home Assistant is a browsing surface like the debug UI's default
    #: views, so a synthetic or replay detection must not appear there either.
    suppressed_synthetic_total: int = 0
    #: Detections that named nothing, withheld from Home Assistant
    #: (settings.mqtt_publish_unidentified). Counted, not silent.
    suppressed_unidentified_total: int = 0
    #: Detections withheld because a plausibility review withdrew the claim
    #: (ADR-044). Expected to stay at zero on a healthy station -- see
    #: `_handle_detection` for why a non-zero value here is worth investigating.
    suppressed_withdrawn_total: int = 0
    last_error: str | None = None
    last_connected_utc: str | None = None
    last_disconnected_utc: str | None = None
    last_publish_utc: str | None = None
    #: Populated from the EventBus subscription's own counters once created.
    dropped_total: int = 0
    queued: int = 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class MqttPublisher:
    """Owns one MQTT connection (with reconnect) and everything published on it."""

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        station_id_provider: StationIdProvider,
        health_provider: HealthProvider,
        capture_status_provider: CaptureStatusProvider,
        client_factory: ClientFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self._station_id_provider = station_id_provider
        self._health_provider = health_provider
        self._capture_status_provider = capture_status_provider
        self._client_factory = client_factory or self._default_client_factory
        self._clock = clock

        self.stats = MqttStats(enabled=settings.mqtt_enabled)
        self._subscription: Any = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

        # -- rolling state for the derived entities --------------------------
        self._species_today: set[str] = set()
        self._species_day: str | None = None
        self._bat_passes_tonight = 0
        self._bat_night_key: str | None = None
        self._last_bat_pass_monotonic: float | None = None
        self._last_published_bat_activity: bool | None = None
        self._discovery_published = False

    # ------------------------------------------------------------------
    # lifecycle

    async def start(self) -> None:
        if not self.settings.mqtt_enabled:
            log.info("mqtt.disabled")
            return
        self._subscription = self.bus.subscribe(
            types=_SUBSCRIBED_TYPES, maxsize=self.settings.mqtt_queue_depth, label="mqtt"
        )
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="mqtt-publisher")
        log.info("mqtt.started", host=self.settings.mqtt_host, port=self.settings.mqtt_port)

    async def stop(self) -> None:
        self._stopping = True
        if self._subscription is not None:
            self._subscription.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.stats.connected = False

    # ------------------------------------------------------------------
    # snapshot / metrics

    def snapshot(self) -> dict[str, Any]:
        if self._subscription is not None:
            self.stats.dropped_total = self._subscription.dropped
            self.stats.queued = self._subscription.queue.qsize()
        return {
            "enabled": self.stats.enabled,
            "connected": self.stats.connected,
            "connect_attempts": self.stats.connect_attempts,
            "connect_successes": self.stats.connect_successes,
            "published_total": self.stats.published_total,
            "publish_failures": self.stats.publish_failures,
            "dropped_total": self.stats.dropped_total,
            "queued": self.stats.queued,
            "suppressed_synthetic_total": self.stats.suppressed_synthetic_total,
            "suppressed_unidentified_total": self.stats.suppressed_unidentified_total,
            "suppressed_withdrawn_total": self.stats.suppressed_withdrawn_total,
            "last_error": self.stats.last_error,
            "last_connected_utc": self.stats.last_connected_utc,
            "last_disconnected_utc": self.stats.last_disconnected_utc,
            "last_publish_utc": self.stats.last_publish_utc,
        }

    def render_metrics(self) -> tuple[bytes, str]:
        registry = CollectorRegistry()
        snap = self.snapshot()
        Gauge("oo_mqtt_enabled", "1 when MQTT publishing is enabled", registry=registry).set(
            1.0 if snap["enabled"] else 0.0
        )
        Gauge("oo_mqtt_connected", "1 when connected to the MQTT broker", registry=registry).set(
            1.0 if snap["connected"] else 0.0
        )
        Gauge(
            "oo_mqtt_connect_attempts_total", "MQTT connection attempts", registry=registry
        ).set(float(snap["connect_attempts"]))
        Gauge(
            "oo_mqtt_published_total", "Messages published to MQTT", registry=registry
        ).set(float(snap["published_total"]))
        Gauge(
            "oo_mqtt_publish_failures_total", "Publish calls that raised", registry=registry
        ).set(float(snap["publish_failures"]))
        Gauge(
            "oo_mqtt_dropped_total",
            "Bus events dropped from the bounded MQTT queue (broker slow or absent)",
            registry=registry,
        ).set(float(snap["dropped_total"]))
        Gauge(
            "oo_mqtt_queue_depth", "Events currently queued for MQTT publish", registry=registry
        ).set(float(snap["queued"]))
        Gauge(
            "oo_mqtt_suppressed_synthetic_total",
            "Detections withheld from MQTT because capture was not live (ADR-020)",
            registry=registry,
        ).set(float(snap["suppressed_synthetic_total"]))
        Gauge(
            "oo_mqtt_suppressed_withdrawn_total",
            "Detections withheld from MQTT because a plausibility review withdrew "
            "the claim (ADR-044)",
            registry=registry,
        ).set(float(snap["suppressed_withdrawn_total"]))
        return generate_latest(registry), "text/plain; version=0.0.4; charset=utf-8"

    # ------------------------------------------------------------------
    # connection loop

    def _default_client_factory(self) -> aiomqtt.Client:
        s = self.settings
        tls_context: ssl.SSLContext | None = None
        if s.mqtt_tls:
            tls_context = ssl.create_default_context()
            if s.mqtt_tls_insecure:
                tls_context.check_hostname = False
                tls_context.verify_mode = ssl.CERT_NONE
        station_id = self._station_id_provider() or "unknown"
        topics = TopicLayout(s, station_id)
        will = aiomqtt.Will(
            topic=topics.availability,
            payload=b"offline",
            qos=s.mqtt_qos,
            retain=s.mqtt_retain_state,
        )
        return aiomqtt.Client(
            hostname=s.mqtt_host,
            port=s.mqtt_port,
            username=s.mqtt_username or None,
            password=s.mqtt_password or None,
            identifier=s.mqtt_client_id,
            keepalive=s.mqtt_keepalive_s,
            tls_context=tls_context,
            will=will,
        )

    async def _run(self) -> None:
        backoff = self.settings.mqtt_reconnect_min_s
        while not self._stopping:
            station_id = self._station_id_provider()
            if not station_id:
                await asyncio.sleep(min(backoff, 1.0))
                continue
            self.stats.connect_attempts += 1
            try:
                client = self._client_factory()
                async with client:
                    self.stats.connected = True
                    self.stats.connect_successes += 1
                    self.stats.last_connected_utc = _now_iso()
                    self.stats.last_error = None
                    backoff = self.settings.mqtt_reconnect_min_s
                    log.info("mqtt.connected", host=self.settings.mqtt_host)
                    await self._on_connected(client, station_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # aiomqtt.MqttError and friends
                self.stats.last_error = str(exc) or type(exc).__name__
                log.warning("mqtt.connection_failed", error=self.stats.last_error, backoff_s=backoff)
            finally:
                if self.stats.connected:
                    self.stats.connected = False
                    self.stats.last_disconnected_utc = _now_iso()
            if self._stopping:
                return
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * 2, self.settings.mqtt_reconnect_max_s)

    async def _on_connected(self, client: aiomqtt.Client, station_id: str) -> None:
        topics = TopicLayout(self.settings, station_id)
        await self._publish(client, topics.availability, b"online", retain=True)
        if self.settings.mqtt_discovery_enabled:
            await self._publish_discovery(client, station_id)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._consume_events(client, topics))
            tg.create_task(self._periodic_state(client, topics))

    async def _publish_discovery(self, client: aiomqtt.Client, station_id: str) -> None:
        entities = build_entities(self.settings, station_id)
        for entity in entities:
            await self._publish(client, entity.topic, entity.payload, retain=True)
        self._discovery_published = True
        log.info("mqtt.discovery_published", count=len(entities))

    async def _publish(
        self, client: aiomqtt.Client, topic: str, payload: Any, *, retain: bool, qos: int | None = None
    ) -> None:
        if isinstance(payload, bytes | bytearray):
            body = bytes(payload)
        elif isinstance(payload, str):
            # A bare scalar topic (species_today's "2", bat_activity's "ON"/"OFF"):
            # published as plain text, not a JSON-quoted string, so a simple HA
            # sensor state_topic with no value_template reads it directly.
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")
        resolved_qos = self.settings.mqtt_qos if qos is None else qos
        try:
            await client.publish(topic, body, qos=resolved_qos, retain=retain)
            self.stats.published_total += 1
            self.stats.last_publish_utc = _now_iso()
        except Exception:
            self.stats.publish_failures += 1
            raise

    # ------------------------------------------------------------------
    # per-connection workers

    async def _consume_events(self, client: aiomqtt.Client, topics: TopicLayout) -> None:
        assert self._subscription is not None
        async for event in self._subscription:
            event_type = event.get("event_type")
            if event_type == EventType.DETECTION_CREATED:
                await self._handle_detection(client, topics, event)
            elif event_type in (EventType.CAPTURE_STARTED, EventType.CAPTURE_STOPPED):
                await self._publish(
                    client, topics.capture, self._capture_status_provider(), retain=True
                )
            elif event_type == EventType.HEALTH_EVENT:
                await self._publish(client, topics.health, self._health_provider(), retain=True)

    async def _periodic_state(self, client: aiomqtt.Client, topics: TopicLayout) -> None:
        while True:
            await asyncio.sleep(self.settings.mqtt_health_publish_interval_s)
            await self._publish(client, topics.health, self._health_provider(), retain=True)
            await self._publish(
                client, topics.capture, self._capture_status_provider(), retain=True
            )
            # Also re-checked here (not just on a new pass) so activity turns
            # back OFF on its own once mqtt_bat_activity_window_s has elapsed,
            # even on a quiet night with no further events to trigger it.
            await self._publish_bat_activity(client, topics)

    # ------------------------------------------------------------------
    # detection handling

    async def _handle_detection(
        self, client: aiomqtt.Client, topics: TopicLayout, event: dict[str, Any]
    ) -> None:
        capture = self._capture_status_provider()
        if not capture.get("is_live_hardware", True):
            # ADR-020: a browsing/notification surface must not present
            # detections made against a synthetic or replay source as
            # observations. The row exists in the database regardless; it is
            # simply not this station's job to tell Home Assistant about it.
            self.stats.suppressed_synthetic_total += 1
            log.debug("mqtt.suppressed_synthetic_detection")
            return

        data = event.get("data", {})

        if plausibility.is_withdrawn(data.get("native_result")) or data.get("withdrawn"):
            # ADR-044. A Home Assistant entity state is a bare claim: a name, a
            # time, and no room for "we no longer stand behind this". Nothing
            # subscribed to `.../detection` can render a caveat, and a retained
            # `last_detection` would sit on a dashboard indefinitely. So a
            # withdrawn row is not published at all, exactly as it is not put on
            # the wall display.
            #
            # This is expected to be dead code on a healthy station, and is here
            # anyway: withdrawal is written by a repair CLI run *after* capture,
            # so a live bus event should never carry one. If this counter ever
            # moves, something is republishing historical rows onto the bus, and
            # that is worth knowing about rather than quietly forwarding.
            self.stats.suppressed_withdrawn_total += 1
            log.info("mqtt.suppressed_withdrawn_detection", detection=data.get("id"))
            return

        plugin_id = data.get("detector", {}).get("plugin_id")
        taxonomic_group = data.get("taxonomic_group")
        is_bat = plugin_id == BAT_PLUGIN_ID or taxonomic_group == BAT_GROUP

        # An "acoustic event" names nothing: no species, no taxonomic group.
        # It is a true record that *something* was loud, and it stays in the
        # database, but forwarding it to Home Assistant buries the actual
        # identifications -- on this station it is roughly three quarters of
        # all detections, and every one becomes a state change and an entity
        # history entry. The debug UI has hidden these by default since
        # Milestone 2 for exactly this reason; MQTT now matches it.
        #
        # A bat pass is not unidentified: it claims a pass occurred, which is
        # a positive statement, so `is_bat` short-circuits this.
        # Deliberately does NOT consult `taxonomic_group`. `activity-v1` sets it
        # to the sentinel `"acoustic_event"`, which is truthy, so treating a
        # non-null group as an identification let every acoustic event straight
        # through -- measured on the live station: 779 messages published and
        # `suppressed_unidentified_total` still 0, while 45 of the 60 most
        # recent detections were acoustic events.
        #
        # A group without a name is not an identification anyway. A species
        # name is; and a bat pass is covered by `is_bat` above, which reads the
        # plugin id as well as the group.
        identified = bool(is_bat or data.get("scientific_name") or data.get("common_name"))
        if not identified and not self.settings.mqtt_publish_unidentified:
            self.stats.suppressed_unidentified_total += 1
            log.debug("mqtt.suppressed_unidentified_detection", detector=plugin_id)
            return

        await self._publish(client, topics.detection, event, retain=False)

        self._roll_day_counters_if_needed()

        if is_bat:
            self._bat_passes_tonight += 1
            self._last_bat_pass_monotonic = self._clock()
            last_detection_payload = {
                "display_name": "Bat pass",
                "detected_at": data.get("event_end_utc"),
                "detector": plugin_id,
                "taxonomic_group": "bat",
                # Deliberately no "score": bat passes are always shown and
                # never scored (operator decision, 2026-08-08 session; see
                # FANOUT_BRIEF.md). No species field either -- see the module
                # docstring's honesty constraint.
            }
            await self._publish(client, topics.bat_passes_tonight, str(self._bat_passes_tonight), retain=True)
            await self._publish_bat_activity(client, topics, force_on=True)
        else:
            display_name = (
                data.get("display_name") or data.get("common_name") or data.get("label") or "Unknown"
            )
            species_key = data.get("scientific_name") or data.get("common_name") or display_name
            self._species_today.add(species_key)
            last_detection_payload = {
                "display_name": display_name,
                "detected_at": data.get("event_end_utc"),
                "detector": plugin_id,
                "scientific_name": data.get("scientific_name"),
                "taxonomic_group": taxonomic_group,
                # Raw detector score, explicitly not a calibrated probability
                # (CLAUDE.md, normaliser.py `calibrated_probability` is the
                # separate, usually-null field for that). Never rename this
                # key to "confidence" or expose it with device_class:
                # probability in discovery.py.
                "score": data.get("score"),
            }
            await self._publish(
                client, topics.species_today, str(len(self._species_today)), retain=True
            )

        await self._publish(client, topics.last_detection, last_detection_payload, retain=True)

        event_type = "bat_pass_detection" if is_bat else (
            "bird_detection" if taxonomic_group == "bird" else "other_detection"
        )
        await self._publish(
            client,
            topics.detection_event,
            {"event_type": event_type, **last_detection_payload},
            retain=False,
        )

    async def _publish_bat_activity(
        self, client: aiomqtt.Client, topics: TopicLayout, *, force_on: bool = False
    ) -> None:
        active = force_on or self._bat_activity_active()
        if active == self._last_published_bat_activity:
            return
        await self._publish(client, topics.bat_activity, b"ON" if active else b"OFF", retain=True)
        self._last_published_bat_activity = active

    def _bat_activity_active(self) -> bool:
        if self._last_bat_pass_monotonic is None:
            return False
        return (self._clock() - self._last_bat_pass_monotonic) <= self.settings.mqtt_bat_activity_window_s

    def _roll_day_counters_if_needed(self) -> None:
        """Reset species-today at local midnight in the station's timezone.

        Bat-passes-tonight is an approximation of "since dusk", reset at the
        same local-midnight boundary rather than true civil dawn: precise dawn
        rollover would need this module to depend on `schedule.py`'s solar
        geometry and the station's coordinates, which are optional. Documented
        as an approximation in docs/operations/HOME_ASSISTANT.md; a pass just
        after local midnight but before dawn is rare enough at UK latitudes in
        most of the year to be an acceptable simplification for a
        conversation-piece HA sensor, not a scientific record (the database
        keeps the authoritative timestamps regardless).
        """
        tz: tzinfo = UTC
        with contextlib.suppress(Exception):
            tz = ZoneInfo(self.settings.timezone)
        today_key = datetime.now(tz).date().isoformat()
        if self._species_day != today_key:
            self._species_today.clear()
            self._species_day = today_key
        if self._bat_night_key != today_key:
            self._bat_passes_tonight = 0
            self._bat_night_key = today_key
