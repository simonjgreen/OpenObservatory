"""Relational schema.

Follows ``docs/data/DATA_MODEL.md``, with one deliberate omission recorded here:
``detector_run`` is **not** persisted per window. The activity detector alone
produces two runs a second, roughly 172,000 rows a day, which buys nothing the
Prometheus counters do not already give and would dominate the database. Runs are
counted and exposed as metrics; only their *results* are persisted.

Nothing here uses a dialect-specific type, so the SQLite developer profile and
the PostgreSQL production profile share one schema (ADR-007). UUIDs and JSON are
stored portably.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict] = {dict[str, Any]: JSON}


class Station(Base):
    __tablename__ = "station"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    software_version: Mapped[str] = mapped_column(String(32), default="0.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AudioDevice(Base):
    __tablename__ = "audio_device"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station.id"))
    stable_device_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    usb_vendor_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    usb_product_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    usb_serial: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alsa_address: Mapped[str | None] = mapped_column(String(120), nullable=True)
    negotiated_sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    negotiated_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    negotiated_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AudioStream(Base):
    __tablename__ = "audio_stream"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    audio_device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audio_device.id"), nullable=True
    )
    source_kind: Mapped[str] = mapped_column(String(24))
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_monotonic_ns: Mapped[int] = mapped_column(BigInteger)
    end_monotonic_ns: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sample_rate: Mapped[int] = mapped_column(Integer)
    sample_format: Mapped[str] = mapped_column(String(32))
    channels: Mapped[int] = mapped_column(Integer, default=1)
    frame_count: Mapped[int] = mapped_column(BigInteger, default=0)
    discontinuity_count: Mapped[int] = mapped_column(Integer, default=0)
    end_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The UTC time of the most recently *delivered* audio block, written
    #: periodically (every housekeeping tick, ~10 s) while the stream is open --
    #: not just at close. This is what lets a crashed process's stream row be
    #: closed honestly: without it, `end_utc` is either the wall-clock moment the
    #: read finally errored (which can be hours after audio actually stopped -- see
    #: ADR-024) or, for a process that never notices its own death, absent
    #: entirely. NULL for rows written before this column existed.
    last_frame_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    gaps: Mapped[list[CaptureGap]] = relationship(back_populates="stream")


class CaptureGap(Base):
    __tablename__ = "capture_gap"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    stream_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audio_stream.id"), index=True)
    start_monotonic_ns: Mapped[int] = mapped_column(BigInteger)
    end_monotonic_ns: Mapped[int] = mapped_column(BigInteger)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estimated_missing_frames: Mapped[int] = mapped_column(BigInteger, default=0)
    reason: Mapped[str] = mapped_column(String(48))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    stream: Mapped[AudioStream] = relationship(back_populates="gaps")

    __table_args__ = (Index("ix_gap_stream_start", "stream_id", "start_monotonic_ns"),)


class Detector(Base):
    __tablename__ = "detector"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    plugin_id: Mapped[str] = mapped_column(String(80), index=True)
    plugin_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str] = mapped_column(String(64))
    model_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    licence_name: Mapped[str] = mapped_column(String(160), default="")
    licence_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    claim: Mapped[str] = mapped_column(Text, default="")
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_detector_identity", "plugin_id", "plugin_version", "model_version", unique=True),
    )


class Detection(Base):
    __tablename__ = "detection"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: Not indexed (ADR-037 option B, revision 0004). Holds exactly one distinct
    #: value across the whole live database and no query filters or orders by
    #: it; the composite ``ix_detection_station_start`` that used to cover it
    #: was dropped alongside it.
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station.id"))
    #: Indexed, kept deliberately: ADR-037 proposed dropping this one too, but
    #: re-verification against the live database found
    #: ``plausibility_repair.reconcile_plausibility`` (ADR-032) joins *from*
    #: ``detector`` (filtered by ``plugin_id``) *into* ``detection``, and
    #: ``EXPLAIN QUERY PLAN`` shows SQLite uses this exact index to satisfy
    #: that join rather than scanning the whole table. Confirmed 2026-08-09.
    detector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("detector.id"), index=True)
    stream_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    window_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    event_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Frame bounds in the *native* stream, so evidence is exactly reproducible.
    source_start_frame: Mapped[int] = mapped_column(BigInteger)
    source_end_frame: Mapped[int] = mapped_column(BigInteger)

    detector_label: Mapped[str | None] = mapped_column(String(240), nullable=True)
    common_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    scientific_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    #: Not indexed (ADR-037 option B, revision 0004): only ever selected, never
    #: filtered or ordered by -- ``retention.py``'s exemplar scan reads it into
    #: Python after the query, it does not query by it.
    canonical_taxon_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rank: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Not indexed on its own (ADR-037 option B, revision 0004): every equality
    #: filter on this column in the codebase also filters ``event_start_utc``,
    #: and ``ix_detection_group_start`` below is a covering composite for that
    #: shape, so a lone index here was always a dead prefix of it.
    taxonomic_group: Mapped[str] = mapped_column(String(48), default="unknown")

    score: Mapped[float] = mapped_column(Float)
    calibrated_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_frequency_hz: Mapped[float | None] = mapped_column(Float, nullable=True)

    native_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    media: Mapped[list[DetectionMedia]] = relationship(
        back_populates="detection", cascade="all, delete-orphan"
    )
    detector: Mapped[Detector] = relationship(lazy="joined")

    __table_args__ = (Index("ix_detection_group_start", "taxonomic_group", "event_start_utc"),)


class MediaAsset(Base):
    __tablename__ = "media_asset"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    storage_uri: Mapped[str] = mapped_column(String(600))
    mime_type: Mapped[str] = mapped_column(String(80))
    stream_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_start_frame: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_end_frame: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    byte_length: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Set by the retention sweeper (``retention.py``) when it deletes the file
    #: this row describes. The row itself is never deleted -- detection history
    #: must keep working, just without audio -- so ``reclaimed_at`` is how a
    #: history view or an operator distinguishes "gone" from "never existed",
    #: and ``reclaim_reason`` (the tier name, e.g. ``"native"``, ``"watermark"``)
    #: is how a wrongly-reclaimed clip can be explained after the fact.
    reclaimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    reclaim_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DetectionMedia(Base):
    __tablename__ = "detection_media"

    detection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detection.id"), primary_key=True
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_asset.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), default="evidence")

    detection: Mapped[Detection] = relationship(back_populates="media")
    asset: Mapped[MediaAsset] = relationship(lazy="joined")


class HealthEvent(Base):
    __tablename__ = "health_event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    service: Mapped[str] = mapped_column(String(64), index=True)
    component: Mapped[str] = mapped_column(String(64), default="")
    severity: Mapped[str] = mapped_column(String(16), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class User(Base):
    """A local operator account (Milestone 4, ADR-034).

    Exactly the fields a single-station appliance needs: no roles, no
    groups, no org model -- this is authentication, not authorisation.
    ``password_hash`` is always an Argon2id PHC string
    (``$argon2id$v=19$...``), produced by :mod:`open_observatory.auth`;
    nothing in this codebase ever stores or logs the plaintext.
    """

    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    #: Set on the bootstrap account and after an administrative reset; the
    #: login endpoint still succeeds (so the operator is never simply locked
    #: out) but the client is told to route straight to the change-password
    #: form rather than the app.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthSession(Base):
    """A browser session backing an `HttpOnly` cookie.

    The cookie carries a high-entropy opaque token; only its SHA-256 is
    stored here, so a stolen database dump cannot be replayed as a session
    any more than a stolen one could be turned back into a password. This is
    a fast, unsalted hash deliberately -- the token itself already has
    ~256 bits of entropy from :func:`secrets.token_urlsafe`, so it needs no
    slow KDF, and a session lookup happens on every authenticated request.
    """

    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Free text, for an operator glancing at "what is logged in right now"
    #: in a future admin view. Never parsed, never trusted for anything.
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class ApiToken(Base):
    """A long-lived, revocable credential for a machine client.

    Same hashed-at-rest treatment as :class:`AuthSession`. ``token_prefix``
    (the first 8 characters of the token, stored in the clear) lets a token
    be looked up without a full-table scan while the value that actually
    authenticates -- the hash of the whole token -- never leaves this row.
    An operator names each token at creation (``name``) so a future "why
    does the display keep showing as logged in" question has an answer.
    """

    __tablename__ = "api_token"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_prefix: Mapped[str] = mapped_column(String(16), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Review(Base):
    """Append-only. Current status is derived from the latest valid review."""

    __tablename__ = "review"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    detection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("detection.id"), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="local")
    status: Mapped[str] = mapped_column(String(24))
    corrected_taxon_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Denormalised from `Detection.common_name`/`scientific_name` for the
    #: matched taxon at the moment of correction (see `review.resolve_taxon`),
    #: so a corrected identification can be displayed -- in history, export,
    #: the review drawer -- without a runtime join or a taxonomy service that
    #: doesn't exist. Set together with `corrected_taxon_id`, only when
    #: `status == "corrected"` (ADR-043).
    corrected_common_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    corrected_scientific_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    supersedes_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
