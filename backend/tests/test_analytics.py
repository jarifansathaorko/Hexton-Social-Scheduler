"""Tests for multi-channel video analytics and 1K+ view deliverable tracking."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from hexton.api.app import create_app
from hexton.bootstrap import AppContext
from hexton.connectors.base import VideoStatus
from hexton.db.models import (
    ChannelIdentity,
    ContentMetadata,
    MediaFile,
    PlatformConnection,
    Schedule,
    ScheduledJob,
)
from hexton.domain.jobstate import JobState
from hexton.services.analytics import (
    QUALIFIED_VIEW_THRESHOLD,
    export_deliverables_csv,
    get_analytics_overview,
    list_analytics_videos,
    sync_channel_video_statistics,
)
from sqlalchemy.orm import Session


def _seed_channel_and_videos(session: Session) -> tuple[ChannelIdentity, list[ScheduledJob]]:
    conn = PlatformConnection(
        provider="youtube",
        label="Test Channel Conn",
        status="active",
        scopes=["https://www.googleapis.com/auth/youtube.readonly"],
    )
    session.add(conn)
    session.flush()

    chan = ChannelIdentity(
        connection_id=conn.id,
        channel_id="UC_TEST_123",
        title="Brand Alpha Clips",
        handle="@brandalpha",
        verification_source="operator_confirmed",
        subscriber_count=5000,
    )
    session.add(chan)
    session.flush()

    media = MediaFile(
        path="clip1.mp4",
        sha256="a" * 64,
        size_bytes=1024,
        container="mp4",
        duration_seconds=15.0,
    )
    session.add(media)
    session.flush()

    jobs: list[ScheduledJob] = []
    # Video 1: 1,500 views (Qualified)
    # Video 2: 750 views (Approaching)
    # Video 3: 200 views (Fresh)
    counts = [1500, 750, 200]
    for i, count in enumerate(counts):
        meta = ContentMetadata(
            title=f"Viral Brand Clip #{i + 1}",
            description="Testing clip views",
            privacy="public",
        )
        sched = Schedule(
            publish_at_utc=datetime.now(UTC),
            display_timezone="UTC",
        )
        session.add_all([meta, sched])
        session.flush()

        job = ScheduledJob(
            schedule_id=sched.id,
            metadata_id=meta.id,
            media_id=media.id,
            connection_id=conn.id,
            channel_identity_id=chan.id,
            state=JobState.PUBLISHED.value,
            idempotency_key=f"idemp-analytics-{i}",
            video_id=f"vid-analytics-{i}",
            video_url=f"https://www.youtube.com/watch?v=vid-analytics-{i}",
            published_at_remote=datetime.now(UTC),
            view_count=count,
            like_count=count // 10,
            comment_count=count // 50,
        )
        session.add(job)
        jobs.append(job)

    session.commit()
    return chan, jobs


def test_analytics_overview_empty(session: Session) -> None:
    res = get_analytics_overview(session)
    assert res["total_videos"] == 0
    assert res["total_views"] == 0
    assert res["qualified_videos"] == 0
    assert res["qualification_rate"] == 0.0
    assert res["threshold"] == QUALIFIED_VIEW_THRESHOLD


def test_analytics_overview_and_channels(session: Session) -> None:
    _chan, _jobs = _seed_channel_and_videos(session)
    res = get_analytics_overview(session)

    assert res["total_videos"] == 3
    assert res["total_views"] == 1500 + 750 + 200
    assert res["qualified_videos"] == 1  # only 1500 is >= 1000
    assert res["approaching_videos"] == 1  # 750 is >= 500 and < 1000
    assert res["qualification_rate"] == 33.3
    assert len(res["channel_summaries"]) == 1

    summary = res["channel_summaries"][0]
    assert summary["channel_id"] == "UC_TEST_123"
    assert summary["channel_title"] == "Brand Alpha Clips"
    assert summary["qualified_count"] == 1
    assert summary["total_views"] == 2450


def test_list_analytics_videos_filtering(session: Session) -> None:
    _seed_channel_and_videos(session)

    # All videos
    all_res = list_analytics_videos(session)
    assert all_res["total"] == 3
    assert len(all_res["items"]) == 3

    # Only qualified videos (>= 1,000 views)
    qual_res = list_analytics_videos(session, qualified_only=True)
    assert qual_res["total"] == 1
    assert qual_res["items"][0]["view_count"] == 1500
    assert qual_res["items"][0]["is_qualified"] is True
    assert qual_res["items"][0]["progress_ratio"] == 1.0

    # Search filter
    search_res = list_analytics_videos(session, search="Clip #2")
    assert search_res["total"] == 1
    assert search_res["items"][0]["view_count"] == 750
    assert search_res["items"][0]["is_qualified"] is False
    assert search_res["items"][0]["progress_ratio"] == 0.75


def test_list_analytics_videos_sorting(session: Session) -> None:
    _seed_channel_and_videos(session)

    # 1. Most views (default: 1500, 750, 200)
    most_views_res = list_analytics_videos(session, sort_by="most_views")
    counts = [item["view_count"] for item in most_views_res["items"]]
    assert counts == [1500, 750, 200]

    # 2. Least views (200, 750, 1500)
    least_views_res = list_analytics_videos(session, sort_by="least_views")
    counts = [item["view_count"] for item in least_views_res["items"]]
    assert counts == [200, 750, 1500]

    # Stagger publication dates
    from sqlalchemy import select
    jobs = list(session.execute(select(ScheduledJob).order_by(ScheduledJob.id)).scalars().all())
    jobs[0].published_at_remote = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    jobs[1].published_at_remote = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
    jobs[2].published_at_remote = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    session.commit()

    # 3. Last uploaded (newest first: Video 3, Video 2, Video 1)
    newest_res = list_analytics_videos(session, sort_by="last_uploaded")
    titles = [item["title"] for item in newest_res["items"]]
    assert titles == ["Viral Brand Clip #3", "Viral Brand Clip #2", "Viral Brand Clip #1"]

    # 4. First uploaded (oldest first: Video 1, Video 2, Video 3)
    oldest_res = list_analytics_videos(session, sort_by="first_uploaded")
    titles = [item["title"] for item in oldest_res["items"]]
    assert titles == ["Viral Brand Clip #1", "Viral Brand Clip #2", "Viral Brand Clip #3"]


def test_export_deliverables_csv(session: Session) -> None:
    _seed_channel_and_videos(session)

    # Export >= 1K qualified videos
    csv_out = export_deliverables_csv(session, min_views=1000)
    assert "Brand Alpha Clips" in csv_out
    assert "Viral Brand Clip #1" in csv_out
    assert "QUALIFIED (1K+ Views)" in csv_out
    assert "Viral Brand Clip #2" not in csv_out  # 750 views not in >= 1000 export


def test_sync_channel_video_statistics(session: Session, ctx: AppContext) -> None:
    _chan, jobs = _seed_channel_and_videos(session)
    # Store mock access token in credentials
    conn_id = jobs[0].connection_id
    ctx.credentials.write(f"conn:{conn_id}:access", "test-token")

    # Mock connector returns updated view counts
    fake_conn = ctx.connector
    fake_conn.state.videos["vid-analytics-0"] = VideoStatus(  # type: ignore[attr-defined]
        video_id="vid-analytics-0",
        privacy_status="public",
        upload_status="uploaded",
        view_count=5200,
        like_count=340,
        comment_count=42,
    )

    res = sync_channel_video_statistics(session, ctx)
    assert res["synced_count"] >= 1
    assert res["qualified_count"] >= 1

    # Check job in DB was updated
    updated_job = session.get(ScheduledJob, jobs[0].id)
    assert updated_job is not None
    assert updated_job.view_count == 5200
    assert updated_job.like_count == 340


def test_analytics_api_endpoints(session: Session, ctx: AppContext) -> None:
    _seed_channel_and_videos(session)

    app = create_app(ctx)
    client = TestClient(app, base_url="http://127.0.0.1:8787")

    # Establish session
    client.post("/api/session")

    # Overview endpoint
    ov_resp = client.get("/api/analytics/overview")
    assert ov_resp.status_code == 200
    ov_data = ov_resp.json()
    assert ov_data["total_videos"] == 3
    assert ov_data["qualified_videos"] == 1

    # Videos endpoint with sort
    sort_resp = client.get("/api/analytics/videos?sort_by=least_views")
    assert sort_resp.status_code == 200
    assert sort_resp.json()["items"][0]["view_count"] == 200

    # Videos endpoint
    vid_resp = client.get("/api/analytics/videos?qualified_only=true")
    assert vid_resp.status_code == 200
    vid_data = vid_resp.json()
    assert vid_data["total"] == 1
    assert vid_data["items"][0]["view_count"] == 1500

    # CSV export endpoint
    csv_resp = client.get("/api/analytics/export/csv?min_views=1000")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "Viral Brand Clip #1" in csv_resp.text

    # Approval note endpoint
    item_id = vid_data["items"][0]["id"]
    csrf = client.post("/api/session").json()["csrf_token"]
    appr_resp = client.post(
        f"/api/analytics/videos/{item_id}/approval",
        json={"approval": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert appr_resp.status_code == 200
    assert appr_resp.json()["creator_approval"] == "approved"

    # Verify reflected in list
    v_updated = client.get("/api/analytics/videos").json()
    matched = next(x for x in v_updated["items"] if x["id"] == item_id)
    assert matched["creator_approval"] == "approved"



def test_custom_spreadsheet_export_service_and_api(session: Session, ctx: AppContext) -> None:
    from hexton.services.analytics import export_custom_spreadsheet

    _seed_channel_and_videos(session)

    # 1. Test service directly for CSV with custom columns
    csv_content, mime, filename = export_custom_spreadsheet(
        session,
        columns=["title", "video_url", "channel_title", "view_count"],
        file_format="csv",
    )
    assert mime == "text/csv;charset=utf-8"
    assert filename.endswith(".csv")
    assert csv_content.startswith("\ufeff")
    assert "Video Title,YouTube URL,Channel Name,Total Views" in csv_content
    assert "Viral Brand Clip #1" in csv_content

    # 2. Test service for native Excel XML
    xml_content, xml_mime, xml_filename = export_custom_spreadsheet(
        session,
        columns=["title", "view_count"],
        file_format="excel",
    )
    assert "application/vnd.ms-excel" in xml_mime
    assert xml_filename.endswith(".xls")
    assert "<?mso-application progid=\"Excel.Sheet\"?>" in xml_content
    assert "Viral Brand Clip #1" in xml_content

    # 3. Test API endpoint /api/analytics/export/custom
    app = create_app(ctx)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    client.post("/api/session")

    resp = client.get("/api/analytics/export/custom?columns=title,channel_title,view_count&file_format=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "Video Title,Channel Name,Total Views" in resp.text
    assert "Viral Brand Clip #1" in resp.text
