"""Job endpoints: the content library, the queue, and the publication controls."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from hexton.api.deps import CtxDep, SessionDep, ZoneDep
from hexton.api.schemas import (
    CancelIn,
    EventOut,
    JobCreateIn,
    JobDetailOut,
    JobListOut,
    JobOut,
    JobUpdateIn,
    ObservationOut,
    ReconcileIn,
    ReconcileOut,
    RescheduleIn,
    UploadSessionOut,
    ValidateOut,
)
from hexton.api.views import job_to_out
from hexton.bootstrap import AppContext
from hexton.db import events as ev
from hexton.db.models import (
    ContentMetadata,
    JobEvent,
    RemoteObservation,
    ScheduledJob,
    UploadSession,
)
from hexton.domain.jobstate import TRANSITIONS, JobState
from hexton.domain.scheduling import ensure_utc
from hexton.reconciliation.ladder import operator_decision, reconcile
from hexton.services import jobs as svc

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

EAGER = (
    selectinload(ScheduledJob.schedule),
    selectinload(ScheduledJob.content),
    selectinload(ScheduledJob.media),
    selectinload(ScheduledJob.connection),
    selectinload(ScheduledJob.channel),
    selectinload(ScheduledJob.observations),
    selectinload(ScheduledJob.upload_sessions),
)


def _get(session: Session, job_id: int) -> ScheduledJob:
    job = session.execute(
        select(ScheduledJob).where(ScheduledJob.id == job_id).options(*EAGER)
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} does not exist.")
    return job


@router.get("", response_model=JobListOut)
def list_jobs(
    response: Response,
    q: str | None = Query(default=None, max_length=200),
    state: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobListOut:
    # Filtering, ordering, and paging all happen in the database. This used to load
    # *every* job with all seven of its eager relationships and then sort and slice in
    # Python, so a list page cost the whole table rather than one page of it — and the
    # cost grew without bound as the schedule accumulated history.
    stmt = select(ScheduledJob)
    if state:
        stmt = stmt.where(ScheduledJob.state == state)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.join(ScheduledJob.content).where(
            (ContentMetadata.title.like(like)) | (ContentMetadata.description.like(like))
        )

    # Counted from the *filtered* statement, before the eager options are attached, so
    # ``total`` still means "how many match this query" and pagination stays honest.
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        session.execute(
            stmt.options(*EAGER).order_by(ScheduledJob.id.desc()).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )

    counts = dict(
        session.execute(select(ScheduledJob.state, func.count()).group_by(ScheduledJob.state)).all()
    )
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    return JobListOut(
        items=[job_to_out(j, zone, now=now) for j in rows],
        total=total,
        states={s.value: counts.get(s.value, 0) for s in JobState},
    )


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(
    job_id: int,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobDetailOut:
    job = _get(session, job_id)
    now = datetime.now(UTC)
    events = (
        session.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job.id)
            .order_by(JobEvent.occurred_at.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    return JobDetailOut(
        job=job_to_out(job, zone, now=now),
        events=[
            EventOut(
                id=e.id,
                occurred_at=e.occurred_at,
                kind=e.kind,
                message=e.message,
                payload=e.payload,
            )
            for e in events
        ],
        observations=[
            ObservationOut(
                id=o.id,
                observed_at=o.observed_at,
                video_id=o.video_id,
                remote_state=o.remote_state,
                privacy_status=o.privacy_status,
                upload_status=o.upload_status,
                processing_status=o.processing_status,
                publish_at=o.publish_at,
            )
            for o in sorted(job.observations, key=lambda o: o.id, reverse=True)
        ],
        upload_sessions=[
            UploadSessionOut(
                id=s.id,
                phase=s.phase,
                bytes_uploaded=s.bytes_uploaded,
                total_bytes=s.total_bytes,
                attempt=s.attempt,
                insert_may_have_succeeded=s.insert_may_have_succeeded,
                created_at=s.created_at,
            )
            for s in sorted(job.upload_sessions, key=lambda s: s.id, reverse=True)
        ],
        allowed_transitions=sorted(s.value for s in TRANSITIONS[JobState(job.state)]),
        candidates=_reconcilable_candidates(ctx, session, job),
        can_requeue=_is_awaiting_review(job),
    )


def _is_awaiting_review(job: ScheduledJob) -> bool:
    """True when the upload outcome is genuinely uncertain.

    Kept separate from the reconcile endpoint's own guard so that both read the same
    rule. ``insert_may_have_succeeded`` is deliberately never cleared automatically
    (``DF-12``), so this stays true until an operator resolves it.
    """
    return bool(job.insert_may_have_succeeded) or JobState(job.state) is JobState.NEEDS_REVIEW


def _reconcilable_candidates(ctx: AppContext, session: Session, job: ScheduledJob) -> list[str]:
    """The IDs an operator may confirm, surfaced on the detail view.

    Previously these only ever appeared inside a ``reconcile_unresolved`` event
    payload, which the frontend has to page through and parse — so the one control that
    resolves a duplicate-risk situation was reachable only by reading raw JSON out of an
    audit record.
    """
    if not _is_awaiting_review(job):
        return []
    return _candidate_ids(ctx, session, job)


@router.post("/validate", response_model=ValidateOut)
def validate(
    payload: JobCreateIn,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> ValidateOut:
    """Dry-run validation. Used by the editor so problems surface before saving.

    There is deliberately no second ``check_publish_time`` call here. This endpoint used
    to re-run it and append the result *only when* ``prepare`` had produced no schedule
    issues at all — a conditional that could never fire usefully, because ``prepare``
    runs the identical check with a superset of the parameters, and could only ever
    diverge if one site were edited without the other. ``prepare`` is the single source
    of truth for schedule issues, including the ``DF-04`` past-publication-time block.
    """
    now = datetime.now(UTC)
    prepared = svc.prepare(ctx, session, payload, now=now)
    return ValidateOut(
        ok=prepared.ok,
        issues=prepared.issues,
        effective_publish_at_utc=(
            ensure_utc(payload.publish_at_utc) if payload.publish_at_utc else None
        ),
        schedule_issues=prepared.schedule_issues,
    )


@router.post("", response_model=JobOut, status_code=201)
def create(
    payload: JobCreateIn,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobOut:
    try:
        job = svc.create_job(ctx, session, payload, now=datetime.now(UTC), queue=payload.queue)
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return job_to_out(_get(session, job.id), zone)


@router.put("/{job_id}", response_model=JobOut)
def update(
    job_id: int,
    payload: JobUpdateIn,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobOut:
    job = _get(session, job_id)
    try:
        svc.update_job(ctx, session, job, payload, now=datetime.now(UTC))
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return job_to_out(_get(session, job_id), zone)


@router.delete("/{job_id}", status_code=204)
def delete(job_id: int, session: Session = SessionDep) -> Response:
    job = _get(session, job_id)
    try:
        svc.delete_draft(session, job)
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return Response(status_code=204)


@router.post("/{job_id}/duplicate", response_model=JobOut, status_code=201)
def duplicate(
    job_id: int,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobOut:
    job = _get(session, job_id)
    clone = svc.duplicate_job(ctx, session, job, now=datetime.now(UTC))
    session.commit()
    return job_to_out(_get(session, clone.id), zone)


@router.post("/{job_id}/queue", response_model=JobOut)
def queue(
    job_id: int,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobOut:
    job = _get(session, job_id)
    try:
        svc.queue_job(session, job, now=datetime.now(UTC))
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return job_to_out(_get(session, job_id), zone)


@router.post("/{job_id}/retry", response_model=JobOut)
def retry(
    job_id: int,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobOut:
    job = _get(session, job_id)
    try:
        svc.retry_job(session, job, now=datetime.now(UTC))
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return job_to_out(_get(session, job_id), zone)


@router.post("/{job_id}/reschedule", response_model=JobOut)
def reschedule(
    job_id: int,
    payload: RescheduleIn,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> JobOut:
    job = _get(session, job_id)
    try:
        svc.reschedule_job(ctx, session, job, payload.publish_at_utc, now=datetime.now(UTC))
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return job_to_out(_get(session, job_id), zone)


@router.post("/{job_id}/cancel")
def cancel(
    job_id: int,
    payload: CancelIn,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> dict:
    job = _get(session, job_id)
    try:
        outcome = svc.cancel_job(
            ctx,
            session,
            job,
            now=datetime.now(UTC),
            delete_remote=payload.delete_remote,
            reason=payload.reason,
        )
    except svc.JobOperationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return {
        "local_cancelled": outcome.local_cancelled,
        "remote_attempted": outcome.remote_attempted,
        "remote_succeeded": outcome.remote_succeeded,
        "message": outcome.message,
        "job": job_to_out(_get(session, job_id), zone).model_dump(mode="json"),
    }


# ``POST /{job_id}/mention/confirm`` and ``POST /{job_id}/mention/clear`` used to live
# here. They were removed: this router is registered before
# :mod:`hexton.api.routers.mentions`, so FastAPI matched these paths first and the
# newer workflow routes were unreachable. The copies here also accepted an empty body
# and marked a mention VERIFIED with no attestation — which ``DF-19`` forbids. The
# mention workflow now has exactly one owner, in ``routers/mentions.py``.


RESOLUTION_ADOPT = "adopt"
RESOLUTION_NOT_UPLOADED = "not_uploaded"
RESOLUTION_CANCEL = "cancel"
RESOLUTIONS = (RESOLUTION_ADOPT, RESOLUTION_NOT_UPLOADED, RESOLUTION_CANCEL)


@router.post("/{job_id}/reconcile", response_model=ReconcileOut)
def reconcile_job(
    job_id: int,
    payload: ReconcileIn,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
    zone: ZoneInfo = ZoneDep,
) -> ReconcileOut:
    """Record the operator's decision about an uncertain upload outcome.

    ``DF-08``: an activity-feed or uploads-playlist match is evidence, not proof. The
    accepted video IDs are exactly those the ladder surfaced — the persisted ID, or
    live recent-upload matches — so a screen cannot attribute an unrelated upload to a
    schedule by typing an ID.

    Three dispositions rather than two. Choosing a video *adopts* it. Choosing nothing
    used to mean "cancel" by default, which left an operator who had checked YouTube,
    found no upload, and wanted the job re-run with no way to express it — the job was
    destroyed instead, and re-creating it lost the schedule, the media reference and the
    audit trail. ``not_uploaded`` is the assertive form of that: the operator states
    that no video exists, which is the only reading under which a second
    ``videos.insert`` cannot duplicate anything. It clears ``insert_may_have_succeeded``
    as a recorded consequence of that assertion, never as a default.
    """
    job = _get(session, job_id)
    if not _is_awaiting_review(job):
        raise HTTPException(status_code=409, detail="This job has no uncertain outcome to resolve.")

    candidates = _candidate_ids(ctx, session, job)
    resolution = payload.resolution or (RESOLUTION_ADOPT if payload.video_id else RESOLUTION_CANCEL)
    if resolution not in RESOLUTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown resolution {resolution!r}. Expected one of {', '.join(RESOLUTIONS)}.",
        )
    if resolution == RESOLUTION_ADOPT and not payload.video_id:
        raise HTTPException(
            status_code=422,
            detail="Adopting requires a video_id. Pass resolution='not_uploaded' to "
            "re-run a job you have confirmed does not exist.",
        )
    if resolution != RESOLUTION_ADOPT and payload.video_id:
        raise HTTPException(
            status_code=422,
            detail="A video_id may only be sent with resolution='adopt'.",
        )

    result = replace(
        reconcile(
            has_session_uri=bool(
                job.upload_sessions and any(s.session_uri for s in job.upload_sessions)
            ),
            has_video_id=bool(job.video_id),
        ),
        candidates=tuple(candidates),
    )

    now = datetime.now(UTC)
    if resolution == RESOLUTION_ADOPT:
        try:
            decided = operator_decision(result, chosen_video_id=payload.video_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job.video_id = payload.video_id
        job.video_url = f"https://www.youtube.com/watch?v={payload.video_id}"
        job.insert_may_have_succeeded = False
        job.uploaded_at = job.uploaded_at or now
        job.state = (
            JobState.SCHEDULED.value
            if job.schedule.publish_at_utc and job.schedule.publish_at_utc > now
            else JobState.UPLOADED.value
        )
        job.next_poll_at = now
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_RECONCILED,
            message=decided.reason,
            payload={
                "video_id": payload.video_id,
                "resolution": RESOLUTION_ADOPT,
                # Kept alongside ``resolution``: this is attestation, not verification.
                # ``DF-08`` makes the distinction load-bearing, and it has to survive in
                # the audit record rather than only in the reason string.
                "operator_confirmed": True,
            },
        )
    elif resolution == RESOLUTION_NOT_UPLOADED:
        # Guarded by the transition table: only a job already in NEEDS_REVIEW may go
        # back to the queue, so this can never be used to re-run a job that is merely
        # mid-flight. Nothing is uploaded by this call; it only re-queues.
        if JobState(job.state) is not JobState.NEEDS_REVIEW:
            raise HTTPException(
                status_code=409,
                detail="Only a job held for review can be re-run. Cancel it instead.",
            )
        job.insert_may_have_succeeded = False
        job.state = JobState.QUEUED.value
        job.next_attempt_at = now
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_RECONCILED,
            message="The operator confirmed by hand that no matching upload exists on "
            "YouTube, and asked for the job to run again. This is a recorded human "
            "decision; a second videos.insert may follow because the operator has "
            "stated that the first one did not land.",
            payload={"resolution": RESOLUTION_NOT_UPLOADED, "candidates": candidates},
        )
    else:
        job.state = JobState.CANCELLED.value
        job.cancelled_at = now
        ev.record_event(
            session,
            job_id=job.id,
            kind=ev.KIND_RECONCILED,
            message="The operator rejected every candidate. The job is cancelled "
            "locally; any video that exists on YouTube was left untouched and must be "
            "checked by hand.",
            payload={"resolution": RESOLUTION_CANCEL, "candidates": candidates},
        )
    session.commit()
    job = _get(session, job_id)
    return ReconcileOut(
        job=job_to_out(job, zone),
        candidates=candidates,
        can_requeue=_is_awaiting_review(job),
    )


def _candidate_ids(ctx: AppContext, session: Session, job: ScheduledJob) -> list[str]:
    """Video IDs an operator is allowed to confirm as this schedule's.

    The persisted ID is always included when present. Beyond that, live recent-upload
    matches are offered as *candidates* only — the operator still has to choose, which
    is the whole point of the R3 step (``08`` §7.2).
    """
    found: list[str] = [job.video_id] if job.video_id else []
    if job.connection is None or job.channel is None or job.connection.status != "active":
        return found
    key = f"conn:{job.connection.public_id}"
    access = ctx.credentials.read(f"{key}:access")
    if not access:
        refresh = ctx.credentials.read(f"{key}:refresh")
        if not refresh:
            return found
        from hexton.connectors.base import ConnectorError

        try:
            access = ctx.connector.refresh(refresh).access_token
        except ConnectorError:
            return found
        ctx.credentials.write(f"{key}:access", access)
    try:
        for vid in ctx.connector.list_recent_upload_ids(access, job.channel.channel_id):
            if vid not in found:
                found.append(vid)
    except Exception:
        pass
    return found[:25]


@router.get("/{job_id}/events", response_model=list[EventOut])
def job_events(job_id: int, session: Session = SessionDep) -> list[EventOut]:
    job = _get(session, job_id)
    rows = (
        session.execute(
            select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.occurred_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        EventOut(
            id=e.id, occurred_at=e.occurred_at, kind=e.kind, message=e.message, payload=e.payload
        )
        for e in rows
    ]


@router.get("/{job_id}/observations", response_model=list[ObservationOut])
def job_observations(job_id: int, session: Session = SessionDep) -> list[ObservationOut]:
    job = _get(session, job_id)
    rows = (
        session.execute(
            select(RemoteObservation)
            .where(RemoteObservation.job_id == job.id)
            .order_by(RemoteObservation.observed_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        ObservationOut(
            id=o.id,
            observed_at=o.observed_at,
            video_id=o.video_id,
            remote_state=o.remote_state,
            privacy_status=o.privacy_status,
            upload_status=o.upload_status,
            processing_status=o.processing_status,
            publish_at=o.publish_at,
        )
        for o in rows
    ]


@router.get("/{job_id}/sessions", response_model=list[UploadSessionOut])
def job_sessions(job_id: int, session: Session = SessionDep) -> list[UploadSessionOut]:
    job = _get(session, job_id)
    rows = (
        session.execute(
            select(UploadSession)
            .where(UploadSession.job_id == job.id)
            .order_by(UploadSession.id.desc())
        )
        .scalars()
        .all()
    )
    return [
        UploadSessionOut(
            id=s.id,
            phase=s.phase,
            bytes_uploaded=s.bytes_uploaded,
            total_bytes=s.total_bytes,
            attempt=s.attempt,
            insert_may_have_succeeded=s.insert_may_have_succeeded,
            created_at=s.created_at,
        )
        for s in rows
    ]
