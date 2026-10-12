"""Deterministic backend fixtures for the Playwright suite.

Run **before** the API starts, against a throwaway ``HEXTON_DATA_DIR``. It does the
three things a fixture has to do and nothing else:

1. Create the data directory and migrate it to head with the *real* migrations, so the
   schema the server opens is the schema the app ships.
2. Register a genuinely parseable clip — media validation reads the ``ftyp`` brand and
   the ``mvhd`` timescale, so a placeholder would make the media assertions pass for the
   wrong reason.
3. Write the states a UI cannot reach through the API on its own: a finished transfer
   that YouTube has not yet confirmed, a public video with its evidence, an ambiguous
   upload, a retry with a next attempt, an *expired* authorization, and report rows that
   were delivered, refused and left unconfirmed.

Jobs are built with the backend test-suite's own ``make_clip`` / ``make_media`` /
``make_job`` helpers rather than with hand-rolled inserts, so the fixtures cannot drift
away from the invariants those helpers exist to maintain.

The worker is deliberately **not** started by this script or by the server. Every
scheduled job therefore stays exactly where it is put, which is what makes an assertion
about a queue meaningful — a state that changes underneath a test proves only that the
test was fast enough to catch it.

Nothing here writes to Windows Credential Manager: the store is the plaintext file
inside the throwaway data directory, and the tokens are the same obvious fakes the
backend suite uses.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
BACKEND = REPO / "backend"
TESTS = BACKEND / "tests"
for entry in (str(BACKEND), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

if len(sys.argv) > 1:
    DATA_DIR = Path(sys.argv[1])
else:
    import tempfile

    # Outside the repository by default, so a fixture can never be mistaken for a file
    # that belongs in a review.
    DATA_DIR = Path(tempfile.gettempdir()) / "opencode" / "hexton-e2e"

# Must be set before `get_settings()` is first called: pydantic-settings reads the
# environment when the settings object is built, not when the module is imported.
os.environ["HEXTON_DATA_DIR"] = str(DATA_DIR)
os.environ.setdefault("HEXTON_CONNECTOR_KIND", "fake")
os.environ.setdefault("HEXTON_CREDENTIAL_STORE_KIND", "plaintext")
os.environ.setdefault("HEXTON_ALLOW_PLAINTEXT_CREDENTIALS", "true")
os.environ.setdefault("HEXTON_ALLOW_PLAINTEXT", "1")

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

from hexton.bootstrap import build_context  # noqa: E402
from hexton.config import get_settings  # noqa: E402
from hexton.connectors.registry import BuildProfile  # noqa: E402
from hexton.domain.report_schema import SCHEMA_VERSION  # noqa: E402
from hexton.connectors.sheets import KEY_COLUMN  # noqa: E402

#: Obvious fakes, matching the backend suite. They are written only into the
#: throwaway store; they are never returned by any endpoint.
FAKE_ACCESS = "access-token"
FAKE_REFRESH = "refresh-token"


def migrate() -> None:
    settings = get_settings()
    settings.ensure_directories()
    config = AlembicConfig(str(BACKEND / "alembic.ini"))
    # `script_location` in alembic.ini is relative to the working directory, and this
    # script is run from wherever the launcher happens to be. Pinning it to an absolute
    # path makes the fixture independent of that.
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{settings.database_path}")
    alembic_command.upgrade(config, "head")


def seed() -> dict[str, object]:
    from conftest import make_clip, make_job, make_media
    from hexton.db.models import (
        ChannelIdentity,
        PlatformConnection,
        RemoteObservation,
        SheetReportRecord,
    )
    from hexton.services.sheets import upsert_config

    settings = get_settings()
    ctx = build_context(settings, profile=BuildProfile.DEVELOPMENT, log_to_stderr=False)
    now = datetime.now(UTC)

    clip_path = DATA_DIR / "e2e-clip.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    make_clip(clip_path)

    summary: dict[str, object] = {"jobs": {}}
    try:
        with ctx.sessions() as session:
            # ------------------------------------------------------------------
            # Connections and channel identities
            # ------------------------------------------------------------------
            youtube = PlatformConnection(
                provider="youtube",
                label="Hexton test channel",
                status="active",
                scopes=[
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly",
                ],
                granted_at=now - timedelta(days=20),
                # Deliberately in the past: the Channels screen must say that
                # reauthorization is required rather than showing a green light.
                token_expires_at=now - timedelta(days=3),
                last_token_check_at=now - timedelta(days=3),
                last_token_check_ok=False,
            )
            session.add(youtube)
            session.flush()
            ctx.credentials.write(f"conn:{youtube.public_id}:access", FAKE_ACCESS)
            ctx.credentials.write(f"conn:{youtube.public_id}:refresh", FAKE_REFRESH)

            confirmed = ChannelIdentity(
                connection_id=youtube.id,
                channel_id="UC-e2e-main",
                title="Hexton Test Channel",
                handle="@hextontest",
                verification_source="channels_list_mine",
                verified_at=now - timedelta(days=20),
                operator_confirmed_at=now - timedelta(days=20),
            )
            unconfirmed = ChannelIdentity(
                connection_id=youtube.id,
                channel_id="UC-e2e-brand",
                title="Brand account channel",
                handle="@hextonbrand",
                verification_source="channels_list_mine",
                verified_at=now - timedelta(days=20),
                is_brand_candidate=True,
            )
            session.add_all([confirmed, unconfirmed])

            sheets = PlatformConnection(
                provider="sheets",
                label="Google Sheets reporting",
                status="active",
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
                granted_at=now - timedelta(days=10),
                token_expires_at=now + timedelta(days=30),
            )
            session.add(sheets)
            session.flush()
            ctx.credentials.write(f"conn:{sheets.public_id}:access", FAKE_ACCESS)
            ctx.credentials.write(f"conn:{sheets.public_id}:refresh", FAKE_REFRESH)
            session.flush()

            # ------------------------------------------------------------------
            # Media
            # ------------------------------------------------------------------
            media = make_media(session, clip_path)

            # ------------------------------------------------------------------
            # Jobs across the states the UI has to tell apart
            # ------------------------------------------------------------------
            def job(title: str, state: str, *, publish: datetime | None, connection=None):
                return make_job(
                    session,
                    media,
                    connection if connection is not None else youtube,
                    title=title,
                    state=state,
                    publish_at=publish,
                    channel_id=(confirmed.id if connection is None else None),
                )

            draft = job("Zeta draft waiting for a time", "JOB-DRAFT", publish=None)
            ready = job(
                "Alpha ready for tomorrow", "JOB-READY", publish=now + timedelta(days=1, hours=3)
            )
            scheduled = job(
                "Bravo uploaded not verified", "JOB-SCHEDULED", publish=now + timedelta(hours=6)
            )
            published = job(
                "Charlie published and verified", "JOB-PUBLISHED", publish=now - timedelta(hours=2)
            )
            review = job(
                "Delta upload outcome uncertain", "JOB-NEEDS_REVIEW", publish=now + timedelta(hours=2)
            )
            retry = job(
                "Echo retry is scheduled", "JOB-FAILED_RETRY", publish=now + timedelta(hours=9)
            )
            uploading = job(
                "Foxtrot still transferring", "JOB-UPLOADING", publish=now + timedelta(hours=4)
            )

            # A finished transfer whose publication is still unverified. The remote
            # state is not a column on the job — it is read from the latest
            # observation, which is where the evidence lives.
            scheduled.video_id = "vid-bravo-sched"
            scheduled.video_url = "https://www.youtube.com/watch?v=vid-bravo-sched"
            scheduled.uploaded_at = now - timedelta(minutes=40)
            scheduled.published_at_remote = None
            scheduled.poll_result = "not_yet"
            scheduled.last_polled_at = now - timedelta(minutes=5)
            session.add(
                RemoteObservation(
                    job_id=scheduled.id,
                    observed_at=now - timedelta(minutes=5),
                    video_id="vid-bravo-sched",
                    remote_state="remote.scheduled",
                    privacy_status="private",
                    publish_at=now + timedelta(hours=6),
                )
            )

            # Transfer finished *and* observed public — the only route to Published.
            published.video_id = "vid-charlie-live"
            published.video_url = "https://www.youtube.com/watch?v=vid-charlie-live"
            published.uploaded_at = now - timedelta(hours=2, minutes=20)
            published.published_at_remote = now - timedelta(hours=2, minutes=15)
            published.poll_result = "confirmed"
            published.last_polled_at = now - timedelta(minutes=3)
            session.add(
                RemoteObservation(
                    job_id=published.id,
                    observed_at=now - timedelta(hours=2, minutes=15),
                    video_id="vid-charlie-live",
                    remote_state="remote.public",
                    privacy_status="public",
                    publish_at=now - timedelta(hours=2, minutes=15),
                )
            )

            # An ambiguous `videos.insert`: never retried automatically, and the
            # flag is never cleared without a human answering the question.
            review.insert_may_have_succeeded = True
            review.attempt = 1
            review.failure_kind = "insert_may_have_succeeded"
            review.failure_message = (
                "The upload request timed out after the bytes were sent, so the video "
                "may or may not exist."
            )
            review.last_polled_at = now - timedelta(minutes=10)
            session.add(
                RemoteObservation(
                    job_id=review.id,
                    observed_at=now - timedelta(minutes=10),
                    remote_state="remote.unknown",
                    inconclusive=True,
                    note="Could not determine whether a video was created.",
                )
            )

            # A refusal with a scheduled retry, so the queue can show both fields.
            retry.attempt = 1
            retry.next_attempt_at = now + timedelta(minutes=12)
            retry.failure_kind = "quota_exhausted"
            retry.failure_message = "Daily quota exhausted; next attempt scheduled."

            uploading.progress_percent = 42
            session.add(
                RemoteObservation(
                    job_id=uploading.id,
                    observed_at=now,
                    remote_state="remote.not_uploaded",
                    privacy_status="private",
                )
            )

            # ------------------------------------------------------------------
            # Reporting: destination, and one row per delivery outcome
            # ------------------------------------------------------------------
            config = upsert_config(
                session,
                {
                    "connection_id": sheets.id,
                    # The fake Sheets connector's default visibility is
                    # ("sheet-1", "Report"); a fixture that pointed elsewhere would
                    # make every "connection test" fail for a reason that has nothing
                    # to do with the UI under test.
                    "spreadsheet_id": "sheet-1",
                    "worksheet": "Report",
                    "enabled": True,
                    "verified_at": now - timedelta(days=1),
                    "last_test_write_at": now - timedelta(days=1),
                    "last_sync_at": now - timedelta(hours=2),
                    "last_sync_status": "ok",
                },
                now=now,
            )
            config.last_sync_at = now - timedelta(hours=2)
            config.last_sync_status = "ok"

            def outbox(
                job_row,
                status: str,
                *,
                attempts: int,
                error: str | None,
                synced_at=None,
                outcome: str,
            ) -> SheetReportRecord:
                report_date = (job_row.schedule.publish_at_utc or now).date().isoformat()
                payload = {
                    KEY_COLUMN: f"SCH_{report_date}_{job_row.public_id}_{SCHEMA_VERSION}",
                    "Schedule ID": job_row.public_id,
                    "Report Date": report_date,
                    "YouTube Video ID": job_row.video_id or "",
                    "Upload Outcome": outcome,
                }
                # One row per job: the table carries a unique FK, and the Sync Key is
                # unique on top of it, so the local side cannot duplicate either.
                row = SheetReportRecord(
                    job_id=job_row.id,
                    sync_key=payload[KEY_COLUMN],
                    status=status,
                    attempts=attempts,
                    last_error=error,
                    last_synced_at=synced_at,
                    payload=payload,
                )
                session.add(row)
                return row

            outbox(
                published,
                "synced",
                attempts=1,
                error=None,
                synced_at=now - timedelta(hours=2),
                outcome="Published",
            )
            outbox(scheduled, "pending", attempts=0, error=None, outcome="Pending")
            outbox(
                uploading,
                "failed",
                attempts=3,
                error="The Sheets API refused the append with 429: quota exceeded.",
                outcome="Uploading",
            )
            outbox(
                retry,
                "ambiguous",
                attempts=1,
                error=(
                    "The append was written but the response was lost. The destination is "
                    "re-read before any retry so a landed row is never duplicated."
                ),
                outcome="Pending",
            )

            session.flush()

            summary["jobs"] = {
                "draft": draft.id,
                "ready": ready.id,
                "scheduled": scheduled.id,
                "published": published.id,
                "review": review.id,
                "retry": retry.id,
                "uploading": uploading.id,
            }
            summary["media_id"] = media.id
            summary["youtube_connection_id"] = youtube.id
            summary["sheets_connection_id"] = sheets.id
            summary["spreadsheet_id"] = "sheet-1"
            summary["report_date"] = (published.schedule.publish_at_utc or now).date().isoformat()
            summary["data_dir"] = str(DATA_DIR)
            session.commit()
    finally:
        ctx.close()

    return summary


def main() -> int:
    if DATA_DIR.exists():
        import shutil

        # A fresh directory every run: a fixture that inherits last run's queue is a
        # fixture whose assertions depend on test order.
        shutil.rmtree(DATA_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    migrate()
    result = seed()
    (DATA_DIR / "fixtures.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
