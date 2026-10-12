# MVP PRD — Local YouTube Shorts Scheduler

**Version:** 1.1 (revision 2026-10-11; see the correction log in `README.md`)

## 1. MVP outcome

Deliver a reliable Windows-local workflow for creating, scheduling, uploading and reporting YouTube Shorts across explicitly connected channels. The MVP is successful when a user can schedule a test clip, close the browser, allow the local worker to run, see the video accepted by YouTube for future publication, retrieve the correct URL, observe it publish, and find one correct entry in Google Sheets.

Do not build other social connectors, AI editing, thumbnail generation, or a public cloud backend before this vertical slice is proven.

## 2. MVP assumptions and hard gates

- One Windows PC and one primary operator.
- The frontend is served on localhost; the scheduler/worker is a separate persistent process running as the same interactive user that holds the credentials (`02` §10.2).
- Source media stays on local storage until uploaded to YouTube.
- YouTube Data API and OAuth are the publishing path.
- Each authorized destination channel must be confirmed from the actual API identity. **The credential is the unit of authorization; a channel ID chosen in the UI is never authority** (`02` §7.1).
- Scheduled publication sends `status.privacyStatus=private` with `status.publishAt` in the same `videos.insert` body, and only after the `FEAS-03` real-account test establishes a workable minimum lead time.
- **Unverified API project behaviour is an open question, not a safety guarantee.** Public publishing is treated as blocked until `FEAS-02` has a verdict on the actual project.
- True selected-channel @mention tagging is **not creatable through the documented API**; the `manual_required` workflow is the shipped behaviour, not a fallback to be upgraded later.
- Google Sheets is a report destination, not the source of truth for jobs.

If a gate is blocked, report it early and present the safe fallback before increasing scope.

### 2.1 Proof-of-concept gate — blocks all feature work

The items in `07-feasibility-checklist.md` must each have a written, dated verdict with evidence before the corresponding P0 work begins. `FEAS-01` (multi-channel) and `FEAS-02` (privacy behaviour) block the **entire** product premise and must go first.

## 3. P0 scope

### Setup
- First-run setup wizard.
- **Privacy policy and consent screen, accepted before feature access** (`COMP-01`, `COMP-02`).
- Detect/create local data directory and database.
- Configure timezone and Windows start behavior.
- Configure Google Cloud OAuth credentials from a user-provided client configuration without committing it to source control, using exactly one API project (`COMP-03`).
- Explain YouTube API project verification/audit constraints **as observed on this project** and how to test a real upload (`FEAS-02`).
- Connect YouTube identity; fetch and display the channel identity; require operator confirmation.
- Connect Google Sheets and select spreadsheet/tab through OAuth.

### Media library
- Add one or more local media files via picker.
- Save stable asset ID, path, SHA-256, size, duration, dimensions, and validation results.
- Detect missing or changed files before the job enters upload state.
- Preview title/description and selected source clip reference; no video transcoding, no thumbnail generation, no thumbnail upload.

### Scheduler
- Create/edit/clone/cancel schedule.
- Assign unique schedule ID automatically; allow a custom external reference field but not editing the internal ID.
- Select destination channel, publish time/timezone, title, description, hashtags, API-supported tags, privacy (**default Private**), audience declarations, mention requirement, and upload lead time.
- Calendar (month view plus list) and list/queue views.
- Preflight errors and warnings before queuing, including a hard block on a past publish time.
- Background execution with persistent status after browser is closed.
- Configurable behavior for missed windows.

### Upload worker
- Resumable upload over a first-party HTTP client that persists the session URI (`02` §1).
- Progress and stage display.
- Record API-returned video ID and canonical URL.
- Send `status.publishAt` within the insert request when the validated strategy permits, and verify the remote accepted state (`02` §6.1).
- **PublishConfirmationSweep** to distinguish scheduled from verified-published (`02` §6.3).
- Track processing and publication status separately.
- Retry transient failures within `max_upload_attempts`, honouring `upload_spacing_seconds`; stop on authorization, quota, policy, or invalid-input failures.
- Reconcile ambiguous upload outcomes via the `02` §6.2 ladder; never automatically create a second upload.

### Reporting
- Daily report time and timezone.
- End-of-day report of terminal jobs, defaulting to successful jobs and configurable failed/needs-review jobs.
- Required fields: schedule ID, content title, channel, target/actual time, video ID, URL, job status, mention status and errors.
- **Mandatory non-optional `Sync Key` column** with read-before-append deduplication (`02` §8.1).
- Local report outbox and sync ledger.
- Retry and reconciliation screen.
- Preview rows before first live write.

### Admin and operations
- Queue, job detail, logs safe for user viewing, retry, cancel if safe, needs-review resolution.
- Connection health and last authorization check.
- **Disable and Revoke as separate, separately-labelled actions** (`02` §7.2).
- Local database backup/export and restore guidance.
- Test mode with fake connectors disabled from production destinations.
- Retention sweep for Authorized Data, and an inactivity heartbeat (`02` §11.1, `COMP-09`).

## 4. P1/P2 excluded from the MVP

P1: CSV bulk import, folder watcher, reusable templates, channel presets, configurable upload windows, per-job immediate Sheets logging, week/day calendar views, drag-to-reschedule.

P2: TikTok/Instagram/Facebook connectors, cloud sync, multi-user access, multi-PC worker coordination, AI-generated descriptions, advanced channel analytics, remote control.

**Explicitly deferred from P0 despite appearing in the visual references:** thumbnail upload, automatic thumbnail generation, change-thumbnail controls, third-party platform tabs or icons, "Coming Soon" platform affordances, optional/toggleable identity columns in the Sheets report, manual spreadsheet-URL entry as the primary connection path, and plugin-style platform abstraction. See `05-visual-uiux-prd.md` §1.2.

## 5. MVP user journeys

1. **First run:** start local app, configure timezone, OAuth client details, data location, worker startup, and Sheets destination.
2. **Connect channel:** authorize through browser, confirm returned channel title/ID, and save connection securely.
3. **Create schedule:** select local file, preview metadata, enter publish time, select a channel, pass preflight checks and queue.
4. **Scheduled upload:** worker claims job before publish time, transfers via resumable upload, stores video ID, and schedules publication through YouTube if supported.
5. **Failure:** worker classifies error, applies bounded retry where safe, shows next action and preserves evidence.
6. **Daily report:** report outbox is assembled at cutoff; write is deduplicated; success/failure is visible.
7. **Restart:** close UI/restart Windows, reopen dashboard; queue and job state reconcile from SQLite.

## 6. Acceptance criteria

### AC-MVP-001: Local-only UI
Given the app is installed, when the backend starts, then the UI is reachable only on loopback by default and the upload worker runs without an open browser tab, started through the documented interactive-user mechanism and verified by `FEAS-08`.

### AC-MVP-002: Channel confirmation
Given a new YouTube authorization, when the API identity is read, then the UI shows the authorized channel name and ID and requires explicit confirmation before that connection can be targeted by production jobs. A channel ID supplied by the client is never treated as authority; the stored channel is re-verified against the API before the first production upload.

### AC-MVP-003: Schedule validation
Given a job with a missing file, invalid title or past publication time, when the user queues it, then the app blocks it or requires an explicit supported correction; it must not silently publish immediately. Privacy defaults to Private and an explicit privacy value is always sent.

### AC-MVP-004: Real upload
Given a valid test job and authorized test channel, when the worker runs, then a real upload occurs through the documented API and the returned video ID is stored against the original schedule ID.

### AC-MVP-005: Scheduled publication
Given a valid future time and eligible channel/API configuration, when the upload succeeds, then the app sends `publishAt` with the insert request, verifies that YouTube accepted the intended publication timestamp, stores the UTC publish time, and distinguishes platform-scheduled state from local-queue state.

### AC-MVP-006: Safe ambiguous result
Given the worker loses its connection after an upload request, when it restarts, then it follows the reconciliation ladder (`02` §6.2) or pauses for review; it does not blindly send a second `videos.insert` request.

### AC-MVP-007: URL
Given a confirmed YouTube video ID, when a successful upload is recorded, then the job detail and report include the canonical video URL built from that ID and confirmed against the API response where applicable.

### AC-MVP-008: Google Sheets
Given terminal jobs for the reporting day, when the report runs twice or a network timeout occurs, then each `Sync Key` appears at most once and the local outbox shows unresolved syncs. The `Schedule ID` and `Sync Key` columns are present and cannot be disabled.

### AC-MVP-009: Honest mention state
Given a caption asks for a real channel mention, when the system cannot create or verify a selected-channel mention by a documented API mechanism — which is the documented state of the platform — then it marks the job `manual_required`, holds it before publication, and does not claim the mention is verified.

### AC-MVP-010: Restart recovery
Given a queued or uploading job, when the browser or worker closes, or the machine sleeps and resumes, then the durable job record survives and the worker resumes or reconciles it on next startup.

### AC-MVP-011: Credential protection
Given stored OAuth refresh tokens, when database contents, frontend assets and ordinary logs are inspected by an automated scan, then no token is present outside the approved OS-backed secure store.

### AC-MVP-012: Test mode isolation
Given test mode is enabled, when test events are generated, then no real YouTube video can be uploaded and no production Sheets report can be polluted unless the operator explicitly runs a separate labelled integration test. Test mode is bound by dependency injection at start-up, not by a runtime flag inside production paths.

### AC-MVP-013: Publication verification
Given a job whose publish time has passed, when the PublishConfirmationSweep runs, then the job shows "Published (verified)" only if the remote status was observed as public. If the app was offline, the job shows "publication awaiting verification" until a poll confirms it, and no job is labelled published on the basis of time alone.

### AC-MVP-014: Revocation and retention
Given a revoked connection, when the operator completes revocation, then the token is deleted from the secret store, the revocation is sent to Google, and stored Authorized Data is purged within the approved deadline, with local bookkeeping retained and the purge recorded in an audit trail.

### AC-MVP-015: Consent gate
Given a fresh install, when the user first opens the app, then feature access is blocked until the privacy policy is presented and accepted, and the accepted policy version is recorded.

## 7. Release gates

- Clean install, update, backup and restore tested on Windows.
- Physical end-to-end test: OAuth → channel confirmation → resumable upload → insert-time schedule → YouTube publish → video ID/URL → Sheets row.
- **Every `FEAS-*` item in `07-feasibility-checklist.md` has a written verdict and its evidence is stored.**
- Channel identity re-confirmed for every stored connection.
- Observed unverified-project privacy behaviour documented for this project, replacing the revision 1.0 general claim.
- Mention capability documented with evidence, including whether a Studio-created mention survives a later metadata update.
- Quota and API-project constraints documented.
- Retry, crash, network drop, OAuth revoke, timezone, past-time, and duplicate-report tests pass.
- No critical security defects and no secrets in logs, bundles, or source control — verified by automated scan.
- Compliance items `COMP-01` through `COMP-10` implemented or explicitly waived by the owner with a recorded reason.

## 8. MVP launch checklist

- [ ] Google Cloud project and enabled APIs configured (single project, `COMP-03`).
- [ ] OAuth consent/publishing status decided (`D-2`); test users configured if in Testing status.
- [ ] YouTube API project verification/restrictions checked and observed behaviour recorded (`FEAS-02`).
- [ ] Multi-channel/brand-account verdict recorded (`FEAS-01`) and product wording matches it.
- [ ] Minimum scheduling lead time measured (`FEAS-03`) and defaults set from it.
- [ ] Test channel authorized and verified.
- [ ] Future publish time tested successfully end to end, including observed publication.
- [ ] Ambiguous-upload reconciliation tested (`FEAS-04`, `FEAS-05`).
- [ ] Google Sheet test-write and dedupe validated.
- [ ] Mention requirement tested and the `manual_required` workflow confirmed.
- [ ] Worker starts after restart as the interactive user (`FEAS-08`).
- [ ] Privacy policy accepted; revocation and retention purge exercised.
- [ ] Operator has a recovery/runbook document.
