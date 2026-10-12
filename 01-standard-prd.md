# Standard Product Requirements Document (PRD)

## Project: Local YouTube Shorts Scheduler
**Version:** 1.1 (revision 2026-10-11; see the correction log in `README.md`)  
**Product type:** Windows-first local application with a localhost web UI  
**Initial platform:** YouTube Shorts publishing  
**Future direction:** Pluggable connectors for other social platforms

## 1. Executive summary

Build a Windows application that runs locally, exposes a browser-based dashboard on the local machine, and automates scheduled publishing of short-form videos to multiple authorized YouTube channels. A user can import or select local clips, define titles, descriptions/captions, hashtags, intended channel mentions, target channels, and publish dates/times. The system validates each job, uploads through documented YouTube APIs, schedules publication where supported, records the resulting YouTube video ID and URL, and writes an end-of-day report to a configured Google Spreadsheet.

YouTube is the only production publishing connector required for the MVP. The web UI and internal architecture must support additional platform connectors later without requiring a redesign of the scheduler, media library, job history, or reporting system.

This is a local-first system, not a public SaaS. Content files, schedules, operational database, and OAuth tokens remain on the Windows machine by default. The system still requires internet access to authenticate, upload, query platform status, and update Google Sheets.

## 2. Problem statement

The operator manages clips across multiple YouTube accounts/channels and needs reliable, repeatable publishing at scheduled times. Manual uploading consumes time, creates avoidable errors, and makes it difficult to provide a company with a consistent record of successfully published content.

Specific pain points:
- Repeatedly selecting videos and entering titles/descriptions manually.
- Posting a clip to the wrong channel or account.
- Scheduling mistakes and timezone confusion.
- Unclear status when uploads fail or Windows/network connectivity is interrupted.
- Difficulty retrieving each published video's URL and matching it to an internal schedule ID.
- Spreadsheet reporting is repetitive and can miss successful uploads.
- A plain `@handle` in description text may not create a real YouTube channel mention; this needs an explicit, validated workflow rather than an assumption.

## 3. Product goals

1. Schedule and publish YouTube Shorts to multiple explicitly authorized channels.
2. Reduce manual steps while preserving safe controls and visible job state.
3. Never lose the relationship between a schedule ID, source file, destination channel, published video ID, title, and URL.
4. Recover from network failures and restarts without silently losing or duplicating jobs.
5. Generate reliable Google Sheets reporting after the configured publishing day.
6. Make setup, channel authorization, scheduling, and troubleshooting manageable through a local web UI.
7. Keep the architecture extensible to other social platforms through connector adapters.
8. Comply with the YouTube API Services Terms of Service and Developer Policies, and never present an unverified state as a verified one.

## 4. Non-goals for MVP

- A hosted multi-tenant SaaS or remote control plane.
- A cloud storage system for source clips.
- Fully automatic editing, generation, or repurposing of videos.
- TikTok, Instagram, Facebook, or other publishing connectors in the first release. **The MVP must not display third-party platform icons, tabs or "coming soon" affordances for unimplemented platforms**; doing so suggests affiliation and contradicts the YouTube Branding Guidelines.
- Video thumbnail upload or thumbnail generation. Out of MVP scope.
- Scraping platforms or using undocumented/private endpoints.
- Bypassing OAuth, upload quotas, platform restrictions, or account permissions.
- Claiming a real YouTube @mention merely because a handle-shaped string was inserted into a description.
- Guaranteed publishing while the PC is offline before the media has been uploaded and scheduling accepted by YouTube.
- A hard "zero duplicate videos" guarantee. See §7 and §10 for the guarantee that is actually achievable and why.

## 5. Target user and use cases

**Primary user:** An operator or content editor who manages multiple YouTube channels and supplies completed clips to a company.

Core journeys:
1. Install and start the app on Windows; open the local dashboard.
2. Connect each YouTube identity through Google's authorization flow and verify the channel shown by the system.
3. Connect a Google Spreadsheet or select an existing spreadsheet/tab for reporting.
4. Add a clip, assign a stable schedule ID, choose the target channel, enter title/description/hashtags/mention requests, and choose a local publish date/time.
5. Validate the job and see any missing file, invalid metadata, authorization, Shorts-format, or mention-workflow warnings.
6. Save the schedule and see its lifecycle in the queue/calendar.
7. The worker uploads at the configured lead time and asks YouTube to accept a future publication time for the private video in the same request.
8. The worker verifies the API response, records the video ID and URL, and marks the job correctly.
9. A publish-confirmation sweep later observes the remote status so the app can distinguish "scheduled on YouTube", "published (verified)" and "publication time passed, verification pending".
10. At the reporting cutoff, write a deduplicated daily report to Google Sheets; show sync status and allow safe retry.
11. Investigate failed or ambiguous jobs without accidentally publishing duplicates.

## 6. Product scope and priorities

### P0 — MVP
- Local Windows backend and localhost web UI.
- Persistent job scheduler and worker that continue when the browser tab is closed, running as the same interactive Windows user that holds the credentials.
- Media library referencing local files; metadata and file validation.
- Multiple YouTube channel connections with explicit destination-channel confirmation, where each connection is one authorized Google identity resolving to exactly one channel.
- Job creation, editing, cloning, cancellation, calendar/queue, validation, and status history.
- YouTube upload through documented Data API with resumable upload support.
- Schedule publication by sending `status.privacyStatus=private` together with `status.publishAt` in the same `videos.insert` body, then verifying the remote accepted state.
- Publish-confirmation sweep, so remote publication can be observed and reported honestly rather than assumed.
- Retry/backoff, upload progress, ambiguous-failure reconciliation, and job logs.
- Title, description, hashtags, API-supported video tags, privacy/audience settings, and mention resolution status.
- Google Sheets daily report with schedule ID, title, channel, status, publish time, video ID, URL, source file, and error details where appropriate.
- Local database backup/export, token security, setup diagnostics, and clear setup guide.
- Test mode that cannot contaminate production jobs or publish real content.
- Compliance baseline: privacy policy and consent gate, links to the YouTube Terms of Service and Google Privacy Policy, separation of Disable from Revoke, Authorized Data retention handling, and a low-cost activity heartbeat so the API project does not become inactive.

### P1 — after MVP is stable
- Bulk CSV import/export and reusable caption templates.
- More advanced calendar filters and per-channel publishing windows.
- Media folder watch/import.
- Report on each success in near-real time in addition to end-of-day report.
- Channel-specific defaults and reusable upload presets.
- Configurable daily upload capacity warnings and advanced analytics.

### P2 — future direction
- Additional platform connectors.
- Multi-user local permissions.
- Remote notifications or remote access, only after a separate security design.
- AI-assisted metadata suggestions with user review.
- Multi-PC coordination, cloud sync, or distributed workers.

## 7. Core product rules

- Every job receives a unique, immutable internal schedule ID before it enters the queue.
- **A connection is an authorized credential, not a chosen channel.** The destination channel is read from the API for that credential and confirmed by the operator. A channel ID selected in the UI is never treated as authority and can never redirect a credential.
- A job is never considered successful only because an upload request was sent. Store the returned video ID, platform status, and URL when available.
- The channel identity returned by the authorized connection must be displayed before scheduling and before the first production upload.
- Never assume that choosing a Google account always means the intended YouTube channel. Require channel verification during connection.
- Never silently change a requested channel, publish time, privacy, title, or destination file.
- **Default privacy is Private.** The system always sends an explicit `status.privacyStatus` and never relies on a server-side default, because the behaviour of uploads from an unverified API project is currently documented inconsistently and is treated as unknown.
- Store times in UTC and preserve the user's chosen IANA/Windows timezone context for display and reports.
- Upload retries must not blindly create a second video after an ambiguous timeout. Reconcile with the persisted upload session first; if the outcome still cannot be determined safely, transition to `JOB_NEEDS_REVIEW`. The enforceable guarantee is **no duplicates caused by automatic retry** — it is not possible to prove that no duplicate exists after an outcome-unknown insert, and attempting to do so drives unsafe retries.
- Spreadsheet retries must not append duplicate rows for the same schedule ID and reporting version.
- A text `@handle` is not proof that a real YouTube mention entity was created. Mention support must have an explicit status and evidence.
- If the PC is off or has no network before upload/scheduling is completed, the local worker cannot perform that step. The UI must mark missed jobs and offer the configured recovery action.
- Future social platforms must implement a documented connector contract; platform-specific behavior cannot be assumed universal.
- **Disabling a connection is not revoking it.** Disabling is local, reversible, and keeps job history. Revoking calls Google's revocation endpoint, deletes the stored token, and requires purging stored Authorized Data within the period the YouTube API Services Terms of Service specifies. Both actions must be separately labelled in the UI.
- **"Published" may only be shown when the platform status supports it.** Because publication is delegated to YouTube, a job whose publication time has passed while the app was offline is *not* reported as published; it reports as verification pending until the remote status is observed.
- **A past `publishAt` value publishes immediately**, so a target time in the past must be blocked or explicitly confirmed, never silently accepted.

## 8. YouTube-specific requirements

The system must use official YouTube Data API operations and OAuth authorization. Upload metadata should include the title, description, API-supported tags, category, privacy, audience declarations, and future publication time as applicable. Use resumable uploads and preserve job state during interruption.

### 8.1 Authorization and destination channels

OAuth 2.0 user authorization is required. Ordinary personal-channel publishing must not assume a service account works: the YouTube Data API documents the service account flow and the `onBehalfOfContentOwner` / `onBehalfOfContentOwnerChannel` parameters as reserved for YouTube Content ID partners.

**The credential is the unit of authorization.** `videos.insert` publishes to the channel resolved from the authorizing credential; no documented parameter lets an ordinary user redirect an upload to an arbitrary channel ID. Multiple channels therefore means multiple authorized identities, one channel each. Whether a single Google identity that manages a brand account can address that brand channel through the API is not documented either way and is a mandatory proof of concept before any multi-channel UI is built. If it proves negative, the product states plainly that one connected identity publishes to one channel.

### 8.2 Scheduling

`status.publishAt` is a settable field on `videos.insert`. The system sends `status.privacyStatus=private` and `status.publishAt` in the same insert body rather than performing a separate update call. This halves quota cost and, more importantly, removes the failure window in which a video exists as private with no schedule after the process dies between two calls.

Documented constraints on `publishAt` that constrain the product:

- It can be set only while `privacyStatus` is private.
- It can only be set on a video that **has never been published**. A cancel or reschedule path is therefore unavailable for an already-published video, and the UI must say so rather than offering an action that cannot succeed.
- A past value publishes the video immediately. Preflight must treat this as a hard block.

### 8.3 Actual channel mentions are not creatable through the documented API

YouTube's creator help describes a mention workflow where the author types `@` and selects a channel from a recommendation list. The documented `videos` resource exposes `snippet.title`, `snippet.description`, `snippet.tags[]` and `status`, and **no** mention entity. No documented API field creates or verifies that selected mention.

Therefore: true selected-channel mentions are **not supported by the documented API**. The app must not claim automatic mention tagging, must not label a text-only handle as verified, and must not use private endpoints, cookie replay, or browser automation to evade the limitation. For jobs that require a real mention, the approved path is a human-in-the-loop step through official YouTube Studio while the video is still private, with the job held at `manual_required` until the operator confirms completion. The Technical PRD must additionally verify that such a mention survives any later metadata update made by this app.

### 8.4 API project verification and audit status

The behaviour of uploads from an unverified YouTube API project is **currently unknown**, because Google's own live documentation contradicts itself: the video resource page states such uploads are set to private by default and require an audit to become public, while the `videos.insert` `status.privacyStatus` parameter states that videos uploaded from unverified API projects are not restricted to private viewing mode. This PRD no longer asserts either as fact.

The consequence is a hard safety rule rather than a documentation claim: the app always sets `status.privacyStatus` explicitly, defaults to Private, and treats public publishing from an unverified project as blocked until empirically demonstrated on the actual API project. Setup must surface the project's verification and audit state and explain the potential consequences.

### 8.5 Ambiguous upload outcomes

A `videos.insert` call can be accepted by YouTube while the client never receives the response. `videos.list` no longer supports `mine=true` or `chart=mostRecent`, so there is no single call that enumerates "my recent uploads". Reconciliation must use the documented ladder in the Technical PRD — resumable session status probe, then channel activity or uploads-playlist inspection — and must escalate to `JOB_NEEDS_REVIEW` rather than issuing a second insert.

### 8.6 Policy obligations

Usage must comply with the YouTube API Services Terms of Service and Developer Policies. These impose obligations beyond API mechanics, including a privacy policy that users must accept before using features, a link to the YouTube Terms of Service and Google Privacy Policy, exactly one API project per application, a stated limit on retaining Authorized Data, and restrictions on modifying user-supplied metadata without explicit consent. The Technical PRD specifies these requirements.

## 9. Google Sheets reporting

- User connects Google Sheets through OAuth; do not collect Google passwords. The user selects the spreadsheet and tab through that connection; a spreadsheet may be selected by OAuth picker only, and if a URL is pasted it must be parsed and validated rather than stored verbatim.
- User selects spreadsheet ID, worksheet/tab, report timezone, cutoff time, and included job statuses.
- Default report: one row per schedule ID per report revision, limited to terminal jobs for the completed reporting day; successful jobs include video ID and URL.
- Include at least: report date, schedule ID, channel name/ID, content title, source filename, target publish time, actual upload time, actual publish time/status if known, video ID, content URL, job result, mention state, and error code/message if failed.
- Every row carries a deterministic **sync key** of the form `SCH_{report_date}_{schedule_id}_{schema_version}` in a dedicated column. Before any append, the app reads the existing sync keys in the target range and appends only the missing ones. The sheet write is therefore idempotent across reruns, timeouts and partial batches.
- **The sync key column and the schedule ID column are not user-configurable.** Optional column selection is allowed for presentation columns only; disabling an identity column would break deduplication.
- Maintain a local sync ledger keyed by the same sync key. Before retrying a sheet write, reconcile whether the row has already been written.
- Show last successful sync, pending rows, and failure details. The local job history is the source of truth; Google Sheets is an external report destination.

## 10. Success metrics

Proposed MVP metrics (targets to validate during implementation):

- 100% of terminal upload jobs have a traceable schedule ID and channel identity.
- **0 duplicate videos caused by automatic retry.** Every outcome the system cannot determine safely is routed to `JOB_NEEDS_REVIEW` and is never auto-retried. A literal "0 duplicates ever" target is not achievable, because an insert whose response was lost cannot be proven to have failed; stating it would pressure the system into unsafe retries.
- 100% of automatic upload retries are preceded by a reconciliation step that recorded evidence.
- 100% of reported successful jobs include a verified video ID and URL.
- 100% of jobs shown as "Published" have an observed remote status backing that label; jobs whose publication time has passed while the app was offline are shown as verification pending.
- At least 99% of eligible report rows are synchronized after retry under normal network conditions, with 0 duplicate rows per sync key.
- Scheduler timing accuracy is measured separately for (a) local upload start and (b) YouTube's accepted scheduled publish time.
- Zero secrets in frontend bundles, logs, or exported diagnostics, verified by automated scanning rather than by inspection.
- No release-blocking failure in restart, timezone, and OAuth-revocation tests.
- No unrevoked credential retains stored Authorized Data beyond the retention period.
- 100% of jobs that required a true channel mention were held at `manual_required` rather than reported as verified, unless a documented API path is demonstrated.

## 11. Risks and dependencies

- YouTube API quota. Default allocation is 100 `videos.insert` calls per day in their own bucket, plus 10,000 units per day for everything else. `videos.insert` costs 1 unit; `videos.update` costs 50. Scheduling via insert rather than a follow-up update is therefore a quota decision as well as a correctness one.
- OAuth project verification/audit state, and the currently contradictory documentation about what an unverified project does to uploads.
- **Multiple channels per identity.** The credential, not the channel, is the unit of authorization, and brand-account behaviour through the API is undocumented. This is the single largest scope risk in the product.
- OAuth consent configuration, testing-mode behaviour (authorizations and refresh tokens expire after seven days), and the cost of recurring reauthorization.
- **API project inactivity.** YouTube may disable or curtail a project inactive for 90 consecutive days, which a sporadically used local tool can trigger. A low-cost authenticated heartbeat mitigates this.
- Policy obligations: privacy policy and consent, Authorized Data retention limits, revocation and data deletion deadlines, one API project per client, and consent before modifying user-supplied metadata.
- YouTube UI/API changes and future social-platform requirements.
- Local PC sleep/shutdown, Windows startup behavior, and the constraint that the worker must run as the interactive user to read stored credentials.
- Upload bandwidth, transcoding/processing delay, rate limits, and source-video compatibility.
- Google OAuth consent configuration and Sheets API scopes.

## 12. Release criteria

Do not release until a real YouTube test channel is connected, an actual test video is uploaded, the intended channel identity is verified, a scheduled-publication test is observed end to end, the video ID/URL are stored, and the report row is written to Sheets without duplication.

Additionally, do not release until:

- The multi-channel proof of concept has a written verdict and the product's multi-channel claim matches that verdict.
- The unverified-project behaviour has been observed on the actual API project and the setup documentation states the observed result.
- The mention requirement is verified separately and the result is disclosed honestly, including whether a Studio-completed mention survives a later metadata update by this app.
- At least one job has been observed through publication by the confirmation sweep, or the honest limitation is documented.
- The privacy policy is accepted in the running app, the revocation and data-deletion flow has been exercised, and the Authorized Data retention behaviour has been demonstrated.
- The worker has been shown to start after a Windows reboot through the documented startup mechanism, running as the user that authorized the accounts.

## 13. Official references

All pages re-checked 2026-10-11. See `07-feasibility-checklist.md` for which of these are documented facts and which require testing.

- YouTube video upload: https://developers.google.com/youtube/v3/guides/uploading_a_video
- `videos.insert` (settable fields, `status.privacyStatus`, `status.publishAt`): https://developers.google.com/youtube/v3/docs/videos/insert
- Video resource and `publishAt` constraints: https://developers.google.com/youtube/v3/docs/videos
- `videos.list` (available filters): https://developers.google.com/youtube/v3/docs/videos/list
- `channels.list` (`mine=true`): https://developers.google.com/youtube/v3/docs/channels/list
- `activities.list`: https://developers.google.com/youtube/v3/docs/activities/list
- Quota calculator: https://developers.google.com/youtube/v3/determine_quota_cost
- Quota and compliance audits: https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
- Resumable upload protocol: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- YouTube OAuth for web applications (service-account and `onBehalfOfContentOwner` restriction): https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps
- Google OAuth desktop apps: https://developers.google.com/identity/protocols/oauth2/native-app
- OAuth Testing mode (7-day expiry): https://support.google.com/cloud/answer/15549945
- YouTube mention workflow: https://support.google.com/youtube/answer/9637404
- YouTube API Services Terms: https://developers.google.com/youtube/terms/api-services-terms-of-service/
- YouTube API Developer Policies: https://developers.google.com/youtube/terms/developer-policies
- YouTube Branding Guidelines: https://developers.google.com/youtube/terms/branding-guidelines
- Google Sheets append: https://developers.google.com/sheets/api/reference/rest/v4/spreadsheets.values/append

## 14. Traceability

Requirement IDs used across the pack:

| Prefix | Meaning | Defined in |
|---|---|---|
| `CORE-*` | Product rules | `01` §7 |
| `P0-*` | MVP scope items | `01` §6 |
| `FEAT-*` | Feature behaviours | `04` §1 |
| `AC-MVP-*` | MVP acceptance criteria | `03` §6 |
| `CAP-*` | Connector capabilities | `02` §9 |
| `JOB-*` | Canonical job states | `02` §4 |
| `COMP-*` | Compliance obligations | `02` §11 |
| `FEAS-*` | Proof-of-concept items | `07` |

This PRD states intent. `02` states mechanism, `03` gates the release, `04` states user-visible behaviour, `05` states presentation, `06` states the working protocol, and `07` holds everything that is not yet known.
