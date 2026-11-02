"""Media library: add a clip, inspect it, and detect it changing later."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hexton.api.deps import CtxDep, SessionDep
from hexton.api.schemas import (
    MediaAddIn,
    MediaCheckOut,
    MediaIssueOut,
    MediaOut,
    MediaPickIn,
    MediaPickOut,
)
from hexton.bootstrap import AppContext
from hexton.db import events as ev
from hexton.db.models import MediaFile, ScheduledJob
from hexton.media import is_allowed_path, validate_file, verify_unchanged
from hexton.media.picker import pick_video_file

router = APIRouter(prefix="/api/media", tags=["media"])


@router.post("/pick", response_model=MediaPickOut)
def pick(payload: MediaPickIn | None = None) -> MediaPickOut:
    """Prompt the operator to select a video file using the native OS file picker."""
    initial = payload.initial_dir if payload else None
    chosen = pick_video_file(initial_dir=initial)
    return MediaPickOut(path=chosen)


@router.post("/check", response_model=MediaCheckOut)
def check(payload: MediaAddIn) -> MediaCheckOut:
    """Inspect a file without adding it, so the editor can show problems first.

    Nothing is written here: an operator browsing for a clip should not leave a
    half-registered record behind if they change their mind.

    Two fields are withheld for anything that is not a candidate clip. ``POST
    /api/media/check`` accepts an arbitrary absolute path, so without this it is a
    content-fingerprint oracle over the whole filesystem: asking it about the session
    signing key would return that file's SHA-256 and length. Existence and readability
    are still reported, because "the clip moved" is exactly what this endpoint exists
    to tell an operator.
    """
    report = validate_file(payload.path)
    is_clip = report.container is not None or is_allowed_path(report.path)
    if not report.container:
        report.sha256 = None
    if not is_clip:
        report.size_bytes = 0
    return MediaCheckOut(
        path=report.path,
        exists=report.exists,
        readable=report.readable,
        size_bytes=report.size_bytes,
        sha256=report.sha256,
        container=report.container,
        brand=report.brand,
        duration_seconds=report.duration_seconds,
        content_type=report.content_type,
        issues=[
            MediaIssueOut(code=i.code, message=i.message, blocking=i.blocking)
            for i in report.issues
        ],
        checked_at=report.checked_at,
    )


@router.post("", response_model=MediaOut, status_code=201)
def add(payload: MediaAddIn, session: Session = SessionDep) -> MediaOut:
    """Register a clip in the library, refusing one that cannot be uploaded."""
    report = validate_file(payload.path)
    if not report.ok:
        messages = "; ".join(i.message for i in report.blocking_issues)
        raise HTTPException(
            status_code=422,
            detail=f"That file cannot be added: {messages}",
        )

    from pathlib import Path

    path = str(Path(report.path))
    existing = session.execute(select(MediaFile).where(MediaFile.path == path)).scalar_one_or_none()
    now = datetime.now(UTC)
    record = existing or MediaFile(path=path)
    record.original_name = Path(report.path).name
    record.sha256 = report.sha256 or ""
    record.size_bytes = report.size_bytes
    record.duration_seconds = report.duration_seconds
    record.container = report.container
    record.is_valid = True
    record.validated_at = now
    record.verified_at = now
    record.issues = [
        {"code": i.code, "message": i.message, "blocking": i.blocking} for i in report.issues
    ]
    if existing is None:
        session.add(record)
    session.commit()
    session.refresh(record)
    return MediaOut.model_validate(record)


@router.post("/upload", response_model=MediaOut, status_code=201)
async def upload(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> MediaOut:
    """Accept video file stream from the browser, save to media directory, and register."""
    clean_name = Path(filename).name
    if not clean_name or clean_name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename provided.")

    ctx.settings.ensure_directories()
    dest_dir = ctx.settings.media_dir
    dest_path = dest_dir / clean_name

    stem = dest_path.stem
    suffix = dest_path.suffix
    counter = 1
    while dest_path.exists():
        dest_path = dest_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        with dest_path.open("wb") as buffer:
            async for chunk in request.stream():
                buffer.write(chunk)
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}") from exc

    report = validate_file(dest_path)
    if not report.ok:
        dest_path.unlink(missing_ok=True)
        messages = "; ".join(i.message for i in report.blocking_issues)
        raise HTTPException(
            status_code=422,
            detail=f"That file cannot be added: {messages}",
        )

    path_str = str(dest_path.resolve())
    existing = session.execute(
        select(MediaFile).where(MediaFile.path == path_str)
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    record = existing or MediaFile(path=path_str)
    record.original_name = clean_name
    record.sha256 = report.sha256 or ""
    record.size_bytes = report.size_bytes
    record.duration_seconds = report.duration_seconds
    record.container = report.container
    record.is_valid = True
    record.validated_at = now
    record.verified_at = now
    record.issues = [
        {"code": i.code, "message": i.message, "blocking": i.blocking}
        for i in report.issues
    ]
    if existing is None:
        session.add(record)
    session.commit()
    session.refresh(record)
    return MediaOut.model_validate(record)


@router.get("", response_model=list[MediaOut])
def list_media(session: Session = SessionDep) -> list[MediaOut]:
    rows = session.execute(select(MediaFile).order_by(MediaFile.id.desc())).scalars().all()
    return [MediaOut.model_validate(r) for r in rows]


@router.get("/{media_id}", response_model=MediaOut)
def get_media(media_id: int, session: Session = SessionDep) -> MediaOut:
    record = session.get(MediaFile, media_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media file {media_id} does not exist.")
    return MediaOut.model_validate(record)


@router.post("/{media_id}/recheck", response_model=MediaCheckOut)
def recheck(media_id: int, session: Session = SessionDep) -> MediaCheckOut:
    """Re-hash a clip and report whether it is still the file that was approved.

    This is the check the worker runs before every upload. Exposing it lets an
    operator see *why* a job is blocked without waiting for the queue to reach it.
    """
    record = session.get(MediaFile, media_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media file {media_id} does not exist.")
    unchanged, problem = verify_unchanged(record.path, record.sha256)
    report = validate_file(record.path)
    if unchanged:
        record.verified_at = datetime.now(UTC)
        session.commit()
    return MediaCheckOut(
        path=report.path,
        exists=report.exists,
        readable=report.readable,
        size_bytes=report.size_bytes,
        sha256=report.sha256,
        container=report.container,
        brand=report.brand,
        duration_seconds=report.duration_seconds,
        content_type=report.content_type,
        issues=[
            MediaIssueOut(
                code=i.code,
                message=i.message
                if i.blocking or unchanged
                else (problem.message if problem else i.message),
                blocking=i.blocking or not unchanged,
            )
            for i in report.issues
        ]
        + (
            []
            if unchanged or problem is None
            else [MediaIssueOut(code=problem.code, message=problem.message, blocking=True)]
        ),
        checked_at=report.checked_at,
    )


@router.get("/{media_id}/jobs")
def jobs_using(media_id: int, session: Session = SessionDep) -> dict[str, Any]:
    rows = (
        session.execute(select(ScheduledJob).where(ScheduledJob.media_id == media_id))
        .scalars()
        .all()
    )
    return {
        "media_id": media_id,
        "jobs": [{"id": j.id, "public_id": j.public_id, "state": j.state} for j in rows],
    }


@router.delete("/{media_id}", status_code=204)
def delete_media(media_id: int, session: Session = SessionDep) -> None:
    record = session.get(MediaFile, media_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Media file {media_id} does not exist.")
    in_use = session.execute(
        select(ScheduledJob.id).where(ScheduledJob.media_id == media_id).limit(1)
    ).first()
    if in_use is not None:
        raise HTTPException(
            status_code=409,
            detail="This clip is referenced by at least one job. Delete or reassign "
            "those jobs first, so a schedule is never left pointing at nothing.",
        )
    session.delete(record)
    session.commit()


_ = ev  # events are recorded by the job layer, not here
