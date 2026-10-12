"""The local API: security envelope, then behaviour.

Two groups. The first pins the envelope this application depends on — loopback binding
alone is not a boundary, because another process on the same machine (or another user
account) can reach ``127.0.0.1``. The second pins the behaviour the queue screen relies
on, including the two properties that are easy to lose in a refactor: an operator's
text is stored verbatim, and no credential ever appears in a response.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import BASE, FUTURE, make_media
from fastapi.testclient import TestClient
from hexton.api.app import create_app
from hexton.api.security import COOKIE_SECURE, CSRF_HEADER, SessionSigner
from hexton.bootstrap import load_or_create_session_key
from hexton.db.models import ChannelIdentity, ScheduledJob

ACCESS_SECRET = "access-token"
REFRESH_SECRET = "refresh-token"


@pytest.fixture
def app(ctx, tmp_path):
    # A deliberately absent bundle: the API must work with the frontend unbuilt.
    return create_app(ctx, frontend_dist=tmp_path / "not-built")


@pytest.fixture
def client(ctx, app):
    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as c:
        yield c


@pytest.fixture
def ids(ctx, session, connection, clip) -> dict:
    channel = session.query(ChannelIdentity).one()
    media = make_media(session, clip)
    return {
        "media_id": media.id,
        "connection_id": connection.id,
        "channel_identity_id": channel.id,
    }


@pytest.fixture
def auth(ctx, client) -> dict:
    response = client.post("/api/session")
    assert response.status_code == 200
    return {CSRF_HEADER: response.json()["csrf_token"]}


def job_payload(ids: dict, **overrides) -> dict:
    payload = {
        "media_id": ids["media_id"],
        "title": "A test Short",
        "description": "A description",
        "privacy": "private",
        "connection_id": ids["connection_id"],
        "channel_identity_id": ids["channel_identity_id"],
    }
    payload.update(overrides)
    return payload


# ======================================================================#
# Security envelope
# ======================================================================#


def test_health_is_reachable_without_a_session(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["connector"] == "fake"


def test_the_api_docs_are_not_reachable_without_a_session(client) -> None:
    assert client.get("/api/openapi.json").status_code == 401


def test_minting_a_session_returns_only_a_csrf_token(ctx, client) -> None:
    response = client.post("/api/session")
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"csrf_token", "expires_in"}
    assert len(body["csrf_token"]) >= 32


def test_the_session_cookie_is_httponly_and_samesite_strict(ctx, client) -> None:
    response = client.post("/api/session")
    header = response.headers["set-cookie"]
    assert header.startswith(ctx.settings.session_cookie_name + "=")
    assert "HttpOnly" in header
    assert "samesite=strict" in header.lower()
    # Loopback is plain http: a Secure cookie would simply never be sent back.
    assert ("Secure" in header) is COOKIE_SECURE
    assert "Path=/" in header
    # The cookie's value is a signature, not a token we hand back in the body.
    assert response.json()["csrf_token"] not in header


def test_reading_a_job_requires_a_session(client, ids) -> None:
    response = client.get("/api/jobs")
    assert response.status_code == 401
    assert response.json()["code"] == "no_session"


def test_a_state_change_without_a_csrf_header_is_refused(client, auth, ids) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth)
    assert created.status_code == 201
    job_id = created.json()["id"]

    response = client.post(f"/api/jobs/{job_id}/queue", json={}, headers={})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_a_wrong_csrf_token_is_refused(client, auth, ids) -> None:
    response = client.post(
        "/api/jobs", json=job_payload(ids), headers={CSRF_HEADER: "not-the-token"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_a_csrf_token_from_another_session_is_refused(client, auth, ids) -> None:
    """Otherwise a second local page could drive the first one's session."""
    stale = auth[CSRF_HEADER]
    assert client.post("/api/session").status_code == 200  # re-mint; the nonce moves on

    response = client.post("/api/jobs", json=job_payload(ids), headers={CSRF_HEADER: stale})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_a_tampered_cookie_is_refused(ctx, client) -> None:
    cookie, _ = SessionSigner(load_or_create_session_key(ctx.settings.session_key_path)).mint()
    tampered = cookie[:-4] + ("AAAA" if not cookie.endswith("AAAA") else "BBBB")
    response = client.get(
        "/api/jobs", headers={"Cookie": f"{ctx.settings.session_cookie_name}={tampered}"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "no_session"


def test_an_expired_session_is_refused(ctx, client) -> None:
    key = load_or_create_session_key(ctx.settings.session_key_path)
    expired, _ = SessionSigner(key, max_age_seconds=-10).mint()
    response = client.get(
        "/api/jobs", headers={"Cookie": f"{ctx.settings.session_cookie_name}={expired}"}
    )
    assert response.status_code == 401


def test_a_foreign_host_header_is_refused_even_for_open_paths(client) -> None:
    """Another host is not this application, whatever the path says."""
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "host_not_allowed"


def test_the_documented_loopback_hosts_are_accepted(client, auth) -> None:
    host = client.app.state.ctx.settings.host
    port = client.app.state.ctx.settings.port
    for allowed in (f"{host}:{port}", f"localhost:{port}"):
        assert client.get("/api/health", headers={"Host": allowed}).status_code == 200


def test_a_foreign_origin_is_refused_on_a_state_change(client, auth, ids) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth)
    job_id = created.json()["id"]

    response = client.post(
        f"/api/jobs/{job_id}/queue",
        json={},
        headers={**auth, "Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_the_same_origin_is_accepted_on_a_state_change(client, auth, ids) -> None:
    settings = client.app.state.ctx.settings
    created = client.post(
        "/api/jobs",
        json=job_payload(ids),
        headers={**auth, "Origin": f"http://{settings.host}:{settings.port}"},
    )
    assert created.status_code == 201


def test_hardening_headers_are_present_on_every_response(client, auth) -> None:
    for response in (client.get("/api/health"), client.get("/api/jobs", headers=auth)):
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_denied_responses_carry_the_hardening_headers_too(client) -> None:
    """A 401 has an origin and a body, so it needs them as much as a 200 does."""
    response = client.get("/api/jobs")
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_a_cross_origin_page_cannot_mint_a_session(client) -> None:
    """/api/session is open, but not open to some other site's Origin header."""
    response = client.post("/api/session", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_the_documented_origin_minting_a_session_still_works(client) -> None:
    settings = client.app.state.ctx.settings
    response = client.post(
        "/api/session", headers={"Origin": f"http://{settings.host}:{settings.port}"}
    )
    assert response.status_code == 200


def test_a_denied_request_says_why_without_leaking_internals(client) -> None:
    response = client.get("/api/jobs")
    assert response.status_code == 401
    assert set(response.json()) == {"detail", "code"}
    assert "Traceback" not in response.text


def test_no_credential_material_ever_appears_in_a_response(client, auth, ids) -> None:
    """The one rule that must survive every future endpoint."""
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth)
    job_id = created.json()["id"]
    client.post(f"/api/jobs/{job_id}/queue", headers=auth)

    bodies = []
    for path in ("/api/health", "/api/jobs", f"/api/jobs/{job_id}"):
        response = client.get(path, headers=auth)
        assert response.status_code == 200
        bodies.append(response.text)
    bodies.append(client.get("/api/dashboard", headers=auth).text)
    bodies.append(client.get("/api/settings", headers=auth).text)
    bodies.append(client.get("/api/connections", headers=auth).text)

    for body in bodies:
        for secret in (ACCESS_SECRET, REFRESH_SECRET):
            assert secret not in body, f"{secret!r} leaked into a response"


# ======================================================================#
# Behaviour
# ======================================================================#


def test_creating_a_job_stores_the_operators_text_verbatim(client, auth, ids) -> None:
    """No silent trimming, no truncation, no rewriting."""
    title = "   leading and trailing   "
    description = "one\r\ntwo\r\n"

    response = client.post(
        "/api/jobs",
        json=job_payload(ids, title=title, description=description, tags=["  b  ", "a"]),
        headers=auth,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == title
    assert body["description"] == description
    # Tags are the one documented exception: they are a list, so trimming is a
    # normalization of the delimiter rather than of prose.
    assert body["tags"] == ["b", "a"]


def test_an_overlong_title_is_rejected_not_silently_cut(client, auth, ids) -> None:
    from hexton.domain.validation import TITLE_MAX_CHARS

    too_long = "x" * (TITLE_MAX_CHARS + 1)
    response = client.post("/api/jobs", json=job_payload(ids, title=too_long), headers=auth)

    assert response.status_code == 422
    assert str(TITLE_MAX_CHARS) in response.json()["detail"]
    assert "truncat" not in response.json()["detail"].lower()


def test_a_past_publication_time_is_refused_with_a_reason(client, auth, ids) -> None:
    response = client.post(
        "/api/jobs",
        json=job_payload(ids, publish_at_utc=(BASE - timedelta(days=1)).isoformat()),
        headers=auth,
    )
    assert response.status_code == 422
    assert "publication time is in the past" in response.json()["detail"].lower()


def test_public_privacy_cannot_carry_a_publication_time(client, auth, ids) -> None:
    """DF-04: publishAt on a public video publishes immediately."""
    response = client.post(
        "/api/jobs",
        json=job_payload(ids, privacy="public", publish_at_utc=FUTURE.isoformat()),
        headers=auth,
    )
    assert response.status_code == 422


def test_an_unknown_time_zone_is_refused(client, auth, ids) -> None:
    response = client.post(
        "/api/jobs",
        json=job_payload(ids, display_timezone="Mars/Olympus_Mons"),
        headers=auth,
    )
    assert response.status_code == 422


def test_validating_does_not_persist_anything(client, auth, ids) -> None:
    response = client.post(
        "/api/jobs/validate",
        json=job_payload(ids, title=""),
        headers=auth,
    )
    assert response.status_code == 400 or response.status_code == 422
    listing = client.get("/api/jobs", headers=auth).json()
    assert listing["total"] == 0


def test_the_list_carries_everything_the_queue_screen_needs(client, auth, ids) -> None:
    """Phase 2 §7: video, times, local state, remote state, errors, retry, ID/URL."""
    created = client.post(
        "/api/jobs", json=job_payload(ids, publish_at_utc=FUTURE.isoformat()), headers=auth
    ).json()

    required = {
        "state",
        "display_label",
        "publish_at_utc",
        "publish_at_display",
        "uploaded_at",
        "remote_state",
        "remote_privacy_status",
        "poll_result",
        "awaiting_verification",
        "progress_percent",
        "video_id",
        "video_url",
        "failure_message",
        "next_attempt_at",
        "next_attempt_display",
        "insert_may_have_succeeded",
        "mention_state",
        "can_retry",
        "can_cancel",
        "primary_action",
    }
    assert required <= set(created)


def test_search_and_state_filter_narrow_the_list(client, auth, ids) -> None:
    client.post("/api/jobs", json=job_payload(ids, title="Watermelon run"), headers=auth)
    client.post("/api/jobs", json=job_payload(ids, title="Citrus cut"), headers=auth)

    assert client.get("/api/jobs?q=watermelon", headers=auth).json()["total"] == 1
    assert client.get("/api/jobs?q=nothing", headers=auth).json()["total"] == 0

    filtered = client.get("/api/jobs?state=JOB-DRAFT", headers=auth).json()
    assert filtered["total"] == 2
    assert filtered["states"]["JOB-DRAFT"] == 2
    assert client.get("/api/jobs?state=JOB-PUBLISHED", headers=auth).json()["total"] == 0


def test_queueing_a_draft_makes_it_claimable(client, auth, ids) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth).json()
    assert created["state"] == "JOB-DRAFT"

    queued = client.post(f"/api/jobs/{created['id']}/queue", json={}, headers=auth)
    assert queued.status_code == 200
    assert queued.json()["state"] == "JOB-QUEUED"


def test_queueing_a_past_job_is_refused_with_the_reason(client, auth, ctx, ids) -> None:
    """DF-04 again: a missed window must not become an immediate upload."""
    created = client.post(
        "/api/jobs",
        json=job_payload(ids, publish_at_utc=FUTURE.isoformat()),
        headers=auth,
    ).json()
    job_id = created["id"]
    client.post(f"/api/jobs/{job_id}/queue", headers=auth)

    # The publication time passes while the job sits in the queue.
    with ctx.sessions() as s:
        job = s.get(ScheduledJob, job_id)
        job.schedule.publish_at_utc = BASE - timedelta(hours=1)
        job.schedule.report_date = BASE - timedelta(hours=1)
        s.commit()

    response = client.post(f"/api/jobs/{job_id}/queue", json={}, headers=auth)
    assert response.status_code == 409
    assert "publication time has passed" in response.json()["detail"].lower()


def test_rescheduling_a_published_job_is_refused(client, auth, ids, session) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth).json()
    job_id = created["id"]
    with client.app.state.ctx.sessions() as s:
        job = s.get(ScheduledJob, job_id)
        job.state = "JOB-PUBLISHED"
        job.video_id = "vid-pub"
        s.commit()

    response = client.post(
        f"/api/jobs/{job_id}/reschedule",
        json={"publish_at_utc": FUTURE.isoformat()},
        headers=auth,
    )
    assert response.status_code == 409
    assert "already published" in response.json()["detail"].lower()


def test_cancelling_reports_both_the_local_and_remote_outcome(client, auth, ids) -> None:
    created = client.post(
        "/api/jobs",
        json=job_payload(ids, publish_at_utc=FUTURE.isoformat()),
        headers=auth,
    ).json()
    job_id = created["id"]
    client.post(f"/api/jobs/{job_id}/queue", headers=auth)

    response = client.post(
        f"/api/jobs/{job_id}/cancel",
        json={"reason": "changed my mind"},
        headers=auth,
    )
    body = response.json()
    assert response.status_code == 200
    assert body["local_cancelled"] is True
    assert body["remote_attempted"] is False
    assert body["remote_succeeded"] is None
    assert "No video had been created" in body["message"]
    assert body["job"]["state"] == "JOB-CANCELLED"


def test_a_draft_can_be_deleted_but_a_queued_job_cannot(client, auth, ids) -> None:
    draft = client.post("/api/jobs", json=job_payload(ids), headers=auth).json()
    assert client.delete(f"/api/jobs/{draft['id']}", headers=auth).status_code == 204

    queued = client.post(
        "/api/jobs", json=job_payload(ids, publish_at_utc=FUTURE.isoformat()), headers=auth
    ).json()
    client.post(f"/api/jobs/{queued['id']}/queue", headers=auth)
    conflict = client.delete(f"/api/jobs/{queued['id']}", headers=auth)
    assert conflict.status_code == 409


def test_a_missing_job_is_404_not_an_error_page(client, auth) -> None:
    response = client.get("/api/jobs/99999", headers=auth)
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_duplicate_makes_an_independent_draft(client, auth, ids) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth).json()
    clone = client.post(f"/api/jobs/{created['id']}/duplicate", headers=auth).json()

    assert clone["id"] != created["id"]
    assert clone["state"] == "JOB-DRAFT"  # never queued implicitly
    assert clone["title"] == created["title"]
    assert client.get("/api/jobs", headers=auth).json()["total"] == 2


# ======================================================================#
# Reconciliation through the API
# ======================================================================#


def _ambiguous(ctx, session, new_job) -> int:
    from hexton.worker.runner import JobRunner

    job = new_job()
    ctx.connector.state.drop_upload_response = True
    JobRunner(ctx, owner="w").run_once(now=BASE)
    with ctx.sessions() as s:
        row = s.get(ScheduledJob, job.id)
        assert row.state == "JOB-NEEDS_REVIEW"
        assert row.insert_may_have_succeeded
    return job.id


def test_reconcile_refuses_an_id_that_was_never_surfaced(
    client, auth, ctx, session, new_job
) -> None:
    """DF-08: a screen must not be able to attribute an arbitrary upload to a schedule."""
    job_id = _ambiguous(ctx, session, new_job)

    response = client.post(
        f"/api/jobs/{job_id}/reconcile", json={"video_id": "vid-invented"}, headers=auth
    )
    assert response.status_code == 422
    assert "not one of the candidates" in response.json()["detail"]


def test_reconcile_records_the_operators_decision_as_attestation(
    client, auth, ctx, session, new_job
) -> None:
    job_id = _ambiguous(ctx, session, new_job)
    ctx.connector.state.recent_upload_ids = ["vid-mine"]

    response = client.post(
        f"/api/jobs/{job_id}/reconcile", json={"video_id": "vid-mine"}, headers=auth
    )
    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == "vid-mine"
    assert body["state"] in ("JOB-UPLOADED", "JOB-SCHEDULED")
    assert body["insert_may_have_succeeded"] is False

    events = client.get(f"/api/jobs/{job_id}/events", headers=auth).json()
    reconciled = [e for e in events if e["kind"] == "reconciled"]
    assert reconciled and reconciled[0]["payload"]["operator_confirmed"] is True


def test_reconcile_is_offered_only_where_the_outcome_is_uncertain(client, auth, ids) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth).json()
    response = client.post(f"/api/jobs/{created['id']}/reconcile", json={}, headers=auth)
    assert response.status_code == 409
    assert "no uncertain outcome" in response.json()["detail"]


def test_declining_every_candidate_cancels_and_touches_nothing(
    client, auth, ctx, session, new_job
) -> None:
    job_id = _ambiguous(ctx, session, new_job)

    response = client.post(f"/api/jobs/{job_id}/reconcile", json={}, headers=auth)
    assert response.status_code == 200
    assert response.json()["state"] == "JOB-CANCELLED"
    messages = [e["message"] for e in client.get(f"/api/jobs/{job_id}/events", headers=auth).json()]
    assert any("must be checked by hand" in m for m in messages)


# ======================================================================#
# Dashboard and settings
# ======================================================================#


def test_the_dashboard_reports_quota_and_worker_liveness(client, auth, new_job) -> None:
    new_job()
    body = client.get("/api/dashboard", headers=auth).json()
    assert body["quota_remaining"] > 0
    assert isinstance(body["worker_alive"], bool)
    assert body["total"] == 1


def test_every_endpoint_agrees_that_no_worker_is_running(client, auth) -> None:
    """/api/health used to answer ``worker_alive: true`` unconditionally.

    Three endpoints answer the same question, so they must reach the same answer —
    otherwise a dashboard saying "no worker" beside a green health check is exactly
    the kind of contradiction an operator cannot act on.
    """
    health = client.get("/api/health").json()["worker_alive"]
    status = client.get("/api/worker/status", headers=auth).json()["alive"]
    dashboard = client.get("/api/dashboard", headers=auth).json()["worker_alive"]

    assert health is False
    assert health == status == dashboard


def test_liveness_becomes_true_once_a_heartbeat_is_fresh(ctx, client, auth) -> None:
    from datetime import UTC, datetime

    from hexton.db.models import WorkerHeartbeat

    with ctx.sessions() as s:
        s.add(WorkerHeartbeat(owner="worker-a", beat_at=datetime.now(UTC), jobs_claimed=3))
        s.commit()

    assert client.get("/api/health").json()["worker_alive"] is True
    assert client.get("/api/worker/status", headers=auth).json()["alive"] is True
    assert client.get("/api/dashboard", headers=auth).json()["worker_alive"] is True


def test_a_stale_heartbeat_does_not_count_as_alive(ctx, client, auth) -> None:
    """Silent for two expected beats means another worker could hold the job."""
    from datetime import UTC, datetime, timedelta

    from hexton.db.models import WorkerHeartbeat

    window = ctx.settings.heartbeat_seconds * 2
    stale = datetime.now(UTC) - timedelta(seconds=window + 5)
    with ctx.sessions() as s:
        s.add(WorkerHeartbeat(owner="worker-a", beat_at=stale, jobs_claimed=3))
        s.commit()

    assert client.get("/api/health").json()["worker_alive"] is False
    assert client.get("/api/worker/status", headers=auth).json()["alive"] is False


def test_settings_report_the_storage_and_quota_configuration(client, auth) -> None:
    body = client.get("/api/settings", headers=auth).json()
    assert body["display_timezone"]
    assert body["quota_daily_units"] > 0
    assert body["quota_resets_at"].endswith("Z") or "+" in body["quota_resets_at"]
    assert body["connector_kind"] == "fake"
    assert "password" not in body


def test_connections_are_listed_without_any_token(client, auth, connection) -> None:
    body = client.get("/api/connections", headers=auth).json()
    text = str(body)
    assert ACCESS_SECRET not in text
    assert REFRESH_SECRET not in text
    assert body[0]["status"] == "active"
