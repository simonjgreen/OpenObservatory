"""Purging evidence that should never have been kept (ADR-049).

``oo clips purge-human-audio`` (``cli.py``) deletes the evidence clips attached
to detections of BirdNET's three human sound classes — "Human vocal", "Human
non-vocal", "Human whistle" — and marks their ``media_asset`` rows reclaimed.

**Why this is a delete and not a withdrawal.** The charter's item 5 rule is
"withdraw, not delete", and it is about *records*: a detection the system got
wrong is evidence about the system. It has never applied to clip bytes.
``retention.py`` deletes those routinely by age, keeps the ``media_asset`` row
with ``reclaimed_at``/``reclaim_reason`` set so ``/api/v1/media/{id}`` can
still answer 410, and never touches the ``detection`` row. This module does
exactly the same thing with a different selection rule, so nothing here is a
new kind of operation — only a new reason for an existing one.

The detection rows survive untouched. "Somebody was talking in the garden at
18:55 on the 8th" stays in the record; the recording of them talking does not.
That split is the charter's privacy constraint read literally: the constraint
is about retaining human speech, not about pretending people were never there.

**Bounded and reversible in the only direction that matters.** Deleting a WAV
of a neighbour's conversation is not a loss the project needs to hedge
against, which is why this is a real delete rather than a quarantine
directory. It is dry-run by default all the same, because an operator should
see the list before it goes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models as orm
from .detectors import birdnet_classes

log = structlog.get_logger(__name__)

#: Written to ``media_asset.reclaim_reason``, alongside retention's tier names
#: ("native", "unkept", "watermark"), so an operator reading the column can
#: tell a privacy purge from an age-out.
RECLAIM_REASON = "privacy_human_audio"


@dataclass(frozen=True, slots=True)
class PurgeItem:
    """One evidence asset attached to a human-sound detection."""

    asset_id: uuid.UUID
    detection_id: uuid.UUID
    common_name: str | None
    event_start_utc: datetime
    kind: str
    path: str
    bytes: int
    existed_on_disk: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": str(self.asset_id),
            "detection_id": str(self.detection_id),
            "common_name": self.common_name,
            "event_start_utc": self.event_start_utc.isoformat(),
            "kind": self.kind,
            "path": self.path,
            "bytes": self.bytes,
            "existed_on_disk": self.existed_on_disk,
        }


@dataclass(slots=True)
class PurgeReport:
    dry_run: bool = True
    items: list[PurgeItem] = field(default_factory=list)
    deleted: int = 0
    bytes_reclaimed: int = 0
    already_missing: int = 0
    detections: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "detections": self.detections,
            "assets": len(self.items),
            "deleted": self.deleted,
            "bytes_reclaimed": self.bytes_reclaimed,
            "already_missing": self.already_missing,
            "failed": self.failed,
            "items": [item.to_dict() for item in self.items],
        }


def find_human_audio_assets(session: Session) -> list[PurgeItem]:
    """Every not-yet-reclaimed asset attached to a human-sound detection.

    The selection is by *detector label*, not by score, band or taxonomic
    group: whether a recording contains a neighbour's voice is not something
    a confidence threshold gets a say in, and the label is the only field that
    answers the question. It works equally on rows written before ADR-049
    (``rank='species'``, ``taxonomic_group='bird'``) and after it, because
    ``detector_label`` was correct all along.
    """
    rows = session.execute(
        select(orm.Detection, orm.MediaAsset)
        .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
        .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
        .where(orm.MediaAsset.reclaimed_at.is_(None))
        .order_by(orm.Detection.event_start_utc)
    ).all()

    items: list[PurgeItem] = []
    for detection, asset in rows:
        if not birdnet_classes.is_human_audio(detection.detector_label):
            continue
        path = Path(asset.storage_uri)
        items.append(
            PurgeItem(
                asset_id=asset.id,
                detection_id=detection.id,
                common_name=detection.common_name,
                event_start_utc=detection.event_start_utc,
                kind=asset.kind,
                path=str(path),
                bytes=int(asset.byte_length),
                existed_on_disk=path.exists(),
            )
        )
    return items


def purge_human_audio(
    session_factory: Callable[[], AbstractContextManager[Session]],
    *,
    dry_run: bool = True,
) -> PurgeReport:
    """Find and (unless ``dry_run``) delete human-sound evidence.

    Deliberately mirrors ``retention.RetentionSweeper._delete_asset``: the
    file is unlinked first and only then is the row marked, so an unlink
    failure leaves ``reclaimed_at`` unset and the asset is retried next run
    rather than being recorded as done while the audio is still on disk.
    """
    report = PurgeReport(dry_run=dry_run)
    with session_factory() as session:
        items = find_human_audio_assets(session)
        report.items = items
        report.detections = len({item.detection_id for item in items})
        for item in items:
            if not item.existed_on_disk:
                report.already_missing += 1
            if dry_run:
                log.info("privacy.would_purge", asset_id=str(item.asset_id), path=item.path)
                continue
            path = Path(item.path)
            if item.existed_on_disk:
                try:
                    path.unlink()
                except OSError as exc:
                    report.failed += 1
                    log.warning(
                        "privacy.unlink_failed",
                        asset_id=str(item.asset_id),
                        path=item.path,
                        error=str(exc),
                    )
                    continue
                report.bytes_reclaimed += item.bytes
            asset = session.get(orm.MediaAsset, item.asset_id)
            if asset is not None:
                asset.reclaimed_at = datetime.now(UTC)
                asset.reclaim_reason = RECLAIM_REASON
            report.deleted += 1
            log.info(
                "privacy.purged",
                asset_id=str(item.asset_id),
                detection_id=str(item.detection_id),
                bytes=item.bytes,
            )
    return report
