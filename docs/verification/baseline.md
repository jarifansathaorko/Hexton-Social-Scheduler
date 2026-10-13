# Baseline Audit Report After OAuth Integration

**Date:** 2026-10-13  
**Auditor:** Antigravity AI Agent  
**Repository:** `Hexton Scheduler` (`jarifansathaorko/Hexton-Social-Scheduler`)  
**Environment:** Windows 11 Pro build 26200, CPython 3.14.7, Node v24 / npm 11, PowerShell 5.1  
**Status:** Baseline Established — No production code edited  

---

## 1. Executive Summary & Repository State

This baseline audit inspects the current repository state following OAuth integration. The application is a Windows-local YouTube Shorts scheduler adhering to the six PRDs (`01-standard-prd.md` through `06-ai-coding-prd.md`), the feasibility checklist (`07-feasibility-checklist.md`), and the Phase 0 feasibility report (`08-phase0-feasibility-report.md`).

### Key Findings
1. **OAuth Integration is Real & Active:** 
   - Real OAuth credentials and active tokens are securely stored in the Windows Credential Manager (`CredentialManagerStore` via `advapi32.dll`).
   - Two active platform connections exist in `%LOCALAPPDATA%\Hexton\hexton.sqlite3`:
     - `PlatformConnection(id=1, provider="youtube", label="My YouTube Channel", status="active")`
     - `PlatformConnection(id=2, provider="sheets", label="Google Sheets Reporting", status="active")`
   - Six real YouTube channels have been resolved and operator-confirmed in `channel_identity`.
2. **Production vs. Test Connector Separation:**
   - The production backend is configured with `HEXTON_CONNECTOR_KIND=youtube` and live Google OAuth Client ID / Secret in `backend/.env`.
   - The automated backend test suite (`backend/tests`, 564 tests) and frontend E2E suite (`frontend/e2e`, 21 tests) execute exclusively against in-memory/in-process fakes (`FakeConnector`, `FakeSheetsConnector`, `InMemoryCredentialStore`).
   - There are currently **zero contract or protocol tests** in `backend/tests` exercising the real HTTP implementations in `hexton/connectors/youtube.py` and `hexton/connectors/sheets_live.py`.
3. **Existing Defects Identified:**
   - **Linter/Formatter failure:** `backend/hexton/media/picker.py` has unsorted imports (Ruff `I001`) and unformatted string literals.
   - **Attribute errors in OAuth flow:** `hexton/api/routers/connections.py` lines 349–352 reference non-existent attributes `channel_title`, `channel_handle`, and `last_polled_at` on the `ChannelIdentity` model (which defines `title`, `handle`, `verified_at`).
   - **Type / Runtime errors in Sheets Live Connector:** `hexton/connectors/sheets_live.py` line 118 attempts `operation.__name__` on lambdas and passes mismatched `SheetError` kinds.
   - **Reporting model mismatch:** `hexton/api/routers/reporting.py` lines 314–319 assigns `SheetSummaryRecord` to `SheetReportRecord` and accesses non-existent `report_date`.
   - **Static analysis:** 76 Mypy type errors across 27 files in `backend/hexton`.

---

## 2. Architecture & Entry Points Inventory

### 2.1 Backend Entry Points (`backend/hexton/cli.py`)
- `python -m hexton migrate`: Runs Alembic migrations (`alembic upgrade head`) against configured SQLite DB.
- `python -m hexton revision --message "..."`: Generates new migration revisions.
- `python -m hexton serve [--no-start-worker]`: Main production entry point. Starts FastAPI via Uvicorn on loopback (`127.0.0.1:8787`) and spawns the background worker as an independent child process (`subprocess.Popen` with `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` on Windows).
- `python -m hexton worker`: Starts the worker process directly in the current terminal.
- `python -m hexton dev`: Developer mode running FastAPI and a worker thread in a single process.
- `python -m hexton info`: Dumps resolved runtime configuration, connector provider, credential store, and data paths.

### 2.2 Worker Process Architecture (`backend/hexton/worker/`)
- **Process Model:** Genuinely decoupled process; closing the browser does not terminate worker scheduling.
- **Worker Main Loop (`hexton/worker/main.py`):**
  - **Startup Recovery:**
    1. Reclaims orphaned leases from dead workers (`runner.reclaim_orphans`).
    2. Expires stale leases (`expire_stale_leases`).
    3. Executes compliance retention sweep (`RetentionSweeper.run`).
  - **Loop Execution:**
    1. **Job Claim & Runner (`hexton/worker/runner.py`):** Claims next due job via `hexton/worker/lease.py` using `BEGIN IMMEDIATE` transaction. Re-verifies media digest (SHA-256), metadata, channel confirmation, and lead time. Runs reconciliation ladder (`hexton/reconciliation/ladder.py`) if previous insert was ambiguous. Performs resumable upload via connector.
    2. **Publish Confirmation Sweep (`hexton/worker/sweep.py`):** Polls remote video status via `get_video` for jobs past publish time using quota-aware backoff.
    3. **Report Sync Sweep (`hexton/worker/report_sync.py`):** Reconciles and appends pending rows to Google Sheets.
    4. **Heartbeat (`hexton/worker/heartbeat.py`):** Appends/updates `worker_heartbeat` table every 15s with PID, timestamp, and jobs claimed.

### 2.3 Database Schema & Persistence (`backend/hexton/db/`)
- **Engine:** SQLite 3 with `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=15000`, `PRAGMA foreign_keys=ON`, and `PRAGMA synchronous=NORMAL`.
- **Atomic Concurrency:** `immediate_transaction` context manager issues explicit `BEGIN IMMEDIATE` on a dedicated raw DBAPI connection.
- **Canonical Schema Models (`hexton/db/models.py`):**
  - `platform_connection`: OAuth connection metadata, status (`disconnected`, `active`, `disabled`, `revoked`, `expired`, `deleted`), scopes, timestamps. (Secrets are never stored in SQLite).
  - `channel_identity`: Channel ID, title, handle, subscriber count, `operator_confirmed_at`, `is_brand_candidate`.
  - `media_file`: Path, SHA-256 digest, size, duration, container, validation issues.
  - `content_metadata`: Title, description, tags, category, explicit `privacy`, mention fields, transformation audit log.
  - `schedule`: Target publish time (UTC), display timezone, minimum lead minutes.
  - `scheduled_job`: Canonical `state` (`JobState`), attempt counter, lease owner/expiration, idempotency key, `insert_may_have_succeeded`, `video_id`, progress percent.
  - `upload_session`: Resumable session URI, phase, bytes uploaded, total bytes, SHA-256.
  - `job_event`: Append-only structured audit log for jobs.
  - `remote_observation`: Append-only poll log recording observed remote video state (`RemoteState`).
  - `sheet_report_record`: Outbox record with mandatory `sync_key`, status, attempts.
  - `data_retention`: Deletion deadlines (7d client revocation, 30d Google revocation).
  - `sheets_config`: Singleton configuration for spreadsheet ID, worksheet, cutoff time, timezone.
  - `sheet_summary_record`: Daily completion summaries keyed by date and channel.
  - `policy_acceptance_record`: Privacy policy version acceptance audit log.
  - `worker_heartbeat`: Worker liveness heartbeat records.
  - `quota_ledger`: Pacific-midnight quota window tracking.
- **Migrations (`backend/migrations/versions/`):**
  - `0001_initial_schema.py`: Initial baseline schema.
  - `0002_phase3.py`: Added `sheets_config`, `sheet_summary_record`, `policy_acceptance_record`.
  - Current database state: At revision `0002`.

### 2.4 Credential Store (`backend/hexton/credentials/store.py`)
- `CredentialManagerStore`: Primary Windows store. Interacts directly with Windows Credential Manager generic credentials via `ctypes` (`advapi32.dll` `CredWriteW`, `CredReadW`, `CredDeleteW`, `CredEnumerateW`). Scoped to current interactive Windows user. Namespaced under `Hexton/`.
- `DpapiFileStore`: Secondary Windows store. DPAPI user-scoped encrypted blob (`CryptProtectData`). Inaccessible to other users or machine-wide services.
- `InMemoryCredentialStore`: Volatile dictionary store used for automated testing.
- `PlaintextStore`: Rejected in production; refused unless `allow_plaintext_credentials=True` and `profile != PACKAGED`.

### 2.5 Connectors (`backend/hexton/connectors/`)
- **Base Interface (`base.py`):** `PlatformConnector` defining `list_owned_channels`, `start_resumable_upload`, `probe_upload`, `perform_upload`, `get_video`, `list_recent_upload_ids`, `update_video_status`, `delete_video`, `refresh`, `revoke`.
- **YouTube Connector (`youtube.py`):**
  - Implements Google YouTube Data API v3 and resumable protocol over `httpx`.
  - Enforces `_GATE`: raises `ConnectorError` if `client_id` is missing or unconfigured.
  - Uses 8 MB chunked uploads with `Range` header tracking.
  - Flags `ambiguous=True` on network error during final chunk to prevent duplicate inserts.
- **Fake Connector (`fake.py`):** In-memory mock simulating chunk uploads, errors (transient, permanent, quota, authorization), 308 resume ranges, and dropped responses.
- **Sheets Connectors (`sheets.py`, `fake_sheets.py`, `sheets_live.py`):**
  - `sheets.py`: Defines protocol, frozen `SHEET_COLUMNS`, `KEY_COLUMN = "Sync Key"`, and `SheetError`.
  - `fake_sheets.py`: In-memory simulated spreadsheet tracking written keys and values.
  - `sheets_live.py`: Uses `googleapiclient.discovery.build("sheets", "v4")` for `spreadsheets.get`, `values.get`, and `values.append`.

### 2.6 Frontend Structure (`frontend/src/`)
- Framework: React 18, Vite 7, TypeScript, Tailwind CSS, Lucide icons.
- Routing (`App.tsx`):
  - `/` -> `DashboardPage.tsx`: Overall KPIs, upcoming schedule, active jobs, worker health.
  - `/calendar` -> `CalendarPage.tsx`: Monthly and list view of scheduled jobs.
  - `/queue` -> `QueuePage.tsx`: Detailed job execution list with progress indicators.
  - `/editor` -> `EditorPage.tsx`: Content creation, file picker, metadata, timezone, scheduling, preflight.
  - `/media` -> `MediaLibraryPage.tsx`: File browser, SHA-256 calculation, container validation.
  - `/channels` -> `ChannelsPage.tsx`: Channel authorization, identity confirmation, enable/disable/revoke.
  - `/reports` -> `ReportsPage.tsx`: Google Sheets outbox, Sync Key ledger, preview, retry sync.
  - `/settings` -> `SettingsPage.tsx`: Policy acceptance, diagnostics export, timezone, token checks.
- API Client (`frontend/src/api/` & `hooks/api.ts`): Typed REST client calling loopback FastAPI backend.

---

## 3. Features Inventory: Implemented, Partial, Missing, Mocked & Unverified

| PRD Feature ID | Feature Name | Code Location | Status | Implementation Type | Verification Status |
|---|---|---|---|---|---|
| **FEAT-001** | First-run setup & Privacy Policy | `api/routers/compliance.py`, `pages/SettingsPage.tsx` | Implemented | Production | **Verified locally** (E2E test passes) |
| **FEAT-002** | OAuth Flow & Channel Identity | `api/routers/connections.py`, `pages/ChannelsPage.tsx` | Implemented | Production | **Verified live** (Real OAuth connected, 6 channels stored) |
| **FEAT-002 (Revoke)** | Disconnect vs Revoke & Data Purge | `api/routers/connections.py`, `compliance/retention.py` | Implemented | Production | **Verified locally** (Unit/E2E tests pass) |
| **FEAT-003** | Media Library & Validation | `media/validate.py`, `media/picker.py`, `pages/MediaLibraryPage.tsx` | Implemented | Production (with Linter bug in `picker.py`) | **Verified locally** |
| **FEAT-004** | Schedule Editor & Preflight | `domain/validation.py`, `domain/scheduling.py`, `pages/EditorPage.tsx` | Implemented | Production | **Verified locally** |
| **FEAT-005** | Queue & Calendar Views | `pages/QueuePage.tsx`, `pages/CalendarPage.tsx` | Implemented | Production | **Verified locally** |
| **FEAT-006** | YouTube Resumable Upload Connector | `connectors/youtube.py`, `worker/runner.py` | Implemented | Production code exists, but tests use Mock | **Unverified with real upload**; Contract tests missing |
| **FEAT-007** | Scheduled Publishing (`publishAt`) | `worker/runner.py`, `domain/scheduling.py` | Implemented | Production | **Unverified with live YouTube API** |
| **FEAT-008** | Mention Workflow (`manual_required`) | `domain/mention.py`, `services/mentions.py`, `pages/EditorPage.tsx` | Implemented | Production (Honest manual workflow) | **Verified locally** |
| **FEAT-009** | Retry, Backoff & Reconciliation Ladder | `reconciliation/ladder.py`, `worker/runner.py` | Implemented | Production | **Verified with fakes**; Unverified on live network loss |
| **FEAT-010** | Google Sheets Reporting & Sync Key | `connectors/sheets_live.py`, `services/sheets.py`, `pages/ReportsPage.tsx` | Partial / Buggy | Production code has typing/runtime bugs; tests use fake | **Unverified with live Sheets** |
| **FEAT-011** | Job Detail & Redacted Event Logs | `api/routers/jobs.py`, `db/events.py` | Implemented | Production | **Verified locally** |
| **FEAT-012** | Database Backup & Restore Guidance | `cli.py`, `README.md` | Partial | Manual file copy documented | **Verified as file-level copy** |
| **FEAT-013** | Publish Confirmation Sweep | `worker/sweep.py` | Implemented | Production | **Verified with fakes** |
| **FEAT-014** | Compliance Heartbeat & Retention Sweeper | `compliance/policy.py`, `worker/main.py` | Implemented | Production | **Verified locally** |
| **FEAT-015** | Metadata Templates | N/A | Excluded from MVP (P1) | Not implemented | Excluded |
| **FEAT-016** | CSV Bulk Import | N/A | Excluded from MVP (P1) | Not implemented | Excluded |
| **FEAT-017** | Week/Day Calendar Drag-to-Reschedule | N/A | Excluded from MVP (P1) | Not implemented | Excluded |
| **FEAT-018** | Thumbnail Upload / AI Generation | N/A | Excluded from MVP (P1) | Not implemented | Excluded |
| **FEAT-019** | Future Social Connectors | N/A | Excluded from MVP (P2) | Not implemented | Excluded |

---

## 4. Test Baseline & Commands Available

### 4.1 Commands Run & Measured Results

| Command | Working Directory | Result | Notes / Failures |
|---|---|---|---|
| `.\.venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q` | `backend/` | **564 passed, 1 warning** (19.67s) | 1 StarletteDeprecationWarning |
| `.\.venv\Scripts\python.exe -m ruff check hexton tests` | `backend/` | **1 error** (exit code 1) | `hexton\media\picker.py:7:1` unsorted imports |
| `.\.venv\Scripts\python.exe -m ruff format --check hexton tests` | `backend/` | **1 file unformatted** (exit code 1) | `hexton\media\picker.py` |
| `.\.venv\Scripts\python.exe -m mypy hexton` | `backend/` | **76 errors in 27 files** | Mypy strict not enforced as CI gate |
| `npm run typecheck` | `frontend/` | **PASSED** (0 errors) | TypeScript check clean |
| `npm run lint` | `frontend/` | **PASSED** (0 errors) | ESLint clean |
| `npm run build` | `frontend/` | **PASSED** (1.61s) | Vite production bundle created |
| `npx playwright test` | `frontend/` | **21 passed** (31.7s) | 1 setup, 19 core, 1 worker tests pass |
| `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q` | `phase0-poc/` | **150 passed, 1 skipped** (12.3s) | Platform guard skipped |

---

## 5. Feasibility Verdicts & Known Blockers Matrix (`07-feasibility-checklist.md`)

| Item | Subject | Status | Basis & Next Gate |
|---|---|---|---|
| `FEAS-01` | Multi-channel / Brand Account targeting | **BLOCKED** | Channel identities resolved in local DB, but upload targeting brand account unverified against live YouTube API. |
| `FEAS-02` | Unverified API project privacy behavior | **BLOCKED** | Official docs contradict. Fallback implemented: explicit `privacyStatus=private`. Needs live test with test channel. |
| `FEAS-03` | Minimum scheduling lead time | **BLOCKED** | Set to 30 min default. Needs measurement with live API. |
| `FEAS-04` | Duplicate prevention under ambiguous insert | **VERIFIED (LOCAL)** | Verified against local protocol mock. Unverified against live edge timeout. |
| `FEAS-05` | Resumable session expiry duration | **BLOCKED** | Undocumented by Google. Tested locally for 404 recovery. |
| `FEAS-06` | @mention durability under `videos.update` | **BLOCKED** | Manual Studio step implemented. Survives if omitted properties are re-asserted. |
| `FEAS-07` | Google Sheets append deduplication | **VERIFIED (LOCAL)** | Tested against `FakeSheetsConnector`. Unverified with live Google Sheets API. |
| `FEAS-08` Step 1 | Windows Credential Manager round-trip | **VERIFIED** | Real OS calls via `ctypes` tested and live OAuth tokens confirmed stored. |
| `FEAS-08` Step 2 | Non-elevated Task Scheduler logon task | **BLOCKED** | Denied without elevation on Windows 11 build 26200. Interactive user required. |
| `FEAS-08` Step 3 | `tzdata` / IANA timezone conversion on Windows | **VERIFIED** | Verified with `zoneinfo` and `tzdata` package on Windows. |
| `FEAS-09` | Activity-feed appearance latency | **BLOCKED** | Unverified against live YouTube API. |
| `FEAS-10` | 7-day client vs 30-day Google deletion | **VERIFIED (LOCAL)** | Local database bookkeeping tested. External compliance unverified. |
| `FEAS-11` | Daylight saving cutover behavior | **VERIFIED (LOCAL)** | Tested with UTC conversions and synthetic fold/gap scenarios. |

---

## 6. Prioritized Feature Test Matrix for Upcoming Prompts

| Priority | Phase / Prompt | Target Area | Key Test Objectives |
|---|---|---|---|
| **P0** | **Prompt 2** | Test Infrastructure & Safe Test Mode | - Ensure no production run can accidentally select `FakeConnector`.<br>- Add contract tests for `YouTubeConnector` and `LiveSheetsConnector` using local mock HTTP server.<br>- Fix linter error in `hexton/media/picker.py`. |
| **P0** | **Prompt 3** | OAuth Regression & Channel Identity | - Audit PKCE, state token TTL (10m), redirect URI safety.<br>- Verify token refresh and revocation against Credential Manager.<br>- Fix attribute bugs in `connections.py:349-352`.<br>- Verify channel identity confirmation gate. |
| **P0** | **Prompt 4** | Database, State Machine & Job Claiming | - Concurrency stress tests with multiple workers racing for `BEGIN IMMEDIATE` claim.<br>- Verify state machine legal/illegal transitions (`can_transition`).<br>- Verify lease expiration recovery and orphan reclamation. |
| **P0** | **Prompt 5** | Media Validation & Preflight | - Test corrupt/missing files, changed SHA-256 digests.<br>- Validate metadata character limits and Shorts aspect ratio checks.<br>- Preflight hard block on past `publishAt`. |
| **P0** | **Prompt 6** | YouTube Upload & Resumable Recovery | - Local protocol contract tests for 308 range resume, 404 expired session, 5xx backoff.<br>- Ambiguous insert recovery ladder without blind retries.<br>- Authorized private test upload against live test channel if approved. |
| **P0** | **Prompt 7** | Scheduling, Timezones & Sweep | - Asia/Dhaka and multi-timezone UTC conversions.<br>- Verify `PublishConfirmationSweep` polling logic and backfill after worker restart. |
| **P0** | **Prompt 8** | Windows Worker & Process Lifecycle | - Test worker process independent of browser session.<br>- Crash/restart recovery and lease timeout.<br>- Honest Task Scheduler / startup reporting. |
| **P0** | **Prompt 9** | Google Sheets Outbox & Idempotency | - Fix type/runtime errors in `sheets_live.py` and `reporting.py`.<br>- Verify read-before-append deduplication with mandatory `Sync Key`.<br>- Ensure reporting failure never triggers video re-upload. |
| **P0** | **Prompt 10** | Creator Mentions & Manual Fallback | - Verify `manual_required` state machine and operator attestation.<br>- Confirm descriptions are not truncated or deleted on update. |
| **P0** | **Prompt 11** | UI-to-Backend End-to-End Tests | - Expand Playwright tests to cover error boundaries, offline states, and live API fallback banners. |
| **P0** | **Prompt 12–17** | Defect Loop, Optimization, Cleanup, Security & Release | - Fix verified bugs, benchmark hot spots, harden security (Host/Origin/DACL), and produce final release checklist. |

---

## 7. Next Gate
- **Current Gate:** Prompt 1 Baseline Audit Complete. No production code was modified.
- **Next Gate:** **Prompt 2 — Test infrastructure and safe test mode.** Establish reproducible contract testing with local HTTP server and fix test runner configuration.
