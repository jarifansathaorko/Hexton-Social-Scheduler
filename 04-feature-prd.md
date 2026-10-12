# Feature PRD — Local YouTube Shorts Scheduler

**Version:** 1.1 (revision 2026-10-11; see the correction log in `README.md`)

This document specifies user-visible feature behavior. The Standard PRD defines the why; the Technical PRD defines implementation boundaries; the MVP PRD defines what ships first; the feasibility checklist defines what is not yet known. Where a feature depends on YouTube API behavior, validate it through official docs and a real test channel rather than assuming the behavior.

Job states referenced here are the canonical `JOB-*` states defined in `02` §4.1, rendered with the labels in `02` §4.3. This document does not define its own state vocabulary.

## 1. Feature inventory

| ID | Feature | MVP priority | Completion evidence |
|---|---|---:|---|
| FEAT-001 | First-run setup | P0 | Fresh install walkthrough passes |
| FEAT-002 | OAuth / channel connections | P0 | Returned channel identity verified |
| FEAT-003 | Media library | P0 | File metadata and hash persisted |
| FEAT-004 | Schedule editor | P0 | Validation and saved job tests pass |
| FEAT-005 | Queue / calendar | P0 | State and local times remain consistent |
| FEAT-006 | YouTube resumable upload | P0 | Real test video and progress verified |
| FEAT-007 | YouTube scheduled publish | P0 | Future publish confirmed on test channel |
| FEAT-008 | Mention workflow | P0 gate | `manual_required` workflow shipped and tested |
| FEAT-009 | Retry and recovery | P0 | Fault-injection suite passes |
| FEAT-010 | Google Sheets reporting | P0 | Daily row written once, including URL |
| FEAT-011 | Job detail and logs | P0 | Clear diagnostic and timeline |
| FEAT-012 | Backup/restore | P0 | Restored DB preserves traceability |
| FEAT-013 | Publish confirmation sweep | P0 | Scheduled vs verified-published distinguished |
| FEAT-014 | Compliance, consent and retention | P0 | Consent gate, revocation purge and sweep demonstrated |
| FEAT-015 | Metadata templates | P1 | Reusable fields applied predictably |
| FEAT-016 | Bulk import | P1 | Invalid rows safely rejected |
| FEAT-017 | Week/day calendar and drag-to-reschedule | P1 | Confirmation and safe remote update |
| FEAT-018 | Thumbnail selection or generation | P1 | Explicitly **not** in MVP (`03` §4) |
| FEAT-019 | Future social connectors | P2 | Connector contract and capability matrix |

FEAT IDs were renumbered in revision 1.1 to insert the new P0 items. Any earlier reference to FEAT-013/014/015 as templates/bulk/future-connectors now means FEAT-015/016/019.

## 2. FEAT-001 — First-run setup

**User story:** As the operator, I want a guided setup so that I know what is configured and what remains blocked.

Requirements:
- **Present the privacy policy and require acceptance before any feature is usable** (`COMP-01`). Record the accepted policy version.
- Show local app status, data directory, timezone, worker state, YouTube API config, YouTube channel connections, Sheets connection and report destination.
- Never ask for a Google account password.
- Clearly distinguish `not configured`, `connected`, `needs reauthorization`, `restricted`, and `not verified`.
- Run a safe connectivity/auth test without uploading media.
- **Explain the API verification/audit situation as observed on this project**, citing the `FEAS-02` result rather than asserting a general platform rule.
- State plainly how many channels one connected identity can publish to, per the `FEAS-01` verdict.

Acceptance: first-run state persists after restart; diagnostics return actionable error messages; unconfigured services do not appear healthy; feature access is blocked until the policy is accepted.

## 3. FEAT-002 — YouTube identities and channels

**User story:** As an operator managing multiple channels, I want to choose the exact destination channel and avoid posting to the wrong account.

Requirements:
- Add a connection through system-browser OAuth.
- Read the available channel identity using documented API calls after consent.
- Display channel title, channel ID, connected Google identity label if available, scopes, and last validation time.
- **One connection addresses exactly one channel.** The channel shown is the one resolved from the credential, never one chosen by the client.
- Ask the operator to confirm the channel. Require a second confirmation when a job uses a channel not previously used by that schedule/template.
- **Disable** (local, reversible, keeps history) and **Revoke Google access** (deletes the token, notifies Google, purges Authorized Data) are separate actions with distinct, plainly-worded labels and consequences (`02` §7.2).
- When an account manages multiple channels, test the actual API identity/selection behavior; do not simply store an arbitrary user-selected channel ID as though it changed API authority. If `FEAS-01` shows one identity cannot address the desired channel, **say so in the UI** rather than offering a selection that cannot work.
- Before upload, confirm the chosen credential still corresponds to the intended channel whenever possible; block on mismatch.
- Surface token expiry proactively. Under OAuth Testing status, authorizations and refresh tokens expire after seven days (`FEAS-07`).

Errors: revoked token -> `JOB_BLOCKED` with an authorization reason; channel identity mismatch -> block upload; API quota -> quota-specific blocked state or retry only when quota resets; unknown errors -> diagnostic and bounded retry policy.

## 4. FEAT-003 — Media library and preflight

Requirements:
- Select local video files using a native OS file picker or a validated file path.
- Record path, size, modification time, SHA-256 and media metadata.
- Detect deleted/moved/changed file before upload; request reselect or reconfirm if hash changed.
- Inspect duration, aspect ratio, resolution, container/codec where possible; show pass/warning/fail under a configurable Shorts profile.
- No hidden transcode in MVP. Unsupported file must be blocked or explicitly permitted with a warning if API/platform confirms it is acceptable.
- Prevent path traversal and reject directories/unsupported file types.

## 5. FEAT-004 — Schedule editor

Fields:
- Internal Schedule ID (generated, immutable).
- Optional company/reference ID.
- Source media file.
- Destination platform (YouTube in MVP) and verified channel.
- Title, description, hashtags and API-supported tags.
- Mention requests: target channel display name, canonical channel ID/URL, entered handle, requirement level and state.
- Target publish date/time and timezone.
- Pre-upload lead time.
- Privacy, audience declaration, category and other API-supported metadata.
- Reporting inclusion and notes.

Validation:
- Required title/file/channel/date-time.
- Prevent invalid or past publish time from slipping through as an accidental immediate public post.
- Check metadata length constraints from current official API rules.
- Validate mention targets against operator's saved registry, but don't infer that this creates the platform's actual mention.
- Warn on conflicting jobs scheduled for the same channel/time and on estimated quota pressure.
- Save draft without requiring every production-only field, but require full validation before queueing.

Editing policy: if the worker has begun upload, changing file/channel/title/publish time requires pausing/canceling safely or creating a new job revision; don't mutate a live worker operation in place.

## 6. FEAT-005 — Calendar and queue

Views: list/queue, **month** calendar, channel filter, status filter, date range and search by schedule ID/title/video ID. Week and day views, and drag-to-reschedule, are P1 (FEAT-017).

Statuses are rendered from the canonical `JOB-*` states using the mapping in `02` §4.3. Do not define an independent status list.

Local job state and remote YouTube state are shown as separate facts from the same job: a job may read "Queued locally" with a remote note of "not yet uploaded", or "Scheduled on YouTube" with a remote note of "processing". Display timezone explicitly. Each row opens job detail with event timeline. Actions are state-aware; e.g., retry is **not** offered as safe when the previous upload outcome is ambiguous (`JOB_NEEDS_REVIEW` offers resolution, not retry).

## 7. FEAT-006 — Upload execution

- Upload by `videos.insert` with a resumable protocol implemented over a first-party HTTP client that persists the session URI (`02` §1).
- Stream media in chunks; don't load the entire video into RAM.
- Show phase: waiting, initializing, uploading %, processing, scheduling, completed/error.
- Persist job intent before the first remote side effect and persist upload checkpoints after each safe transition.
- Save returned video ID immediately and construct `https://www.youtube.com/watch?v={video_id}`; store any canonical URL returned/confirmed.
- Detect retryable errors, throttling and quota. Honor retry-after guidance if present and use bounded exponential backoff with jitter, bounded by `max_upload_attempts`.
- Respect `upload_spacing_seconds` between consecutive uploads on the same connection.
- Permanent auth, account, quota, invalid metadata and policy errors need explicit user action or a known reset time.
- Network timeout after remote acceptance -> follow the reconciliation ladder; do not duplicate uploads.

## 8. FEAT-007 — Future publication

- Send `status.privacyStatus=private` together with `status.publishAt` in the insert request when the validated strategy permits (`02` §6.1).
- Confirm that the remote resource has accepted the intended publication timestamp; save the response and remote status.
- Show `Scheduled on YouTube` only when remote state supports it. A local queued time is not the same as a remote scheduled publish.
- For past dates, time ambiguity or API errors, block and ask for correction; avoid accidental immediate public release.
- Allow user-configured upload lead time and missed-window policy.
- **Reschedule and cancel are limited** (`02` §5.2): they apply only to a video that has never been published. A published video offers neither action, and the UI must not present them as available.
- Cancel sets privacy to private and clears `publishAt`, then verifies the result against the remote resource.

## 9. FEAT-013 — Publish confirmation sweep

- A recurring worker task that polls remote status for jobs whose publication time has passed, so "published" is verified rather than assumed (`02` §6.3).
- The UI distinguishes four conditions and must label them differently:
  1. **Upload completed** — media accepted, video ID known, processing not yet confirmed.
  2. **Scheduled publication** — YouTube accepted a future publish time.
  3. **Published (verified)** — remote status observed as public.
  4. **Publication awaiting verification** — the publish time has passed but no confirming observation exists, typically because the PC was off.
- Condition 4 is a normal, non-error state and must not be rendered as a failure or as success.
- If the connection's token is invalid, the job stays unverified with the reason shown.

## 10. FEAT-008 — Actual YouTube channel mentions

**Established limitation, not an open gate.** YouTube's creator help describes mentions as typing `@` and choosing a channel from a suggested list. The documented `videos` resource exposes `snippet.title`, `snippet.description`, `snippet.tags[]`, `localizations` and `status`, and contains **no mention entity**. No documented API field creates or verifies that selected mention.

The shipped behavior is therefore:

1. A mention request on a job produces `MentionState = manual_required`, not a text substitution presented as success.
2. **A plain `@handle` in a title or description is text.** It must never be reported, stored, or displayed as a verified mention. There is no API path to verify it.
3. The job uploads as Private and is **held before publication**. The app presents a guided YouTube Studio step explaining exactly what to select, then requires operator confirmation.
4. The job must not be given a publish time that would make it public before that confirmation.
5. If the text is written into the description, the state is `text_only` and is labelled as such.

Outstanding verification (`FEAS-06`): whether a Studio-created mention survives a later `videos.update` issued by this app. If it does not, the app must not issue post-upload metadata edits on `manual_required` jobs.

Mention states: `not_requested`, `text_only`, `manual_required`, `verified`, `failed`. `verified` is reachable only through an explicitly demonstrated, documented mechanism. Every job record and report row must preserve the state.

## 11. FEAT-009 — Retry and recovery

Error taxonomy: network/transient, OAuth-expired, permission-denied, quota, invalid metadata/file, API restriction, processing failure, unknown/ambiguous result.

Requirements:
- Bounded retry, backoff and a next-attempt timestamp, governed by `max_upload_attempts`, `retry_backoff_base_seconds` and `retry_backoff_max_seconds`.
- Configurable `upload_spacing_seconds` between consecutive uploads on the same connection.
- Durable operation checkpoints.
- Worker lease and restart recovery, including after Windows sleep/resume.
- **Reconciliation of ambiguous upload outcomes** using the ladder in `02` §6.2. The UI offers **Resolve**, not **Retry**, for `JOB_NEEDS_REVIEW`, and shows the candidates and evidence gathered.
- Job event timeline without secrets, recording every reconciliation step.
- Pause/resume worker, cancel not-yet-started jobs and safe stop controls.
- Dry-run preflight must perform no upload or publish mutation.

## 12. FEAT-010 — Google Sheets daily report

At configured daily cutoff and timezone, generate report rows from the local DB. Support retries if Sheets is unavailable. Default to one terminal job per schedule ID and reporting date; prevent duplication if the report is run twice.

Required columns: `Report Date`, `Schedule ID`, `Sync Key`, `Company Reference`, `Platform`, `Channel Name`, `Channel ID`, `Content Title`, `Source Filename`, `Planned Publish Time`, `Actual Upload Time`, `Remote Publish Status`, `Video ID`, `Content URL`, `Mention State`, `Job Result`, `Error Code`, `Error Summary`, `Synced At`.

- **`Sync Key` and `Schedule ID` are mandatory and always present.** They are not presented as optional checkboxes; they are the mechanism that prevents duplicates (`02` §8.1).
- Any column configuration UI may toggle presentation columns only.
- Deduplication is read-before-append on the `Sync Key` column, with a read-based reconciliation after any timeout. A timed-out write is never retried blindly.
- Use a local outbox and sync ledger.
- Show preview, target spreadsheet/tab, last successful sync, pending rows, and per-row failure.
- The spreadsheet is chosen through the OAuth connection. If a URL is pasted, it is parsed and validated rather than stored verbatim.

## 13. FEAT-011 — Job detail and audit timeline

Each job detail shows the source file/hash, channel identity, metadata snapshot, intended publish time/timezone, worker transitions, attempts, API result IDs, URL, remote state, report sync state and mention state. Logs must redact tokens and sensitive headers. Preserve enough history to explain what happened without retaining excessive data forever.

Because local job state and remote state are separate, job detail must present both side by side rather than merging them into one line.

## 15. FEAT-012 — Backup and restore

Export DB and configuration without raw OAuth secrets. Because credentials live in the OS secret store rather than the database, a database backup is inherently secret-free; this must be stated in the UI rather than leaving the operator to assume it. Validate backup integrity and schema version. On restore, require revalidation of file paths, channel credentials and Sheets connection, and re-check the accepted privacy-policy version (`AC-MVP-015`). Test migration from the previous database schema.

## 14. FEAT-014 — Compliance, consent and retention

User-visible behaviour required by `COMP-01` through `COMP-10` (`02` §11):

- **Consent gate:** the privacy policy is shown on first run and feature access is blocked until it is accepted. The policy links the YouTube Terms of Service and the Google Privacy Policy, describes what Authorized Data is accessed and stored, links Google's security settings for revocation, and gives a contact route.
- **Revocation:** a clearly labelled action that deletes the stored credential, notifies Google, and reports what Authorized Data will be purged and when. It is not merged with "disable".
- **Retention:** stored YouTube-sourced values carry a visible "last checked" age, and the operator can see what will be refreshed or deleted. Local bookkeeping (schedule ID, state, timestamps, video ID) is retained and distinguishable.
- **Metadata transformations:** any optional automatic change to a title, description or tags (for example adding a hashtag) is off by default, requires a recorded consent, is shown to the operator as an exact preview of what will be sent, and **never truncates the user's text** to fit a platform limit. If it would not fit, it is blocked with an explanation.
- **Project activity:** a low-cost authenticated heartbeat keeps the API project from becoming inactive; its last run is visible in diagnostics.

## 16. P1/P2 feature requirements

Bulk CSV import must validate each row independently, produce an import preview, reject invalid rows with line-specific errors, and never queue all rows without confirmation. Templates must version metadata and not overwrite explicit job values silently. Future connectors must expose capability support per feature (schedule, mentions, privacy, tags) and use the shared scheduler/report models. Thumbnail selection or generation (FEAT-018) is P1 and is not implied by any MVP screen.

## 17. Cross-feature acceptance rules

- All user-facing counts/statuses derive from persisted canonical records.
- Every published result traces back to schedule ID and verified channel.
- All timestamps include timezone context in UI and are UTC-normalized in storage.
- Every failure is visible with a next action.
- Report retries don't produce duplicate rows.
- Mention status can never be promoted to verified without evidence.
- No screen labels a job "Published" without an observed remote status.
- No screen displays a channel that this app has not connected and confirmed for that credential.
