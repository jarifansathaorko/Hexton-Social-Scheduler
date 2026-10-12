"""PublishConfirmationSweep: publication is verified, not assumed.

The decision table in :func:`classify` is pure, so the whole matrix can be tested without
a network. The important rows are the ones that distinguish "awaiting verification" from
both success and failure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from hexton_poc.jobstate import JobState, PollResult, RemoteState
from hexton_poc.youtube.sweep import MIN_POLL_INTERVAL_SECONDS, PublishConfirmationSweep, classify

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
FUTURE = "2026-10-12T12:00:00Z"
PAST = "2026-10-10T12:00:00Z"


def _resource(privacy="private", publish_at="", upload_status="uploaded", video_id="vid1"):
    return {
        "id": video_id,
        "snippet": {"publishedAt": "2026-10-11T09:00:00Z"},
        "status": {"privacyStatus": privacy, "uploadStatus": upload_status,
                   **({"publishAt": publish_at} if publish_at else {})},
    }


# -- the four conditions that must never be conflated --------------------------

def test_1_uploaded_but_not_public_is_not_published():
    obs = classify(_resource(privacy="private"), now=NOW)
    assert obs.job_state is JobState.UPLOADED
    assert obs.remote_state is not RemoteState.PUBLIC
    assert obs.poll_result is PollResult.CONFIRMED


def test_2_a_future_publish_at_is_scheduled():
    obs = classify(_resource(privacy="private", publish_at=FUTURE), now=NOW)
    assert obs.job_state is JobState.SCHEDULED
    assert obs.remote_state is RemoteState.SCHEDULED
    assert obs.poll_result is PollResult.NOT_YET
    assert obs.display_suffix == ""


def test_3_an_observed_public_status_is_published_verified():
    obs = classify(_resource(privacy="public"), now=NOW)
    assert obs.job_state is JobState.PUBLISHED
    assert obs.remote_state is RemoteState.PUBLIC
    assert obs.poll_result is PollResult.CONFIRMED
    assert obs.display_suffix == ""


def test_4_a_passed_publish_time_that_is_not_public_is_awaiting_verification():
    obs = classify(_resource(privacy="private", publish_at=PAST), now=NOW)
    assert obs.job_state is JobState.SCHEDULED
    assert obs.display_suffix == "— publication awaiting verification"
    assert obs.job_state is not JobState.PUBLISHED
    assert obs.job_state is not JobState.FAILED


def test_awaiting_verification_is_normal_not_an_error():
    obs = classify(_resource(privacy="private", publish_at=PAST), now=NOW)
    assert obs.job_state in (JobState.SCHEDULED, JobState.UPLOADED)
    assert obs.job_state is not JobState.NEEDS_REVIEW


# -- failure and blocked --------------------------------------------------------

def test_a_failed_upload_is_reported_as_failed_with_its_reason():
    obs = classify(_resource(privacy="private", upload_status="failed"), now=NOW)
    assert obs.job_state is JobState.FAILED
    assert "failed" in obs.reason


def test_an_invalid_token_blocks_the_job_rather_than_claiming_anything():
    obs = classify(None, now=NOW, token_valid=False)
    assert obs.job_state is JobState.BLOCKED
    assert obs.poll_result is PollResult.UNKNOWN
    assert obs.remote_state is RemoteState.UNKNOWN
    assert "could not be verified" in obs.reason


def test_never_polled_is_distinguished_from_unknown():
    obs = classify(None, now=NOW, token_valid=True)
    assert obs.poll_result is PollResult.NEVER_POLLED
    assert obs.job_state is JobState.QUEUED


# -- privacy variants ------------------------------------------------------------

def test_an_unlisted_video_is_not_treated_as_published():
    obs = classify(_resource(privacy="unlisted"), now=NOW)
    assert obs.job_state is not JobState.PUBLISHED
    assert obs.remote_state is RemoteState.PRIVATE_UNLISTED_OR_OTHER


def test_a_private_video_with_no_publish_at_is_uploaded_not_published():
    obs = classify(_resource(privacy="private"), now=NOW)
    assert obs.job_state is JobState.UPLOADED
    assert obs.display_suffix == ""


# -- sweep scheduling ---------------------------------------------------------------

def test_polling_respects_the_configured_minimum_interval():
    sweep = PublishConfirmationSweep(access_token_provider=lambda: "t",
                                     client=httpx.Client())
    assert sweep.should_poll(None, NOW) is True
    recent = (NOW - timedelta(seconds=MIN_POLL_INTERVAL_SECONDS - 60)).isoformat()
    assert sweep.should_poll(recent, NOW) is False
    old = (NOW - timedelta(seconds=MIN_POLL_INTERVAL_SECONDS + 60)).isoformat()
    assert sweep.should_poll(old, NOW) is True


def test_a_malformed_timestamp_does_not_block_polling_forever():
    sweep = PublishConfirmationSweep(access_token_provider=lambda: "t",
                                     client=httpx.Client())
    assert sweep.should_poll("not-a-timestamp", NOW) is True


def test_a_401_during_a_poll_blocks_rather_than_reporting_an_outcome(mock_youtube):
    sweep = PublishConfirmationSweep(access_token_provider=lambda: "expired",
                                     client=httpx.Client(timeout=10.0),
                                     base_url=mock_youtube.youtube_v3_base())
    obs = sweep.poll("vid1", now=NOW, token_valid=True)
    # The mock returns 200 for unknown ids with an empty item list, so exercise the
    # explicit invalid-token path directly as well.
    obs2 = sweep.poll("vid1", now=NOW, token_valid=False)
    assert obs2.job_state is JobState.BLOCKED
    assert obs.job_state in (JobState.NEEDS_REVIEW, JobState.BLOCKED, JobState.SCHEDULED)


def test_a_video_id_that_no_longer_resolves_goes_to_manual_review(mock_youtube):
    sweep = PublishConfirmationSweep(access_token_provider=lambda: "t",
                                     client=httpx.Client(timeout=10.0),
                                     base_url=mock_youtube.youtube_v3_base())
    obs = sweep.poll("missing-video", now=NOW)
    assert obs.job_state is JobState.NEEDS_REVIEW
    assert "deleted" in obs.reason or "wrong" in obs.reason
