# Verification Report: Scheduling, Time Zones, and Publication Confirmation

**Document:** `docs/verification/scheduling-and-publication.md`  
**Playbook Phase:** Prompt 7 — Scheduling, Time Zones, and Publication Confirmation  
**Date:** 2026-10-13  
**Status:** PASS  
**Test Suite:** `backend/tests/test_scheduling_and_publication.py` (8/8 passed), `backend/tests/test_sweep.py` (22/22 passed)

---

## 1. Executive Summary

This phase verified and regression-tested the scheduling engine, time-zone conversion mechanics on Windows, YouTube API `publishAt` compliance invariants, and the `PublishConfirmationSweep` lifecycle.

Crucially, **the arrival or passing of scheduled publication time is never treated as evidence of publication**. In accordance with PRD-03 §7.2 and PRD-05 §C-21, a job transitions to `JOB-PUBLISHED` **only** upon observing `privacyStatus: public` via remote poll against the YouTube Data API v3 (`videos.list(id=..., part=status)`).

All 8 comprehensive scheduling tests in `backend/tests/test_scheduling_and_publication.py` pass without regression.

---

## 2. Test Matrix and Verified Behaviors

| Test Identifier | Verified Condition / Rule | Result |
| :--- | :--- | :--- |
| `test_iana_timezones_resolve_via_tzdata` | Windows environment cleanly resolves IANA canonical timezones (`Asia/Dhaka`, `America/New_York`, `UTC`) using Python `zoneinfo` backed by the `tzdata` package. Invalid zones raise `ZoneInfoNotFoundError`. | **PASS** |
| `test_midnight_boundaries_across_timezones` | Local wall-clock midnight (00:00:00) conversions to UTC across disparate offsets (`Asia/Dhaka` UTC+6, `America/New_York` UTC-5/UTC-4) correctly preserve monotonic absolute timestamps without day shifts or truncation. | **PASS** |
| `test_dst_transitions_gap_and_fold` | Spring-forward gap (02:30 AM non-existent time) and Fall-back fold (01:30 AM ambiguous time with `fold=0` vs `fold=1`) resolve deterministically without exceptions or silent corruptions. | **PASS** |
| `test_publish_at_requires_private_visibility_and_future_instant` | YouTube Data API contract enforced: `publishAt` is strictly rejected if `privacyStatus != "private"`, or if timestamp is in the past. Naive datetimes without timezones are rejected. | **PASS** |
| `test_rescheduling_already_published_video_is_strictly_forbidden` | Invariant enforcement: a video that was previously published or reached public status can never have `publishAt` set again; update requests raise explicit domain rejection. | **PASS** |
| `test_publication_time_passing_does_not_mark_published` | **Core Invariant:** Wall-clock time passing beyond `publish_at_utc` leaves the job in `JOB-SCHEDULED` with `poll_result=NOT_YET`. UI displays `Awaiting Verification`. | **PASS** |
| `test_publication_confirmed_only_when_remote_reports_public` | Only when `videos.list` returns `privacyStatus: public` does the job transition to `JOB-PUBLISHED`. Every check appends a timestamped `RemoteObservation` row. | **PASS** |
| `test_sweep_recovers_pending_verification_after_process_restart` | Worker restart after machine sleep or process crash catches up on unconfirmed jobs, querying YouTube and confirming publication without dropping jobs. | **PASS** |

---

## 3. Detailed Audit Findings

### 3.1 Windows IANA Timezone Handling
On Windows, native C-runtime timezone databases do not bundle standard POSIX/IANA identifiers (`Asia/Dhaka`, etc.). Hexton explicitly depends on `tzdata>=2024.1` in `backend/pyproject.toml`.
- Tested: `ZoneInfo("Asia/Dhaka")` correctly maps to UTC+06:00.
- Tested: `ZoneInfo("America/New_York")` accurately toggles between EST (UTC-05:00) and EDT (UTC-04:00).
- Handled: User input of invalid time zones cleanly triggers a 400 validation error rather than crashing background worker processes.

### 3.2 Past Timestamp Rejection
Per PRD-03 §7.1 and YouTube API specification:
- Setting a `publishAt` instant in the past (e.g. `now - 1 second`) raises `PreflightError(E_INVALID_METADATA)` or `PublishAtInPastError`.
- Naive datetimes (lacking `tzinfo`) are rejected unconditionally before reaching network callers.
- Only valid future UTC ISO-8601 strings (`YYYY-MM-DDTHH:MM:SS.sssZ` or `+00:00`) are submitted in YouTube API request bodies.

### 3.3 YouTube Reschedule Guard
- Videos once set to `public` cannot be rescheduled back to scheduled or private with future `publishAt`.
- The system enforces `job.state != JobState.PUBLISHED` before any schedule update mutation is accepted.

### 3.4 Publication Confirmation Sweep & Remote Observations
- The `PublishConfirmationSweep` operates on scheduled jobs whose `schedule.publish_at_utc <= now`.
- Quota optimization: Each check performs a targeted `videos.list(id=..., part="status")` consuming only 1 quota unit.
- If YouTube reports `privacyStatus: "private"`, the job remains in `JobState.SCHEDULED`, `poll_result="not_yet"`, and schedules next poll with exponential backoff (`poll_delay`).
- If YouTube reports `privacyStatus: "public"`, the job transitions to `JobState.PUBLISHED`, records `published_at_remote = now`, logs event `KIND_PUBLISHED`, and enqueues the Google Sheets outbox payload.
- Every poll writes a durable record to `remote_observations` table including `job_id`, `video_id`, `observed_at`, `privacy_status`, `upload_status`, `rejection_reason`.

---

## 4. Exit Gate Evaluation

| Exit Gate Requirement | Status | Verification Evidence |
| :--- | :--- | :--- |
| **No past-time request can be sent** | **SATISFIED** | Preflight validation and `test_publish_at_requires_private_visibility_and_future_instant` block any past or naive timestamp. |
| **Local state distinguishes states clearly** | **SATISFIED** | `JobState` explicitly distinguishes: `QUEUED`, `PROCESSING`, `FAILED`, `SCHEDULED` (upload complete, waiting on YouTube), `PUBLISHED` (remote public verified), and `NEEDS_MANUAL_REVIEW`. |
| **Time passing != Published** | **SATISFIED** | Verified by `test_publication_time_passing_does_not_mark_published`. |
| **Restart recovery verified** | **SATISFIED** | Verified by `test_sweep_recovers_pending_verification_after_process_restart`. |

**Phase Status:** Prompt 7 Complete. Proceeding to Prompt 8.
