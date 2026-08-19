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
    text,
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
    #: filtered or ordered by.
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

    # -- refinement bookkeeping (charter item 5, ADR-045) -------------------
    #: Denormalised from the newest ``refinement`` row, deliberately. The
    #: charter's retention decision is "delete on 'refinement has run', not on
    #: age alone ... each event should carry the fact that refinement ran, at
    #: what version, with what outcome, and deletion should require it" --
    #: which means the retention sweeper, which runs *in the capture process*
    #: on a paced budget (ADR-033), must be able to answer "has this been
    #: refined?" from an indexed column rather than a correlated subquery
    #: against a second table. NULL means no refiner has ever examined this
    #: event, which is exactly the state the charter's safeguard protects.
    refined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: ``EvidenceIdentity.version_label`` of the most recent refinement -- "at
    #: what version". Free text on purpose: it has to survive a refiner being
    #: renamed or a model being swapped without a schema change.
    refinement_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: ``RefinementOutcome`` of the most recent refinement -- "with what
    #: outcome". ``unavailable`` and ``failed`` are recorded here too, and are
    #: deliberately *not* the same thing as ``no_change``: a clip the refiner
    #: never managed to read has not been examined, and a retention rule that
    #: treated it as examined would destroy the evidence silently.
    refinement_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # -- operator keep flag (ADR-061) ----------------------------------------
    #: Set by a human who wants this recording kept; cleared only by a
    #: human. Every retention tier exempts a row with this set, including the
    #: 90-day expiry and the disk watermark -- "keep" that a sweep can overrule
    #: is not a keep.
    #:
    #: **Deliberately not indexed**, and revision 0009 drops the index revision
    #: 0008 created. `kept_at IS NULL` matches ~99.8% of rows (112 non-null of
    #: ~46,000 on the live station), so an index on it narrows nothing -- but
    #: SQLite preferred it anyway, which cost the planner
    #: `ix_detection_event_start_utc` and turned `_strip_native`'s ordered,
    #: indexed scan into a temp B-tree sort. On the station that blocked one
    #: `sweep()` inside a single statement for over five minutes and wedged the
    #: housekeeping loop behind it. Measured: 0.555 s with the index, 0.117 s
    #: without. See ADR-061 and revision 0009 before adding one back.
    kept_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who kept it. "exemplar-backfill" for the rows migrated from the computed
    #: first-of-species rule this replaced.
    kept_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    media: Mapped[list[DetectionMedia]] = relationship(
        back_populates="detection", cascade="all, delete-orphan"
    )
    detector: Mapped[Detector] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_detection_group_start", "taxonomic_group", "event_start_utc"),
        # Partial, and the `WHERE` is the entire point (ADR-061, revision 0010).
        # It indexes only the kept rows -- 112 of 290,956 on the station -- so
        # the planner may use it for the `kept_at IS NOT NULL` count and *cannot*
        # use it for the `kept_at IS NULL` filter in every tier's candidate
        # query. A plain index on this column is available to both, and SQLite
        # prefers it over `ix_detection_event_start_utc`, losing the index that
        # serves the range predicate and the `ORDER BY` together. That wedged
        # the station for five minutes inside one statement.
        Index(
            "ix_detection_kept_at_partial",
            "kept_at",
            sqlite_where=text("kept_at IS NOT NULL"),
        ),
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
    #: Set by the retention sweeper (``retention.py``) when it deletes the file
    #: this row describes. The row itself is never deleted -- detection history
    #: must keep working, just without audio -- so ``reclaimed_at`` is how a
    #: history view or an operator distinguishes "gone" from "never existed",
    #: and ``reclaim_reason`` (the tier name, e.g. ``"native"``, ``"watermark"``)
    #: is how a wrongly-reclaimed clip can be explained after the fact.
    #:
    #: Two reasons are deliberately not tier names. ``"privacy_human_audio"``
    #: (ADR-049) is an operator deleting human speech. ``"missing"`` (ADR-057)
    #: is the file having gone without any policy deciding to give it up --
    #: 8,067 rows on the live station, unlinked by the pre-ADR-026 filesystem
    #: sweep, which never marked them. Keeping that distinct from a tier is
    #: the point: a clip aged out on purpose and a clip that vanished are
    #: different facts about this system, and only one of them was a decision.
    #: Deliberately **not** plainly indexed (revision 0011). A plain index here
    #: is the third instance on this project of the same mistake: a
    #: low-selectivity index the planner prefers over the one that actually
    #: serves the query. `reclaimed_at` is NULL on 176,231 of 214,499 live
    #: rows, so `ix_media_asset_reclaimed_at` looked to SQLite like a cheap
    #: way in, and it took it -- losing the `ORDER BY created_at` and adding a
    #: `USE TEMP B-TREE`. Measured on the station's own database: the native
    #: tier's candidate query took 2.20 s with it and 0.0049 s without.
    #: See ADR-062, and ADR-061's addenda for the two earlier instances
    #: (`ix_detection_kept_at`, revision 0009; and the composite it displaced).
    reclaimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reclaim_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        # The two indexes every retention tier walks (ADR-062). Both are
        # partial on `reclaimed_at IS NULL`, and that is the entire point:
        # a reclaimed row *leaves* the index, so the work a sweep has already
        # done never has to be walked past again. Without them the native
        # tier re-examined ~210,000 already-reclaimed detections on every
        # pass to find the ~1,699 outstanding ones, which is why the sweep
        # got slower every time it succeeded until it could no longer finish
        # inside `retention_batch_budget_s` at all.
        #
        # `(kind, created_at)` serves the native tier (`kind='evidence_native'`
        # plus an age range, ordered by age); `(created_at)` serves the unkept
        # and watermark tiers, which are kind-agnostic. Both put `created_at`
        # last so the range predicate and the `ORDER BY` are satisfied by the
        # same index and the scan terminates at `LIMIT` instead of sorting.
        Index(
            "ix_media_asset_live_kind_created",
            "kind",
            "created_at",
            sqlite_where=text("reclaimed_at IS NULL"),
        ),
        Index(
            "ix_media_asset_live_created",
            "created_at",
            sqlite_where=text("reclaimed_at IS NULL"),
        ),
    )


class DetectionMedia(Base):
    __tablename__ = "detection_media"

    detection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detection.id"), primary_key=True
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("media_asset.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), default="evidence")

    __table_args__ = (
        # The composite primary key indexes `(detection_id, media_asset_id)`,
        # which serves detection -> asset. Retention walks the other way --
        # it picks assets by age and needs their detection -- and without this
        # that join was a full scan of the primary key's covering index
        # (measured: 0.114 s per 250 candidates, against 0.0046 s with it).
        Index("ix_detection_media_asset", "media_asset_id"),
    )

    detection: Mapped[Detection] = relationship(back_populates="media")
    asset: Mapped[MediaAsset] = relationship(lazy="joined")


class CapturePause(Base):
    """One deliberate operator pause (ADR-055).

    The row exists so a pause is a *recorded* absence rather than an
    unexplained hole. Charter item 2 makes "a quiet night versus a dead
    microphone" a first-class distinction; an operator pause is a third thing,
    and coverage that could not tell it from either of the others would invite
    exactly the wrong conclusion about a silent afternoon.

    ``ends_utc`` is the deadline the pause was set to and never changes.
    ``ended_utc`` is when it actually stopped, which is earlier if the operator
    resumed and equal if it simply ran out -- and NULL while it is running, so
    a restart can find and re-adopt it. A pause is never deleted or rewritten:
    like every other record here, it is evidence about what the station did.
    """

    __tablename__ = "capture_pause"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    station_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("station.id"), nullable=True
    )
    started_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: When the pause was *set* to end. Fixed at the moment it started.
    ends_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: When it actually ended. NULL while running.
    ended_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: "expired", "resumed", "superseded" or "unknown" (a process that died
    #: mid-pause, closed by the next start-up).
    end_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    #: The preset key the operator chose, e.g. "1h" or "until-midnight", with
    #: the label it was shown as. Stored rather than derived: the catalogue may
    #: change, and what an operator was told they were choosing should not.
    preset: Mapped[str] = mapped_column(String(40), default="")
    label: Mapped[str] = mapped_column(String(80), default="")
    actor: Mapped[str] = mapped_column(String(80), default="operator")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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


class Refinement(Base):
    """One examination of an already-stored detection by new information.

    Append-only, like :class:`Review`. Charter item 5 in table form:

    * **Only from new information** -- ``evidence_fingerprint`` is the SHA-256
      of the refiner, model, weights and configuration that produced this row
      (``refinement.contracts.EvidenceIdentity``). ``ix_refinement_evidence``
      is unique per detection, so the same instrument, at the same version,
      under the same settings, physically cannot bank a second, more
      optimistic answer about the same event.
    * **Preserve the original claim** -- the ``original_*`` columns snapshot
      the detection's claim verbatim at the moment of refinement, so the prior
      verdict stays visible and attributable even if a later refinement (or a
      human review) does change the detection row.
    * **Distinguishable, with what changed it and when** -- ``basis``,
      ``outcome``, ``reason``, ``refiner_*``, ``model_*`` and ``created_at``.

    ``evidence`` holds the refiner's own output verbatim, the way
    ``detection.native_result`` holds a detector's -- including, for the
    BatDetect2 cascade, the measurements a human needs to check the proposal
    against physics rather than against another score.

    Nothing here is a station claim. A row with ``outcome='proposed'`` is a
    question put to a person, not an identification; only a ``review`` row can
    settle it.
    """

    __tablename__ = "refinement"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    detection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("detection.id"), index=True)

    #: The identity of the new information. See ``EvidenceIdentity``.
    refiner_id: Mapped[str] = mapped_column(String(80), index=True)
    refiner_version: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str] = mapped_column(String(64))
    model_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))

    #: ``RefinementBasis`` -- new_model / corrected_prior / human_ear.
    basis: Mapped[str] = mapped_column(String(24))
    #: ``RefinementOutcome``.
    outcome: Mapped[str] = mapped_column(String(24), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")

    #: The original claim, snapshotted. Never derived from the detection row at
    #: read time -- that row may itself have been refined since.
    original_common_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    original_scientific_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    original_taxonomic_group: Mapped[str | None] = mapped_column(String(48), nullable=True)
    original_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    proposed_common_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    proposed_scientific_name: Mapped[str | None] = mapped_column(String(240), nullable=True)
    proposed_rank: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proposed_taxonomic_group: Mapped[str | None] = mapped_column(String(48), nullable=True)
    #: The refiner's own number. Not a probability; never presented as one.
    proposed_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Whether this refinement was permitted to change the detection row. False
    #: for every refiner shipped today -- see ADR-045 on why the BatDetect2
    #: cascade may only propose.
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Set when a human accepts or rejects a proposal, via a ``review`` row.
    #: NULL means nobody has looked yet.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review.id"), nullable=True
    )

    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        # Rule 1, in the schema rather than only in the writer: one verdict per
        # (event, instrument+config). A refiner whose model, weights or settings
        # changed gets a new fingerprint and may speak again; one that changed
        # nothing cannot.
        Index("ix_refinement_evidence", "detection_id", "evidence_fingerprint", unique=True),
    )


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
