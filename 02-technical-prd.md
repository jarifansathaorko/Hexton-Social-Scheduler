# Technical PRD — Local YouTube Shorts Scheduler

**Version:** 1.1 (revision 2026-10-11; see the correction log in `README.md`)  
**Target environment:** Windows 10/11 x64, local-first  
**Initial connector:** YouTube Data API  
**UI:** Localhost web application  

## 1. Architecture decision

Use a modular local application with a persistent backend worker. Proposed baseline:

- **Backend/API:** Python with FastAPI and Pydantic models.
- **Worker/scheduler:** A separate long-running Python worker process claiming due jobs from a database-backed queue. **The database is the source of scheduled jobs.** Do not introduce an external scheduler library, message broker or cache: on a single PC a short polling loop over an indexed `next_attempt_at` column is simpler to reason about and more recoverable than an in-memory timer or an out-of-process queue.
- **Frontend:** React + TypeScript + Vite, built and served by the backend from the same origin so the app is same-origin by construction.
- **Persistence:** SQLite with migrations and explicit transactions, running in **WAL** mode (see §2.1). SQLAlchemy or an equivalent well-maintained data layer.
- **Platform clients:** Google API Python client libraries for the cheap JSON calls (identity, status, metadata update, Sheets). **Resumable upload is implemented directly over an HTTP client** following the documented resumable protocol, because the high-level client's chunked upload hides the resumable session URI and therefore cannot satisfy the persistence and restart requirements in `AC-MVP-006` and `AC-MVP-010`.
- **Windows integration:** Startup via Windows Task Scheduler, a tray indicator, or both, so the worker can start at logon/boot with clear user configuration. **It must run as the interactive user who authorized the accounts** (see §10.2). Do not require the browser tab to stay open.
- **Secrets:** Windows Credential Manager as the primary store, with a DPAPI-backed option. Never store refresh tokens as plaintext in SQLite or frontend storage. SQLite holds only an opaque reference.
- **Time zones:** `tzdata` is a **required** runtime dependency. CPython on Windows ships no system IANA time zone database, and `zoneinfo` lookups fail without it (see §10.1).
- **Packaging:** Reproducible local installer or documented packaged build. Keep source/developer mode available.

Validate package versions, library maintenance, current Google API compatibility, and Windows startup behavior before choosing exact versions. The frontend must never hold Google refresh tokens or call write-capable APIs directly.

### 1.1 Decisions that differ from a naive reading of the platform

| Decision | Rationale |
|---|---|
| `status.publishAt` is sent in the `videos.insert` body, not in a follow-up `videos.update` | `publishAt` is a documented settable field on insert. This removes the failure window in which a video exists as private with no schedule, and avoids a second call. See §6.1. |
| The resumable uploader is first-party HTTP rather than a client-library helper | The library's `next_chunk()` does not expose or persist the resumable session URI, which is the exact state required to recover an interrupted upload across a process restart. |
| A credential maps to exactly one channel | `videos.insert` publishes to the channel resolved from the authorizing credential; there is no documented parameter that redirects an upload to an arbitrary channel ID. See §7.1. |
| Ambiguous upload outcomes are never auto-retried | An insert whose response was lost cannot be proven to have failed. Reconciliation is attempted (§6.2); anything unresolved goes to `JOB_NEEDS_REVIEW`. |

## 2. Process layout

1. **Local UI** — schedules, channel connections, media library, reports, settings, job history.
2. **Local API** — validation, auth initiation/callback, DB operations, job controls, status/events, diagnostics.
3. **Scheduler/worker** — claims due tasks, uploads files, queries platform state, retries safe operations, publishes results to the DB.
4. **Connector layer** — `YouTubeConnector` initially; future connectors implement a common interface with explicit capabilities (§9).
5. **Report adapter** — Google Sheets output with an idempotent sync ledger.
6. **Publish confirmation sweep** — a recurring worker task that polls remote state so publication can be verified rather than assumed (§6.3).
7. **Compliance tasks** — token-freshness and inactivity heartbeat, Authorized Data retention sweep, consent state enforcement (§11).
8. **SQLite database** — schedules, media metadata, channels, jobs, upload sessions, event log, report sync ledger, settings.

Bind by default to `127.0.0.1` only. Do not expose the dashboard to the LAN or internet. Implement CSRF/session protections, a strict `Host` allowlist, `Origin` checks to reduce DNS-rebinding risk, and same-origin serving of the built frontend. Choose **one** local authentication mechanism — a per-install session cookie is sufficient given same-origin serving — and do not also build a parallel shared-secret scheme.

### 2.1 SQLite concurrency model

Two processes (API and worker) share one database file.

- Enable **WAL** journaling. Under the default rollback journal, a long write transaction blocks readers and the UI stalls while the worker commits.
- Set a `busy_timeout` so a transient lock waits rather than immediately raising.
- All job claiming uses `BEGIN IMMEDIATE` (write transaction acquired up front). Claiming under a deferred transaction that upgrades later can fail with `SQLITE_BUSY` after the claim has already been decided.
- Claim pattern: select eligible job IDs, then update those rows with a lease holder and expiry, in one write transaction, returning the rows actually updated. A job with an expired lease is eligible again.
- Keep write transactions short. Never hold a transaction open across an HTTP call to Google; the claim is committed **before** the external side effect and the result is recorded in a second transaction (§4.4).

## 3. Domain model

Minimum entities:

- `MediaAsset`: asset_id, path, file_size, modified_at, sha256, duration, width, height, mime_type, validation_state.
- `PlatformConnection`: connection_id, platform, provider_account_label, **channel_id (read from the API for this credential, never supplied by the UI)**, channel_title, channel_handle, credential_ref (opaque pointer to the secret store), scopes, granted_scopes_at_connect, connected_at, last_verified_at, state (`active` / `disabled` / `revoked`), is_default, `authorized_data_refreshed_at`, `consent_revoked_at`.
- `Schedule`: schedule_id (stable unique identifier), asset_id, platform, connection_id, title, description_template, resolved_description, platform_tags, hashtags, mention_requests, mention_state, privacy, audience_declaration, target_publish_at_utc, display_timezone, utc_offset_at_target, preupload_lead_minutes, retry_policy, requires_mention_confirmation, report_included, created_at, updated_at, version.
- `PublishJob`: job_id, schedule_id, **state (see §4)**, attempt_number, next_attempt_at, claimed_by, lease_until, upload_session_uri_ref, session_started_at, bytes_uploaded, total_bytes, platform_video_id, platform_url, started_at, upload_completed_at, publish_accepted_at, publish_verified_at, last_error_code, last_error_message, review_reason, next_action.
- `RemoteVideoState`: job_id (1:1), upload_status, privacy_status, publish_at_utc, processing_status, published_at_utc, failure_reason, last_polled_at, poll_result (`confirmed` / `not_yet` / `unknown`).
- `JobEvent`: event_id, job_id, timestamp_utc, severity, type, safe_message, structured_context_redacted.
- `SheetConfig`: spreadsheet_id, tab/range, report_timezone, cutoff, include_statuses, enabled.
- `SheetSyncRecord`: **sync_key** (primary idempotency identity, see §9), schedule_id, report_date, schema_version, state, synced_at, attempts, last_error.
- `AppSettings`: timezone, startup behavior, worker concurrency, **max_upload_attempts**, **retry_backoff_base_seconds**, **retry_backoff_max_seconds**, **upload_spacing_seconds** (delay between consecutive uploads on the same connection), log_level, retention settings, feature flags.
- `ConsentRecord`: scope, granted_at, withdrawn_at, policy_version.
- `ApiQuotaLedger`: method, units_consumed, bucket, window_start, observed_at.

Use foreign keys, unique IDs, indexes for due jobs and history search, migration scripts, and UTC timestamps. Store the intended wall-clock timezone and the resolved UTC offset for auditing. File paths must be validated and cannot point outside permitted folders unless the user explicitly adds them.

Stored values that originate from YouTube (channel titles, video titles, descriptions, subscriber counts, thumbnails) are **Authorized Data** and carry a `*_fetched_at` column so the retention sweep in §11.1 can refresh or purge them. Locally created values (schedule ID, job state, timestamps, the video ID returned by our own upload) are bookkeeping and are retained per the local retention setting.

## 4. State model

**One canonical local state enum, plus one separate remote-state record.** Revision 1.0 of this PRD specified a schedule state machine, a separate job state machine, a third status list in the feature PRD, and a fourth list of UI labels, with no mapping between them. That has been replaced by a single source of truth.

### 4.1 `JOB-*` canonical local states

| ID | State | Meaning | Terminal |
|---|---|---|---|
| `JOB-DRAFT` | Draft | Editable, not validated | No |
| `JOB-READY` | Ready to queue | Validated, awaiting queue action | No |
| `JOB-QUEUED` | Queued locally | Waiting for the worker's upload window | No |
| `JOB-UPLOADING` | Uploading | Media transfer in progress | No |
| `JOB-UPLOADED` | Uploaded — processing | Insert accepted, video ID known, remote processing not yet confirmed | No |
| `JOB_SCHEDULED` | Scheduled on YouTube | Remote has accepted a future publication time | No |
| `JOB_PUBLISHED` | Published (verified) | Remote status observed as public | Yes |
| `JOB_FAILED_RETRY` | Retry waiting | Transient failure, next attempt scheduled | No |
| `JOB_BLOCKED` | Blocked | Auth, quota or policy problem; no automatic progress | No |
| `JOB_MISSED_WINDOW` | Missed window | Upload window elapsed while offline; recovery policy applies | No |
| `JOB_NEEDS_REVIEW` | Needs review — outcome uncertain | An insert may have succeeded and cannot be resolved automatically | No |
| `JOB_CANCELLED` | Cancelled | Withdrawn by the operator | Yes |
| `JOB_FAILED` | Failed | Permanent failure after retries exhausted | Yes |

Notes:

- `JOB-QUEUED` is a **local** state only. It must never be displayed with wording that implies YouTube has accepted anything.
- `JOB_SCHEDULED` requires remote evidence: a `RemoteVideoState.publish_at_utc` that matches the intended time.
- `JOB_PUBLISHED` requires an observed `privacy_status` of `public` from `RemoteVideoState`. Time alone never satisfies it (§6.3).
- `JOB_NEEDS_REVIEW` has no automatic exit. Resolution is always an explicit operator decision, and that decision is recorded as a `JobEvent`.

### 4.2 Remote state is a separate record

`RemoteVideoState` (§3) holds `upload_status`, `privacy_status`, `publish_at_utc`, `processing_status` and `published_at_utc` as observed from the API, with `last_polled_at`. Local state and remote state are never merged into one column. A single video can be `JOB_SCHEDULED` locally while its remote `upload_status` is still `uploaded` and unprocessed; both facts must be displayable at once.

### 4.3 Internal state to UI label mapping

This is the **only** mapping. `05-visual-uiux-prd.md` renders these labels verbatim and must not introduce additional states.

| Internal state | UI label | Icon/shape cue | Never implies |
|---|---|---|---|
| `JOB-DRAFT` | Draft | Grey outline | Validation passed |
| `JOB-READY` | Ready to queue | Grey outline | Queued or uploaded |
| `JOB-QUEUED` | Queued locally | Blue outline | YouTube has accepted a schedule |
| `JOB-UPLOADING` | Uploading (n%) | Blue progress | Publication is imminent |
| `JOB-UPLOADED` | Uploaded — processing | Blue filled | Published |
| `JOB_SCHEDULED` | Scheduled on YouTube | Green outline, clock | Already public |
| `JOB_PUBLISHED` | Published (verified) | Green filled | — |
| `JOB_FAILED_RETRY` | Retry waiting | Amber | Permanently failed |
| `JOB_BLOCKED` | Authorization required / Quota blocked / Policy blocked | Amber, explicit reason | Recoverable automatically |
| `JOB_MISSED_WINDOW` | Missed window — action required | Amber | Silently published late |
| `JOB_NEEDS_REVIEW` | Needs review — upload outcome uncertain | Red warning | Retrying is safe |
| `JOB_CANCELLED` | Cancelled | Grey struck | Deleted from YouTube |
| `JOB_FAILED` | Failed | Red filled | — |

Additional display conditions layered on top of a state, never replacing one:

- `RemoteVideoState.poll_result == 'unknown'` and the publish time has passed → suffix the label with "— publication awaiting verification".
- `MentionState == 'manual_required'` → an explicit mention chip, independent of job state.

### 4.4 Worker claim model

Use atomic DB transactions to claim a due job and create a lease (§2.1). Recover expired leases after crash. Record the job attempt **before** any external side effect. Maintain operation checkpoints. Retry transient HTTP/network failures with exponential backoff and jitter, bounded by `max_upload_attempts` and capped by `retry_backoff_max_seconds`. Do not retry permanent metadata, OAuth, quota, account, or policy errors automatically.

An upload timeout may occur after YouTube accepted the media. Because an insert provides no application-level idempotency, use the persisted resumable session state and the reconciliation ladder in §6.2. If the outcome cannot be determined safely, transition to `JOB_NEEDS_REVIEW`; do not issue a second `videos.insert`.

## 5. Scheduling and publish timing

Default strategy: send `status.privacyStatus=private` together with `status.publishAt` set to the requested future time **in the same `videos.insert` body**, when the validated scheduling strategy permits it (§6.1). This delegates the actual scheduled publication to YouTube once the API accepts the schedule, so the Windows app need not stay awake at the exact publication instant. Make pre-upload lead time configurable and show whether a job is only locally queued or is already scheduled on YouTube.

Store UTC internally, display in the configured local timezone, and handle daylight-saving/timezone changes explicitly. **Reject any target time in the past**, or require a deliberate, separately-labelled confirmation for immediate publication; never accept it silently. If the computer is offline at the required upload window, mark `JOB_MISSED_WINDOW` and apply a user-configured recovery policy: upload ASAP and schedule if still possible, reschedule with approval, or skip. Never silently publish late.

If a target publish time is already in the past when sent to YouTube, the API publishes the video immediately, which is equivalent to changing `privacyStatus` from private to public. Preflight must prevent this. See the official video resource documentation.

### 5.1 Consequences of delegating publication

Delegation is the right architecture for a PC that may be off at the publish instant, but it has an honest cost: once YouTube accepts the schedule, the app has no way to know whether publication actually happened unless it asks. Two consequences follow, and both are MVP requirements:

1. Nothing may be labelled "Published" without an observed remote status (§6.3).
2. A job whose publish time passed while the app was offline reports as *publication awaiting verification* until a poll confirms it. This is a distinct, non-erroneous display condition.

### 5.2 Reschedule and cancel limits

`publishAt` can only be set on a video that **has never been published**, and only while `privacyStatus` is private. Therefore:

- Rescheduling is available for `JOB_SCHEDULED` jobs only.
- A published video cannot be rescheduled or returned to a scheduled state through the documented API. The UI must not offer the action for such jobs.
- A cancel of a scheduled job sets `privacyStatus=private` and clears `publishAt`; this must be verified against the remote resource, not assumed.
- Any `videos.update` this app issues must **re-assert `status.privacyStatus`**, as the documentation requires even when the video is already private. Centralize every update in one connector method so this cannot be forgotten.

### 5.3 Timezone handling on Windows

All IANA zones used by the UI and reports require the `tzdata` package (§10.1). Store `target_publish_at_utc`, the user's `display_timezone`, and the `utc_offset_at_target` resolved at scheduling time so a later display-timezone change does not silently reinterpret a stored schedule. A schedule that lands inside a daylight-saving spring-forward gap must be flagged at validation time rather than silently shifted.

## 6. YouTube connector requirements

Use documented APIs and OAuth 2.0 user authorization. Do not request a user's Google password or rely on a service account for ordinary personal channels. The YouTube Data API documents the service-account flow and the `onBehalfOfContentOwner` / `onBehalfOfContentOwnerChannel` parameters as reserved for YouTube Content ID partners; neither is a general multi-channel mechanism.

The user may connect multiple authorized identities. After each authorization, query the API to confirm the channel identity available to that credential, store it, and have the operator explicitly confirm it before that connection can be targeted by any job. See §7.1 for the resulting channel-authority rule.

Implement:
- `list_authorized_channels()` / connection verification.
- `validate_upload_metadata()`.
- `begin_resumable_upload()` / resumable upload flow.
- `get_video_status(video_id)`.
- `schedule_publish(video_id, publish_at)` only where allowed.
- `get_video_url(video_id)`.
- Safe error classification for retryable, auth, quota, permission, invalid metadata, processing and policy failures.

Use resumable uploads for reliable transfer and persist enough state to recover. Capture API responses with sensitive headers/tokens redacted. Apply configurable conservative concurrency, likely one upload per connection initially, a configurable `upload_spacing_seconds` delay between consecutive uploads on the same connection, and quota-aware backoff.

### 6.1 Scheduling in the insert request

`status.publishAt` is listed among the properties settable by `videos.insert`. When the validated scheduling strategy permits it — target time in the future, privacy Private, video never previously published — send it in the same request body as the media, together with an explicit `status.privacyStatus=private`.

Constraints enforced before the request is sent, rather than discovered from an error:

- `privacyStatus` must be `private`. A past `publishAt` publishes immediately.
- The video must never have been published before (§5.2).
- **Real-account testing is required before production use.** The minimum accepted lead time, and whether remote processing must complete before a schedule is accepted, are not documented. Until `FEAS-03` has a written verdict, preflight enforces a conservative configured minimum lead time and classifies any rejection rather than silently falling back.

Retain `videos.update` for reschedule, cancel, and later metadata edits, always re-asserting `privacyStatus` (§5.2).

### 6.2 Ambiguous and interrupted uploads — reconciliation ladder

A `videos.insert` may be accepted by YouTube while the client never receives the response, or the process may die mid-transfer. `videos.list` accepts only `id`, `myRating` and `chart` (which supports only `mostPopular`), so there is **no** documented call that enumerates "my recent uploads". Reconciliation therefore proceeds in a fixed order, stopping at the first conclusive answer.

| Step | Action | Outcome |
|---|---|---|
| R1 | Probe the persisted resumable session URI: `PUT` with `Content-Range: bytes */<total>` and an empty body | `201` → video ID recovered. `308` → resume at the `Range` offset. `404` → session expired; go to R2. |
| R2 | `activities.list(part=snippet,contentDetails, channelId=<destination>, publishedAfter=<attempt started at>)` | Upload activities appearing after the attempt began. Activity-feed latency is not documented (`FEAS-05`). |
| R3 | `channels.list(part=contentDetails)` → `contentDetails.relatedPlaylists.uploads`, then `playlistItems.list` for the most recent items | The channel's actual most recent uploads, independent of activity-feed lag |
| R4 | Operator resolution | `JOB_NEEDS_REVIEW` with candidates and evidence attached to the job detail |

Rules:

- **Never issue a second `videos.insert` for a job whose previous insert may have succeeded.** Automatic retry is permitted only when the previous attempt provably never reached acceptance — for example a rejected metadata request, or an expired session before any bytes were committed **and** R2/R3 showing no candidate.
- R2 and R3 are evidence, not proof. A video appearing at that time may have been an unrelated manual Studio upload, so a candidate is offered for operator confirmation rather than adopted automatically.
- Every reconciliation attempt writes a `JobEvent` recording the step and result, so the decision is auditable.
- Session URIs have a finite lifetime; on `404` the upload must restart from byte zero, which is exactly the situation that can produce a duplicate. Treat it as a review candidate unless R2/R3 are conclusive.

### 6.3 PublishConfirmationSweep

A recurring worker task, independent of the upload path, that makes publication verifiable rather than assumed.

- Trigger: periodically, and on worker start for any `JOB_SCHEDULED` job whose `publish_at_utc` is in the past, plus any job already recorded as awaiting verification.
- Call: `videos.list(part=status,id=<video id>)` — documented and low cost (1 unit).
- Update `RemoteVideoState`: `privacy_status`, `publish_at_utc`, `upload_status`, `published_at_utc`, `last_polled_at`.
- Transition: `privacy_status == public` → `JOB_PUBLISHED`, setting `publish_verified_at`. `publish_at_utc` still in the future → remain `JOB_SCHEDULED`. Publish time passed but the state is not yet conclusive → `JOB_SCHEDULED` with the *publication awaiting verification* display suffix (§4.3).
- If the connection's token is no longer valid, record the reason and leave the job unverified rather than assuming success.
- The sweep tolerates the PC being off: it runs again on next start and back-fills jobs whose publication time passed meanwhile.

This is what separates "we sent a request", "YouTube accepted a schedule", "YouTube published it", and "we verified it published".

### 6.4 YouTube Shorts validation

Provide a preflight checklist for file readability, duration, orientation/aspect ratio, resolution, title/description limits, file size, and codec metadata as supported by the current API and Shorts requirements. Do not hardcode mutable Shorts qualification rules without a documented version/reference and tests. Warn instead of transcoding unless a reliable, separately scoped media-processing module is approved.

Metadata transformation rules (see also `COMP-06`):

- The title limit is 100 characters and an API Client must not impose a shorter limit. If an optional automatic transformation would push a title past that limit, **do not truncate the user's text**; block the transformation and tell the operator.
- The description limit is 5000 bytes. The tag limit is 500 characters across the whole tag list, with quoting rules that make a space-containing tag count as longer than it appears.

### 6.5 Channel mention limitation

The documented `videos` resource exposes `snippet.title`, `snippet.description`, `snippet.tags[]`, `localizations` and `status`, and contains **no mention entity**. YouTube's creator help describes mentions as typing `@` and choosing a channel from a suggested list — a user-interface affordance with no documented API counterpart.

Accordingly, genuine creator mentions **cannot be created or verified through the documented API**. The system must:

- Never claim a mention succeeded because an `@handle` string was present in the title or description.
- Expose `MentionState` as `not_requested` / `text_only` / `manual_required` / `verified`, where `verified` is reachable only through an explicitly demonstrated, documented mechanism.
- For jobs requiring a real mention, upload as Private and hold the job at `manual_required`, present a guided YouTube Studio step, and require operator confirmation before the schedule proceeds. Do not set a publish time that would make the video public before that step completes.
- Verify (`FEAS-06`) that a Studio-created mention survives any later `videos.update` this app performs. If it does not, the app must avoid post-upload metadata edits on such jobs and the limitation must be disclosed.

Do not use hidden endpoints, cookie replay, or unauthorized browser automation.

### 6.6 API project restrictions — an unresolved platform question

The behaviour of uploads from an **unverified** YouTube API project is currently **unknown**, because Google's live documentation contradicts itself: the video resource page states such uploads are set to private by default and require an audit to become public, while the `videos.insert` `status.privacyStatus` parameter states that videos uploaded from unverified API projects are not restricted to private viewing mode.

This PRD therefore asserts neither as fact and imposes hard rules instead:

- **Always send an explicit `status.privacyStatus`.** Never rely on a server-side default.
- **Default privacy is Private** (`01` §7).
- Treat public publishing from an unverified project as **blocked** until `FEAS-02` has a written verdict on the actual API project.
- Surface the project's verification and audit state during onboarding, and state the observed behaviour rather than a general claim.

Setup diagnostics must report the verification state as a fact about this project, and the release documentation must carry the `FEAS-02` result.

## 7. OAuth security

Use least-privilege scopes. Separate YouTube connection scopes from Google Sheets report scopes and show a clear explanation for each. YouTube verification requires read access (`channels.list`), so the read/write YouTube scope is the correct minimum for a publishing client; it is still narrower than `youtube.force-ssl`. Use the system browser for OAuth and a supported desktop/loopback flow for Windows. Validate OAuth `state`, redirect URI, and authorization responses. Store refresh tokens in Windows-backed secure storage; encrypt/redact local backups; support the separate disable and revoke actions defined in §7.2, and handle token refresh failures visibly.

Do not assume test consent grants long-lived access: Google documents that authorizations **and** refresh tokens issued to external apps in **Testing** status expire after seven days. Document the Cloud Console publishing/verification steps, test-user constraints, and recovery when tokens expire, and treat the real reauthorization burden under a production-status project as an open question (`FEAS-07`). Do not request broad Drive access if a narrower Sheets/Drive-file scope meets the feature requirements.

### 7.1 Channel authority — the credential is the unit of authorization

`videos.insert` publishes to the channel resolved from the authorizing credential. There is no documented parameter that redirects an upload to an arbitrary channel ID for an ordinary user; the content-partner parameters are not available here. Consequences:

- The UI may **select** a channel, but only from the set of channels this app has already connected and confirmed. A channel ID arriving from the client is never trusted as authority.
- On connect, and before the first production upload on a connection, call `channels.list(mine=true)` and compare the returned channel ID with the stored one. A mismatch blocks the upload and surfaces `JOB_BLOCKED` with an explicit reason.
- Re-verify when `last_verified_at` is older than a configured interval, and always after a reauthorization.
- Display the channel title and ID — not merely the account email — everywhere a destination is chosen (`05` §6).

### 7.2 Disable versus revoke

These are separate operations with different consequences and must never share one button.

| | **Disable** | **Revoke Google access** |
|---|---|---|
| Trigger | Operator hides a connection from selection | Operator removes Google authorization |
| Google call | None | Call the token revocation endpoint immediately |
| Stored token | Retained in the secret store | **Deleted** from the secret store |
| Job history | Retained | Retained as local bookkeeping (schedule ID, state, timestamps, video ID) |
| Authorized Data | Retained under the retention policy | **Purged**, as the Terms of Service require within a defined deadline |
| Reversible | Yes | No |

Revocation must be implemented, not merely described: it calls Google's revocation endpoint, deletes the credential, records `consent_revoked_at`, and enqueues the Authorized Data purge described in §11.1. The purge deadline and the exact data classes are a compliance decision recorded in §11; they must be settled before release.

### 7.3 Multi-channel scope reality

Multiple channels are supported by connecting multiple authorized identities, one channel each. Whether a single Google identity that manages a **brand account** can address that brand channel through the API is **not documented** and is `FEAS-01`. Until that verdict exists, no screen may imply that one Google account publishes to several channels. If `FEAS-01` returns a negative result, the truthful fallback is stated plainly in the UI and in onboarding: one connected identity publishes to one channel, and a brand channel must be connected with an identity that can publish to it.

## 8. Google Sheets connector

Use the official Sheets API. User selects a spreadsheet and tab through the OAuth connection; support a test-write action and report preview. Prefer the narrowest appropriate scope. Never put sheet credentials in the browser. Maintain a local outbox so temporary Sheets failures do not block YouTube uploads.

At cutoff, generate rows from locally authoritative terminal job records. A report run must have a stable ID, deterministic date boundaries, schema version and local sync ledger.

### 8.1 Idempotency mechanism

`spreadsheets.values.append` is **not** an idempotency guarantee; a retried append after a timeout duplicates rows. The mechanism is a deterministic sync key plus read-before-append:

1. Every row carries `Sync Key = SCH_{report_date}_{schedule_id}_{schema_version}` in a dedicated column.
2. Before appending, read the existing `Sync Key` column for the target range and compute the set difference against the rows to be written.
3. Append only the missing keys, with `valueInputOption=RAW`.
4. On any timeout or 5xx, **do not retry blindly.** Re-run step 2; the read is the reconciliation.
5. Mark a key synced in `SheetSyncRecord` only after a confirmed success response.

**The `Sync Key` and `Schedule ID` columns are mandatory and not user-configurable.** Optional column selection may apply to presentation columns only; allowing an identity column to be disabled would break deduplication and fail `AC-MVP-008`. The UI must present these two columns as required, not as optional checkboxes.

### 8.2 Report configuration

The spreadsheet is selected through OAuth (a file picker backed by the narrow Drive file scope). If an operator pastes a spreadsheet URL, it must be parsed to extract the spreadsheet ID and validated against the Sheets API before use; the raw URL is not stored as configuration.

Recommended columns: Report Date, Schedule ID, Sync Key, Platform, Channel Name, Channel ID, Content Title, Source Filename, Planned Publish Time, Actual Upload Time, Publish Status, YouTube Video ID, Content URL, Mention Status, Result, Error Code, Error Summary, Last Updated.

## 9. Future connector interface

Define a **narrow** platform-neutral interface. Revision 1.0 specified nine methods and a full capability matrix for connectors that do not exist in the MVP; that is deferred. The MVP ships two implementations of a small contract:

- `capabilities() -> Capabilities` — a fixed capability record describing what this connector supports (scheduled publishing, tags, privacy levels, audience declaration, true mentions, resumable upload, remote status polling).
- `verify_identity() -> ChannelIdentity` — resolve and return the channel this credential addresses.
- `validate_media_and_metadata(job) -> list[ValidationIssue]`
- `upload(job, progress_callback) -> UploadResult` — includes the video/external ID and the canonical URL.
- `fetch_remote_state(external_id) -> RemoteVideoState`

Plus a shared, platform-neutral error classifier and a shared `FakeConnector` used by tests and by test mode.

Expose `Capabilities` per connector instead of assuming all platforms support identical scheduling, mentions, metadata, privacy options, or upload APIs. The UI must render a capability-gated control: a feature a connector does not support is disabled with an explanation, never silently omitted and never presented as working. A new connector must not break YouTube behaviour.

**Deferred to P1/P2:** a plugin registry, runtime connector discovery, per-connector capability configuration screens, and a platform-neutral metadata schema broader than what YouTube needs. Building these during the MVP produces the "Coming Soon" platform tabs seen in the reference designs, which contradict `01` §4 and risk implying affiliation under the YouTube Branding Guidelines.

## 10. Security, privacy and Windows platform requirements

### 10.1 Time zone data

CPython on Windows ships **no system IANA time zone database**. `zoneinfo.ZoneInfo("Asia/Dhaka")` raises `ZoneInfoNotFoundError` on a stock Windows install without the `tzdata` package. Because the product stores and displays IANA zones (§5.3), `tzdata` is a **required runtime dependency**, not an optional extra, and the packaging step must install it into the bundled application. Every timezone test must run against it; a passing suite without it proves nothing about Windows.

### 10.2 Credential storage and the interactive-user constraint

Tokens are stored in **Windows Credential Manager** as generic credentials keyed by connection ID, with only an opaque reference in SQLite. A DPAPI-protected encrypted blob is the documented fallback. Consequences:

- Because the OS secret store and user-scoped DPAPI are keyed to a Windows user account, **the API and the worker must run as the same interactive user who completed the OAuth flow.**
- Therefore the background worker is started by **Task Scheduler with an "At log on" trigger, running only when the user is logged on**, optionally with a tray application. It must **not** be installed as a Windows Service, must **not** run as SYSTEM, and must **not** use Task Scheduler's "Run whether user is logged on or not" option. All of those execute in a different security context and cannot read that user's stored credentials, which would surface as an unexplained authentication failure on every job.
- This choice looks wrong to a reader and must be documented as such in the runbook, with the reason stated.
- Verified on the target machine as part of `FEAS-08`, not assumed.

### 10.3 Background execution, sleep and resume

- The worker must run with no browser tab open and must survive browser closure (`AC-MVP-001`, `AC-MVP-010`).
- On Windows sleep or hibernation the process is frozen, not stopped. On resume, the worker must detect a long wall-clock gap, re-check every in-flight job's lease, and reconcile rather than assume continuity.
- If the machine sleeps mid-upload, the resumable session may have expired. On resume the worker performs the §6.2 ladder rather than restarting the insert.
- A missed upload window while the machine was asleep produces `JOB_MISSED_WINDOW` and applies the configured recovery policy — it must never publish late silently.
- Consider a Task Scheduler "wake the computer to run this task" option only after its interaction with sleep and hibernate is tested (`FEAS-08`); it is not required for correctness, since publication is delegated to YouTube (§5.1).

### 10.4 Local network exposure

- Localhost only by default; no remote control endpoints and no listener on `0.0.0.0`.
- Reject requests whose `Host` header is not the expected loopback authority, to mitigate DNS rebinding.
- Validate `Origin` on state-changing requests and enforce a CSRF token.
- Serve the built frontend from the API's own origin so that same-origin protections apply without exception configuration.
- Path validation for all media paths; no path may escape the permitted roots.

### 10.5 Logging, backup and data handling

- No Google passwords, OAuth tokens, or full request headers in logs, events, or exported diagnostics.
- Backups exclude secrets by default, since secrets live outside the database. Restore must re-validate file paths, channel credentials and the Sheets connection, and must confirm consent state (§11).
- Explicit, configurable retention for job logs and local media references.
- No source video is uploaded to any third-party service other than the user-selected destination platform.
- Dependency vulnerability scanning and secure update guidance.

## 11. Compliance obligations (`COMP-*`)

These are requirements from the YouTube API Services Terms of Service and Developer Policies, not optional hardening. All were absent from revision 1.0.

| ID | Obligation | Implementation |
|---|---|---|
| `COMP-01` | **Privacy policy presented and accepted before feature access.** An API Client must require agreement to a privacy policy before users can use its features. | A first-run consent gate blocks feature access until accepted. Not satisfied by a `LICENSE` file or a settings-page link. |
| `COMP-02` | **Policy links.** Display a link to the YouTube Terms of Service; the privacy policy must reference and link the Google Privacy Policy, list the Authorized Data accessed/stored/shared, link Google's security-settings page for revocation, and give a contact route. | Rendered in the app footer and in the consent screen; links verified, not placeholders. |
| `COMP-03` | **One API project per application.** An API Client must create exactly one API project, and must not share credentials across clients or embed them in source. | Setup documents and validates a single project/client; no credentials in source control. |
| `COMP-04` | **Revocation and data deletion.** On revocation the client must programmatically revoke the token immediately and delete the Authorized Data accessed or stored under that consent, within the deadline the Terms specify. | §7.2 implementation plus the retention sweep in §11.1. |
| `COMP-05` | **Authorized Data retention.** Stored Authorized Data other than the categories the Terms permit indefinitely must be refreshed or deleted within the stated period; statistics must be re-verified periodically and deleted when authorization lapses. | `*_fetched_at` columns (§3) plus the sweep in §11.1. |
| `COMP-06` | **No unconsented modification of user content.** A client must not truncate, append to, or otherwise alter user-provided values before sending them to YouTube without the user's explicit consent. | Optional transformations (for example an automatic `#shorts` hashtag) require a recorded, versioned `ConsentRecord` and per-job visibility of what will be sent. Never truncate a title at the 100-character limit to fit a transformation (§6.4); block and inform instead. |
| `COMP-07` | **Visibility must be surfaced.** Any action that inserts or modifies a video must clearly identify the channel it affects and the visibility setting it will apply, and must not change existing visibility without express instruction. | The schedule editor always shows destination channel and privacy; default Private (`01` §7). |
| `COMP-08` | **Branding and affiliation.** YouTube-sourced content must be attributed per the Branding Guidelines, and content not originating from YouTube must not be presented as if it did. | No third-party platform tabs or icons in MVP (`05` §1); YouTube content rendered with the required attribution. |
| `COMP-09` | **API project activity.** YouTube may disable or curtail access to a project inactive for 90 consecutive days. | A low-cost authenticated heartbeat (for example `channels.list(mine=true)`, 1 unit) on start when the last successful call is older than a configured interval, plus documentation. |
| `COMP-10` | **Undocumented services.** Only documented API surfaces may be used; no scraping, no reverse engineering, no private endpoints. | Connector code review checklist; already a product non-goal (`01` §4). |

### 11.1 Authorized Data retention sweep

A recurring worker task (alongside §6.3) that enforces `COMP-05`:

- Refresh stored YouTube-sourced values whose `*_fetched_at` exceeds the configured interval, provided authorization is still valid.
- Delete stored Authorized Data belonging to a connection that was revoked, within the deadline set in `COMP-04`.
- Delete Authorized Data for connections whose tokens can no longer be refreshed.
- Retain local bookkeeping indefinitely: schedule ID, job state, timestamps, attempt history, and the external video ID returned by our own upload.
- Every purge writes a `JobEvent`-style audit record so deletion is demonstrable, which is what a release review will ask to see.

**Open decision requiring approval:** the exact deadline to encode for `COMP-04` and the exact data classes classified as Authorized Data versus bookkeeping. See the decisions list at the end of this document.

## 12. Performance/reliability targets

Proposed targets to validate on the development PC:

- UI status update under 1 second for local state changes under normal load.
- No job loss after process termination at any documented checkpoint.
- Safe resumable transfer after network interruption when the API session remains valid; otherwise mark/reconcile per §6.2.
- No duplicate report rows in retry/replay tests.
- No duplicate video in any scenario that does not end in `JOB_NEEDS_REVIEW`.
- Scheduler survives browser closure and normal Windows restart.
- Memory use remains bounded for large libraries; video files are streamed rather than loaded entirely into memory.
- **Upload pacing is configurable**: `max_upload_attempts`, `retry_backoff_base_seconds`, `retry_backoff_max_seconds`, and `upload_spacing_seconds` between consecutive uploads on the same connection. These are `AppSettings` fields (§3) and are surfaced in Settings and Diagnostics.
- Concurrency defaults to one upload per connection. A configurable daily capacity warning is P1.

### 12.1 Quota budget

Default allocation is 100 `videos.insert` calls per day in their own bucket, plus 10,000 units per day shared across all other endpoints, resetting at midnight Pacific Time. Relevant costs: `videos.insert` 1 unit; `videos.update` 50; `channels.list` 1; `videos.list` 1; `activities.list` 1; `playlistItems.list` 1; `thumbnails.set` 50.

Design consequences: scheduling via the insert body rather than a follow-up update is both a correctness and a quota decision; the reconciliation ladder and confirmation sweep are cheap enough to run routinely; and identity verification should use `channels.list(mine=true)`, not an expensive alternative. Record observed usage in `ApiQuotaLedger` and warn before exhaustion. Verify actual costs on the real project (`FEAS-10`).

## 13. Testing

Unit: metadata validation, timezone parsing (with `tzdata`), state machine transitions and invariants, backoff, idempotency keys, report mapping, and the rule that mention state cannot become verified without evidence.

Contract: replay recorded request/response fixtures for the resumable protocol (including 308/`Range`, 404, `Retry-After`) and for the JSON endpoints. Assert that `privacyStatus` is always present and explicit.

Integration: SQLite transactions under WAL, concurrent job claims, worker lease expiry and reclaim, migration from every prior schema, Google token refresh, Sheets sync and reconciliation, API error mapping.

Sandbox/manual: actual test-channel OAuth, upload, scheduled publish, channel switch identity, mention feasibility, end-of-day Sheets row.

Resilience: network drop, force-kill worker, duplicate task delivery, 401 token expiry, 403 quota/permission error, API 5xx, missing media file, Windows sleep/restart, report timeout, and expired resumable session.

Security: localhost binding, `Host`/`Origin` rejection, CSRF, token redaction, secret-store inspection, path validation, and an automated scan asserting no token-shaped string appears in the database file, the frontend bundle, or any log file.

## 14. Official references

All pages re-checked 2026-10-11.

- YouTube upload: https://developers.google.com/youtube/v3/guides/uploading_a_video
- Resumable upload: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- Video insert and settable fields: https://developers.google.com/youtube/v3/docs/videos/insert
- Video resource / `publishAt`: https://developers.google.com/youtube/v3/docs/videos
- `videos.list` (available filters): https://developers.google.com/youtube/v3/docs/videos/list
- `channels.list`: https://developers.google.com/youtube/v3/docs/channels/list
- `activities.list`: https://developers.google.com/youtube/v3/docs/activities/list
- Quota calculator: https://developers.google.com/youtube/v3/determine_quota_cost
- Quota and compliance audits: https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
- OAuth web server (service-account restriction): https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps
- OAuth desktop apps: https://developers.google.com/identity/protocols/oauth2/native-app
- OAuth Testing status: https://support.google.com/cloud/answer/15549945
- API Services Terms of Service: https://developers.google.com/youtube/terms/api-services-terms-of-service/
- Developer Policies: https://developers.google.com/youtube/terms/developer-policies
- Branding Guidelines: https://developers.google.com/youtube/terms/branding-guidelines
- YouTube creator mentions: https://support.google.com/youtube/answer/9637404
- Google Sheets scopes: https://developers.google.com/workspace/sheets/api/scopes
- Google Sheets append: https://developers.google.com/sheets/api/reference/rest/v4/spreadsheets.values/append

## 15. Decisions requiring explicit approval

These are recorded rather than silently chosen, because each changes scope or imposes an obligation.

| # | Decision | Status |
|---|---|---|
| D-1 | Authorized Data retention: the exact deadline to encode for `COMP-04`, and which stored values count as Authorized Data versus local bookkeeping | **Open — needs owner decision** |
| D-2 | OAuth app posture: whether to operate in Google "Testing" status (accepting 7-day reauthorization) or pursue "In production"/verification, which triggers `COMP-01`–`COMP-03` obligations in full | **Open — needs owner decision** |
| D-3 | Whether optional metadata transformations (auto `#shorts`, auto title suffix) are wanted at all, given `COMP-06` | **Open — needs owner decision** |
| D-4 | Default `preupload_lead_minutes`, pending the `FEAS-03` measurement | Blocked on `FEAS-03` |
| D-5 | Multi-channel product wording if `FEAS-01` returns negative | Blocked on `FEAS-01` |
| D-6 | Whether `videos.update` is ever issued on a `manual_required` mention job, pending `FEAS-06` | Blocked on `FEAS-06` |
