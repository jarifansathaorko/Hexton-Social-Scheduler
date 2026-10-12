"""Creator mentions: a human action, tracked honestly.

``DF-19``, ``FEAS-06``, ``AC-MVP-009``, ``04-feature-prd.md`` §10 FEAT-008.

The single property this file exists to protect: **a mention state never becomes
VERIFIED without a human attestation**. ``DF-19`` records that the ``videos`` resource
exposes no mention entity, so no API response, sync, retry, or migration can be evidence
that a mention exists. Every test below either upholds that, or checks that the app says
plainly what it cannot do.

Second property: **text is never evidence**. An ``@handle`` in a description is
characters. It moves a mention towards "manual required", never towards "verified".
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import BASE, make_job, make_media
from fastapi.testclient import TestClient
from hexton.db.models import ScheduledJob
from hexton.domain.jobstate import JobState, MentionState
from hexton.domain.mention import (
    MENTION_VERIFIED_STATES,
    assess,
    extract_handles,
    mention_display,
    mention_is_verified,
    mention_workflow,
    removal_risk,
)
from hexton.services import mentions as mention_service

# ==========================================================================
# The vocabulary
# ==========================================================================


def test_every_mention_state_has_an_operator_facing_label() -> None:
    """The UI renders labels, not enum values, so a gap here is a blank on screen."""
    for state in MentionState:
        assert mention_display(state), f"{state} has no label"


def test_only_an_attestation_can_be_verified() -> None:
    """``DF-19``. One element, and widening it needs a documentation pin."""
    assert frozenset({MentionState.VERIFIED}) == MENTION_VERIFIED_STATES
    assert mention_is_verified(MentionState.VERIFIED)
    for state in MentionState:
        if state is MentionState.VERIFIED:
            continue
        assert not mention_is_verified(state), f"{state} must never read as verified"


def test_the_verified_label_names_the_evidence_source() -> None:
    """A bare "Verified" would let a UI imply an API check nobody performed."""
    label = mention_display(MentionState.VERIFIED).lower()
    assert "operator" in label
    assert "human" in label
    assert "attestation" in label


def test_text_only_is_labelled_as_not_verified() -> None:
    """The one state most likely to be mistaken for a mention, and most wrong to be."""
    label = mention_display(MentionState.TEXT_ONLY).lower()
    assert "not verified" in label
    assert "text" in label


def test_every_state_is_in_the_display_map_and_nowhere_else() -> None:
    """A typo in the map is a ``KeyError`` rendered straight to the operator."""
    from hexton.domain.mention import MENTION_DISPLAY_MAP

    assert set(MENTION_DISPLAY_MAP) == set(MentionState)


# ==========================================================================
# Text is never evidence
# ==========================================================================


def test_a_handle_in_the_caption_never_becomes_verified() -> None:
    """The whole reason :func:`assess` exists."""
    assessment = assess(
        mention_requested=True,
        text="Thanks to @somecreator for the duet!",
        operator_confirmed_in_studio=False,
    )
    assert assessment.state is MentionState.MANUAL_REQUIRED
    assert not assessment.verified
    assert assessment.requires_operator_action


def test_a_requested_mention_with_no_handle_text_is_labelled_not_verified() -> None:
    """Requested, no text, nobody has done anything: not verified, and not a mention."""
    assessment = assess(mention_requested=True, text="A Short\ndesc")
    assert assessment.state is MentionState.TEXT_ONLY
    assert not assessment.verified
    assert assessment.handles_found == ()


def test_no_mention_requested_is_not_a_mention_state_to_act_on() -> None:
    assessment = assess(mention_requested=False, text="Thanks to @somecreator!")
    assert assessment.state is MentionState.NOT_REQUESTED
    assert not assessment.requires_operator_action


def test_extraction_reports_text_and_never_claims_a_mention() -> None:
    """``extract_handles`` is a text scanner and says so by returning bare strings."""
    assert extract_handles("shoutout to @alice and @bob!") == ("alice", "bob")
    assert extract_handles("no handles here") == ()
    assert extract_handles("") == ()


def test_an_email_address_is_not_read_as_a_handle() -> None:
    assert extract_handles("write to me@example.com") == ()


def test_a_second_documented_api_mechanism_is_the_only_widening_switch() -> None:
    """The escape hatch must be explicit and must not be reachable from text.

    ``documented_api_mechanism_demonstrated`` is the one argument that can produce a
    verified state, and it defaults off. If Google ever documents a mention entity this
    is the switch — and it must be flipped alongside the documentation pin.
    """
    from_text = assess(mention_requested=True, text="@alice")
    with_switch = assess(
        mention_requested=True, text="@alice", documented_api_mechanism_demonstrated=True
    )
    assert not from_text.verified
    assert with_switch.verified


def test_no_module_writes_verified_from_anything_but_an_attestation() -> None:
    """Structural: the only assignment of a verified mention lives in one function.

    A future helper that set the state from an API response would show up here as a new
    assignment, which is exactly the change that must not land silently.
    """
    import hexton

    pattern = re.compile(r"mention_state\s*=\s*[^#\n]*VERIFIED")
    offenders: list[str] = []
    for path in Path(hexton.__path__[0]).rglob("*.py"):
        if path.name == "mentions.py" and "services" in path.parts:
            continue  # confirm_mention, the single documented route
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert offenders == [], "unexpected VERIFIED assignment:\n" + "\n".join(offenders)


# ==========================================================================
# The workflow
# ==========================================================================


def test_the_workflow_says_what_the_app_will_not_do() -> None:
    """``FEAS-06``: the boundary belongs in the panel, not only in the docs."""
    for state in MentionState:
        workflow = mention_workflow(state)
        assert workflow.prohibitions, f"{state} shows no boundary statement"
        assert any("no mention entity" in p for p in workflow.prohibitions), (
            f"{state} does not name DF-19"
        )


def test_the_workflow_names_the_intended_creator_when_one_is_known() -> None:
    workflow = mention_workflow(MentionState.MANUAL_REQUIRED, handles=("alice",))
    assert "alice" in workflow.intended_creator
    assert any("alice" in step for step in workflow.steps)


def test_the_workflow_tells_the_operator_to_use_the_studio_picker() -> None:
    """Typing @handle as text is precisely the mistake this guards against."""
    workflow = mention_workflow(MentionState.TEXT_ONLY, handles=("alice",))
    assert any("picker" in step.lower() for step in workflow.steps)


def test_the_confirmed_workflow_never_says_the_api_verified_anything() -> None:
    workflow = mention_workflow(MentionState.VERIFIED)
    joined = " ".join(workflow.steps).lower()
    assert "api" in joined
    assert "did not" in joined or "cannot" in joined


def test_the_workflow_says_nothing_to_do_when_nothing_was_requested() -> None:
    assert mention_workflow(MentionState.NOT_REQUESTED).steps == ()


# ==========================================================================
# A rewrite can destroy a mention the operator created by hand
# ==========================================================================


def test_rewriting_a_description_that_drops_a_manual_mention_requires_a_decision() -> None:
    """``AC-MVP-009``: the API cannot read the mention back, so it cannot detect loss."""
    for state in (MentionState.VERIFIED, MentionState.PENDING_MANUAL, MentionState.MANUAL_REQUIRED):
        risk = removal_risk(state, description_changing=True, new_description_contains_handle=False)
        assert risk.requires_decision, f"{state} must warn before a rewrite"
        assert "cannot" in risk.reason.lower()


def test_even_keeping_the_handle_does_not_make_a_rewrite_safe() -> None:
    """The API still cannot see the mention, so a rewrite is still a risk."""
    risk = removal_risk(
        MentionState.VERIFIED, description_changing=True, new_description_contains_handle=True
    )
    assert risk.requires_decision
    assert "cannot" in risk.reason.lower()


def test_leaving_the_description_alone_is_never_a_risk() -> None:
    risk = removal_risk(
        MentionState.VERIFIED, description_changing=False, new_description_contains_handle=False
    )
    assert not risk.requires_decision
    assert "not being changed" in risk.reason.lower()


def test_a_not_requested_mention_carries_no_rewrite_risk() -> None:
    risk = removal_risk(
        MentionState.NOT_REQUESTED, description_changing=True, new_description_contains_handle=False
    )
    assert not risk.requires_decision


def test_the_operator_text_still_counts_as_theirs() -> None:
    """Even a mere ``@handle`` was typed by a person and must not vanish silently."""
    risk = removal_risk(
        MentionState.TEXT_ONLY, description_changing=True, new_description_contains_handle=False
    )
    assert risk.requires_decision
    assert "operator" in risk.reason.lower()


# ==========================================================================
# The service: the only route to VERIFIED, and it needs words
# ==========================================================================


def _job_with_mention(session, connection, clip, **overrides):
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.content.mention_requested = True
    job.content.mention_handles = ["alice"]
    job.content.title = "A Short"
    job.content.description = "Shoutout to @alice"
    for key, value in overrides.items():
        setattr(job, key, value)
    session.commit()
    return job


def test_confirming_requires_the_operator_to_write_what_they_did(
    ctx, session, connection, clip
) -> None:
    """A checkbox is not evidence. The words are stored, verbatim."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    with pytest.raises(mention_service.MentionError, match="sentence"):
        mention_service.confirm_mention(session, job, attestation="ok", now=BASE)


def test_an_empty_attestation_is_refused(ctx, session, connection, clip) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    with pytest.raises(mention_service.MentionError, match="sentence"):
        mention_service.confirm_mention(session, job, attestation="   ", now=BASE)


def test_a_confirmation_records_the_attestation_text_verbatim(
    ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    words = "I opened Studio and picked Alice from the mention picker on the description."

    mention_service.confirm_mention(session, job, attestation=words, now=BASE)
    session.commit()

    content = job.content
    assert content.mention_state == MentionState.VERIFIED.value
    assert content.mention_confirmed_at == BASE
    assert repr(words) in content.mention_reason, "the operator's own words are kept"


def test_a_confirmation_always_says_it_is_an_attestation(ctx, session, connection, clip) -> None:
    """The stored reason must never read as an API verification."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()

    reason = job.content.mention_reason.lower()
    assert "attestation" in reason
    assert "not an api verification" in reason


def test_a_draft_cannot_be_confirmed_because_there_is_no_video_to_mention_in(
    ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.DRAFT.value
    session.commit()

    with pytest.raises(mention_service.MentionError, match="no video in YouTube Studio"):
        mention_service.confirm_mention(
            session, job, attestation="I added the mention in Studio.", now=BASE
        )


def test_a_job_that_does_not_request_a_mention_cannot_be_confirmed(
    ctx, session, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.state = JobState.SCHEDULED.value
    session.commit()

    with pytest.raises(mention_service.MentionError, match="does not request"):
        mention_service.confirm_mention(
            session, job, attestation="I added the mention in Studio.", now=BASE
        )


def test_clearing_the_confirmation_removes_the_verified_claim(
    ctx, session, connection, clip
) -> None:
    """An attestation withdrawn after an edit must not keep claiming verification."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()
    assert mention_service.current_state(job) is MentionState.VERIFIED

    mention_service.clear(session, job)
    session.commit()

    assert job.content.mention_confirmed_at is None
    assert mention_service.current_state(job) is MentionState.MANUAL_REQUIRED
    assert not mention_is_verified(mention_service.current_state(job))


def test_clearing_a_text_only_job_leaves_it_text_only(ctx, session, connection, clip) -> None:
    """Clearing is idempotent and never invents a state."""
    job = _job_with_mention(session, connection, clip)
    job.content.description = "A Short"
    job.content.title = "A Short"
    job.state = JobState.SCHEDULED.value
    session.commit()

    mention_service.clear(session, job)
    session.commit()

    assert mention_service.current_state(job) is MentionState.TEXT_ONLY
    assert not mention_is_verified(mention_service.current_state(job))


def test_reporting_a_removed_mention_clears_the_attestation(ctx, session, connection, clip) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()

    mention_service.report_removed(session, job, now=BASE)
    session.commit()

    assert job.content.mention_state == MentionState.FAILED.value
    assert job.content.mention_confirmed_at is None
    assert not mention_is_verified(mention_service.current_state(job))
    assert "cannot detect removal" in job.content.mention_reason


def test_reporting_a_removal_on_a_job_without_a_mention_is_refused(
    ctx, session, connection, clip
) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    with pytest.raises(mention_service.MentionError, match="does not request"):
        mention_service.report_removed(session, job, now=BASE)


def test_beginning_the_manual_workflow_is_a_distinct_state(ctx, session, connection, clip) -> None:
    """ "Being worked on" and "nobody has looked at it" are different urgencies."""
    job = _job_with_mention(session, connection, clip)
    job.content.mention_state = MentionState.MANUAL_REQUIRED.value
    job.state = JobState.SCHEDULED.value
    session.commit()

    mention_service.begin_manual(session, job, now=BASE)
    session.commit()

    assert job.content.mention_state == MentionState.PENDING_MANUAL.value
    assert not mention_is_verified(mention_service.current_state(job))
    assert "nothing has been verified" in job.content.mention_reason.lower()


def test_beginning_after_a_confirmation_is_refused(ctx, session, connection, clip) -> None:
    """An attestation already on record must not be quietly downgraded to 'in progress'."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()

    with pytest.raises(mention_service.MentionError, match="already confirmed"):
        mention_service.begin_manual(session, job, now=BASE)

    assert mention_service.current_state(job) is MentionState.VERIFIED


def test_beginning_on_a_job_with_no_mention_is_refused(ctx, session, connection, clip) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    with pytest.raises(mention_service.MentionError, match="does not request"):
        mention_service.begin_manual(session, job, now=BASE)


def test_a_freshly_requested_mention_is_not_reported_as_nothing_to_do(
    ctx, session, connection, clip
) -> None:
    """``mention_state`` defaults to ``not_requested``; that default is not an opinion.

    A job whose mention was just requested still carries the column's default value.
    Reading it back as a decision would tell the operator there is nothing to do, which
    is the opposite of the truth.
    """
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    job.content.mention_requested = True
    session.commit()
    assert job.content.mention_state == MentionState.NOT_REQUESTED.value

    assert mention_service.current_state(job) is not MentionState.NOT_REQUESTED
    assert mention_service.view(session, job).steps, "the panel must still say what to do"


def test_an_operator_decision_survives_a_recomputation(ctx, session, connection, clip) -> None:
    """``PENDING_MANUAL`` and ``FAILED`` are decisions, not derivations.

    Reading the state back only from the caption text would erase them the moment the
    panel opened — "I have started this" and "this was removed" would both silently
    fall back to "confirmation required".
    """
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    for decided in (MentionState.PENDING_MANUAL, MentionState.FAILED):
        mention_service.begin_manual(session, job, now=BASE)
        if decided is MentionState.FAILED:
            mention_service.report_removed(session, job, now=BASE)
        session.commit()
        assert mention_service.current_state(job) is decided
        assert mention_service.view(session, job).state == decided.value


def test_the_view_never_reports_verified_without_an_attestation_on_record(
    ctx, session, connection, clip
) -> None:
    """Recomputed from stored evidence, not read from a cache that could go stale."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    # Someone set the column directly, as a bad migration or a manual edit might.
    job.content.mention_state = MentionState.VERIFIED.value
    session.commit()

    view = mention_service.view(session, job)

    assert view.state != MentionState.VERIFIED.value
    assert view.attestation_only is False, "no attestation on record, so no verified claim"
    assert not mention_is_verified(mention_service.current_state(job))


def test_the_view_says_verified_is_an_attestation_when_it_is(
    ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()

    view = mention_service.view(session, job)

    assert view.state == MentionState.VERIFIED.value
    assert view.attestation_only is True
    assert "attestation" in view.label.lower() or "operator" in view.label.lower()
    assert view.confirmed_at == BASE


def test_a_verified_mention_warns_that_a_rewrite_could_remove_it(
    ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()

    view = mention_service.view(session, job)

    assert view.rewrite_warning, "a verified mention must warn before any rewrite"
    assert "cannot" in view.rewrite_warning.lower()


def test_an_unrequested_mention_shows_no_rewrite_warning(ctx, session, connection, clip) -> None:
    job = make_job(session, make_media(session, clip), connection, publish_at=BASE)
    session.commit()
    view = mention_service.view(session, job)
    assert view.rewrite_warning is None
    assert view.actionable is False


def test_the_view_says_what_the_app_cannot_do(ctx, session, connection, clip) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    view = mention_service.view(session, job)
    assert view.prohibitions
    assert any("DF-19" in p for p in view.prohibitions)
    assert view.steps


def test_the_view_never_leaks_a_token(ctx, session, connection, clip) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    rendered = repr(mention_service.view(session, job))
    assert "access-token" not in rendered
    assert "refresh-token" not in rendered


def test_the_rewrite_risk_is_read_from_the_new_description(ctx, session, connection, clip) -> None:
    """The service derives the flag from the text, not from a caller's assertion."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()

    keeping = mention_service.rewrite_risk(
        job, description_changing=True, new_description="Thanks to @alice and more"
    )
    dropping = mention_service.rewrite_risk(
        job, description_changing=True, new_description="Thanks to nobody in particular"
    )
    unchanged = mention_service.rewrite_risk(
        job, description_changing=False, new_description="Thanks to nobody in particular"
    )

    assert keeping.requires_decision
    assert dropping.requires_decision
    assert not unchanged.requires_decision


# ==========================================================================
# The API surface
# ==========================================================================


@pytest.fixture
def client(ctx):
    from hexton.api.app import create_app

    app = create_app(ctx)
    with TestClient(app, base_url=f"http://{ctx.settings.host}:{ctx.settings.port}") as c:
        c.post("/api/session")
        yield c


@pytest.fixture
def raw(client) -> dict:
    """A CSRF header for a client that has *not* accepted the privacy policy.

    Separate from :func:`auth` because the gate tests need the closed gate, and a
    fixture that quietly opened it would make them pass for the wrong reason.
    """
    from hexton.api.security import CSRF_HEADER

    response = client.post("/api/session")
    assert response.status_code == 200
    return {CSRF_HEADER: response.json()["csrf_token"]}


@pytest.fixture
def auth(raw, client) -> dict:
    """A CSRF header, with the privacy policy accepted first — the ordinary case."""
    response = client.post(
        "/api/compliance/policy/accept", json={"accepted_by": "the operator"}, headers=raw
    )
    assert response.status_code == 200, response.text
    return raw


@pytest.fixture
def accepted(auth) -> dict:
    """Alias for :func:`auth`, named for the tests that need the ``COMP-01`` gate open."""
    return auth


def test_the_mention_route_exposes_the_workflow(client, ctx, session, connection, clip) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    body = client.get(f"/api/jobs/{job.id}/mention").json()

    assert body["requested"] is True
    assert body["handles"] == ["alice"]
    assert body["steps"], "the panel must tell the operator what to do"
    assert body["prohibitions"], "the panel must say what Hexton will not do"
    assert body["attestation_only"] is False
    assert any("DF-19" in p for p in body["prohibitions"])


def test_confirming_through_the_api_stores_the_attestation(
    client, accepted, ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.post(
        f"/api/jobs/{job.id}/mention/confirm",
        json={"attestation": "I picked Alice in the Studio mention picker."},
        headers=accepted,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "verified"
    assert body["attestation_only"] is True, "never present this as verified-by-API"
    assert "I picked Alice" in body["reason"]


def test_a_too_short_attestation_is_refused(
    client, accepted, ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.post(
        f"/api/jobs/{job.id}/mention/confirm", json={"attestation": "done"}, headers=accepted
    )

    assert response.status_code == 409
    assert "sentence" in response.json()["detail"]


def test_the_state_route_cannot_reach_verified(
    client, accepted, ctx, session, connection, clip
) -> None:
    """``VERIFIED`` is reachable only through ``/confirm``, which needs the words."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.post(
        f"/api/jobs/{job.id}/mention/state", json={"state": "verified"}, headers=accepted
    )

    assert response.status_code == 422, "the schema refuses VERIFIED outright"


def test_the_state_route_can_move_to_pending_manual(
    client, accepted, ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.content.mention_state = MentionState.MANUAL_REQUIRED.value
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.post(
        f"/api/jobs/{job.id}/mention/state", json={"state": "pending_manual"}, headers=accepted
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending_manual"
    assert response.json()["attestation_only"] is False


def test_the_begin_route_acknowledges_without_verifying(
    client, accepted, ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.content.mention_state = MentionState.MANUAL_REQUIRED.value
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.post(f"/api/jobs/{job.id}/mention/begin", headers=accepted)

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending_manual"
    assert response.json()["attestation_only"] is False
    assert response.json()["confirmed_at"] is None


def test_reporting_a_removal_through_the_api_clears_the_claim(
    client, accepted, ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    client.post(
        f"/api/jobs/{job.id}/mention/confirm",
        json={"attestation": "I added the mention in Studio."},
        headers=accepted,
    )

    response = client.post(f"/api/jobs/{job.id}/mention/removed", headers=accepted)

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert response.json()["attestation_only"] is False


def test_clearing_through_the_api_withdraws_the_attestation(
    client, accepted, ctx, session, connection, clip
) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    client.post(
        f"/api/jobs/{job.id}/mention/confirm",
        json={"attestation": "I added the mention in Studio."},
        headers=accepted,
    )

    response = client.post(f"/api/jobs/{job.id}/mention/clear", headers=accepted)

    assert response.status_code == 200
    assert response.json()["state"] != "verified"


def test_a_state_change_never_reaches_a_job_state_or_an_upload(
    client, accepted, ctx, session, connection, clip
) -> None:
    """A mention is metadata. It has no business moving a job or triggering work."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    job.video_id = "fake-0077"
    session.commit()
    before = (job.state, job.video_id, job.insert_may_have_succeeded)

    assert client.post(f"/api/jobs/{job.id}/mention/removed", headers=accepted).status_code == 200

    session.expire_all()
    after = session.get(ScheduledJob, job.id)
    assert (after.state, after.video_id, after.insert_may_have_succeeded) == before


def test_the_api_never_returns_a_token(client, auth, ctx, session, connection, clip) -> None:
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.get(f"/api/jobs/{job.id}/mention")
    assert "access-token" not in response.text
    assert "refresh-token" not in response.text


def test_a_missing_job_is_a_404_not_a_500(client) -> None:
    assert client.get("/api/jobs/999999/mention").status_code == 404


def test_the_panel_is_readable_before_the_policy_is_accepted(
    client, ctx, session, connection, clip
) -> None:
    """``COMP-01``, read side. Hiding the boundaries behind consent hides the reason.

    The panel is where an operator learns that Hexton cannot create a mention, what the
    attestation means, and what a rewrite risks. That has to be readable *before* they
    agree to anything, or the agreement is not informed.
    """
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()

    response = client.get(f"/api/jobs/{job.id}/mention")

    assert response.status_code == 200
    assert any("DF-19" in p for p in response.json()["prohibitions"])


def test_recording_a_mention_decision_is_gated_on_the_policy(
    client, raw, ctx, session, connection, clip
) -> None:
    """``COMP-01``, write side. A recorded decision about a Google account needs consent."""
    job = _job_with_mention(session, connection, clip)
    job.content.mention_state = MentionState.MANUAL_REQUIRED.value
    job.state = JobState.SCHEDULED.value
    session.commit()

    gated = [
        client.post(f"/api/jobs/{job.id}/mention/begin", headers=raw),
        client.post(
            f"/api/jobs/{job.id}/mention/confirm",
            json={"attestation": "I added the mention in Studio."},
            headers=raw,
        ),
        client.post(f"/api/jobs/{job.id}/mention/removed", headers=raw),
        client.post(f"/api/jobs/{job.id}/mention/clear", headers=raw),
        client.post(f"/api/jobs/{job.id}/mention/state", json={"state": "failed"}, headers=raw),
    ]

    for response in gated:
        assert response.status_code == 403, response.text
        assert "privacy policy" in response.json()["detail"].lower()

    # Nothing was recorded by a refused request.
    session.expire_all()
    assert job.content.mention_confirmed_at is None
    assert mention_service.current_state(job) is MentionState.MANUAL_REQUIRED


def test_the_confirm_route_is_the_attestation_route_and_nothing_else(client) -> None:
    """A shadowed route is how "attestation required" quietly stopped being required.

    ``routers/jobs`` is registered before ``routers/mentions``. When both declared
    ``/{job_id}/mention/confirm``, FastAPI matched the jobs copy first — which accepted
    an empty body, marked the mention VERIFIED, and returned a job rather than a mention
    view. Nothing in the request or the response made that visible. So this test checks
    the *schema* of the route that actually answers, not the text of the URL.
    """
    paths = client.get("/api/openapi.json").json()["paths"]
    confirm = paths["/api/jobs/{job_id}/mention/confirm"]["post"]
    clear = paths["/api/jobs/{job_id}/mention/clear"]["post"]

    assert "MentionOut" in confirm["responses"]["200"]["content"]["application/json"]["schema"].get(
        "$ref", ""
    )
    assert "MentionOut" in clear["responses"]["200"]["content"]["application/json"]["schema"].get(
        "$ref", ""
    )

    ref = confirm["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    body = client.get("/api/openapi.json").json()["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert "attestation" in body["properties"], (
        "the confirm route must require the operator's words; a body without them "
        "would let anyone mark a mention verified"
    )


def test_no_two_routers_declare_the_same_path() -> None:
    """Registration order must never decide which handler answers a request.

    OpenAPI collapses duplicate paths into one entry, so the published document cannot
    show the collision. This reads the routers directly, which can.
    """
    from hexton.api.routers import (
        compliance,
        connections,
        dashboard,
        events,
        jobs,
        media,
        mentions,
        reporting,
        system,
    )

    seen: dict[tuple[str, str], str] = {}
    for module in (
        system,
        dashboard,
        media,
        jobs,
        connections,
        events,
        reporting,
        compliance,
        mentions,
    ):
        for route in module.router.routes:
            for method in getattr(route, "methods", ()):
                key = (method, f"{module.router.prefix or ''}{route.path}")
                owner = f"{module.__name__}.{route.name}"
                assert key not in seen, (
                    f"{key} is declared by both {seen[key]} and {owner}; the first "
                    f"registered router silently shadows the second"
                )
                seen[key] = owner


# ==========================================================================
# Durability
# ==========================================================================


def test_the_confirmation_survives_a_session_reopen(ctx, session, connection, clip) -> None:
    """The attestation is durable evidence, not a UI flash."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=BASE
    )
    session.commit()
    job_id = job.id

    with ctx.sessions() as fresh_session:
        reopened = fresh_session.get(ScheduledJob, job_id)
        assert reopened.content.mention_state == MentionState.VERIFIED.value
        assert reopened.content.mention_confirmed_at == BASE
        assert "I added the mention in Studio" in reopened.content.mention_reason


def test_an_attestation_is_not_dated_in_the_future(ctx, session, connection, clip) -> None:
    """A sanity check on the clock path, because a future attestation reads as a bug."""
    job = _job_with_mention(session, connection, clip)
    job.state = JobState.SCHEDULED.value
    session.commit()
    now = datetime.now(UTC)
    mention_service.confirm_mention(
        session, job, attestation="I added the mention in Studio.", now=now
    )
    session.commit()
    assert job.content.mention_confirmed_at <= now + timedelta(seconds=1)
