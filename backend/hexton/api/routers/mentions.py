"""The creator-mention workflow, exposed.

``04-feature-prd.md`` §10 FEAT-008, ``DF-19``, ``FEAS-06``, ``AC-MVP-009``.

``DF-19`` is the constraint the whole router is shaped by: the ``videos`` resource has no
mention entity, so there is no API call that creates one. Everything here is therefore
about *helping a human* and *keeping an honest ledger* — never about performing the
action. The router contains no HTTP client, no browser automation, and no reference to
anything that could reach a private endpoint.

The one thing it will not do is let a mention become "verified" without an operator
attestation. That is enforced in :mod:`hexton.services.mentions`, in one function, and
this router has no way around it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from hexton.api.deps import SessionDep
from hexton.api.schemas_phase3 import (
    MentionConfirmIn,
    MentionOut,
    MentionStateIn,
    MentionWorkflowStep,
)
from hexton.db.models import ScheduledJob
from hexton.domain.jobstate import MentionState
from hexton.domain.mention import mention_is_verified
from hexton.services import mentions as mention_service

router = APIRouter(prefix="/api/jobs", tags=["mentions"])


#: ``COMP-01``. The same gate the reporting router uses, applied here for the same reason:
#: these routes record a decision about a Google account, and the operator must have read
#: and accepted the policy before Hexton starts keeping that record. It is applied in one
#: place so a new mutating route cannot forget it.
#:
#: ``GET /{job_id}/mention`` is deliberately exempt. Reading the panel is how an operator
#: finds out what Hexton will and will not do — including the ``DF-19`` boundary and the
#: policy links — and hiding that behind a consent they have not yet given would hide the
#: thing they need in order to give it knowingly.
def _require_policy(session: Session) -> None:
    from hexton.compliance.policy import (
        Feature,
        gate,
    )
    from hexton.db.models import PolicyAcceptanceRecord

    record = session.scalars(
        select(PolicyAcceptanceRecord)
        .where(PolicyAcceptanceRecord.superseded_at.is_(None))
        .order_by(PolicyAcceptanceRecord.accepted_at.desc())
        .limit(1)
    ).one_or_none()
    acceptance = None
    if record is not None and record.accepted_at is not None:
        from hexton.compliance.policy import PolicyAcceptance

        acceptance = PolicyAcceptance(
            version=record.version,
            accepted_at=record.accepted_at,
            accepted_by=record.accepted_by,
        )
    result = gate(acceptance, Feature.MENTION_WORKFLOW)
    if not result.allowed:
        raise HTTPException(status_code=403, detail=result.reason)


def _load(session: Session, job_id: int) -> ScheduledJob:
    job = session.get(ScheduledJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} does not exist.")
    return job


def _out(view: mention_service.MentionView) -> MentionOut:
    return MentionOut(
        job_id=view.job_id,
        state=view.state,
        label=view.label,
        requested=view.requested,
        handles=list(view.handles),
        intended_creator=view.intended_creator,
        attestation_only=view.attestation_only,
        confirmed_at=view.confirmed_at,
        reason=view.reason,
        steps=[
            MentionWorkflowStep(index=index, instruction=step)
            for index, step in enumerate(view.steps, start=1)
        ],
        prohibitions=list(view.prohibitions),
        rewrite_warning=view.rewrite_warning,
    )


@router.get("/{job_id}/mention", response_model=MentionOut)
def get_mention(job_id: int, session: Session = SessionDep) -> MentionOut:
    """The workflow panel: intended creator, the manual steps, and the honest label."""
    return _out(mention_service.view(session, _load(session, job_id)))


@router.post("/{job_id}/mention/begin", response_model=MentionOut)
def begin(job_id: int, session: Session = SessionDep) -> MentionOut:
    """The operator acknowledged the workflow and is doing it in Studio now."""
    job = _load(session, job_id)
    _require_policy(session)
    try:
        mention_service.begin_manual(session, job, now=datetime.now(UTC))
    except mention_service.MentionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _out(mention_service.view(session, job))


@router.post("/{job_id}/mention/confirm", response_model=MentionOut)
def confirm(job_id: int, payload: MentionConfirmIn, session: Session = SessionDep) -> MentionOut:
    """Record a human attestation. The only route to a verified mention, anywhere.

    ``DF-19``: the API cannot create or read back a mention, so the operator's own words
    are the evidence. They are stored verbatim, and the response always carries
    ``attestation_only: true`` so no UI can present this as an API verification.
    """
    job = _load(session, job_id)
    _require_policy(session)
    try:
        mention_service.confirm_mention(
            session, job, attestation=payload.attestation, now=datetime.now(UTC)
        )
    except mention_service.MentionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _out(mention_service.view(session, job))


@router.post("/{job_id}/mention/removed", response_model=MentionOut)
def removed(job_id: int, session: Session = SessionDep) -> MentionOut:
    """The operator reports the mention was removed or never landed."""
    job = _load(session, job_id)
    _require_policy(session)
    try:
        mention_service.report_removed(session, job, now=datetime.now(UTC))
    except mention_service.MentionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _out(mention_service.view(session, job))


@router.post("/{job_id}/mention/clear", response_model=MentionOut)
def clear(job_id: int, session: Session = SessionDep) -> MentionOut:
    """Withdraw an attestation, e.g. after editing the caption."""
    job = _load(session, job_id)
    _require_policy(session)
    mention_service.clear(session, job)
    session.commit()
    return _out(mention_service.view(session, job))


@router.post("/{job_id}/mention/state", response_model=MentionOut)
def set_state(job_id: int, payload: MentionStateIn, session: Session = SessionDep) -> MentionOut:
    """Move a mention to a state that does **not** require claiming verification.

    ``VERIFIED`` is deliberately absent from the accepted values: it is reachable only
    through :func:`confirm`, which requires the attestation sentence. Allowing it here
    would create a second route to the one claim the whole design exists to protect.
    """
    job = _load(session, job_id)
    _require_policy(session)
    content = job.content
    if content is None or not content.mention_requested:
        raise HTTPException(status_code=409, detail="This job does not request a mention.")
    target = MentionState(payload.state)
    if mention_is_verified(target):
        raise HTTPException(  # pragma: no cover - the schema already refuses it
            status_code=409,
            detail="A mention can only become verified by an operator attestation.",
        )
    content.mention_state = target.value
    content.mention_confirmed_at = None
    content.mention_reason = {
        MentionState.MANUAL_REQUIRED: (
            "A mention is required and has not been confirmed in YouTube Studio."
        ),
        MentionState.PENDING_MANUAL: (
            "The operator has acknowledged the manual mention workflow and is completing "
            "it in YouTube Studio. Nothing has been verified."
        ),
        MentionState.FAILED: (
            "The operator reported that this mention was removed or did not take effect."
        ),
    }[target]
    session.commit()
    return _out(mention_service.view(session, job))


__all__ = ["router"]
