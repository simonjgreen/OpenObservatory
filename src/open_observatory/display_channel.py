"""The inside observer's push channel: a detections-only feed sized for an ESP32.

ADR-038. The wall display (ADR-023) originally polled four REST endpoints every
20 s, which cost the station ~315 ms of query work and ~127 kB of payload per
cycle to render six rows -- 71 kB of that was forty detection records, each ~1.8 kB
of evidence checksums, media URLs, detector metadata and UUIDs, fetched so the
device could throw almost all of it away. This module is the other end of that
trade: the station decides what the screen needs and sends only that, once, when
it happens.

Three properties shape every decision here.

**One packet.** A detection event is a few dozen bytes, so it lands inside a
single Ethernet MTU with room to spare and the ESP32 never reassembles anything.
Keys are one or two characters, whitespace is stripped, and any field the glass
cannot render is simply absent -- there is no "reserved for later" on this wire.

**No score, ever.** ADR-023's rule is enforced at the boundary rather than
trusted to the client: the threshold decides what is sent, and the number itself
is not a field on this channel. A bat pass carries no name at all -- only a
marker and a peak frequency -- because ``ultrasonic-pass-v1`` claims a pass, not
a species (ADR-013), and the words "Bat pass" are supplied by the firmware.

**Capture always wins.** Everything here is synchronous and non-blocking. The
per-client queue is bounded and sheds the oldest *detection* first, so a display
that cannot keep up converges back to now and loses history rather than losing
the status frame that tells a person the station is broken.

This module is deliberately free of FastAPI, sockets and the database, so all of
it is exercised by ``tests/test_display_channel.py`` without a server.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import plausibility

#: Bumped whenever a field changes meaning. The firmware refuses a wire version
#: it does not understand rather than rendering a frame it has half-parsed.
WIRE_VERSION = 1

#: Safe payload budget inside one Ethernet MTU (1500) once IPv4, TCP and
#: WebSocket framing are paid for. Nothing this channel sends may exceed it; the
#: tests assert it, and :func:`encode` is where a regression would show up.
MAX_FRAME_BYTES = 1400

#: The only bat-pass detector the station runs. Checked alongside the
#: normaliser's taxonomic group, because either signal alone is a single point of
#: failure and the consequence of getting it wrong is a pass being score-filtered.
BAT_PLUGIN_ID = "ultrasonic-pass-v1"
BAT_GROUP = "bat"


@dataclass(frozen=True)
class DisplayFilter:
    """What one connected display asked for, applied server-side.

    Applied here so the ESP32 never receives-and-discards: the polled transport
    fetched forty rows to render six, and this is the endpoint of that lesson.
    """

    #: Never rendered anywhere. Decides which *named* detections are sent.
    min_score: float = 0.75
    #: Bat passes bypass ``min_score`` entirely; this only says whether the
    #: operator wants them on the glass at all.
    show_bats: bool = True
    #: Rows the panel has space for, which bounds the connect snapshot.
    rows: int = 6


def is_taxonomic_name(scientific: str | None, common: str | None) -> bool:
    """True when this looks like a real binomial rather than a BirdNET class.

    The same rule the firmware applies in ``detection_feed.cpp``, kept in step
    deliberately: BirdNET's non-taxonomic classes (``Engine``, ``Siren``,
    ``Human vocal``) arrive with ``rank`` of ``species`` but with the scientific
    name equal to the common name, and they are not garden species.
    """
    if not scientific or not common:
        return False
    if scientific == common:
        return False
    return " " in scientific


def _epoch_seconds(value: Any) -> int | None:
    """Whole UTC seconds from either an ISO-8601 string or a ``datetime``.

    Whole seconds because the display renders whole seconds; the fractional part
    is 6 bytes of wire for a distinction nobody can read across a room.
    """
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(moment.timestamp())
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp())


def _is_withdrawn(detection: Mapping[str, Any]) -> bool:
    """Withdrawn (ADR-044), from either shape this module is fed.

    The REST payload carries a top-level ``withdrawn`` boolean and a
    ``flags.withdrawn``; a bus event's ``DetectionRecord.to_dict`` carries the
    whole ``native_result`` instead. All three are checked, because the
    connect snapshot and the live deltas must not be able to disagree about a
    single row -- and the snapshot is additionally filtered in SQL, so this is
    the second of two independent barriers rather than the only one.
    """
    if detection.get("withdrawn"):
        return True
    flags = detection.get("flags")
    if isinstance(flags, Mapping) and flags.get("withdrawn"):
        return True
    return plausibility.is_withdrawn(detection.get("native_result"))


def _is_bat(detection: Mapping[str, Any]) -> bool:
    if detection.get("taxonomic_group") == BAT_GROUP:
        return True
    detector = detection.get("detector")
    if isinstance(detector, Mapping):
        return detector.get("plugin_id") == BAT_PLUGIN_ID
    return False


def wire_item(detection: Mapping[str, Any], filt: DisplayFilter) -> dict[str, Any] | None:
    """One detection reduced to what the screen draws, or ``None`` to send nothing.

    ``detection`` may be either the bus event's payload (``DetectionRecord.to_dict``)
    or the REST shape (``_detection_payload``); every field read below is common to
    both, so the connect snapshot and the live deltas cannot diverge.

    The returned mapping is the whole vocabulary of this wire:

    ==== ============================================================
    key  meaning
    ==== ============================================================
    ``n``  species display name. Absent on a bat pass.
    ``at`` event start, whole seconds since the Unix epoch, UTC.
    ``b``  ``1`` when this is a bat pass. Absent otherwise.
    ``k``  peak frequency in kHz, one decimal. Bat passes only.
    ``r``  detections collapsed into this row. Snapshot rows only, and
           only when greater than 1.
    ==== ============================================================

    A detection whose claim has been withdrawn (ADR-044) returns ``None``,
    whatever else it carries. This wire has no vocabulary for doubt -- there is
    no score, no marker and no room to add one inside an MTU -- so a withdrawn
    *Western Screech-Owl* on this channel would read as a plain factual claim
    that a screech-owl was in the garden, in a living room, with no way for the
    person looking at it to know otherwise. Suppression, not annotation, is the
    only honest option here.
    """
    at = _epoch_seconds(detection.get("event_start_utc"))
    if at is None:
        return None
    if _is_withdrawn(detection):
        return None

    if _is_bat(detection):
        if not filt.show_bats:
            return None
        item: dict[str, Any] = {"b": 1, "at": at}
        hz = detection.get("peak_frequency_hz")
        if isinstance(hz, int | float) and hz > 0:
            item["k"] = round(float(hz) / 1000.0, 1)
        return item

    common = detection.get("common_name")
    scientific = detection.get("scientific_name")
    if not is_taxonomic_name(scientific, common):
        return None
    score = detection.get("score")
    if not isinstance(score, int | float) or float(score) < filt.min_score:
        return None
    return {"n": str(common), "at": at}


def collapse_runs(items: Iterable[Mapping[str, Any]], rows: int) -> list[dict[str, Any]]:
    """Fold consecutive identical rows together, newest first, for the snapshot.

    The same rule the firmware applies to its own candidate list: a wood pigeon
    that called 194 times today would otherwise fill every row with one bird. The
    surviving row keeps the most recent time (we walk newest-first) and carries
    ``r``, so the count on the glass is the station's, not a number the device
    invented from however many rows happened to be sent.
    """
    feed: list[dict[str, Any]] = []
    for item in items:
        if feed:
            last = feed[-1]
            if last.get("n") == item.get("n") and last.get("b") == item.get("b"):
                last["r"] = last.get("r", 1) + item.get("r", 1)
                continue
        if len(feed) >= rows:
            break
        feed.append(dict(item))
    return [{key: value for key, value in item.items() if not (key == "r" and value == 1)} for item in feed]


def encode(frame: Mapping[str, Any]) -> str:
    """Serialise a frame the way it goes on the wire: no whitespace at all.

    ``json.dumps`` defaults to ``", "`` and ``": "``, which is two wasted bytes
    per field. On a five-field frame that is a tenth of the packet.
    """
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=False)


def hello_frame(
    *,
    now: int,
    state: str,
    detail: str,
    species_today: int,
    items: Iterable[Mapping[str, Any]],
    heartbeat_s: int,
) -> dict[str, Any]:
    """The one frame sent on connect: enough rows to fill the screen, then deltas.

    A display that connects at four in the afternoon must not be blank, and must
    not have to ask a second time to stop being blank.
    """
    frame: dict[str, Any] = {
        "t": "h",
        "v": WIRE_VERSION,
        "now": now,
        "hb": heartbeat_s,
        "st": state,
        "sp": species_today,
        "f": list(items),
    }
    if detail:
        frame["d"] = detail
    return frame


def detection_frame(
    item: Mapping[str, Any], *, species_today: int | None = None
) -> dict[str, Any]:
    """One new detection. ``sp`` rides along only when the count actually moved."""
    frame: dict[str, Any] = {"t": "d"}
    frame.update(item)
    if species_today is not None:
        frame["sp"] = species_today
    return frame


def status_frame(*, now: int, state: str, detail: str, species_today: int) -> dict[str, Any]:
    """The heartbeat.

    Three jobs, none of them optional: it proves the feed is alive (so a silent
    channel can be told from a dead one), it re-anchors the display's clock
    against the station's, and it carries state changes on a quiet night when no
    detection would.
    """
    frame: dict[str, Any] = {"t": "s", "now": now, "st": state, "sp": species_today}
    if detail:
        frame["d"] = detail
    return frame


def health_state(health: Mapping[str, Any]) -> tuple[str, str]:
    """``GET /api/v1/health`` reduced to a state letter and one honest line.

    This is the same decision tree the firmware runs in ``parseHealth`` for the
    HTTP fallback path, moved to the station for the push path so the two
    transports cannot describe the same station differently. ``L`` is listening,
    ``D`` is degraded; offline is a fact only the device can know, so it is not
    one of the states the station can send.
    """
    capture = health.get("capture") or {}
    if not capture.get("is_live_hardware", False):
        detail = (
            "NO MICROPHONE - SYNTHETIC SOURCE"
            if capture.get("source_kind") == "synthetic"
            else "NOT LISTENING TO THE MICROPHONE"
        )
        return "D", detail

    problems = health.get("problems") or []
    if problems:
        first = problems[0]
        text = first if isinstance(first, str) else str((first or {}).get("detail", ""))
        return "D", text or "STATION REPORTS A PROBLEM"

    status = health.get("status", "unknown")
    if status != "ok":
        return "D", f"STATION STATUS: {status}"

    capture_state = capture.get("state", "unknown")
    if capture_state != "capturing":
        return "D", f"CAPTURE {capture_state}"

    return "L", ""


@dataclass
class SpeciesToday:
    """The footer count, tracked incrementally instead of re-queried.

    The polled display paid a 112 ms ``/api/v1/history?window=today`` query every
    20 s for a number that changes a handful of times a day. Seeded once from the
    database on connect, then advanced in memory: adding a name to a set is free,
    and the answer is identical because a species counts exactly when one of its
    detections cleared the threshold -- which is exactly when a frame was sent.

    ``names is None`` means "not counted yet", which the display renders as
    "counting..." rather than "0 species today". Those are different facts.
    """

    #: Local calendar day, as ``YYYY-MM-DD`` in the station's configured zone.
    day_key: str
    names: set[str] | None = None
    #: Whether this instance has ever been seeded from the database.
    seeded: bool = field(default=False)

    @property
    def count(self) -> int:
        return -1 if self.names is None else len(self.names)

    def observe(self, day_key: str, detection: Mapping[str, Any]) -> bool:
        """Fold a detection in. Returns True if :attr:`count` may have changed.

        Rolls the set over at local midnight, because the footer says "today".
        """
        changed = False
        if day_key != self.day_key:
            self.day_key = day_key
            self.names = set()
            changed = True
        if self.names is None:
            return changed

        if _is_withdrawn(detection):
            # Never sent to the glass, so it must never be in the count either
            # (ADR-044). The footer has to agree with what a person can read
            # off the feed.
            return changed
        if _is_bat(detection):
            # A pass is not a species and is never counted -- the footer has to
            # agree with what a person can actually read off the feed.
            return changed
        common = detection.get("common_name")
        scientific = detection.get("scientific_name")
        if not is_taxonomic_name(scientific, common):
            return changed
        before = len(self.names)
        self.names.add(str(scientific))
        return changed or len(self.names) != before


class DisplayClient:
    """One connected display, with exactly one task allowed to write its socket.

    ADR-012's single-writer rule is not tidiness and is not negotiable: concurrent
    writes to one Starlette WebSocket destroyed the spectrogram channel in a way
    that was invisible on loopback and near-total over Wi-Fi. Producers call
    :meth:`offer`, which only ever appends to this deque; :meth:`run` is the only
    code in the process that touches ``socket.send_text`` for this socket.

    ``socket`` may be ``None`` in tests, which exercise queueing and shedding
    without a transport.
    """

    def __init__(self, socket: Any, *, maxsize: int = 64) -> None:
        self.socket = socket
        self._pending: deque[dict[str, Any]] = deque()
        self._maxsize = maxsize
        self._wake = asyncio.Event()
        self.closed = False
        self.sent = 0
        self.dropped = 0
        self.bytes_sent = 0

    def offer(self, frame: Mapping[str, Any]) -> None:
        """Queue a frame. Never blocks, never raises, never awaits."""
        if self.closed:
            return
        if len(self._pending) >= self._maxsize:
            # Shed the oldest *detection*. A display behind by a burst of
            # woodpigeons should catch up to now; losing the status frame in the
            # same burst would make a broken station look merely quiet, which is
            # the exact failure ADR-023 exists to prevent.
            for index, queued in enumerate(self._pending):
                if queued.get("t") == "d":
                    del self._pending[index]
                    self.dropped += 1
                    break
            else:
                self._pending.popleft()
                self.dropped += 1
        self._pending.append(dict(frame))
        self._wake.set()

    def pending(self) -> list[dict[str, Any]]:
        """Queued frames, oldest first. For tests and for the status snapshot."""
        return list(self._pending)

    async def run(self) -> None:
        """The only place this socket is ever written. See ADR-012."""
        while not self.closed:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            payload = encode(self._pending.popleft())
            await self.socket.send_text(payload)
            self.sent += 1
            self.bytes_sent += len(payload.encode("utf-8"))

    @property
    def queue_depth(self) -> int:
        return len(self._pending)

    def stats(self) -> dict[str, Any]:
        return {
            "queued": self.queue_depth,
            "sent": self.sent,
            "dropped": self.dropped,
            "bytes_sent": self.bytes_sent,
            "mean_frame_bytes": round(self.bytes_sent / self.sent, 1) if self.sent else None,
        }

    def close(self) -> None:
        self.closed = True
        self._pending.clear()
        self._wake.set()
