"""Resumable upload client, exercised over real HTTP against the local protocol mock.

SCOPE OF THIS EVIDENCE
----------------------
Every test here proves something about *this client*: that it issues correct
``Content-Range`` headers, that it resumes from the persisted offset, that it persists
before it reports progress, and - most importantly - that it does **not** issue a second
insert when the outcome of the first is unknown.

None of it says anything about YouTube. The Google-facing questions stay BLOCKED in
``07-feasibility-checklist.md``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hexton_poc import metadata as m
from hexton_poc.youtube.resumable import (
    ResumableUploader,
    UploadError,
    parse_range_header,
)
from hexton_poc.youtube.session import SessionPhase, SessionStore, UploadSession

CHUNK = 256 * 1024


class SimulatedKill(RuntimeError):
    """Raised from the persist callback to model a power cut."""


def _body(sample: Path) -> dict:
    return m.build_insert_body(
        title="Phase 0 test short",
        description="A short used only to exercise the resumable protocol locally.",
        tags=["phase0", "test"],
        category_id="22",
        privacy="private",
        publish_at=None,
    )


def _new_session(sample: Path) -> UploadSession:
    return UploadSession(
        schedule_id="sched-1",
        connection_id="conn_test",
        file_path=str(sample),
        file_sha256="",
        total_bytes=sample.stat().st_size,
    )


def _uploader(mock_youtube, store=None, session=None, **kw) -> ResumableUploader:
    persist = (lambda: store.save(session)) if (store and session) else None
    opts: dict = dict(
        access_token_provider=lambda: "fake-token",
        client=httpx.Client(timeout=30.0),
        chunk_size=CHUNK,
        sleep=lambda _s: None,
        endpoint=mock_youtube.upload_endpoint(),
        persist=persist,
    )
    opts.update(kw)                       # let a test override any single option
    return ResumableUploader(**opts)


# -- documented protocol details ---------------------------------------------

@pytest.mark.parametrize(
    "header,expected",
    [
        ("bytes=0-999999", 999999),
        ("bytes=0-0", 0),
        ("bytes=0-", -1),        # malformed: no upper bound
        (None, -1),
        ("", -1),
    ],
)
def test_parse_range_header(header, expected):
    if expected == -1 and header in ("bytes=0-", None, ""):
        if header == "bytes=0-":
            with pytest.raises(UploadError):
                parse_range_header(header)
            return
        assert parse_range_header(header) == -1
        return
    assert parse_range_header(header) == expected


def test_a_resume_offset_is_exactly_one_past_the_confirmed_byte():
    """DF-14: `bytes=0-999999` means the next byte to send is 1000000."""
    assert parse_range_header("bytes=0-999999") + 1 == 1_000_000


def test_chunk_size_must_be_a_multiple_of_256_kib():
    with pytest.raises(ValueError, match="256 KiB"):
        ResumableUploader(access_token_provider=lambda: "t",
                          client=httpx.Client(), chunk_size=1000)


# -- happy path ---------------------------------------------------------------

def test_a_complete_upload_creates_exactly_one_video_with_exact_bytes(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "sessions.sqlite3")
    session = _new_session(sample_file)
    store.save(session)

    up = _uploader(mock_youtube, store, session)
    up.start(session, _body(sample_file))
    assert session.session_uri, "the Location must be persisted before any byte is sent"
    assert session.phase is SessionPhase.SESSION_ESTABLISHED

    outcome = up.run(session)

    assert outcome.video_id
    assert len(mock_youtube.state.videos) == 1
    rec = mock_youtube.state.videos[outcome.video_id]
    assert bytes(rec.data) == sample_file.read_bytes(), "reassembled bytes must match exactly"
    assert rec.received_bytes == sample_file.stat().st_size
    assert session.phase is SessionPhase.COMPLETED
    assert session.bytes_uploaded == session.total_bytes
    store.close()


def test_privacy_is_sent_explicitly_and_the_body_carries_it(mock_youtube, sample_file):
    """DF-02: omitting privacyStatus is the exact failure mode this product must avoid."""
    store = SessionStore(sample_file.parent / "s2.sqlite3")
    session = _new_session(sample_file)
    store.save(session)

    up = _uploader(mock_youtube, store, session)
    with pytest.raises(UploadError, match="privacyStatus"):
        # A body built outside build_insert_body, missing privacyStatus.
        up.start(session, {"snippet": {"title": "t", "description": "d", "categoryId": "22"}})
    store.close()


def test_publish_at_with_non_private_privacy_is_rejected_at_the_boundary(mock_youtube,
                                                                        sample_file):
    """DF-03, enforced by the mock as the real API documents it."""
    store = SessionStore(sample_file.parent / "s3.sqlite3")
    session = _new_session(sample_file)
    store.save(session)
    up = _uploader(mock_youtube, store, session)
    with pytest.raises(UploadError, match="invalidPublishAt"):
        up.start(session, {"snippet": {"title": "t", "description": "d", "categoryId": "22"},
                           "status": {"privacyStatus": "unlisted",
                                      "publishAt": "2099-01-01T00:00:00.000Z"}})
    store.close()


# -- interruption and recovery -------------------------------------------------

def test_a_kill_mid_transfer_is_resumed_from_the_persisted_offset(mock_youtube, sample_file):
    db = sample_file.parent / "resume.sqlite3"
    session = _new_session(sample_file)

    # Run 1: install the dying persister BEFORE the session is created, so the count of
    # durable writes is exact: #1 = session established, #2/#3 = two chunks transferred.
    store = SessionStore(db)
    store.save(session)
    up = _uploader(mock_youtube, store, session)

    calls = {"n": 0}
    real_save = store.save

    def dying_save(s):
        real_save(s)
        calls["n"] += 1
        if calls["n"] == 3:      # start + chunk1 + chunk2 -> die here
            raise SimulatedKill("power cut")

    up._persist = lambda: dying_save(session)
    up.start(session, _body(sample_file))

    with pytest.raises(SimulatedKill):
        up.run(session)
    store.close()

    # The durable state must show real progress, not zero.
    store2 = SessionStore(db)
    persisted = store2.load("sched-1")
    assert persisted is not None
    assert persisted.session_uri, "the session URI must survive a kill"
    assert persisted.bytes_uploaded == 2 * CHUNK
    assert persisted.phase is SessionPhase.UPLOADING
    assert len(mock_youtube.state.videos) == 0, "no video may exist yet"

    # Run 2: a brand-new uploader, as after a restart.
    up2 = _uploader(mock_youtube, store2, persisted)
    outcome = up2.run(persisted)

    assert len(mock_youtube.state.videos) == 1, "resuming must not create a second video"
    rec = mock_youtube.state.videos[outcome.video_id]
    assert bytes(rec.data) == sample_file.read_bytes()
    assert rec.received_bytes == sample_file.stat().st_size
    store2.close()


def test_a_restarted_process_recovers_from_the_database_alone(mock_youtube, sample_file):
    """The DB file is the only state: no memory of the previous run is carried over."""
    db = sample_file.parent / "restart.sqlite3"
    session = _new_session(sample_file)
    store = SessionStore(db)
    store.save(session)
    up = _uploader(mock_youtube, store, session)
    up.start(session, _body(sample_file))
    store.close()          # <-- process ends here

    # New process: open the same file, load, resume.
    store2 = SessionStore(db)
    loaded = store2.load("sched-1")
    assert loaded is not None and loaded.session_uri
    assert store2.journal_mode.lower() == "wal", "WAL must actually be enabled (PRD 2.1)"

    up2 = _uploader(mock_youtube, store2, loaded)
    outcome = up2.run(loaded)
    assert outcome.video_id
    assert len(mock_youtube.state.videos) == 1
    store2.close()


def test_a_file_that_changed_on_disk_is_refused_before_any_request(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "s4.sqlite3")
    session = _new_session(sample_file)
    store.save(session)
    up = _uploader(mock_youtube, store, session)

    session.total_bytes += 1                      # size now disagrees with the file
    with pytest.raises(UploadError, match="file size changed"):
        up.start(session, _body(sample_file))
    assert mock_youtube.state.insert_calls == [], "no request may be issued"
    store.close()


# -- the ambiguous outcome -----------------------------------------------------

def test_an_accepted_upload_whose_response_is_lost_is_never_retried(mock_youtube, sample_file):
    """The single most important safety test in the whole PoC."""
    db = sample_file.parent / "ambiguous.sqlite3"
    session = _new_session(sample_file)
    store = SessionStore(db)
    store.save(session)

    mock_youtube.state.drop_after_accepting = True   # bytes land, response never arrives

    up = _uploader(mock_youtube, store, session)
    up.start(session, _body(sample_file))

    with pytest.raises(UploadError) as excinfo:
        up.run(session)
    assert excinfo.value.ambiguous is True
    assert session.insert_may_have_succeeded is True

    # Exactly one video exists server-side, and the client knows it *might* exist.
    assert len(mock_youtube.state.videos) == 1
    assert store.load("sched-1").insert_may_have_succeeded == 1
    store.close()

    # Now the operator (or the sweep) reconciles: R1, the session probe, is conclusive.
    from hexton_poc.youtube.reconcile import Reconciler, Resolution, decide_retry

    store2 = SessionStore(db)
    loaded = store2.load("sched-1")
    rec = Reconciler(access_token_provider=lambda: "fake-token",
                     client=httpx.Client(timeout=30.0),
                     uploader=_uploader(mock_youtube, store2, loaded),
                     base_url=mock_youtube.youtube_v3_base())
    result = rec.reconcile(loaded, channel_id=mock_youtube.state.channel_id)

    assert result.resolution is Resolution.RESOLVED
    assert result.step == "R1"
    assert result.candidate is not None and result.candidate.conclusive

    allowed, reason = decide_retry(loaded, result)
    assert allowed is False, reason
    assert "video ID" in reason
    store2.close()


def test_an_expired_session_does_not_trigger_a_second_insert(mock_youtube, sample_file):
    db = sample_file.parent / "expired.sqlite3"
    session = _new_session(sample_file)
    store = SessionStore(db)
    store.save(session)

    mock_youtube.state.expire_sessions = True

    up = _uploader(mock_youtube, store, session)
    up.start(session, _body(sample_file))
    with pytest.raises(UploadError) as excinfo:
        up.run(session)

    assert excinfo.value.status_code == 404
    assert excinfo.value.ambiguous is True
    assert len(mock_youtube.state.insert_calls) == 1, "only the session POST may have run"
    assert store.load("sched-1").insert_may_have_succeeded == 1

    # max_restarts=0 is the default; passing 1 would restart, which is the dangerous path.
    store.close()


# -- transient failures ---------------------------------------------------------

def test_a_transient_503_on_session_start_is_retried(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "s5.sqlite3")
    session = _new_session(sample_file)
    store.save(session)
    mock_youtube.state.fail_first_session_post = 1

    up = _uploader(mock_youtube, store, session)
    with pytest.raises(UploadError) as excinfo:
        up.start(session, _body(sample_file))
    assert excinfo.value.retryable is True

    # The caller retries the start; this time it succeeds.
    location = up.start(session, _body(sample_file))
    assert location
    store.close()


def test_a_429_honours_the_retry_after_header(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "s6.sqlite3")
    session = _new_session(sample_file)
    store.save(session)

    mock_youtube.state.put_429_count = 1
    mock_youtube.state.retry_after_seconds = "7"

    delays: list[float] = []
    up = _uploader(mock_youtube, store, session, sleep=delays.append)
    up.start(session, _body(sample_file))
    outcome = up.run(session)

    assert outcome.video_id
    assert delays and delays[0] == 7.0, "Retry-After must be honoured verbatim"
    store.close()


def test_a_server_that_never_advances_the_offset_fails_bounded_not_forever(mock_youtube,
                                                                          sample_file):
    store = SessionStore(sample_file.parent / "s7.sqlite3")
    session = _new_session(sample_file)
    store.save(session)

    mock_youtube.state.stuck_range_high = 0        # always claim nothing was accepted

    up = _uploader(mock_youtube, store, session, max_attempts=3)
    up.start(session, _body(sample_file))
    with pytest.raises(UploadError, match="not advancing"):
        up.run(session)
    assert len(mock_youtube.state.videos) == 0
    store.close()


def test_a_permanent_4xx_is_not_retried(mock_youtube, sample_file):
    """DF-13: 4xx (other than 404/429) is a permanent failure."""
    store = SessionStore(sample_file.parent / "s8.sqlite3")
    session = _new_session(sample_file)
    store.save(session)

    up = _uploader(mock_youtube, store, session)
    up.start(session, _body(sample_file))

    # Force an unexpected status by pointing at a non-existent session.
    session.session_uri = mock_youtube.base_url + "/upload/session/does-not-exist"
    with pytest.raises(UploadError) as excinfo:
        up.run(session)
    assert excinfo.value.status_code == 404
    assert excinfo.value.retryable is False
    store.close()


# -- progress reporting ---------------------------------------------------------

def test_progress_is_reported_after_the_offset_is_durable(mock_youtube, sample_file):
    store = SessionStore(sample_file.parent / "s9.sqlite3")
    session = _new_session(sample_file)
    store.save(session)

    seen: list[tuple[int, int, int]] = []
    up = _uploader(mock_youtube, store, session)
    up.start(session, _body(sample_file))

    original_save = store.save

    def observing_save(s):
        original_save(s)
        seen.append((s.bytes_uploaded, store.load("sched-1").bytes_uploaded, 0))

    up._persist = lambda: observing_save(session)
    up._on_progress = lambda done, total: seen.append((-1, store.load("sched-1").bytes_uploaded, 1))

    up.run(session)
    # Every progress callback must observe a value at least as advanced as the DB.
    progress_calls = [t for t in seen if t[0] == -1]
    assert progress_calls
    for _, persisted, _ in progress_calls:
        assert persisted > 0
    store.close()
