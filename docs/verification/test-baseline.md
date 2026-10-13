# Test Baseline & Safe Test Mode Report

**Date:** 2026-10-13  
**Status:** Complete  
**Scope:** Prompt 2 of Playbook — Test Infrastructure, Safe Test Mode Isolation, and Contract Tests

---

## 1. Verified Working Commands

| Category | Command | Result |
|---|---|---|
| **Quality Check Script** | `powershell -ExecutionPolicy Bypass -File scripts/check.ps1` | Passed |
| **Test Suite Script** | `powershell -ExecutionPolicy Bypass -File scripts/test.ps1` | Passed |
| **Backend Unit & Contract Tests** | `backend/.venv/Scripts/pytest backend/tests` | **573 passed** in ~24s |
| **Backend Linter** | `backend/.venv/Scripts/ruff check backend` | **0 errors / All checks passed** |
| **Backend Formatter** | `backend/.venv/Scripts/ruff format --check backend` | **87 files already formatted** |
| **Frontend Typecheck** | `npm --prefix frontend run typecheck` | Passed |
| **Frontend Linter** | `npm --prefix frontend run lint` | Passed |
| **Frontend Playwright E2E** | `npm --prefix frontend run test:e2e` | **21 passed** in ~33s |

---

## 2. Test-Mode vs. Production-Mode Separation

### Vulnerability Identified in Prompt 1 Audit
Previously:
- `Settings.connector_kind` defaulted to `"fake"`.
- Production entrypoints (`hexton serve`, `hexton worker`) did not prevent fake connectors from being chosen if environment variables were omitted.
- Test suites could inadvertently run against real channels or production credentials if environment variables leaked.

### Remediation Implemented
1. **Production Default Protection:**
   - `Settings.connector_kind` in `backend/hexton/config.py` defaults to `"youtube"`.
   - `Settings.test_mode` flag added (defaults to `False`).
2. **CLI Production Safety Guards:**
   - Both `hexton serve` and `hexton worker` verify `ctx.connector.provider != "fake"` when `test_mode=False`.
   - Any attempt to launch `serve` or `worker` with fake connectors in production mode immediately aborts with:
     ```
     error: cannot bind to the fake connector in production. Fake connectors are only permitted in test mode.
     To run with fake connectors in tests, pass --test-mode or set HEXTON_TEST_MODE=1.
     ```
   - Worker processes spawned by `serve` inherit `--test-mode` and `HEXTON_TEST_MODE=1` only when the parent server was explicitly started in test mode.
3. **E2E & Test Isolation:**
   - `frontend/e2e/server.mjs`, `frontend/e2e/worker.spec.ts`, and `frontend/playwright.config.ts` explicitly set `HEXTON_TEST_MODE=1` and pass `--test-mode`.
   - `backend/tests/conftest.py` configures `test_mode=True` across test fixtures.

---

## 3. Protocol Contract Tests for YouTubeConnector

### Gap Identified
Previously, 100% of backend tests ran against `FakeConnector`. The real HTTP client implementation in `backend/hexton/connectors/youtube.py` had zero automated tests verifying HTTP headers, range resumption, and error translation.

### Remediation Implemented
1. **In-Process Mock YouTube Server:**
   - Implemented in `backend/tests/mock_youtube_server.py` using standard library `http.server.HTTPServer` on ephemeral loopback ports (`127.0.0.1:0`).
   - Simulates resumable session creation, Location header return, 308 range resume responses, channel listing, video status get/update/delete, token refresh, and token revoke.
2. **Contract Test Suite (`backend/tests/test_youtube_contract.py`):**
   - 9 new automated contract tests covering:
     - `test_youtube_connector_gate_when_disabled`: Enforces Phase 0 gate when credentials missing.
     - `test_list_owned_channels`: Validates channel identity extraction and subscriber count parsing.
     - `test_start_resumable_upload_success`: Validates session creation and Location header persistence.
     - `test_start_resumable_upload_auth_error`: Verifies HTTP 401 maps to `FailureKind.AUTHORIZATION` (non-retryable).
     - `test_start_resumable_upload_quota_error`: Verifies HTTP 403 quota errors map to `FailureKind.QUOTA`.
     - `test_probe_upload_incomplete`: Verifies status probe parses 308 Range headers and reports offset.
     - `test_perform_upload_chunked_success`: Exercises multi-chunk resumable uploads and progress callbacks.
     - `test_get_and_update_and_delete_video`: Exercises metadata retrieval, status updates, and deletions.
     - `test_oauth_refresh_and_revoke`: Verifies refresh token grant and revocation requests.

---

## 4. Known Gaps & Next Steps (Leading into Prompt 3)

1. **Pre-existing Attribute Errors in API Router:**
   - In `backend/hexton/api/routers/connections.py` (lines 349-352), properties `channel_title`, `channel_handle`, and `last_polled_at` are accessed directly on `ChannelIdentity` ORM models where the actual column names are `title`, `handle`, and `verified_at`. This will be addressed in Prompt 3.
2. **Mypy Strict Typings:**
   - 76 Mypy errors exist across backend modules due to ORM typing annotations. Will be systematically addressed in type audit and hardening phases.
3. **Live Test Channel Integration:**
   - As documented in Prompt 1, live credentials exist in Windows Credential Manager (`Hexton/conn:1`, `Hexton/conn:2`). Live channel verification tests will execute safely with explicit operator opt-in without exposing private keys or tokens.
