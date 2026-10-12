"""Application settings, worker status, and the dashboard summary."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from hexton import __version__, quota
from hexton.api.deps import CtxDep, SessionDep, ZoneDep
from hexton.api.schemas import DashboardOut, JobOut, SettingsOut
from hexton.api.views import job_to_out
from hexton.bootstrap import AppContext
from hexton.db.models import ScheduledJob
from hexton.domain.jobstate import JobState

router = APIRouter(prefix="/api", tags=["system"])


class SettingsUpdateIn(BaseModel):
    display_timezone: str | None = None
    min_lead_minutes: int | None = Field(default=None, ge=0, le=1440)
    warning_lead_minutes: int | None = Field(default=None, ge=0, le=10080)
    missed_grace_minutes: int | None = Field(default=None, ge=0, le=1440)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class WorkerStatusOut(BaseModel):
    owner: str | None
    last_heartbeat_at: datetime | None
    alive: bool
    lease_seconds: int
    poll_seconds: float


@router.get("/settings", response_model=SettingsOut)
def get_settings(ctx: AppContext = CtxDep) -> SettingsOut:
    now = datetime.now(UTC)
    return SettingsOut(
        display_timezone=ctx.settings.display_timezone,
        min_lead_minutes=ctx.settings.min_lead_minutes,
        warning_lead_minutes=ctx.settings.warning_lead_minutes,
        missed_grace_minutes=ctx.settings.missed_grace_minutes,
        lease_seconds=ctx.settings.lease_seconds,
        worker_poll_seconds=ctx.settings.worker_poll_seconds,
        retention_days=ctx.settings.retention_days,
        connector_kind=ctx.settings.connector_kind,
        connector_ready=ctx.connector.reaches_platform,
        build_profile=ctx.profile.value,
        credential_store_kind=ctx.credentials.name,
        data_dir=str(ctx.settings.data_dir),
        quota_daily_units=quota.DAILY_UNITS,
        quota_used_today=quota.units_used(ctx.engine, "youtube", now),
        quota_remaining=quota.remaining(ctx.engine, "youtube", now),
        quota_resets_at=quota.next_reset(now),
    )


@router.put("/settings", response_model=SettingsOut)
def update_settings(payload: SettingsUpdateIn, ctx: AppContext = CtxDep) -> SettingsOut:
    """Adjust runtime settings.

    These are process-lifetime settings rather than database rows on purpose: the
    worker reads them at startup, so silently changing the lease length underneath a
    running worker would make its expiry arithmetic wrong. They are applied on restart.
    """
    from hexton.domain.scheduling import resolve_zone

    if payload.display_timezone is not None:
        resolve_zone(payload.display_timezone)
        ctx.settings.display_timezone = payload.display_timezone
    for name in (
        "min_lead_minutes",
        "warning_lead_minutes",
        "missed_grace_minutes",
        "retention_days",
    ):
        value = getattr(payload, name)
        if value is not None:
            setattr(ctx.settings, name, value)
    return get_settings(ctx)


@router.get("/worker/status", response_model=WorkerStatusOut)
def worker_status(session: Session = SessionDep, ctx: AppContext = CtxDep) -> WorkerStatusOut:
    """Whether a worker is alive, read from its durable heartbeat.

    The API must never guess this from its own state: the worker is a separate process
    and the browser closing does not stop it, which is precisely why its liveness has
    to come from somewhere both can see.
    """
    from hexton.worker.heartbeat import is_alive, latest_heartbeat

    row = latest_heartbeat(session)
    now = datetime.now(UTC)
    alive = is_alive(row, heartbeat_seconds=ctx.settings.heartbeat_seconds, now=now)
    return WorkerStatusOut(
        owner=row.owner if row else None,
        last_heartbeat_at=row.beat_at if row else None,
        alive=alive,
        lease_seconds=ctx.settings.lease_seconds,
        poll_seconds=ctx.settings.worker_poll_seconds,
    )


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> DashboardOut:
    now = datetime.now(UTC)
    rows = list(
        session.execute(
            select(ScheduledJob).options(
                selectinload(ScheduledJob.schedule),
                selectinload(ScheduledJob.content),
                selectinload(ScheduledJob.media),
                selectinload(ScheduledJob.connection),
                selectinload(ScheduledJob.channel),
                selectinload(ScheduledJob.observations),
            )
        ).scalars()
    )
    by_state = {s.value: 0 for s in JobState}
    for row in rows:
        by_state[row.state] = by_state.get(row.state, 0) + 1

    def out(row: ScheduledJob) -> JobOut:
        return job_to_out(row, zone, now=now)

    upcoming = sorted(
        (
            r
            for r in rows
            if r.state in (JobState.QUEUED.value, JobState.READY.value, JobState.SCHEDULED.value)
            and r.schedule.publish_at_utc is not None
        ),
        key=lambda r: r.schedule.publish_at_utc,
    )
    attention = [
        r
        for r in rows
        if r.state
        in (
            JobState.NEEDS_REVIEW.value,
            JobState.BLOCKED.value,
            JobState.FAILED.value,
            JobState.FAILED_RETRY.value,
            JobState.MISSED_WINDOW.value,
        )
    ]
    uploading = [r for r in rows if r.state == JobState.UPLOADING.value]

    from hexton.worker.heartbeat import worker_is_alive

    return DashboardOut(
        total=len(rows),
        by_state=by_state,
        upcoming=[out(r) for r in upcoming[:8]],
        needs_attention=[out(r) for r in attention[:8]],
        uploading=[out(r) for r in uploading[:8]],
        quota_remaining=quota.remaining(ctx.engine, "youtube", now),
        worker_alive=worker_is_alive(
            session,
            heartbeat_seconds=ctx.settings.heartbeat_seconds,
            now=now,
        ),
    )


@router.get("/counts")
def counts(session: Session = SessionDep) -> dict[str, int]:
    rows = session.execute(
        select(ScheduledJob.state, func.count()).group_by(ScheduledJob.state)
    ).all()
    return dict(rows)


__version__ = __version__
