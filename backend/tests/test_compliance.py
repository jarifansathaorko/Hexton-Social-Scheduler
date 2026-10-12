"""Compliance behaviour: the gate, disable-versus-revoke, diagnostics, retention.

``02-technical-prd.md`` §10 (``COMP-01``..``COMP-10``), ``DF-20``/``DF-21``, ``D-1``.

These are the requirements most likely to be dropped quietly under schedule pressure,
because none of them fail loudly when removed — the app keeps working and simply stops
being compliant. So each is pinned here as a behaviour rather than as prose.

The three this file exists to protect:

1. **A revised privacy policy re-closes the gate.** An inherited "yes" is not consent.
2. **Disabling is not revoking.** Disabling erases nothing and needs no reauthorization;
   revoking deletes credentials and starts a deletion deadline that is *recorded*, not
   remembered.
3. **"No credential" and "the request failed" are different answers.** They send an
   operator to different fixes, and conflating them sends them to reauthorize for a
   network blip.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import BASE, FAKE_SHEETS_TOKEN, make_job, make_media
from fastapi.testclient import TestClient
from hexton.compliance.policy import (
    CLIENT_REVOCATION_RETENTION_DAYS,
    GOOGLE_REVOCATION_RETENTION_DAYS,
    NEVER_TRANSFORM_FIELDS,
    POLICY_LINKS,
    PRIVACY_COMMITMENTS,
    PRIVACY_POLICY_VERSION,
    DiagnosticCode,
    Feature,
    MetadataConsent,
    PolicyAcceptance,
    PrivacyPolicyRequired,
    diagnose,
    disable_outcome,
    gate,
    has_accepted,
    may_transform,
    require_gate,
    retention_policy,
    revoke_outcome,
)
from hexton.compliance.retention import (
    RetentionSweeper,
    schedule_for_unrefreshable_token,
)
from hexton.compliance.retention import (
    status as retention_status,
)
from hexton.db.models import (
    ChannelIdentity,
    DataRetention,
    PlatformConnection,
    PolicyAcceptanceRecord,
    ScheduledJob,
)

TOMORROW = BASE + timedelta(days=1)


# ==========================================================================
# COMP-01 - the privacy-policy gate
# ==========================================================================


def test_the_gate_is_closed_until_something_is_accepted() -> None:
    result = gate(None, Feature.SHEETS_REPORTING)
    assert result.allowed is False
    assert PRIVACY_POLICY_VERSION in result.reason
    assert "sheets reporting" in result.reason


def test_require_gate_raises_rather_than_returning() -> None:
    with pytest.raises(PrivacyPolicyRequired):
        require_gate(None, Feature.YOUTUBE_CONNECTION)


def test_accepting_opens_every_feature() -> None:
    acceptance = PolicyAcceptance(version=PRIVACY_POLICY_VERSION, accepted_at=BASE)
    assert has_accepted(acceptance)
    for feature in Feature:
        assert gate(acceptance, feature).allowed, f"{feature} stayed closed after acceptance"


def test_an_acceptance_without_a_timestamp_is_not_an_acceptance() -> None:
    acceptance = PolicyAcceptance(version=PRIVACY_POLICY_VERSION, accepted_at=None)
    assert has_accepted(acceptance) is False
    assert gate(acceptance, Feature.YOUTUBE_CONNECTION).allowed is False


def test_a_revised_policy_version_re_closes_the_gate() -> None:
    """``COMP-01``'s real teeth. A silently inherited "yes" is not consent.

    When the policy text changes, it changes because the data handling changed — so the
    operator has to read it again.
    """
    acceptance = PolicyAcceptance(version="0.9", accepted_at=BASE)
    assert has_accepted(acceptance) is False, "an old version must not carry forward"
    result = gate(acceptance, Feature.YOUTUBE_CONNECTION)
    assert result.allowed is False
    assert "0.9" not in result.reason, "the message names the version they must accept now"


def test_every_feature_is_a_named_api_connected_feature() -> None:
    """A new API-connected feature that is not in the enum cannot be gated at all."""
    assert {f.value for f in Feature} == {
        "youtube_connection",
        "sheets_reporting",
        "mention_workflow",
    }


# --------------------------------------------------------------------------
# COMP-02 - the links and the retention deadlines, from the documentation pins
# --------------------------------------------------------------------------


def test_the_required_policy_links_are_present_and_are_the_pinned_urls() -> None:
    assert len(POLICY_LINKS) >= 4
    for name, url in POLICY_LINKS.items():
        assert url.startswith("https://"), f"{name} is not an https URL"
        assert "google.com" in url or "youtube.com" in url


def test_the_commitments_say_where_credentials_live() -> None:
    joined = " ".join(PRIVACY_COMMITMENTS).lower()
    assert "credential manager" in joined
    assert "windows" in joined
    assert "never sent to a hexton server" in joined


def test_the_retention_windows_are_the_documented_ones() -> None:
    """``DF-20``/``DF-21``: 7 and 30 calendar days. Neither may be lengthened here."""
    assert CLIENT_REVOCATION_RETENTION_DAYS == 7
    assert GOOGLE_REVOCATION_RETENTION_DAYS == 30
    assert revoke_outcome(initiated_by="operator").authorized_data_retention_days == 7
    assert revoke_outcome(initiated_by="google").authorized_data_retention_days == 30
    assert revoke_outcome(initiated_by="unrefreshable").authorized_data_retention_days == 30


def test_the_policy_says_a_backup_cannot_outlive_the_live_data() -> None:
    """A backup that survives the deletion deadline is itself a retention breach."""
    note = retention_policy().backups.lower()
    assert "same deadline" in note


# ==========================================================================
# COMP-06 - optional metadata modification is opt-in, and refuses some fields always
# ==========================================================================


def test_the_metadata_consent_default_is_no_for_everything() -> None:
    """There is no global "transform my titles" default, because the default is *no*."""
    consent = MetadataConsent()
    assert consent.any is False
    assert may_transform(consent, "title") is False
    assert may_transform(consent, "description") is False


def test_consent_grants_only_the_field_it_names() -> None:
    titles_only = MetadataConsent(transform_titles=True, granted_by="the operator", granted_at=BASE)
    assert may_transform(titles_only, "title") is True
    assert may_transform(titles_only, "description") is False


def test_a_field_nobody_can_ever_consent_to_is_refused_even_with_full_consent() -> None:
    """Privacy, publish time and tags are never transformed, whatever is granted."""
    everything = MetadataConsent(transform_titles=True, transform_descriptions=True)
    for field_name in NEVER_TRANSFORM_FIELDS:
        assert may_transform(everything, field_name) is False, field_name


def test_an_unnamed_field_is_refused() -> None:
    everything = MetadataConsent(transform_titles=True, transform_descriptions=True)
    assert may_transform(everything, "thumbnail") is False
    assert may_transform(everything, "") is False


# ==========================================================================
# COMP-07 - disabling is not revoking
# ==========================================================================


def test_disable_and_revoke_are_different_operations() -> None:
    """The distinction the Phase 2 review was explicit about, pinned structurally."""
    disabled = disable_outcome()
    revoked = revoke_outcome()

    assert disabled.deletes_credentials is False
    assert disabled.revokes_grant is False
    assert disabled.deletes_bookkeeping is False

    assert revoked.deletes_credentials is True
    assert revoked.revokes_grant is True
    assert revoked.deletes_bookkeeping is False, "bookkeeping is the operator's record"

    assert disabled.explanation != revoked.explanation
    assert "nothing is deleted" in disabled.explanation.lower()
    assert "7 calendar days" in revoked.explanation.lower()


def test_disabling_erases_nothing(ctx, session, connection) -> None:
    """``COMP-07``, at the storage layer: no secret touched, no row scheduled for purge."""
    conn = connection
    conn.granted_at = BASE
    session.commit()
    key = f"conn:{conn.public_id}"
    before = ctx.credentials.read(f"{key}:access")
    assert before, "the fixture must have written a credential for this to mean anything"

    with ctx.sessions() as s:
        row = s.get(PlatformConnection, conn.id)
        row.status = "disabled"
        row.disabled_at = BASE
        s.commit()

    with ctx.sessions() as s:
        row = s.get(PlatformConnection, conn.id)
        assert row.status == "disabled"
        assert row.disabled_at == BASE
        assert row.revoked_at is None
        assert s.query(DataRetention).filter_by(subject_id=conn.public_id).count() == 0, (
            "disabling starts no deletion clock, because nothing is being deleted"
        )
    assert ctx.credentials.read(f"{key}:access") == before
    assert ctx.credentials.read(f"{key}:refresh") is not None


def test_enabling_after_disabling_needs_no_reauthorization(ctx, session, connection) -> None:
    conn = connection
    conn.granted_at = BASE
    session.commit()
    from hexton.api.app import create_app

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        headers = _csrf(client)
        client.post(f"/api/compliance/connections/{conn.id}/disable", headers=headers)
        body = client.post(f"/api/compliance/connections/{conn.id}/enable", headers=headers).json()

    assert body["status"] == "active"
    assert any("reauthoriz" in item.lower() for item in body["did_not"])
    assert ctx.credentials.read(f"conn:{conn.public_id}:access") is not None


def test_revoking_deletes_the_credentials_and_starts_a_recorded_deadline(
    ctx, session, connection
) -> None:
    """``D-1``: the deadline is a row, not a memory. An operator who closes the app for
    a month must still be compliant when it reopens."""
    conn = connection
    conn.granted_at = BASE
    session.commit()

    from hexton.api.app import create_app

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        headers = _csrf(client)
        body = client.post(f"/api/compliance/connections/{conn.id}/revoke", headers=headers).json()

    assert body["status"] == "revoked"
    assert ctx.credentials.read(f"conn:{conn.public_id}:access") is None
    assert ctx.credentials.read(f"conn:{conn.public_id}:refresh") is None
    assert body["authorized_data_retention_days"] == CLIENT_REVOCATION_RETENTION_DAYS

    with ctx.sessions() as s:
        row = s.query(DataRetention).filter_by(subject_id=conn.public_id).one()
        assert row.reason == "revocation_client"
        assert row.purged_at is None, "recording a deadline is not the same as purging"
        # Measured from when the row was written, not from the test's fixed clock: the
        # route stamps its own now. A few microseconds of slack because ``now`` is
        # captured before the flush that stamps ``created_at``; the property under test
        # is the length of the window, not the exact instant.
        assert abs((row.deadline - row.created_at) - timedelta(days=7)) < timedelta(seconds=5)
        assert row.deadline > datetime.now(UTC), "the deadline is in the future by construction"


def test_a_revoked_connection_refuses_to_be_disabled_or_enabled(ctx, session, connection) -> None:
    """The two operations must not be interchangeable, or one click undoes the other."""
    conn = connection
    conn.granted_at = BASE
    session.commit()

    from hexton.api.app import create_app

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        headers = _csrf(client)
        client.post(f"/api/compliance/connections/{conn.id}/revoke", headers=headers)
        disable = client.post(f"/api/compliance/connections/{conn.id}/disable", headers=headers)
        enable = client.post(f"/api/compliance/connections/{conn.id}/enable", headers=headers)

    assert disable.status_code == 409
    assert enable.status_code == 409
    assert "reauthorize" in enable.json()["detail"].lower()


def test_the_two_revoke_endpoints_record_the_same_deadline(ctx, session, connection) -> None:
    """Both routers revoke. They must not drift apart on the number that matters."""
    from hexton.api.app import create_app

    conn = connection
    conn.granted_at = BASE
    session.commit()

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        headers = _csrf(client)
        client.post(f"/api/compliance/connections/{conn.id}/revoke", headers=headers)

    with ctx.sessions() as s:
        deadline = s.query(DataRetention).filter_by(subject_id=conn.public_id).one().deadline

    second = PlatformConnection(
        provider="sheets", label="Second", status="active", scopes=["spreadsheets"]
    )
    with ctx.sessions() as s:
        s.add(second)
        s.flush()
        ctx.credentials.write(f"conn:{second.public_id}:access", FAKE_SHEETS_TOKEN)
        s.commit()
        second_id = second.id

    from hexton.api.app import create_app as _create

    with TestClient(
        _create(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        headers = _csrf(client)
        client.post(f"/api/connections/{second_id}/revoke", headers=headers)

    with ctx.sessions() as s:
        other = s.query(DataRetention).filter_by(subject_id=second.public_id).one()

    assert other.deadline.date() == deadline.date()
    assert other.reason == "revocation_client"


def test_a_disable_records_a_timestamp_so_the_distinction_is_visible_in_the_data(
    ctx, session, connection
) -> None:
    """``COMP-07`` in the schema, not only in the UI copy."""
    conn = connection
    conn.granted_at = BASE
    session.commit()
    from hexton.api.app import create_app

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        client.post(f"/api/compliance/connections/{conn.id}/disable", headers=_csrf(client))

    with ctx.sessions() as s:
        row = s.get(PlatformConnection, conn.id)
        assert row.disabled_at is not None
        assert row.revoked_at is None
        assert row.status == "disabled"


# ==========================================================================
# COMP-10 - diagnostics distinguish the two failure families
# ==========================================================================


def test_no_credential_and_a_failed_request_are_different_answers() -> None:
    missing = diagnose(connected=False, policy_accepted=True, credential_present=False)
    failed = diagnose(connected=True, policy_accepted=True, remote_error="connection reset by peer")

    assert missing.code is DiagnosticCode.NOT_CONNECTED
    assert failed.code is DiagnosticCode.REMOTE_FAILED
    assert missing.code != failed.code


def test_only_a_remote_failure_is_worth_retrying() -> None:
    """Retrying a missing credential cannot help; retrying a network blip can.

    ``retry_worthwhile`` is what the UI uses to decide whether to offer "Try again", so
    getting it backwards either wastes the operator's time or hides the fix.
    """
    failed = diagnose(connected=True, policy_accepted=True, remote_error="connection reset by peer")
    missing = diagnose(connected=False, policy_accepted=True, credential_present=False)

    assert failed.retry_worthwhile is True
    assert missing.retry_worthwhile is False


def test_the_remedy_for_a_failed_request_says_reauthorizing_will_not_help() -> None:
    failed = diagnose(connected=True, policy_accepted=True, remote_error="503 from Google")
    assert "reauthorizing will not help" in failed.remedy.lower()


def test_an_unaccepted_policy_beats_every_other_cause() -> None:
    """Earliest cause wins, so the operator is not sent to fix the second problem."""
    result = diagnose(
        connected=False,
        policy_accepted=False,
        credential_present=False,
        remote_error="also broken",
    )
    assert result.code is DiagnosticCode.POLICY_NOT_ACCEPTED


def test_a_disabled_connection_is_not_an_authorization_problem() -> None:
    result = diagnose(connected=True, policy_accepted=True, enabled=False)
    assert result.code is DiagnosticCode.DISABLED
    assert "does not revoke" in result.remedy.lower()


def test_an_expired_credential_is_its_own_answer() -> None:
    result = diagnose(
        connected=True, policy_accepted=True, credential_expired=True, remote_error="401"
    )
    assert result.code is DiagnosticCode.CREDENTIAL_UNUSABLE
    assert result.retry_worthwhile is False


def test_a_healthy_connection_is_just_ok() -> None:
    result = diagnose(connected=True, policy_accepted=True)
    assert result.code is DiagnosticCode.OK
    assert result.remedy


# ==========================================================================
# D-1 - retention: a deadline is a record, and the sweep honours it
# ==========================================================================


def test_an_unrefreshable_token_starts_the_thirty_day_clock(ctx, session, connection) -> None:
    """``DF-21``. The operator did not ask for this and may not have noticed."""
    conn = connection
    row = schedule_for_unrefreshable_token(session, conn, now=BASE)
    session.commit()

    assert row.reason == "revocation_google"
    assert row.deadline.date() - BASE.date() == timedelta(days=30)
    assert row.purged_at is None


def test_the_google_side_path_is_not_confused_with_the_client_side_one(
    ctx, session, connection
) -> None:
    """``DF-21`` vs ``DF-20``. Getting the wrong one doubles a retention window silently."""
    google_side = schedule_for_unrefreshable_token(session, connection, now=BASE)
    session.commit()

    assert google_side.reason == "revocation_google"
    assert google_side.deadline.date() - BASE.date() == timedelta(days=30)
    assert revoke_outcome(initiated_by="operator").authorized_data_retention_days == 7, (
        "the client-side path stays at the DF-20 window"
    )


def test_the_sweep_purges_only_after_the_deadline(ctx, session, connection) -> None:
    conn = connection
    conn.granted_at = BASE
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=TOMORROW,
        )
    )
    session.commit()
    identities_before = len(conn.identities)

    report = RetentionSweeper(ctx).run(now=BASE)

    assert report.connections_purged == 0
    assert report.retention_rows_closed == 0
    assert ctx.credentials.read(f"conn:{conn.public_id}:access") is not None
    assert len(conn.identities) == identities_before


def test_the_sweep_purges_at_the_deadline_and_closes_the_row(ctx, session, connection) -> None:
    """The row must end up closed *with a note*, so an audit can tell purge from expiry."""
    conn = connection
    conn.granted_at = BASE
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=BASE,
        )
    )
    session.commit()

    report = RetentionSweeper(ctx).run(now=BASE + timedelta(minutes=1))

    assert report.connections_purged == 1
    assert report.retention_rows_closed == 1
    assert ctx.credentials.read(f"conn:{conn.public_id}:access") is None
    assert ctx.credentials.read(f"conn:{conn.public_id}:refresh") is None

    with ctx.sessions() as s:
        row = s.query(DataRetention).filter_by(subject_id=conn.public_id).one()
        assert row.purged_at is not None
        assert "deadline" in (row.purge_note or "").lower()
        purged = s.get(PlatformConnection, conn.id)
        assert purged.status == "deleted"
        assert purged.authorized_data_deleted_at is not None
        assert purged.scopes == [], "the granted scopes are Authorized Data"
        assert purged.identities == [], "channel identities are Authorized Data"


def test_a_purged_connection_cannot_be_reenabled(ctx, session, connection) -> None:
    """Deleting the authorization must not leave a route back in."""
    conn = connection
    conn.granted_at = BASE
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=BASE,
        )
    )
    session.commit()
    RetentionSweeper(ctx).run(now=BASE + timedelta(minutes=1))

    from hexton.api.app import create_app

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as client:
        response = client.post(
            f"/api/compliance/connections/{conn.id}/enable", headers=_csrf(client)
        )

    assert response.status_code == 409


def test_the_retention_status_reports_what_is_still_open(ctx, session, connection) -> None:
    conn = connection
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=BASE + timedelta(days=7),
        )
    )
    session.commit()

    open_now = retention_status(session, now=BASE)
    later = retention_status(session, now=BASE + timedelta(days=8))

    assert len(open_now.open_deadlines) == 1
    assert open_now.worst_case_days_remaining == 7
    assert open_now.overdue is False
    assert later.overdue is True, "an overdue deadline is an operator problem, not a silent one"


def test_a_closed_row_leaves_nothing_open(ctx, session, connection) -> None:
    RetentionSweeper(ctx).run(now=BASE)
    with ctx.sessions() as s:
        assert retention_status(s, now=BASE).open_deadlines == ()


# ==========================================================================
# The API surface
# ==========================================================================


def _csrf(client) -> dict:
    from hexton.api.security import CSRF_HEADER

    response = client.post("/api/session")
    assert response.status_code == 200, response.text
    return {CSRF_HEADER: response.json()["csrf_token"]}


@pytest.fixture
def client(ctx):
    from hexton.api.app import create_app

    with TestClient(
        create_app(ctx), base_url=f"http://{ctx.settings.host}:{ctx.settings.port}"
    ) as c:
        c.post("/api/session")
        yield c


@pytest.fixture
def auth(client) -> dict:
    return _csrf(client)


def test_the_policy_status_starts_unaccepted(client) -> None:
    body = client.get("/api/compliance/policy").json()
    assert body["accepted"] is False
    assert body["version"] == PRIVACY_POLICY_VERSION
    assert body["accepted_at"] is None


def test_accepting_supersedes_rather_than_overwriting(client, ctx) -> None:
    """An audit trail showing only the latest "yes" cannot answer "when did that stop?"."""
    headers = _csrf(client)
    first = client.post(
        "/api/compliance/policy/accept", json={"accepted_by": "operator one"}, headers=headers
    ).json()
    second = client.post(
        "/api/compliance/policy/accept", json={"accepted_by": "operator two"}, headers=headers
    ).json()

    assert first["accepted"] is True and second["accepted"] is True
    assert second["accepted_by"] == "operator two"

    with ctx.sessions() as s:
        rows = s.query(PolicyAcceptanceRecord).all()
        assert len(rows) == 2, "the earlier acceptance is kept, not deleted"
        assert sum(1 for r in rows if r.superseded_at is not None) == 1


def test_the_policy_detail_carries_the_links_and_the_deadlines(client) -> None:
    body = client.get("/api/compliance/policy/detail").json()

    assert body["version"] == PRIVACY_POLICY_VERSION
    assert set(body["links"]) == set(POLICY_LINKS)
    assert body["retention"]["client_revocation_days"] == 7
    assert body["retention"]["google_revocation_days"] == 30
    assert "same deadline" in body["retention"]["note"].lower()


def test_a_feature_check_is_refused_before_acceptance_and_allowed_after(client) -> None:
    headers = _csrf(client)

    closed = client.post("/api/compliance/policy/check/sheets_reporting", headers=headers)
    assert closed.status_code == 403
    assert "privacy policy" in closed.json()["detail"].lower()

    client.post(
        "/api/compliance/policy/accept", json={"accepted_by": "the operator"}, headers=headers
    )
    opened = client.post("/api/compliance/policy/check/sheets_reporting", headers=headers)
    assert opened.status_code == 200
    assert opened.json()["accepted"] is True


def test_an_unknown_feature_is_a_404_not_a_silent_allow(client) -> None:
    response = client.post("/api/compliance/policy/check/everything", headers=_csrf(client))
    assert response.status_code == 404
    assert "everything" in response.json()["detail"]


def test_reporting_is_gated_before_the_policy_is_accepted(
    client, session, sheets_connection
) -> None:
    """``COMP-01`` on the feature that actually talks to Google."""
    headers = _csrf(client)

    gated = [
        client.get("/api/reporting/config"),
        client.get("/api/reporting/health"),
        client.put("/api/reporting/config", json={}, headers=headers),
        client.post("/api/reporting/verify", headers=headers),
        client.post("/api/reporting/test-write", headers=headers),
        client.post("/api/reporting/sync", headers=headers),
        client.get("/api/reporting/outbox"),
    ]

    for response in gated:
        assert response.status_code == 403, (response.request.url, response.text)
        assert "privacy policy" in response.json()["detail"].lower()

    assert client.get("/api/reporting/limits").status_code == 200, (
        "the reconciliation limits are information an operator needs before deciding "
        "whether to connect at all"
    )


def test_the_diagnostic_route_distinguishes_the_two_families(client, ctx, session) -> None:
    never = PlatformConnection(provider="youtube", label="Never", status="active", scopes=[])
    failing = PlatformConnection(
        provider="youtube", label="Failing", status="active", scopes=["upload"]
    )
    with ctx.sessions() as s:
        s.add_all([never, failing])
        s.flush()
        never_id, failing_id = never.id, failing.id
        failing.granted_at = BASE
        failing.last_token_check_ok = False
        s.commit()
    # ``failing`` has to *have* a credential, or the answer really is "not connected"
    # and the test proves nothing about the remote-failure branch. Reading the store is
    # the whole point of ``COMP-10``: an "active" row whose credential is already gone
    # used to be reported as healthy.
    ctx.credentials.write(f"conn:{failing.public_id}:access", "stored-access-token")

    _csrf(client)
    client.post("/api/compliance/policy/accept", json={"accepted_by": "op"}, headers=_csrf(client))

    missing = client.get(f"/api/compliance/connections/{never_id}/diagnostic").json()
    failed = client.get(f"/api/compliance/connections/{failing_id}/diagnostic").json()

    assert missing["code"] == "not_connected"
    assert failed["code"] == "remote_failed"
    assert missing["retry_worthwhile"] is False
    assert failed["retry_worthwhile"] is True


def test_a_credential_missing_from_the_store_is_not_reported_as_connected(
    client, ctx, session, connection
) -> None:
    """``COMP-10``: the diagnostic must read the credential store, not the row's status.

    ``connection`` is ``status="active"`` with a ``granted_at`` set, so every column the
    old code consulted said "connected". Its credential is removed underneath it — the
    state a partial revoke or a manual clear of the store leaves behind — and reporting
    ``ok`` / "Connected and reachable." there sends the operator to the wrong fix.
    """
    conn = connection
    conn.granted_at = BASE
    session.commit()
    ctx.credentials.delete(f"conn:{conn.public_id}:access")
    ctx.credentials.delete(f"conn:{conn.public_id}:refresh")

    _csrf(client)
    client.post("/api/compliance/policy/accept", json={"accepted_by": "op"}, headers=_csrf(client))
    body = client.get(f"/api/compliance/connections/{conn.id}/diagnostic").json()

    assert body["code"] != "ok"
    assert body["retry_worthwhile"] is False
    assert "connected and reachable" not in body["detail"].lower()


def test_every_diagnostic_code_is_reachable_enough_to_be_labelled(client) -> None:
    codes = client.get("/api/compliance/diagnostics/codes").json()
    assert set(codes) == {c.value for c in DiagnosticCode}


def test_a_compliance_response_never_carries_a_token(client, ctx, session, connection) -> None:
    conn = connection
    conn.granted_at = BASE
    session.commit()
    headers = _csrf(client)

    responses = [
        client.get("/api/compliance/policy"),
        client.get("/api/compliance/policy/detail"),
        client.get(f"/api/compliance/connections/{conn.id}/diagnostic"),
        client.post(f"/api/compliance/connections/{conn.id}/disable", headers=headers),
        client.post(f"/api/compliance/connections/{conn.id}/enable", headers=headers),
    ]

    for response in responses:
        assert "access-token" not in response.text
        assert "refresh-token" not in response.text
        assert "sheets-access-token" not in response.text


def test_the_disable_response_names_what_it_did_not_do(client, ctx, session, connection) -> None:
    """An operator must be able to tell disable from revoke from the response alone."""
    conn = connection
    conn.granted_at = BASE
    session.commit()

    body = client.post(
        f"/api/compliance/connections/{conn.id}/disable", headers=_csrf(client)
    ).json()

    assert any("did not delete" in item.lower() for item in body["did_not"])
    assert any("did not revoke" in item.lower() for item in body["did_not"])
    assert any("bookkeeping" in item.lower() for item in body["did_not"])
    assert body["authorized_data_retention_days"] is None, "disabling starts no clock"


def test_the_revoke_response_names_the_retention_days(client, ctx, session, connection) -> None:
    conn = connection
    conn.granted_at = BASE
    session.commit()

    body = client.post(
        f"/api/compliance/connections/{conn.id}/revoke", headers=_csrf(client)
    ).json()

    assert body["authorized_data_retention_days"] == 7
    assert any("deleted" in item.lower() for item in body["did"])


def test_marking_data_deleted_closes_the_retention_row(client, ctx, session, connection) -> None:
    """Recording the deadline and confirming the purge are separate steps, on purpose."""
    conn = connection
    conn.granted_at = BASE
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=BASE + timedelta(days=7),
        )
    )
    session.commit()

    response = client.post(f"/api/connections/{conn.id}/mark-data-deleted", headers=_csrf(client))

    assert response.status_code == 200
    with ctx.sessions() as s:
        row = s.query(DataRetention).filter_by(subject_id=conn.public_id).one()
        assert row.purged_at is not None
        assert "operator confirmed" in (row.purge_note or "").lower()
        assert s.get(PlatformConnection, conn.id).authorized_data_deleted_at is not None


# ==========================================================================
# Cross-cutting: the gate does not hide the explanation of the gate
# ==========================================================================


def test_the_gate_is_the_only_thing_between_an_operator_and_their_own_data(
    client, ctx, session, connection, clip
) -> None:
    """Opening the gate must be enough; nothing else should stay closed afterwards."""
    headers = _csrf(client)
    before = client.get("/api/reporting/config")
    client.post("/api/compliance/policy/accept", json={"accepted_by": "op"}, headers=headers)
    after = client.get("/api/reporting/config")

    assert before.status_code == 403
    assert after.status_code == 200


def test_a_job_created_before_acceptance_is_still_there_after(
    client, ctx, session, connection, clip
) -> None:
    """The gate guards API-connected features, not the operator's own bookkeeping."""
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    headers = _csrf(client)

    before = client.get(f"/api/jobs/{job.id}")
    client.post("/api/compliance/policy/accept", json={"accepted_by": "op"}, headers=headers)
    after = client.get(f"/api/jobs/{job.id}")

    assert before.status_code == 200
    assert after.status_code == 200
    assert after.json()["job"]["id"] == job.id
    assert session.query(ScheduledJob).count() == 1


def test_a_channel_identity_survives_a_disable_but_not_a_purge(ctx, session, connection) -> None:
    """An identity is Authorized Data: kept while the grant stands, gone when it does not."""
    conn = connection
    assert conn.identities, "the fixture grants an identity"

    with ctx.sessions() as s:
        row = s.get(PlatformConnection, conn.id)
        row.status = "disabled"
        s.commit()
    assert len(conn.identities) == 1, "disabling erases nothing"

    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=BASE,
        )
    )
    session.commit()
    RetentionSweeper(ctx).run(now=BASE + timedelta(minutes=1))

    with ctx.sessions() as s:
        assert s.query(ChannelIdentity).filter_by(connection_id=conn.id).count() == 0


# ==========================================================================
# Regression: F-01 (retention purge vs. a job that references the identity)
# ==========================================================================


def test_purging_a_connection_that_has_jobs_does_not_raise(ctx, session, connection, clip) -> None:
    """``scheduled_job.channel_identity_id`` is ``ondelete="RESTRICT"``.

    The sweep deleted the identity while a job still pointed at it, so the flush raised
    ``IntegrityError``. Because ``Worker.startup`` runs this unguarded, that exception
    stopped the *worker* — seven days after any revocation that had a single job behind
    it, every scheduled upload on the machine silently stopped.
    """
    conn = connection
    media = make_media(session, clip)
    job = make_job(session, media, conn, state="JOB-PUBLISHED")
    identity_id = conn.identities[0].id
    assert job.channel_identity_id == identity_id

    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=conn.public_id,
            reason="revocation_client",
            deadline=BASE,
        )
    )
    session.commit()

    report = RetentionSweeper(ctx).run(now=BASE + timedelta(minutes=1))

    assert report.connections_purged == 1
    assert report.retention_rows_closed == 1
    with ctx.sessions() as s:
        assert s.query(ChannelIdentity).count() == 0, "Authorized Data is still deleted"
        surviving = s.get(ScheduledJob, job.id)
        assert surviving is not None, "the job's audit trail is untouched"
        assert surviving.channel_identity_id is None
        assert surviving.state == "JOB-PUBLISHED"
        assert any("retention" in event.message.lower() for event in surviving.events), (
            "the detachment is recorded rather than silent"
        )
    assert ctx.credentials.read(f"conn:{conn.public_id}:refresh") is None


def test_the_worker_starts_with_an_overdue_retention_deadline(
    ctx, session, connection, clip
) -> None:
    """The sweep must not be able to prevent the worker from running.

    This is the load-bearing assertion for F-01: the defect was not that one purge
    failed, but that its failure was fatal to ``Worker.startup``.
    """
    from hexton.worker.main import Worker

    media = make_media(session, clip)
    make_job(session, media, connection, state="JOB-QUEUED")
    session.add(
        DataRetention(
            subject_type="connection",
            subject_id=connection.public_id,
            reason="revocation_client",
            deadline=BASE,
        )
    )
    session.commit()

    worker = Worker(ctx, owner="regression-worker")
    orphans = worker.startup(now=BASE + timedelta(minutes=1))

    assert orphans == []
    with ctx.sessions() as s:
        assert s.query(ChannelIdentity).count() == 0
        assert s.query(ScheduledJob).count() == 1
        row = s.query(DataRetention).one()
        assert row.purged_at is not None
