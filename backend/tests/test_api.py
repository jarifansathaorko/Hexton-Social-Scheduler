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
from conftest import BASE, FUTURE, PAST, make_media
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


def test_a_past_publication_time_always_surfaces_beside_the_other_schedule_issues(
    client, auth, ids
) -> None:
    """F-28: ``validate`` ran ``check_publish_time`` a second time, behind ``if not schedule``.

    ``prepare`` already runs exactly that check, with a superset of the parameters, as
    part of building ``schedule_issues`` — so the second call could only ever duplicate
    or, once someone edited one site and not the other, *disagree with* the first. The
    property worth pinning is the one the suppression threatened: a past publication
    time is a ``DF-04`` hard block and must be reported whatever else is wrong with the
    request, exactly once.
    """
    response = client.post(
        "/api/jobs/validate",
        json=job_payload(
            ids,
            publish_at_utc=PAST.isoformat(),
            display_timezone="Mars/Olympus_Mons",
            privacy="public",
        ),
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    codes = [i["code"] for i in body["schedule_issues"]]
    assert "publish_at_in_past" in codes, "the DF-04 block must not be suppressed"
    assert "unknown_timezone" in codes
    assert "public_conflicts_with_publish_at" in codes
    assert len(codes) == len(set(codes)), "one check, one report"
    assert {i["severity"] for i in body["schedule_issues"]} == {"error"}
    assert body["ok"] is False


def test_a_valid_time_still_validates_clean_against_the_same_check(client, auth, ids) -> None:
    response = client.post(
        "/api/jobs/validate",
        json=job_payload(ids, publish_at_utc=FUTURE.isoformat()),
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [i for i in body["schedule_issues"] if i["severity"] == "error"] == []


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


def test_the_list_pages_in_the_database_and_still_reports_the_filtered_total(
    client, auth, ids
) -> None:
    """F-20: every job was loaded with all seven relationships, then sorted in Python.

    Ordering, offset, and limit now happen in SQL. What must not move is the contract
    the queue screen depends on: ``total`` counts the jobs that *match*, not the jobs on
    this page, and the newest job comes first.
    """
    created = [
        client.post("/api/jobs", json=job_payload(ids, title=f"Job {n}"), headers=auth).json()["id"]
        for n in range(5)
    ]
    newest_first = list(reversed(created))

    first = client.get("/api/jobs?limit=2", headers=auth).json()
    assert first["total"] == 5, "total is the match count, not the page size"
    assert [j["id"] for j in first["items"]] == newest_first[:2]

    second = client.get("/api/jobs?limit=2&offset=2", headers=auth).json()
    assert second["total"] == 5
    assert [j["id"] for j in second["items"]] == newest_first[2:4]

    third = client.get("/api/jobs?limit=2&offset=4", headers=auth).json()
    assert [j["id"] for j in third["items"]] == newest_first[4:]

    past_the_end = client.get("/api/jobs?limit=2&offset=99", headers=auth).json()
    assert past_the_end["items"] == []
    assert past_the_end["total"] == 5

    # A filter narrows ``total`` too — the count is taken from the filtered statement.
    narrowed = client.get("/api/jobs?q=Job 3&limit=1", headers=auth).json()
    assert narrowed["total"] == 1
    assert narrowed["items"][0]["id"] == created[3]


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
        f"/api/jobs/{job_id}/reconcile",
        json={"video_id": "vid-mine", "resolution": "adopt"},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()["job"]
    assert body["video_id"] == "vid-mine"
    assert body["state"] in ("JOB-UPLOADED", "JOB-SCHEDULED")
    assert body["insert_may_have_succeeded"] is False

    events = client.get(f"/api/jobs/{job_id}/events", headers=auth).json()
    reconciled = [e for e in events if e["kind"] == "reconciled"]
    assert reconciled and reconciled[0]["payload"]["operator_confirmed"] is True
    assert reconciled[0]["payload"]["resolution"] == "adopt"


def test_reconcile_is_offered_only_where_the_outcome_is_uncertain(client, auth, ids) -> None:
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth).json()
    response = client.post(f"/api/jobs/{created['id']}/reconcile", json={}, headers=auth)
    assert response.status_code == 409
    assert "no uncertain outcome" in response.json()["detail"]


def test_declining_every_candidate_cancels_and_touches_nothing(
    client, auth, ctx, session, new_job
) -> None:
    job_id = _ambiguous(ctx, session, new_job)

    response = client.post(
        f"/api/jobs/{job_id}/reconcile", json={"resolution": "cancel"}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["job"]["state"] == "JOB-CANCELLED"
    messages = [e["message"] for e in client.get(f"/api/jobs/{job_id}/events", headers=auth).json()]
    assert any("must be checked by hand" in m for m in messages)


def test_an_ambiguous_job_can_be_re_run_when_the_operator_says_nothing_was_uploaded(
    client, auth, ctx, session, new_job
) -> None:
    """F-04: ``NEEDS_REVIEW`` had no exit back to the queue.

    Choosing no video always meant "cancel", so an operator who had checked YouTube,
    found no upload, and wanted the job to run again had to destroy the record and
    rebuild it — losing the schedule, the media reference and the audit trail. The only
    surviving route back was editing the state column by hand.
    """
    job_id = _ambiguous(ctx, session, new_job)

    detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
    assert detail["can_requeue"] is True, "the detail view has to offer the action"
    assert detail["candidates"] == [], "no candidates exist for this job"

    response = client.post(
        f"/api/jobs/{job_id}/reconcile",
        json={"resolution": "not_uploaded"},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()["job"]
    assert body["state"] == "JOB-QUEUED"
    assert body["insert_may_have_succeeded"] is False

    events = client.get(f"/api/jobs/{job_id}/events", headers=auth).json()
    reconciled = [e for e in events if e["kind"] == "reconciled"]
    assert reconciled, "the decision is recorded as an operator assertion, not silently applied"
    assert reconciled[0]["payload"]["resolution"] == "not_uploaded"
    assert "recorded human decision" in reconciled[0]["message"]

    # And it genuinely re-enters the queue rather than merely changing a label.
    assert client.get(f"/api/jobs/{job_id}", headers=auth).json()["job"]["state"] == "JOB-QUEUED"


def test_the_detail_view_surfaces_the_candidates_the_ladder_found(
    client, auth, ctx, session, new_job
) -> None:
    """F-04: candidates only ever appeared inside an event payload.

    The one control that resolves a duplicate-risk situation was reachable by paging
    through raw JSON out of an audit record. The list belongs next to the buttons it
    feeds, it comes from the ladder rather than from anything the client supplies, and
    it empties the moment the uncertainty is resolved so a stale set of options cannot
    be clicked after the decision has already been made.
    """
    job_id = _ambiguous(ctx, session, new_job)
    ctx.connector.state.recent_upload_ids = ["vid-mine", "vid-other"]

    detail = client.get(f"/api/jobs/{job_id}", headers=auth).json()
    assert detail["candidates"] == ["vid-mine", "vid-other"]
    assert detail["can_requeue"] is True

    adopted = client.post(
        f"/api/jobs/{job_id}/reconcile",
        json={"video_id": "vid-mine", "resolution": "adopt"},
        headers=auth,
    )
    assert adopted.status_code == 200

    resolved = client.get(f"/api/jobs/{job_id}", headers=auth).json()
    assert resolved["candidates"] == [], "the decision is made; nothing is left to choose"
    assert resolved["can_requeue"] is False, "re-running an adopted upload is a duplicate risk"


def test_a_video_id_may_not_be_sent_with_a_non_adopt_resolution(
    client, auth, ctx, session, new_job
) -> None:
    """The two fields express different things and must not be quietly combined."""
    job_id = _ambiguous(ctx, session, new_job)

    response = client.post(
        f"/api/jobs/{job_id}/reconcile",
        json={"video_id": "vid-mine", "resolution": "not_uploaded"},
        headers=auth,
    )
    assert response.status_code == 422
    assert "resolution='adopt'" in response.json()["detail"]


def test_adopting_without_a_video_id_is_refused(client, auth, ctx, session, new_job) -> None:
    job_id = _ambiguous(ctx, session, new_job)

    response = client.post(
        f"/api/jobs/{job_id}/reconcile", json={"resolution": "adopt"}, headers=auth
    )
    assert response.status_code == 422
    assert "not_uploaded" in response.json()["detail"]


def test_requeueing_is_not_a_way_to_re_run_an_in_flight_job(
    client, auth, ctx, session, new_job
) -> None:
    """The transition table is the guard: only ``NEEDS_REVIEW`` may go back to ``QUEUED``.

    Without this, ``resolution='not_uploaded'`` would be a general-purpose "run anything
    again" button — including a job already mid-transfer, which is precisely the blind
    re-upload ``DF-12`` forbids.
    """
    from hexton.domain.jobstate import JobState, can_transition

    assert can_transition(JobState.NEEDS_REVIEW, JobState.QUEUED)
    for state in (
        JobState.UPLOADING,
        JobState.UPLOADED,
        JobState.SCHEDULED,
        JobState.PUBLISHED,
        JobState.QUEUED,
    ):
        assert not can_transition(state, JobState.QUEUED), state
    # A retryable failure may legitimately return to the queue — that is the retry
    # policy, not a duplicate risk, because nothing was confirmed uploaded.
    assert can_transition(JobState.FAILED_RETRY, JobState.QUEUED)

    # And the endpoint enforces it rather than trusting the table to be read correctly.
    # Adopting resolves the uncertainty outright, so the general guard already refuses a
    # second call — the specific requeue guard is what stands between an unresolved
    # NEEDS_REVIEW job and a blind re-upload.
    job_id = _ambiguous(ctx, session, new_job)
    ctx.connector.state.recent_upload_ids = ["vid-mine"]
    assert (
        client.post(
            f"/api/jobs/{job_id}/reconcile",
            json={"video_id": "vid-mine", "resolution": "adopt"},
            headers=auth,
        ).status_code
        == 200
    )

    response = client.post(
        f"/api/jobs/{job_id}/reconcile", json={"resolution": "not_uploaded"}, headers=auth
    )
    assert response.status_code == 409
    assert "no uncertain outcome" in response.json()["detail"]


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


# ======================================================================#
# Regression: findings F-14, F-32, F-29, F-11, F-19
# ======================================================================#


@pytest.fixture
def spa_client(ctx, tmp_path):
    """A client over a real bundle, so the SPA fallback route is actually mounted."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>hexton</title>", encoding="utf-8")
    app = create_app(ctx, frontend_dist=dist)
    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as c:
        yield c


def test_an_unknown_api_path_is_a_json_404_not_the_spa_shell(spa_client) -> None:
    """F-14: the single-page fallback used to answer every ``GET /api/*`` with 200 HTML."""
    session = spa_client.post("/api/session")
    assert session.status_code == 200
    headers = {CSRF_HEADER: session.json()["csrf_token"]}

    response = spa_client.get("/api/definitely-not-a-route", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == "not_found"

    nested = spa_client.get("/api/jobs/999999/anything", headers=headers)
    assert nested.status_code == 404
    assert nested.headers["content-type"].startswith("application/json")


def test_the_spa_fallback_still_serves_client_side_routes(spa_client) -> None:
    for path in ("/", "/queue", "/reports"):
        response = spa_client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html"), path


def test_the_oauth_redirect_uri_reaches_the_callback_handler(ctx, app, client, auth) -> None:
    """F-32: ``authorize`` advertised ``/oauth/callback`` while the route was registered
    under ``/api/connections``, so the browser came back to the SPA, ``state`` was never
    validated, and the route sat behind the session guard that ``SameSite=Strict`` makes
    unreachable on a return from accounts.google.com.

    The assertion is behavioural on purpose: the advertised URI is followed exactly as a
    browser would follow it, and the only way to get 403 "unknown state" out of the
    handler is for that handler to be the thing that answered.
    """
    from urllib.parse import urlparse

    created = client.post(
        "/api/connections", json={"provider": "youtube", "label": "Main"}, headers=auth
    )
    assert created.status_code == 201

    advertised = client.post(
        f"/api/connections/{created.json()['id']}/authorize", json={}, headers=auth
    ).json()["redirect_uri"]
    path = urlparse(advertised).path
    assert path == "/oauth/callback"

    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as anon:
        response = anon.get(f"{path}?code=x&state=unknown-state")
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert "state" in response.json()["detail"].lower()


def test_the_oauth_callback_is_reachable_without_a_session(ctx, app) -> None:
    """A browser arrives from accounts.google.com with no cookie (``SameSite=Strict``),
    so the callback must be outside ``/api`` and must not be answered by the SPA."""
    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as anon:
        response = anon.get("/oauth/callback?code=x&state=unknown-state")
    # 403 from the handler itself: an unknown ``state`` is the CSRF condition it exists
    # for. A 401 would mean the session guard swallowed it; text/html would mean the
    # SPA did.
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert "state" in response.json()["detail"].lower()


def test_every_loopback_spelling_is_accepted_as_a_host() -> None:
    """F-29: configuring ``localhost`` left ``127.0.0.1`` off the allowlist, so opening
    the other spelling answered 403 on every request."""
    from hexton.api.security import allowed_hosts_for

    for configured in ("127.0.0.1", "localhost", "::1"):
        hosts = allowed_hosts_for(configured, 8787)
        assert "127.0.0.1:8787" in hosts, configured
        assert "localhost:8787" in hosts, configured
        assert "[::1]:8787" in hosts, configured
        # An unbracketed IPv6 Host header is not a valid Host value and must never be
        # accepted just because it contains the configured text.
        assert "::1:8787" not in hosts, configured


def test_a_connection_referenced_by_a_job_keeps_its_credential_on_a_refused_delete(
    ctx, client, auth, ids, connection
) -> None:
    """F-11: the credential used to be deleted *before* the reference check that raises
    409, so a refused delete had already performed half of a revoke."""
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth)
    assert created.status_code == 201

    response = client.delete(f"/api/connections/{ids['connection_id']}", headers=auth)

    assert response.status_code == 409
    assert ctx.credentials.read(f"conn:{connection.public_id}:access") == ACCESS_SECRET
    assert ctx.credentials.read(f"conn:{connection.public_id}:refresh") == REFRESH_SECRET
    with ctx.sessions() as session:
        from hexton.db.models import PlatformConnection

        surviving = session.get(PlatformConnection, ids["connection_id"])
        assert surviving is not None
        assert surviving.status == "active"


def test_media_check_does_not_fingerprint_a_file_that_is_not_a_clip(client, auth, tmp_path) -> None:
    """F-19: ``POST /api/media/check`` takes any absolute path, so it must not hand back
    the digest of a file it has already decided is not a video."""
    secret = tmp_path / "session.key"
    secret.write_bytes(b"0123456789abcdef" * 4)

    body = client.post("/api/media/check", json={"path": str(secret)}, headers=auth).json()
    assert body["exists"] is True, "existence is what this endpoint is for"
    assert body["sha256"] is None
    assert body["size_bytes"] == 0
    assert any(issue["code"] == "bad_extension" for issue in body["issues"])


def test_media_check_still_reports_a_real_clip_completely(client, auth, clip) -> None:
    body = client.post("/api/media/check", json={"path": str(clip)}, headers=auth).json()
    assert body["sha256"] and len(body["sha256"]) == 64
    assert body["size_bytes"] > 0
    assert body["container"] is not None


def test_a_published_job_still_reads_after_its_observations_are_pruned(
    ctx, client, auth, ids
) -> None:
    """F-02: ``_prune_evidence`` drops ``RemoteObservation`` rows after ``retention_days``
    while the job row stays, and ``display_for`` then raised ``ValueError`` for a
    PUBLISHED state with no surviving observation — taking ``GET /api/jobs`` down with
    it. ``published_at_remote`` is written with the same transition and is not pruned.
    """
    from datetime import UTC, datetime

    created = client.post("/api/jobs", json=job_payload(ids), headers=auth)
    job_id = created.json()["id"]
    with ctx.sessions() as session:
        job = session.get(ScheduledJob, job_id)
        job.state = "JOB-PUBLISHED"
        job.video_id = "vid-pruned"
        job.video_url = "https://www.youtube.com/watch?v=vid-pruned"
        job.published_at_remote = datetime.now(UTC)
        session.commit()
        assert not job.observations, "the fixture never polled; evidence is the job's own"

    listing = client.get("/api/jobs", headers=auth)
    assert listing.status_code == 200
    row = next(item for item in listing.json()["items"] if item["id"] == job_id)
    assert row["display_label"] == "Published (verified)"
    assert row["remote_state"] == "remote.public"

    detail = client.get(f"/api/jobs/{job_id}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["job"]["display_label"] == "Published (verified)"


def test_a_published_job_with_no_evidence_at_all_is_not_labelled_verified(
    ctx, client, auth, ids
) -> None:
    """The read path must degrade, never 500 — and never claim verification it cannot
    support. This state is unreachable through the state machine; it is the corrupt-row
    case, and it is exactly the false publication claim the guard exists to prevent."""
    created = client.post("/api/jobs", json=job_payload(ids), headers=auth)
    job_id = created.json()["id"]
    with ctx.sessions() as session:
        job = session.get(ScheduledJob, job_id)
        job.state = "JOB-PUBLISHED"
        session.commit()

    listing = client.get("/api/jobs", headers=auth)
    assert listing.status_code == 200
    row = next(item for item in listing.json()["items"] if item["id"] == job_id)
    assert row["display_label"] != "Published (verified)"
    assert row["remote_state"] != "remote.public"


def test_the_settings_endpoint_says_whether_the_connector_can_reach_youtube(
    ctx, client, auth
) -> None:
    """F-03: a UI cannot warn about a capability it is never told about.

    ``connector_kind`` alone is not the signal — a build configured for ``youtube``
    while the connector is still a stub would read as working. ``connector_ready`` is
    derived from the connector itself.
    """
    body = client.get("/api/settings", headers=auth).json()
    assert body["connector_kind"] == "fake"
    assert body["connector_ready"] is False
    assert body["build_profile"] == "development"
