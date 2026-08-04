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
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("station.id"), index=True)
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
    canonical_taxon_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    rank: Mapped[str | None] = mapped_column(String(32), nullable=True)
    taxonomic_group: Mapped[str] = mapped_column(String(48), default="unknown", index=True)

    score: Mapped[float] = mapped_column(Float)
    calibrated_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_frequency_hz: Mapped[float | None] = mapped_column(Float, nullable=True)

    native_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    media: Mapped[list[DetectionMedia]] = relationship(
        back_populates="detection", cascade="all, delete-orphan"
    )
    detector: Mapped[Detector] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_detection_station_start", "station_id", "event_start_utc"),
        Index("ix_detection_group_start", "taxonomic_group", "event_start_utc"),
    )


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


class Review(Base):
    """Append-only. Current status is derived from the latest valid review."""

    __tablename__ = "review"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    detection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("detection.id"), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="local")
    status: Mapped[str] = mapped_column(String(24))
    corrected_taxon_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    supersedes_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
