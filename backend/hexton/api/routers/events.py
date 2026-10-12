"""Live updates over Server-Sent Events.

SSE rather than WebSockets because the traffic is one-directional: the worker changes
the database, the browser shows it. There is nothing for the browser to send back that
is not an ordinary HTTP request, and a WebSocket would add a second, harder-to-secure
channel for no gain.

The stream reads from the **database**, not from in-process memory. That is deliberate:
the API and the worker are separate processes, so an in-memory pub/sub would silently
miss every update the other process made — which is exactly the class of bug this
application cannot afford. The cost is a cheap polling query per tick, and the benefit
is that the stream shows the truth regardless of which process produced it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

from hexton.api.deps import CtxDep, ZoneDep
from hexton.api.views import job_to_out
from hexton.bootstrap import AppContext
from hexton.db.models import ScheduledJob
from hexton.domain.scheduling import ensure_utc

router = APIRouter(prefix="/api/events", tags=["events"])

EAGER = (
    selectinload(ScheduledJob.schedule),
    selectinload(ScheduledJob.content),
    selectinload(ScheduledJob.media),
    selectinload(ScheduledJob.connection),
    selectinload(ScheduledJob.channel),
    selectinload(ScheduledJob.observations),
)

#: How often the stream re-reads the database. Short enough to feel live, long enough
#: that a browser tab left open all day costs nothing noticeable.
TICK_SECONDS = 1.5


@router.get("/stream")
async def stream(request: Request, ctx: AppContext = CtxDep, zone: ZoneInfo = ZoneDep):
    async def generate():
        cursor = 0
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            rows, cursor = await run_in_threadpool(_changed, ctx, cursor)
            payload = {
                "type": "jobs",
                "at": datetime.now(UTC).isoformat(),
                "jobs": rows,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(TICK_SECONDS)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            # SSE survives a dropped connection better with no proxy buffering at all.
        },
    )


def _changed(ctx: AppContext, cursor: int) -> tuple[list[dict], int]:
    """Every job updated since ``cursor``, plus the new cursor.

    ``updated_at`` moves on progress writes, lease changes and state changes, so this
    one ordering catches all of them without a second bookkeeping column.
    """
    session = ctx.sessions()
    try:
        top = session.execute(
            select(ScheduledJob.updated_at).order_by(ScheduledJob.id.desc()).limit(1)
        ).scalar_one_or_none()
        new_cursor = int(ensure_utc(top).timestamp() * 1000) if top else cursor
        if new_cursor == cursor:
            return [], cursor
        rows = (
            session.execute(select(ScheduledJob).where(ScheduledJob.id > 0).options(*EAGER))
            .scalars()
            .all()
        )
        zone = ZoneInfo(ctx.settings.display_timezone)
        now = datetime.now(UTC)
        changed = [
            job_to_out(r, zone, now=now).model_dump(mode="json")
            for r in rows
            if int(ensure_utc(r.updated_at).timestamp() * 1000) > cursor
        ]
        return changed, new_cursor
    finally:
        session.close()


@router.get("/job/{job_id}")
async def job_stream(
    job_id: int, request: Request, ctx: AppContext = CtxDep, zone: ZoneInfo = ZoneDep
):
    """A single job's changes, for the details drawer."""

    async def generate():
        cursor = 0
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            payload, cursor = await run_in_threadpool(_one, ctx, job_id, cursor, zone)
            if payload is not None:
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(TICK_SECONDS)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def _one(ctx: AppContext, job_id: int, cursor: int, zone: ZoneInfo):
    session = ctx.sessions()
    try:
        job = session.execute(
            select(ScheduledJob).where(ScheduledJob.id == job_id).options(*EAGER)
        ).scalar_one_or_none()
        if job is None:
            return None, cursor
        stamp = int(ensure_utc(job.updated_at).timestamp() * 1000)
        if stamp <= cursor:
            return None, cursor
        return (
            {
                "type": "job",
                "at": datetime.now(UTC).isoformat(),
                "job": job_to_out(job, zone).model_dump(mode="json"),
            },
            stamp,
        )
    finally:
        session.close()
