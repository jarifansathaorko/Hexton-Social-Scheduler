"""Worker liveness, read from the durable heartbeat both processes share.

The API and the worker are separate processes, so nothing the API believes about its
own memory says whether the queue is being serviced. This module is the one place
that answers that question, so ``/api/health``, ``/api/worker/status`` and the
dashboard cannot drift apart — which they did: ``health`` reported ``worker_alive:
true`` unconditionally while the dashboard said ``false`` on the same machine with
the worker deliberately not started.

Two settings govern it, and neither is invented here:

* ``heartbeat_seconds`` is how often a healthy worker is expected to beat. The API
  tolerates **two** missed intervals before it reports ``false``, because a single
  late beat is a hiccup and a run of them is a stopped worker.
* The worker beats on every pass, which on the default two-second poll is well
  inside that budget; the window is what makes the answer survive a slow pass
  without making it a lie after a crash.

``02`` §7.4 requires that closing the browser does not stop the worker. Making
liveness *visible* is the other half of that promise: an operator has to be able to
tell "worker running" from "browser open".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hexton.db.models import WorkerHeartbeat

#: A worker counts as running until it has missed this many expected beats.
MISSED_BEATS_TOLERATED = 2


def latest_heartbeat(session: Session) -> WorkerHeartbeat | None:
    """The most recent heartbeat row, or ``None`` if no worker has ever run."""
    return (
        session.execute(select(WorkerHeartbeat).order_by(WorkerHeartbeat.id.desc()))
        .scalars()
        .first()
    )


def beat(
    session: Session,
    *,
    owner: str,
    jobs_claimed: int,
    pid: int | None = None,
    started_at: datetime | None = None,
    now: datetime | None = None,
) -> WorkerHeartbeat:
    """Publish that this worker is alive.

    One row per owner, updated in place. The loop runs every couple of seconds, so
    appending would fill the table faster than anything reads it — and a single
    up-to-date row is also all the reader needs to answer "is anything running?".
    """
    moment = now or datetime.now(UTC)
    row = (
        session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.owner == owner))
        .scalars()
        .first()
    )
    if row is None:
        row = WorkerHeartbeat(
            owner=owner,
            beat_at=moment,
            pid=pid,
            jobs_claimed=jobs_claimed,
            started_at=started_at,
        )
        session.add(row)
    else:
        row.beat_at = moment
        row.jobs_claimed = jobs_claimed
        row.pid = pid
        row.started_at = started_at or row.started_at
    session.commit()
    return row


def is_alive(
    row: WorkerHeartbeat | None,
    *,
    heartbeat_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Whether a specific heartbeat row is still fresh.

    Split from :func:`worker_is_alive` so a caller that already fetched the row —
    because it also wants to report *who* is beating — does not query it twice.
    """
    if row is None or row.beat_at is None:
        return False
    beat_at = row.beat_at
    if beat_at.tzinfo is None:
        # The schema forbids this; treat it as UTC rather than raising, because a
        # liveness probe that 500s tells an operator less than a `false` does.
        beat_at = beat_at.replace(tzinfo=UTC)
    window = timedelta(seconds=heartbeat_seconds * MISSED_BEATS_TOLERATED)
    moment = now or datetime.now(UTC)
    return (moment - beat_at) < window


def worker_is_alive(
    session: Session,
    *,
    heartbeat_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Whether a heartbeat arrived inside the tolerated window."""
    return is_alive(
        latest_heartbeat(session),
        heartbeat_seconds=heartbeat_seconds,
        now=now,
    )
