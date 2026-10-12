"""Append-only job events.

Every state change, every remote call, and every decision the worker makes is written
here. This is the audit trail the operator reads when something went wrong at 03:00,
so it is append-only: nothing in the application updates or deletes a ``JobEvent``
while the job exists.

Events are written in the same transaction as the change they describe, which is what
makes the trail trustworthy — a state that changed without an event is a bug, not an
omission.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from hexton.db.models import JobEvent

#: Stable event kinds. The frontend groups on these, so they are a contract.
KIND_CREATED = "created"
KIND_VALIDATED = "validated"
KIND_VALIDATION_FAILED = "validation_failed"
KIND_QUEUED = "queued"
KIND_CLAIMED = "claimed"
KIND_PREFLIGHT = "preflight"
KIND_PREFLIGHT_FAILED = "preflight_failed"
KIND_AUTH_FAILED = "authorization_failed"
KIND_SESSION_STARTED = "upload_session_started"
KIND_UPLOAD_PROGRESS = "upload_progress"
KIND_UPLOAD_COMPLETE = "upload_complete"
KIND_UPLOAD_AMBIGUOUS = "upload_ambiguous"
KIND_RECONCILED = "reconciled"
KIND_RECONCILE_UNRESOLVED = "reconcile_unresolved"
KIND_SCHEDULED_REMOTE = "scheduled_remote"
KIND_RESCHEDULED = "rescheduled"
KIND_OBSERVATION = "observation"
KIND_PUBLISHED = "published"
KIND_VERIFICATION_PENDING = "verification_pending"
KIND_RETRY_SCHEDULED = "retry_scheduled"
KIND_RETRY_EXHAUSTED = "retry_exhausted"
KIND_BLOCKED = "blocked"
KIND_CANCELLED_LOCAL = "cancelled_local"
KIND_CANCELLED_REMOTE = "cancelled_remote"
KIND_CANCEL_REMOTE_FAILED = "cancel_remote_failed"
KIND_MISSED = "missed_window"
KIND_NEEDS_REVIEW = "needs_review"
KIND_LEASE_EXPIRED = "lease_expired"


def record_event(
    session: Session,
    *,
    job_id: int,
    kind: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> JobEvent:
    """Append one event. Caller owns the surrounding transaction."""
    event = JobEvent(job_id=job_id, kind=kind, message=message, payload=payload or {})
    session.add(event)
    return event
