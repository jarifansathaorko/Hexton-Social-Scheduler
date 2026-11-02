"""Analytics router for multi-channel video tracking and client deliverable reporting."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hexton.api.deps import CtxDep, SessionDep
from hexton.bootstrap import AppContext
from hexton.services.analytics import (
    export_custom_spreadsheet,
    export_deliverables_csv,
    get_analytics_overview,
    list_analytics_videos,
    set_video_approval,
    sync_channel_video_statistics,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


class RefreshStatsIn(BaseModel):
    channel_id: str | None = None


class VideoApprovalIn(BaseModel):
    approval: str | None = None  # "approved" | "rejected" | None


@router.get("/overview")
def overview(
    channel_id: str | None = Query(default=None),
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> dict[str, Any]:
    """Retrieve high-level metrics across all channels or a single channel."""
    return get_analytics_overview(session, channel_id=channel_id, ctx=ctx)


@router.get("/videos")
def videos(
    channel_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    qualified_only: bool = Query(default=False),
    min_views: int | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_by: str = Query(default="most_views"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> dict[str, Any]:
    """List published clips with live view counts, filtering, and 1K+ deliverable qualification."""
    return list_analytics_videos(
        session,
        channel_id=channel_id,
        start_date=start_date,
        end_date=end_date,
        qualified_only=qualified_only,
        min_views=min_views,
        search=search,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
        ctx=ctx,
    )


@router.post("/refresh")
def refresh_stats(
    payload: RefreshStatsIn | None = None,
    session: Session = SessionDep,
    ctx: AppContext = CtxDep,
) -> dict[str, Any]:
    """Trigger a batch YouTube statistics sync across all published videos."""
    cid = payload.channel_id if payload else None
    return sync_channel_video_statistics(session, ctx, channel_id=cid)


@router.get("/export/csv")
def export_csv(
    channel_id: str | None = Query(default=None),
    min_views: int = Query(default=1000, ge=0),
    sort_by: str = Query(default="most_views"),
    session: Session = SessionDep,
) -> Response:
    """Download a CSV proof-of-work sheet containing qualified videos for client submission."""
    csv_data = export_deliverables_csv(
        session,
        channel_id=channel_id,
        min_views=min_views,
        sort_by=sort_by,
    )
    filename = f"deliverables_{channel_id or 'all_channels'}_{min_views}views.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/videos/{job_id}/approval")
def update_video_approval(
    job_id: int,
    payload: VideoApprovalIn,
    session: Session = SessionDep,
) -> dict[str, Any]:
    """Record operator/creator approval note on a video clip ('approved', 'rejected', or None)."""
    try:
        return set_video_approval(session, job_id=job_id, approval=payload.approval)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@router.get("/export/custom")
def export_custom(
    channel_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    columns: str | None = Query(default=None),
    file_format: str = Query(default="csv"),
    session: Session = SessionDep,
) -> Response:
    """Download a customized Excel spreadsheet or CSV with user-selected columns and date duration."""
    col_list = [c.strip() for c in columns.split(",") if c.strip()] if columns else None
    csv_data, media_type, filename = export_custom_spreadsheet(
        session,
        channel_id=channel_id,
        start_date=start_date,
        end_date=end_date,
        columns=col_list,
        file_format=file_format,
    )
    return Response(
        content=csv_data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
