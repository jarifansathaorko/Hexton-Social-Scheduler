"""The reconciliation ladder (R1 -> R4) and the retry gate.

The invariant under test everywhere below: **a second `videos.insert` is never issued for
a job whose previous insert may have succeeded.** Every path that could lead there must
end at `decide_retry() -> False`.
"""

from __future__ import annotations

import httpx
import pytest

from hexton_poc.youtube.reconcile import (
    Reconciler,
    Resolution,
    decide_retry,
    verify_video_id,
)
from hexton_poc.youtube.resumable import ResumableUploader, UploadError
from hexton_poc.youtube.session import SessionPhase, SessionStore, UploadSession

CHUNK = 256 * 1024


def _session(sample, *, uri: str = "") -> UploadSession:
    return UploadSession(schedule_id="sched-1", connection_id="conn",
                         file_path=str(sample), file_sha256="",
                         total_bytes=sample.stat().st_size,
                         session_uri=uri,
                         phase=SessionPhase.SESSION_ESTABLISHED if uri
                         else SessionPhase.CREATED)


def _reconciler(mock_youtube, store=None, session=None) -> Reconciler:
    uploader = None
    if store is not None and session is not None:
        uploader = ResumableUploader(
            access_token_provider=lambda: "fake-token",
            client=httpx.Client(timeout=30.0),
            chunk_size=CHUNK,
            sleep=lambda _s: None,
            endpoint=mock_youtube.upload_endpoint(),
            persist=lambda: store.save(session),
        )
    return Reconciler(access_token_provider=lambda: "fake-token",
                      client=httpx.Client(timeout=30.0),
                      uploader=uploader,
                      base_url=mock_youtube.youtube_v3_base())


# -- R1 ------------------------------------------------------------------------

def test_r1_no_session_at_all_means_no_insert_can_have_been_accepted(mock_youtube, sample_file):
    session = _session(sample_file)          # never even started
    result = _reconciler(mock_youtube).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    assert "R1" in result.steps_run
    assert any("no session URI was persisted" in n for n in result.notes)


def test_r1_a_live_session_is_resumed_not_reinserted(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "r1.sqlite3")
    session = _session(sample_file)
    store.save(session)

    up = ResumableUploader(access_token_provider=lambda: "fake-token",
                           client=httpx.Client(timeout=30.0), chunk_size=CHUNK,
                           sleep=lambda _s: None,
                           endpoint=mock_youtube.upload_endpoint(),
                           persist=lambda: store.save(session))
    up.start(session, {"snippet": {"title": "t", "description": "d", "categoryId": "22"},
                       "status": {"privacyStatus": "private"}})
    # Put one chunk on the server, then stop.
    with open(sample_file, "rb") as fh:
        payload = fh.read(CHUNK)
    httpx.Client(timeout=30.0).put(
        session.session_uri,
        content=payload,
        headers={"Content-Length": str(len(payload)),
                 "Content-Type": "video/*",
                 "Content-Range": f"bytes 0-{len(payload) - 1}/{session.total_bytes}"},
    )
    store.save(session)

    result = _reconciler(mock_youtube, store, session).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    assert result.resolution is Resolution.INCONCLUSIVE_RETRY_LATER
    assert result.step == "R1"
    assert "Resume rather than restart" in " ".join(result.notes) or "do not insert" in \
        " ".join(result.notes)
    store.close()


def test_r1_expired_session_falls_through_to_r2_and_r3(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "r2.sqlite3")
    session = _session(sample_file, uri="http://127.0.0.1:1/upload/session/dead")
    store.save(session)
    mock_youtube.state.expire_sessions = True

    result = _reconciler(mock_youtube, store, session).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    assert result.steps_run == ["R1", "R2", "R3", "R4"]
    assert result.resolution is Resolution.NEEDS_MANUAL_REVIEW
    assert result.candidate is None
    store.close()


# -- R2 / R3 -------------------------------------------------------------------

def test_r2_a_candidate_is_evidence_not_proof_and_is_never_auto_adopted(mock_youtube,
                                                                       sample_file):
    store = SessionStore(sample_file.parent / "r3.sqlite3")
    session = _session(sample_file, uri="http://127.0.0.1:1/upload/session/dead")
    store.save(session)
    mock_youtube.state.expire_sessions = True
    mock_youtube.state.activities = [{
        "snippet": {"publishedAt": "2026-10-11T10:00:00Z", "title": "Looks like ours"},
        "contentDetails": {"upload": {"videoId": "vidR2"}},
    }]

    result = _reconciler(mock_youtube, store, session).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    assert result.step == "R2"
    assert result.resolution is Resolution.NEEDS_MANUAL_REVIEW
    assert result.candidate is not None
    assert result.candidate.video_id == "vidR2"
    assert result.candidate.conclusive is False, "R2 must never be conclusive"

    allowed, reason = decide_retry(session, result)
    assert allowed is False
    assert "second video" in reason or "duplicate" in reason
    store.close()


def test_r3_falls_back_to_the_uploads_playlist_when_the_activity_feed_is_empty(mock_youtube,
                                                                              sample_file):
    store = SessionStore(sample_file.parent / "r4.sqlite3")
    session = _session(sample_file, uri="http://127.0.0.1:1/upload/session/dead")
    store.save(session)
    mock_youtube.state.expire_sessions = True
    mock_youtube.state.activities = []
    mock_youtube.state.uploads_playlist = [{
        "snippet": {"resourceId": {"videoId": "vidR3"}, "title": "Recent upload",
                    "publishedAt": "2026-10-11T10:00:00Z"},
    }]

    result = _reconciler(mock_youtube, store, session).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    assert result.steps_run == ["R1", "R2", "R3"]
    assert result.step == "R3"
    assert result.resolution is Resolution.NEEDS_MANUAL_REVIEW
    assert result.candidate.video_id == "vidR3"
    assert result.candidate.source == "uploads_playlist"
    store.close()


def test_r4_is_reached_when_no_step_yields_evidence(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "r5.sqlite3")
    session = _session(sample_file, uri="http://127.0.0.1:1/upload/session/dead")
    store.save(session)
    mock_youtube.state.expire_sessions = True
    mock_youtube.state.activities = []
    mock_youtube.state.uploads_playlist = []

    result = _reconciler(mock_youtube, store, session).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    assert result.step == "R4"
    assert result.resolution is Resolution.NEEDS_MANUAL_REVIEW
    assert "no second insert is issued" in " ".join(result.notes)
    store.close()


# -- decide_retry ----------------------------------------------------------------

def test_retry_is_allowed_only_when_no_session_and_no_candidate(mock_youtube, sample_file):
    session = _session(sample_file)   # no session URI: the POST never succeeded
    result = _reconciler(mock_youtube).reconcile(
        session, channel_id=mock_youtube.state.channel_id)
    allowed, reason = decide_retry(session, result)
    assert allowed is True, reason
    assert "never accepted" in reason or "no insert" in reason


def test_retry_is_refused_once_a_session_exists(mock_youtube, sample_file):
    from hexton_poc.youtube.reconcile import ReconciliationResult

    session = _session(sample_file, uri="http://127.0.0.1:1/upload/session/x")
    allowed, reason = decide_retry(session, ReconciliationResult(
        Resolution.NEEDS_MANUAL_REVIEW, "R4"))
    assert allowed is False
    assert "Resuming it cannot create a second video" in reason


def test_retry_is_refused_once_a_video_id_is_known(mock_youtube, sample_file):
    from hexton_poc.youtube.reconcile import ReconciliationResult

    session = _session(sample_file, uri="http://127.0.0.1:1/upload/session/x")
    session.video_id = "already-found"
    allowed, reason = decide_retry(session, ReconciliationResult(Resolution.RESOLVED, "R1"))
    assert allowed is False
    assert "already-found" in reason


# -- verify_video_id -------------------------------------------------------------

def test_a_recovered_id_is_verified_before_it_is_adopted(mock_youtube):
    mock_youtube.state.videos = {}
    client = httpx.Client(timeout=10.0)
    assert verify_video_id("t", client, "nope",
                           base_url=mock_youtube.youtube_v3_base()) is None
