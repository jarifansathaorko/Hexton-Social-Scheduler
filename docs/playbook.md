# Local YouTube Scheduler — Testing, Fixing, Optimization & Release Prompt Playbook

This playbook assumes OAuth is already integrated. It tells an AI coding agent to inspect the current implementation, test each feature against real behavior, fix verified defects, rerun regressions, optimize only after correctness is established, remove dead code safely, and document what remains unverified.

## How to use

1. Open the existing OpenCode implementation session with the current project loaded.
2. Use **Prompt 1** first. Do not start with broad refactoring.
3. Run prompts in order, one at a time. Wait for a final result before sending the next one.
4. Keep MiMo-V2.6-Flash for implementation. Use Space Bunny in a **separate session** for the independent review prompt near the end.
5. Ask the agent to write durable reports into `docs/verification/`. This helps after context compaction.
6. Do not mark live Google/YouTube behavior as tested unless a real authorized test account and API project were used. Mocks prove local logic only.
7. Use a dedicated test channel and private test videos unless a specific public-publish test is intentionally approved.

## Global instruction (send once before Prompt 1)

You are working in my existing Windows-local YouTube Shorts scheduler. OAuth is already integrated. First inspect the current repository and preserve working code. Follow the six PRDs, the feasibility checklist, and the latest repository state; do not assume old audit findings are still current. Make changes only for the phase I request. Use tests to prove behavior. Never fabricate test results or replace real production integrations with mocks. Never print OAuth tokens, refresh tokens, secrets, or sensitive content. Do not blindly retry an ambiguous YouTube upload. Do not mark an upload as published until remote status is verified. After each phase, report files changed, commands actually run, pass/fail/blocked tests, remaining risks, and the next gate. Stop if a prerequisite is missing or a destructive action is required.

---

## Prompt 1 — Baseline audit after OAuth integration

Inspect the entire current repository, existing OAuth implementation, Phase 0 proof of concept, six PRDs, feasibility checklist, and test results. Do not edit code yet.

Deliver a concise inventory of implemented, partial, missing, mocked, and unverified features. Identify the real entry points, worker process, database schema, connectors, credential store, frontend pages, migrations, test commands, and existing defects. Check whether current code actually matches the docs; do not assume it does.

Create `docs/verification/baseline.md` with repository state, environment, commands available, test baseline, known blockers, and a prioritized feature test matrix. Preserve existing files and reference images. Stop without editing production code.

**Exit gate:** the inventory names actual files and commands, and clearly separates real integrations from mocks.

## Prompt 2 — Test infrastructure and safe test mode

Based on the baseline, set up or repair the smallest useful test foundation without rewriting the architecture. Add or verify unit, database/integration, API contract, frontend, and end-to-end test commands. Ensure test mode is selected at startup and can only use fake connectors in tests; production startup must not accidentally bind to a fake YouTube or Sheets connector.

Use deterministic clocks, temporary databases, fixture files, and a local mock HTTP server for protocol tests. Add a single documented command for quality checks and one for tests. Keep all real credentials outside the test fixtures and logs.

Run the full baseline suite and record failures that existed before your changes. Create `docs/verification/test-baseline.md`.

**Exit gate:** tests run reproducibly on this Windows machine, and no mock connector can be selected accidentally in production.

## Prompt 3 — OAuth regression and channel identity

OAuth is already integrated. Audit and test the current implementation; do not rebuild it unless a specific defect is proven.

Test OAuth `state` validation, loopback callback safety, scope handling, refresh-token storage, token refresh, expired/revoked authorization, reconnect, disable versus revoke, and credential redaction. Verify that channel ID and title come from the authorized API response and require operator confirmation. Confirm that the UI cannot supply an arbitrary channel ID as authority and that the upload connector is bound to the verified connection.

Where configured, run one real identity check against an authorized test account. Brand-account behavior must remain marked verified, blocked, or unverified based on actual evidence. Add regression tests and write `docs/verification/oauth-and-channel.md`.

**Exit gate:** no secret appears in logs, database fields, frontend bundles, or test reports; target channel identity is server-verified.

## Prompt 4 — Database, state machine, and job claiming

Review the existing SQLite schema and job lifecycle. Verify migrations, foreign keys, indexes, WAL mode, `busy_timeout`, transaction boundaries, and concurrent worker behavior. Keep one canonical local `JobState` and a separate remote YouTube state record. Ensure UI labels map from canonical states rather than reimplementing status logic.

Test legal and illegal state transitions, two workers claiming the same due job, expired lease recovery, duplicate delivery, interrupted database writes, migration from prior schema versions, and the invariant that a job cannot be `published` without a persisted video ID and required remote evidence. Use `BEGIN IMMEDIATE` where appropriate for atomic claims.

Fix only demonstrated defects, add tests, and document decisions in `docs/verification/state-and-database.md`.

**Exit gate:** concurrent claim tests prove one claimant per job, migrations pass, and state invariants are tested.

## Prompt 5 — Media validation and metadata preflight

Test the content-preparation pipeline without uploading yet. Cover missing/unreadable files, file changed after scheduling, SHA-256 hash mismatch, unsupported or suspicious media containers, absent metadata, over-limit title/description/tags, invalid time zone, invalid scheduled time, and missing target channel.

Validate current YouTube limits from official docs rather than hardcoding unsupported guesses. Probe video metadata safely; if `ffprobe` is unavailable, use the documented fallback and report `unknown` instead of inventing values. Explicitly set privacy status; default to Private. Reject past publication times as a hard preflight error. Never silently truncate or modify user content. Optional transformations must require explicit consent.

Add one test per boundary/edge case, fix bugs, rerun affected tests, and write `docs/verification/media-preflight.md`.

**Exit gate:** invalid items are rejected before any YouTube API upload request; valid metadata passes deterministically.

## Prompt 6 — YouTube upload, progress, and resumable recovery

Test the existing upload connector end to end. First use contract tests for initiation, session URI persistence, upload progress, `308`/`Range` handling, `Retry-After`, expired sessions, authentication errors, quota errors, 5xx responses, cancellation, and process termination. Then run an authorized private test upload if the credentials and test channel are available.

Persist the upload session URI and required recovery fields. Resume from the server-confirmed range, not a guessed offset. Persist the returned video ID as soon as it is available. If remote acceptance is ambiguous, use the documented reconciliation ladder and move unresolved jobs to `needs_manual_review`; never blindly call `videos.insert` again.

Do not claim mocks prove YouTube accepted the file. Add regression tests, fix proven defects, rerun them, and write `docs/verification/youtube-upload.md`.

**Exit gate:** interrupted local protocol tests recover from persisted session data; a live upload is marked verified only with actual API evidence. Unresolved ambiguity blocks auto-retry.

## Prompt 7 — Scheduling, time zones, and publication confirmation

Audit time handling and scheduled publishing. Require `tzdata` on Windows for IANA zones. Test Asia/Dhaka and another IANA zone, UTC conversion, midnight boundaries, invalid zones, daylight-saving gaps/folds where applicable, past timestamps, and restart after a due time was missed.

Verify the current YouTube `publishAt` constraints against official docs. When the validated implementation permits it, send `privacyStatus=private` and a valid future `publishAt` as part of the insert request. For later `videos.update`, include the required explicit private state and respect the never-published constraint. Do not assume a previously published video can be rescheduled.

Implement or test `PublishConfirmationSweep`: publication time passing is not proof of publication. Poll by known video ID at a quota-aware interval, store remote observations, and retain `verification_pending` when proof is unavailable. The system must recover pending verification after restart.

Add test coverage, fix issues, and write `docs/verification/scheduling-and-publication.md`.

**Exit gate:** no past-time request can be sent; local state distinguishes upload complete, scheduled on YouTube, verification pending, and published verified.

## Prompt 8 — Windows worker, startup, sleep/resume, and shutdown

Test the worker independently of the browser. Verify that closing a browser tab does not stop scheduling. Test worker crash and restart, Windows reboot, user logon, sleep/resume, network unavailable at due time, credential-store access, orderly shutdown, and duplicate worker startup.

Respect the current credential-storage context. If the store is user-scoped, run under that same interactive user; do not silently switch to a Windows Service or SYSTEM account. Test any Task Scheduler setup on this machine and report permission errors such as `Access is denied`; do not claim autostart works without evidence. Prefer least privilege and do not require administrator elevation unless genuinely required.

Define missed-job policy and lease recovery. If upload was in progress before sleep/crash, reconcile the remote session before restarting. Write setup/repair instructions and `docs/verification/windows-worker.md`.

**Exit gate:** worker restart and browser closure are proven; Task Scheduler/autostart status is honestly reported. Machine-off and sleep behavior is documented.

## Prompt 9 — Google Sheets outbox and idempotency

Audit reporting independently from upload execution. Implement or verify a durable outbox and deterministic, mandatory Sync Key such as `SCH_<report_date>_<schedule_id>_<schema_version>`. Keep Schedule ID and Sync Key non-optional.

Before appending, read existing keys and append only missing rows. On timeout-after-write, re-read and reconcile rather than retrying blindly. A Sheets failure must never trigger another video upload. Test duplicate job delivery, report sent ten times, partial batch, network loss after remote acceptance, expired Sheets authorization, bad worksheet, worker restart, daily summary, and reconciliation.

The row must include at least schedule ID, video ID/URL, title, verified target channel, scheduled time, upload outcome, publication verification state, mention state, and reporting state, subject to the approved data-retention policy. Report pending and failed report writes visibly.

Add tests, fix bugs, rerun tests, and write `docs/verification/google-sheets.md`.

**Exit gate:** repeated report execution creates at most one row per Sync Key in the tested conditions; reporting cannot trigger re-upload.

## Prompt 10 — Creator mentions and manual confirmation

Audit the actual mention feature against current documented YouTube API capabilities. Do not assume putting `@handle` into a caption creates a structured mention. Do not use private endpoints, cookie extraction, or unauthorized UI automation.

Represent mention state separately from upload state: `not_required`, `pending_manual_action`, `manual_confirmation_required`, `confirmed_by_operator`, `failed_or_removed`, or `not_verified`, using the project's approved vocabulary. Provide a workflow that displays the intended channel/handle and lets the operator open the permitted manual flow. Never mark a mention verified without the required evidence. Protect a manually selected mention from being silently removed by later API description updates; test this if a supported workflow exists.

Add state-transition and UI tests. Write `docs/verification/creator-mentions.md` and clearly state what cannot be automated.

**Exit gate:** no false success is possible; unsupported automation has an honest manual fallback.

## Prompt 11 — UI-to-backend end-to-end tests

Test the real web UI against the actual local API and a controlled test database. Cover connect/reauthorize status, verified channel selection, create/edit draft, select video, metadata validation, schedule, queue details, upload progress, failure/retry, `needs_manual_review`, publication verification, Sheets sync, filters, time zone display, and browser refresh.

Every button and form must do its advertised action. Test loading, empty, validation, offline, worker-stopped, permission-expired, partial-success, and error states. Ensure UI state is derived from backend data and does not fake upload progress or publication success. Use Playwright for repeatable local flows and real API tests only when credentials/test accounts are available.

Fix UI bugs and rerun targeted plus regression suites. Write `docs/verification/ui-e2e.md`.

**Exit gate:** all P0 UI flows have passing automated/manual evidence or a clear BLOCKED status; no dead visible controls.

## Prompt 12 — Defect-fix loop and regression discipline

Use the current test failures, logs, and verification reports to fix bugs one by one. Do not perform a broad rewrite. For each defect: (1) reproduce it, (2) identify root cause, (3) create a regression test, (4) apply the smallest fix, (5) run the test, (6) rerun affected suites, (7) review the diff, and (8) update the verification report.

Prioritize wrong-channel risk, accidental public publishing, duplicate uploads, credential leaks, lost schedules, inaccurate status, and duplicate Sheets rows. Run at most three focused fix attempts on a persistent defect; if still unresolved, stop, document evidence and likely cause, and ask for a decision instead of making increasingly risky changes.

Never delete a failing test or weaken an assertion just to make the suite pass. Never mark a blocked live test as passed.

Update `docs/verification/bug-fix-log.md` with issue, root cause, files changed, tests executed, result, and remaining risk.

**Exit gate:** every resolved defect has regression coverage; unresolved defects and blocked tests are explicit.

## Prompt 13 — Algorithm and performance optimization

Only after correctness tests are stable, profile the real code and identify measurable hotspots. Do not optimize by intuition or replace clear code without evidence.

Review event/job claiming, queue polling, upload chunking, file hashing, database queries/indexes, Sheets key reconciliation, retry/backoff, polling cadence, log volume, frontend rerenders, and SSE event delivery. Consider incremental aggregates, bounded queues, batching, indexed lookups, backpressure, and quota-aware scheduling only where measurements justify them.

For each proposed optimization, record baseline metric, workload, expected complexity, trade-offs, and regression risk. Benchmark representative local workloads. Preserve idempotency, ordering, recoverability, privacy, and observable state. Do not create premature microservices, add Redis, or adopt a large framework without a measured need.

Implement one optimization at a time, run the full affected correctness suite, then benchmark again. Revert changes that do not measurably improve the target without offsetting regressions. Write `docs/verification/performance.md`.

**Exit gate:** every retained optimization has before/after measurements and the relevant correctness tests still pass.

## Prompt 14 — Remove dead code, junk files, and unused dependencies

Perform a cautious cleanup audit. Do not delete files simply because they look unused.

Identify duplicate implementations, unreachable modules, abandoned prototypes, unused imports/dependencies, stale assets, commented-out code, debug routes, temporary files, test artifacts, unused configuration, and contradictory documentation. Separate safe removals from uncertain ones.

Preserve PRDs, required reference images, migrations, user content, local databases, credentials, and evidence reports. Do not delete unknown files or reset/clean the working tree destructively. Check imports, entry points, build configuration, dynamic references, packaging, tests, and Git status before removal. Prefer small diffs; create a backup or use version control before destructive cleanup.

Remove only confirmed dead code and unused dependencies. Reinstall/resolve dependencies if applicable, run formatting, lint, type checks, unit/integration tests, frontend build, packaging smoke test, and relevant end-to-end tests. Review the final diff and document every deleted item in `docs/verification/cleanup.md`.

**Exit gate:** clean build and tests pass; no required PRD, asset, migration, user data, or runtime entry point was removed.

## Prompt 15 — Security, privacy, and compliance hardening

Audit loopback binding, Host/Origin validation, CSRF, session cookies, local API access, OAuth callback state, secret storage, log redaction, file-path handling, dependency vulnerabilities, backup contents, privacy-policy consent, API terms links, authorization revocation, authorized-data retention, and account/channel separation.

Verify the approved policy deadlines against current official documentation. Do not rely on old policy assumptions. Check that the local server cannot be reached from the LAN and cannot be abused through DNS rebinding or malicious browser requests. Verify no secrets in the SQLite file, frontend build, logs, screenshots, or diagnostic exports. Test path traversal and malformed API requests.

Fix confirmed high-risk defects first. Do not claim legal compliance solely because a checklist exists; list any owner/legal review still needed. Write `docs/verification/security-and-privacy.md`.

**Exit gate:** critical/high technical security findings are fixed or release-blocking; policy-dependent owner decisions are explicit.

## Prompt 16 — Scalability, concurrency, and fault injection

Stress test the local app with synthetic jobs and test files at realistic and elevated volumes. Do not upload thousands of real videos to test scale.

Measure queue throughput, DB contention, memory/CPU, startup recovery time, event-loop responsiveness, API latency, SSE connection behavior, retry storms, log growth, and Sheets reconciliation cost. Simulate parallel workers, duplicate delivery, 401/403/429/5xx, network disconnect, timeout-after-acceptance, worker kill, disk-full/DB write failure where safe, sleep/resume, expired session, and expired tokens.

Define realistic limits for one Windows PC and one YouTube API project. Test API quota budgeting and avoid unnecessary polling or `videos.update` calls. Use bounded concurrency and backpressure. Document behavior when local capacity or remote quota is exhausted.

Optimize only observed bottlenecks, rerun regression tests, and publish reproducible commands and results in `docs/verification/stress-and-recovery.md`.

**Exit gate:** no duplicate work in tested concurrent claims, no blind retry on ambiguous upload, no unbounded queue/log growth, and all known capacity limits are documented.

## Prompt 17 — Final release-readiness audit

Run the complete test and build suite and inspect all verification reports. Verify installation, first-run setup, OAuth connection, channel confirmation, private test upload, scheduled publication, publication confirmation sweep, Sheets report, restart recovery, worker startup instructions, revoke/retention flow, and uninstall behavior.

Classify each acceptance criterion as PASS, FAIL, BLOCKED, or NOT IMPLEMENTED. Cite test names or actual evidence. Do not infer a PASS from code inspection when live API behavior is needed. Do not call it production-ready while a critical release gate is FAIL or BLOCKED.

Deliver a release report with build/version, environment, exact commands, test counts/results, verified real integrations, known limitations, owner decisions, install/update/uninstall steps, backup/recovery steps, and rollback plan. Include a short list of remaining work, ranked by severity.

**Exit gate:** release decision is explicit: READY, NOT READY, or READY WITH DOCUMENTED LIMITATIONS; never claim more than evidence supports.
