"""Tests for ``oo history reconcile-streams``, the repair path for ADR-024.

The live database carries at least one `audio_stream` row whose claimed span
(32 hours) wildly disagrees with what its own `frame_count` says was actually
captured (2.79 hours). This command finds rows like that and, only when asked
twice (``--apply`` plus a confirmation), corrects them -- never silently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from open_observatory.cli import app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

runner = CliRunner()

BASE = datetime(2026, 8, 7, 3, 38, 54, tzinfo=UTC)


def _seed_bad_row(settings) -> uuid.UUID:
    init_engine(settings)
    create_all()
    stream_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            orm.AudioStream(
                id=stream_id,
                source_kind="alsa",
                start_utc=BASE,
                end_utc=BASE + timedelta(hours=32),
                start_monotonic_ns=0,
                sample_rate=384000,
                sample_format="S16_LE",
                frame_count=3_852_212_352,
                end_reason="AlsaCaptureError: ALSA read failed: File descriptor in bad state",
            )
        )
    return stream_id


def test_dry_run_reports_but_does_not_change_anything(settings) -> None:
    set_settings(settings)
    stream_id = _seed_bad_row(settings)

    result = runner.invoke(app, ["history", "reconcile-streams"])

    assert result.exit_code == 0, result.output
    assert str(stream_id) in result.output
    assert "Dry run only" in result.output

    with session_scope() as session:
        row = session.get(orm.AudioStream, stream_id)
        assert row.end_utc == BASE + timedelta(hours=32) or row.end_utc.replace(
            tzinfo=UTC
        ) == BASE + timedelta(hours=32)


def test_apply_without_yes_still_requires_confirmation(settings) -> None:
    set_settings(settings)
    stream_id = _seed_bad_row(settings)

    # Confirmation prompt defaults to "no" (default=False); feeding it nothing
    # (EOF) or "n" must refuse and change nothing.
    result = runner.invoke(app, ["history", "reconcile-streams", "--apply"], input="n\n")

    assert result.exit_code != 0
    with session_scope() as session:
        row = session.get(orm.AudioStream, stream_id)
        end = row.end_utc if row.end_utc.tzinfo else row.end_utc.replace(tzinfo=UTC)
        assert end == BASE + timedelta(hours=32)


def test_apply_with_yes_corrects_the_row_and_preserves_the_claim(settings) -> None:
    set_settings(settings)
    stream_id = _seed_bad_row(settings)

    result = runner.invoke(app, ["history", "reconcile-streams", "--apply", "--yes"])

    assert result.exit_code == 0, result.output
    with session_scope() as session:
        row = session.get(orm.AudioStream, stream_id)
        end = row.end_utc if row.end_utc.tzinfo else row.end_utc.replace(tzinfo=UTC)
        # Corrected down to roughly the frame-derived duration (~2.79h), nowhere
        # near the claimed 32h.
        assert end < BASE + timedelta(hours=6)
        assert end > BASE
        assert row.detail["reconciliation"]["claimed_end_utc"] is not None
        assert "reconciled" in row.end_reason


def test_no_suspect_rows_reports_clean(settings) -> None:
    set_settings(settings)
    init_engine(settings)
    create_all()

    result = runner.invoke(app, ["history", "reconcile-streams"])

    assert result.exit_code == 0, result.output
    assert "No suspect stream rows found" in result.output


def test_open_row_is_never_reported_or_touched(settings) -> None:
    """A NULL end_utc might belong to a station running right now."""
    set_settings(settings)
    init_engine(settings)
    create_all()
    stream_id = uuid.uuid4()
    with session_scope() as session:
        session.add(
            orm.AudioStream(
                id=stream_id,
                source_kind="alsa",
                start_utc=BASE,
                end_utc=None,
                start_monotonic_ns=0,
                sample_rate=384000,
                sample_format="S16_LE",
                frame_count=0,
            )
        )

    result = runner.invoke(app, ["history", "reconcile-streams", "--apply", "--yes"])

    assert result.exit_code == 0, result.output
    assert "No suspect stream rows found" in result.output
    with session_scope() as session:
        row = session.get(orm.AudioStream, stream_id)
        assert row.end_utc is None
