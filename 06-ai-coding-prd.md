# AI Coding PRD — Implementation Protocol and Definition of Done

**Version:** 1.1 (revision 2026-10-11; see the correction log in `README.md`)

## Role

Act as a senior full-stack engineer, Python/FastAPI architect, React/TypeScript engineer, Windows desktop packaging engineer, Google OAuth/API integrator, and QA/release engineer. Implement the Local YouTube Shorts Scheduler in controlled, verifiable slices using this complete PRD set as the source of truth.

## 1. Rules of engagement

- Do not build every screen in one uncontrolled pass.
- Do not claim a feature works just because a button or screen exists.
- Do not use mock data in production paths.
- Do not use hidden APIs or unauthorized automation to bypass YouTube limitations.
- Do not store Google passwords or plaintext refresh tokens.
- Do not blindly retry ambiguous video inserts.
- Do not treat a plain `@handle` as a verified YouTube mention.
- **Never claim tests, installs, API calls or device checks were run if they were not.** In particular, do not state that the unverified-project privacy behaviour, the multi-channel/brand-account behaviour, the minimum scheduling lead time, or the Windows startup mechanism have been confirmed unless the corresponding `FEAS-*` verdict in `07-feasibility-checklist.md` says so.
- When credentials or external approval are unavailable, implement the interface behind a clearly named adapter, write tests with fakes, and mark live integration as unverified rather than simulating a successful real upload.
- Do not create documentation files that restate requirements already defined elsewhere. Add to the owning document and cross-reference it.

## 2. Phase 0 — repository and feasibility assessment

1. Inspect the repository, dependencies, branches, project scripts and assets before changing files.
2. Create a baseline report: existing modules, relevant code, reusable components, vulnerabilities/technical debt and migration options.
3. Record the official API facts the design depends on in a pinned document, and add a test that asserts those recorded values. A change in upstream documentation should surface as a failing test, not as a silent assumption. Confirm at minimum: `status.publishAt` is settable on `videos.insert`; the available `videos.list` filters; `channels.list(mine=true)`; quota costs and the separate 100/day insert bucket; the 100-character title limit; the 500-character tag limit; OAuth Testing-mode seven-day expiry; and the 90-day project inactivity rule.
4. Execute the proof-of-concept items in `07-feasibility-checklist.md` in order. **Do not begin Phase 1 feature work until each has a written verdict with evidence.** `FEAS-01` and `FEAS-02` block the product premise and come first.
5. Investigate the exact @mention requirement. The documented `videos` resource exposes no mention entity, so what is verified is the **manual** workflow and its survival across a later metadata update (`FEAS-06`), not the existence of an API field.
6. Test how credentials map to channels, including brand/multiple-channel accounts (`FEAS-01`). Confirm the returned upload destination through an actual test channel.
7. Maintain the feasibility matrix with `verified`, `not verified`, `blocked` and `not supported` statuses. It lives in `07-feasibility-checklist.md`; do not create a second copy.
8. Set up lint, formatting, type checks, unit tests, integration tests and a reproducible dev setup.
9. Create a traceability matrix mapping every `FEAT-*` and `AC-MVP-*` ID to at least one test before adding production features.

Do not spend days polishing the dashboard before uploading a real test video has been proven possible with the intended OAuth project.

### 2.1 Implementation order

`FEAS-*` gate → foundation → jobs/media → identity → upload → scheduling → confirmation sweep → Sheets → UI → compliance and packaging. The order is dependency-ordered: no UI work starts before the API it calls exists, and no upload work starts before channel identity is verified.

## 3. Phase 1 — architecture foundation

Implement modular layers:
- React frontend (Vite + TypeScript), built and served by the API from the same origin.
- FastAPI local API bound to 127.0.0.1.
- SQLite in **WAL** mode with migrations and transactions, and single-writer claim coordination (`02` §2.1).
- Persistent worker/scheduler process, started as the interactive user (`FEAS-08`).
- YouTube connector: client library for JSON calls, **first-party resumable upload over HTTP** that persists the session URI.
- Sheets reporting adapter with sync-key deduplication.
- Secure Windows credential storage abstraction.
- Structured, redacted job events.
- Canonical `JOB-*` state machine and a separate remote-state record.

Bind to 127.0.0.1 by default. Keep OAuth tokens and API secrets out of frontend code, localStorage, browser devtools state, URLs, logs and source control. Provide sample config files containing placeholders only. Include startup/shutdown handling and worker health reporting.

## 4. Phase 2 — complete vertical slices

### Slice A: local jobs and media
- DB migrations and models.
- Media picker, media metadata, hash, missing/changed file detection.
- Schedule create/edit/preflight and timezone handling.
- Unit tests and persisted restart tests.

### Slice B: OAuth and channel identity
- System browser OAuth flow.
- Validate state and callback.
- Store tokens with Windows-backed protection.
- Display confirmed channel ID/title/scopes.
- Handle expiry, revocation, reconnect and disconnect.
- Test with real authorized test channel when available.

### Slice C: YouTube upload
- Implement resumable upload.
- Track progress and checkpoints.
- Store video ID before proceeding to next phase.
- Reconcile uncertain requests before retry.
- Test real upload with private/unlisted test content first; do not publish public clips accidentally.

### Slice D: scheduled publication
- Send `status.publishAt` with the insert request, with explicit private privacy (`02` §6.1).
- Confirm remote scheduling state and timestamp.
- Test future publication end to end, and observe actual publication via the sweep.
- Prevent past-time values from causing immediate public publication.
- Record the `FEAS-03` measurement as the basis for the default lead time.

### Slice E: mention capability/fallback
- Ship `manual_required` with the guided YouTube Studio step and operator confirmation, since the documented API exposes no mention entity.
- Pause jobs that require a verified mention before publication; never promote the state to `verified`.
- Record the `FEAS-06` verdict on whether a Studio mention survives a later metadata update, and constrain `videos.update` accordingly.
- Do not automate private endpoints or falsely flag success.

### Slice F: Google Sheets outbox
- Configure spreadsheet/tab via OAuth and minimal scopes.
- Preview and test write.
- Build deterministic daily report rows.
- Implement the sync-key read-before-append deduplication and read-based timeout reconciliation.
- Test rerun/retry without duplicates.

### Slice G: publish confirmation sweep
- Poll remote status for jobs whose publish time has passed; back-fill after downtime.
- Distinguish upload completed, scheduled, verified published, and awaiting verification.

### Slice H: dashboard and operations
- Dashboard, queue, calendar, channel management, media library, job details, reports, settings and diagnostics.
- Connect controls only to implemented backend operations.
- Validate loading, empty, offline, blocked, error and success states.
- Render every status label from the `02` §4.3 mapping.

### Slice I: compliance
- Privacy policy and blocking consent gate (`COMP-01`, `COMP-02`).
- Disable and Revoke as separate actions with the revocation call and purge (`COMP-04`).
- Authorized Data retention sweep (`COMP-05`).
- Consent-gated metadata transformations with no truncation (`COMP-06`).
- Visibility surfacing and Private default (`COMP-07`).
- No third-party platform marks (`COMP-08`).
- Project-activity heartbeat (`COMP-09`).

### Slice J: Windows packaging
- Reproducible install, start-at-logon option running as the interactive user, browser launch, worker lifecycle, logs, upgrade, backup and uninstall behaviour.
- Installer removes the database, the credential entries and the Task Scheduler task.

## 5. Mandatory implementation loop

For every meaningful change:
1. Read the relevant FEAT/AC IDs.
2. Inspect affected code and dependencies.
3. Write/adjust tests before or alongside implementation.
4. Make the smallest complete change.
5. Run format/lint, type checks, unit tests and affected integration tests.
6. Build frontend/backend artifacts as applicable.
7. Test state persistence and restart behavior if affected.
8. Run actual Google API integration or document why it cannot be run.
9. Review the diff for security, privacy, UX and regressions.
10. Fix failures and rerun the failing test plus relevant regression suite.
11. Update docs and traceability evidence.
12. Commit only when required gates pass.

A failed or unavailable test remains `failed` or `not verified`; it must not be counted as pass.

## 6. Test requirements

### Unit tests
- Timezone conversion, invalid/past times, schedule preflight.
- State machine transitions.
- Retry and backoff.
- File changes and metadata constraints.
- Report row construction and idempotency fingerprint.
- Mention state cannot become verified without an explicit evidence path.

### Integration tests
- DB constraints, atomic job claims, expired leases, migrations.
- OAuth token refresh/revocation using test credentials where possible.
- API error mapping and resumable upload checkpoint behavior.
- Google Sheets sync outbox and retry/reconciliation.

### Fault injection
- Wi-Fi/network disconnected during upload.
- Worker killed after remote acceptance but before DB commit.
- Worker killed mid-transfer; resume from the persisted session URI and `Range`.
- Duplicate worker delivery, and two workers racing for the same job.
- 401, 403 quota/permission, 429 and 5xx responses.
- Expired resumable session (404) — must route to reconciliation, not a blind restart.
- Missing/changed file.
- PC reboot, sleep/resume, timezone change and daylight-saving boundary.
- Expired OAuth tokens.
- Sheets API accepts write but response times out.
- Publication timestamp accidentally in the past.

### End-to-end release test
1. Connect test channel and confirm identity.
2. Configure a test sheet/tab.
3. Add a valid local clip and schedule ID.
4. Queue a future publish time.
5. Close the browser while the worker continues.
6. Upload and confirm video ID and target channel.
7. Verify remote scheduled status and publish time.
8. Verify the video becomes public and that the sweep confirms it.
9. Run the report and check one matching row.
10. Repeat report and recovery flows; ensure no duplicate row and no duplicate upload outside `JOB_NEEDS_REVIEW`.
11. Test the manual mention workflow separately and record its result.
12. Revoke one connection and confirm the credential is deleted and Authorized Data is purged.

## 7. Error handling and UX

Every backend error that reaches the UI should have a stable code, a safe human-readable explanation, retryability, and a suggested next action. Log trace IDs and sanitized context, not tokens or raw authorization headers. Never show “Published” when only the upload request was accepted.

## 8. Security release gates

- Localhost binding and origin protections verified.
- OAuth state/redirect validation tested.
- Secure token storage tested on target Windows build, with the worker running as the interactive user.
- No secrets committed, bundled into frontend, or leaked into logs — verified by an automated scan of the database file, the built frontend bundle and all log files.
- File paths and file access validated.
- Dependencies audited.
- Disable and revoke are separate, tested actions; revocation calls Google and purges Authorized Data within the approved deadline.
- Consent gate blocks feature access until the privacy policy is accepted, and policy links resolve.
- Retention sweep demonstrated.
- No public/remote listener enabled by default.

## 9. Final delivery package

Provide source code, a README, setup wizard/instructions, `.env.example` or equivalent with placeholders only, DB migration/run commands, clean build commands, test commands and results, Windows start/stop/upgrade instructions, Google Cloud OAuth setup guide, YouTube channel connection guide, Google Sheets report guide, troubleshooting/runbook, known limitations, and requirements-to-tests traceability.

The final report must:

- Separate verified features from unverified integrations.
- State plainly whether genuine creator mentions are automated (they are not) and that the manual step is required.
- Reproduce the `FEAS-*` verdicts, including the observed unverified-project privacy behaviour, the multi-channel result, and the measured minimum scheduling lead time.
- List the owner decisions recorded in `02` §15 that remain open.
