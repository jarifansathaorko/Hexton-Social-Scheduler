# Defect-Fix Log and Regression Discipline

**Document:** `docs/verification/bug-fix-log.md`  
**Playbook Phase:** Prompt 12 — Defect-Fix Loop and Regression Discipline  
**Date:** 2026-10-13  
**Status:** PASS — Zero Failing Tests (636 Backend + 21 E2E = 657 Total Passed)

---

## 1. Overview & Discipline Rules

In accordance with Prompt 12 of the Release Playbook:
1. No broad rewrites were performed; fixes were targeted and minimal.
2. Every bug was reproduced, root cause identified, regression test authored/executed, and verified.
3. No failing tests were deleted or assertions weakened.
4. Blocked or non-permitted API actions (such as out-of-scope video deletion or unelevated Task Scheduler logon registration) are explicitly documented rather than falsely marked as passing.

---

## 2. Defect Resolution Log

### Defect 01: Keyword Argument Mismatch in Channel Discovery
- **Severity:** High (Crash in OAuth Connection Workflow)
- **Discovered In:** Prompt 3 baseline audit & live testing.
- **Symptom:** Calling `/api/connections/youtube/callback` or channel sync resulted in `TypeError: YouTubeConnector.discover_channels() got an unexpected keyword argument 'token'`.
- **Root Cause:** In [`backend/hexton/api/routers/connections.py`](file:///e:/project/Hexton%20Scheduler/backend/hexton/api/routers/connections.py), `discover_channels(token=access_token)` was called with keyword `token=`, whereas `YouTubeConnector.discover_channels(access_token: str)` expects `access_token`.
- **Files Changed:** [`backend/hexton/api/routers/connections.py`](file:///e:/project/Hexton%20Scheduler/backend/hexton/api/routers/connections.py)
- **Regression Coverage:** [`backend/tests/test_oauth_and_channels.py::test_channel_discovery_accepts_access_token_kwarg`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_oauth_and_channels.py)
- **Result:** **RESOLVED**. Live test verified on authorized channel `UCAuriTG-GF_0oHSwzAILPsw` ("The Clip Theory HQ") returning HTTP 200.

---

### Defect 02: Mock Property Mismatch in Scheduling Sweep Fixtures
- **Severity:** Medium (Test Suite Fixture Defect)
- **Discovered In:** Prompt 7 test authoring.
- **Symptom:** `test_publication_confirmed_only_when_remote_reports_public` and restart sweep tests failed with `AttributeError` setting `ctx.connector.mock_video`.
- **Root Cause:** `FakeConnector` maintains internal video dictionary at `self.state.videos`, which `get_video()` consults.
- **Files Changed:** [`backend/tests/test_scheduling_and_publication.py`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_scheduling_and_publication.py)
- **Regression Coverage:** [`backend/tests/test_scheduling_and_publication.py`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_scheduling_and_publication.py) (8/8 passed).
- **Result:** **RESOLVED**.

---

### Defect 03: Task Scheduler Logon Registration Requires Elevation on Windows 11
- **Severity:** High (Autostart Architecture Constraint)
- **Discovered In:** Prompt 8 host probe on Windows 11 Build 26200 (`FEAS-08`).
- **Symptom:** Unelevated PowerShell execution of `Register-ScheduledTask -AtLogOn` returned `Access is denied.`
- **Root Cause:** Windows 11 security policies mandate Administrator elevation to register logon-triggered tasks in Task Scheduler. Furthermore, running as `SYSTEM` or a Windows Service breaks DPAPI and Windows Credential Manager because they are cryptographically bound to the interactive user's SID.
- **Resolution & Mitigation:** Documented honest status in [`docs/verification/windows-worker.md`](file:///e:/project/Hexton%20Scheduler/docs/verification/windows-worker.md). Tested and proved non-elevated user-session autostart via `HKCU:\Software\Microsoft\Windows\CurrentVersion\Run` and Startup folder, both of which execute in the exact interactive user session without elevation.
- **Regression Coverage:** [`backend/tests/test_windows_worker_lifecycle.py::test_interactive_user_context_matches_credential_store_requirements`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_windows_worker_lifecycle.py).
- **Result:** **RESOLVED (Documented Constraint)**.

---

### Defect 04: Google Sheets Audit Header Alignment & Failure Type Checking
- **Severity:** Low (Test Harness & Audit Schema Validation)
- **Discovered In:** Prompt 9 audit tests.
- **Symptom:** `test_report_row_contains_all_mandatory_audit_fields` asserted on shorthand column names (`Title`) instead of frozen schema header (`Video Title`). `test_sheets_failure_cannot_trigger_video_upload` evaluated `result.failures >= 1` against a list.
- **Root Cause:** Header naming discrepancy against `backend/hexton/connectors/sheets.py` canonical constants.
- **Files Changed:** [`backend/tests/test_sheets_idempotency_audit.py`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_sheets_idempotency_audit.py)
- **Regression Coverage:** [`backend/tests/test_sheets_idempotency_audit.py`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_sheets_idempotency_audit.py) (6/6 passed), [`backend/tests/test_sheets_reporting.py`](file:///e:/project/Hexton%20Scheduler/backend/tests/test_sheets_reporting.py) (70/70 passed).
- **Result:** **RESOLVED**.

---

## 3. Explicitly Documented Unverified / Blocked External Capabilities

1. **YouTube Video Deletion via API:**
   - **Status:** **BLOCKED BY DESIGN (LEAST PRIVILEGE)**.
   - **Reason:** Hexton deliberately requests only `youtube.upload` and `youtube.readonly` OAuth scopes. `videos.delete` requires full `https://www.googleapis.com/auth/youtube` scope. Uploaded test video `5LGxax8mxnU` remains private on the channel.
2. **Automated Creator Mention Entity in YouTube API:**
   - **Status:** **NOT SUPPORTED BY YOUTUBE API (`DF-19`)**.
   - **Reason:** YouTube Data API provides no mention resource. Plain text `@handle` is never treated as verified. Human attestation via YouTube Studio mention picker is the sole verified path.
3. **Task Scheduler Registration from Non-Elevated Installer:**
   - **Status:** **BLOCKED WITHOUT ELEVATION ON WINDOWS 11**.
   - **Reason:** Windows 11 requires UAC elevation for Task Scheduler. Least-privilege non-elevated installation utilizes `HKCU Run Key` or `Startup Folder`.

---

## 4. Exit Gate Evaluation

| Exit Gate Requirement | Status | Verification Evidence |
| :--- | :--- | :--- |
| **Every resolved defect has regression coverage** | **SATISFIED** | Regression tests authored and passing for all resolved defects. |
| **Unresolved defects & blocked tests are explicit** | **SATISFIED** | §3 explicitly enumerates all 3 external API/OS constraints. |
| **Complete suite passing** | **SATISFIED** | 636 Backend tests + 21 E2E tests = 657 passing tests, 0 failures. |

**Phase Status:** Prompt 12 Complete. Proceeding to Prompt 13.
