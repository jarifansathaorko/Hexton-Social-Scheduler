# Visual UI/UX PRD — Local YouTube Shorts Scheduler

**Version:** 1.1 (revision 2026-10-11; see the correction log in `README.md`)

## 1. Design goal

Create a modern, polished, efficient local web application for a Windows desktop workflow. It should feel like a professional content operations console, not a generic admin template. The user primarily manages a queue of short videos, channels, publishing times, errors and company reporting.

The UI should prioritize rapid daily operation, clear statuses, safe confirmation for irreversible actions, and easy diagnosis. Avoid decorative complexity that slows repetitive work.

### 1.2 Reconciliation with the supplied visual references

The workspace contains three reference images. Two of them show a dark-first design and a third a light design, and **they disagree with each other on navigation and product scope.** This section is binding on the implementation; where a reference image conflicts with it, this section wins.

| Present in the references | MVP decision |
|---|---|
| Instagram / TikTok / Facebook / **Coming Soon** platform tabs | **Removed.** YouTube is the only implemented platform. Displaying other brands' marks for unimplemented platforms suggests affiliation and conflicts with the YouTube Branding Guidelines (`COMP-08`) and `01` §4. |
| Two different navigation structures across the images | **Use the single structure in §3.** |
| Dark theme as the primary look | Light theme is the default; dark theme is an explicit, separately-designed option (§2). |
| "Change Thumbnail", "Auto-generate thumbnail" | **Deferred to P1** (FEAT-018). No MVP screen implies thumbnail control. |
| "Change Video" button on a live job | **Removed.** A job already uploading must not be mutated in place (`04` §5). |
| Sheets "Columns Configuration" with optional checkboxes | **Replaced** by required identity columns plus optional presentation columns (§4 H). `Schedule ID` and `Sync Key` are not toggleable. |
| Spreadsheet URL + "Sheet Name" manual entry | Secondary path only; the primary is OAuth file selection. A pasted URL is parsed and validated (`02` §8.2). |
| "Default Visibility: Public" | **Changed to Private** (`01` §7). |
| "Add #shorts to title automatically", "Auto add #shorts hashtag" | Allowed only as an off-by-default, consent-gated transformation with an exact preview, and **never** truncating user text (`COMP-06`, `04` §14). |
| "Retry failed uploads automatically / Max retries", "Delay between uploads (minutes)" | **Kept** as real settings (`max_upload_attempts`, `upload_spacing_seconds`) (`02` §3). |
| Subscriber counts next to mention suggestions | Permitted, but must carry a "last checked" age and be subject to the retention sweep (`COMP-05`). |

The reference images remain in the repository as visual inspiration. They are not a specification of scope.

## 2. Visual direction

- Desktop-first responsive layout that still works at narrow browser widths.
- Light theme by default: warm off-white or very light grey background, white surfaces, charcoal text, purple/blue primary accent, green success, amber warning, red error.
- Optional dark theme with deliberate contrast, not simple color inversion.
- Rounded cards with restrained shadows, consistent border radii and whitespace.
- Clear sans-serif typography; readable table text and numeric timestamps.
- Compact, information-dense tables balanced by a spacious job detail panel.
- Use recognizable platform icons only according to brand guidelines and avoid suggesting unofficial affiliation.
- Motion should be subtle and respect reduced-motion settings.

## 3. App shell and navigation

Single canonical navigation. **Do not add a second "Reports" or "Google Sheets" entry, and do not add a top-level "Logs" item** — logs are surfaced inside job detail and Settings & Diagnostics.

1. Dashboard
2. Schedule Calendar
3. Upload Queue
4. Media Library
5. YouTube Channels
6. Reports & Google Sheets
7. Settings & Diagnostics

Templates (P1) appears in the navigation only when the feature ships. There is **no platform switcher in the MVP** (§1.2).

Persistent top bar: current worker health, next due job, local timezone, quick add schedule, and a notification/error indicator. **Do not imply jobs execute while the worker is stopped**, and do not imply a job is published while it is only queued locally. Where a connection's token may have expired (see `FEAS-07`), surface it in the top bar rather than only inside settings.

## 4. Required screens

### A. First-run setup wizard
Steps: privacy policy acceptance (**blocking**), local storage/timezone, Google API/OAuth configuration, connect a YouTube channel, verify displayed channel identity, connect Sheets, choose report worksheet/cutoff, test connections, start worker. Each step shows status and an explanation. Provide a "skip for now" option only where safe and label dependent features blocked.

### B. Dashboard
Cards: queued today, uploading, scheduled on YouTube, published (verified), failed/needs review, report sync health, next upcoming job. Include a recent activity timeline and a warning banner for disconnected credentials or a stopped worker. Metrics must come from actual jobs, and the "published" card must count only jobs with an observed remote status.

### C. Schedule calendar
**Month view plus a list alternative** in the MVP; week and day views are P1 (FEAT-017). Show target time with timezone, channel avatar/name, clip title, and current state. Use distinct, accessible status colors and text labels. Clicking an item opens details. **Drag-to-reschedule is not in the MVP**; if added later it requires confirmation and must update the persisted job and remote schedule safely.

### D. Create/edit schedule wizard
Step 1 media. Step 2 channel. Step 3 title/description/tags/mention request. Step 4 publish time and scheduling policy. Step 5 preflight review. Show live character counts, a metadata preview, local file facts, Shorts validation warnings, destination identity, and mention status. Use a review summary before queueing.

The wizard must make clear **whether the displayed time is the upload start or the publication time**, and must show the timezone beside it.

### E. Queue and job detail
Table columns: schedule ID, content title, target channel, target time, progress/status, remote video ID/URL, next action. Filters for channel, date, state, retry-needed and mention state. Job detail shows an event timeline, upload progress, attempts, **local state and remote state as separate facts**, report sync, and error explanation. Destructive actions need clear confirmation.

### F. Media library
Grid/list toggle, thumbnail if safely generated locally for display, filename, duration/resolution, size, file validation, hash/status and number of schedules using the asset. Missing/changed files must be conspicuous. The UI references local files; it must not imply they are backed up to cloud, and it must not offer video processing the product does not perform.

### G. Channel connections
Cards with platform, channel avatar/title/ID, masked identity label, connection health, granted scope explanation, last verified time and actions to verify, reconnect, disable, or revoke. Channel ID and account identity should be copyable. A newly connected channel must be explicitly confirmed.

**Disable and Revoke must be separate actions with different labels, different confirmations, and a stated consequence.** Revoke must state that it removes the token, notifies Google, and purges stored channel data (`02` §7.2).

Each card must state how many channels one identity publishes to, matching the `FEAS-01` verdict. Do not imply that one connected account publishes to several channels.

### H. Reports & Sheets
Show target spreadsheet and tab, last sync time, current report day/timezone, pending rows, failed rows, daily run history and preview table. First write must offer a preview/test write. Surface duplicate/conflict reconciliation instead of silently appending uncertain rows.

**`Report Date`, `Schedule ID` and `Sync Key` are shown as required columns and cannot be deselected.** Any column configuration applies to presentation columns only.

### I. Settings and diagnostics
Sections: appearance, timezone, startup behavior, worker, scheduling/retry (**including `max_upload_attempts`, backoff bounds and `upload_spacing_seconds`**), data path, backup, YouTube API setup (reporting this project's observed verification state), Sheets, log retention, notification preferences, security, and platform limitations. Provide diagnostic export with secrets redacted.

Settings must also show the state of the Authorized Data retention sweep and the project-activity heartbeat, since both are compliance obligations the operator may be asked about (`COMP-05`, `COMP-09`).

## 5. Status language

Status labels are **generated from the canonical `JOB-*` states using the mapping table in `02` §4.3**. This document does not define a parallel list. If a label is needed that the mapping does not produce, the mapping is extended in `02` first.

Two rules override any design preference:

- Do not use "Published" until the platform status supports it, observed by the PublishConfirmationSweep.
- "Queued locally" and "Scheduled on YouTube" must be visually distinct, and "Publication awaiting verification" is a normal third state, not an error.

## 6. Form usability

- Show field errors inline and at the review step.
- Preserve values when validation fails.
- Keep channel selection visible and display channel identity, not only account email.
- Always show timezone beside publish times.
- **Default privacy is Private**, and the control must present public/unlisted/private explicitly rather than defaulting silently.
- Show title/description counters and current platform limits from validated rules.
- Show which metadata fields are actually sent to YouTube versus local-only notes.
- Mention chips must show target channel identity and whether the mention is verified, text-only, or requires manual action.
- Optional metadata transformations must show an exact preview of the resulting text and must not silently alter user input.
- Where the operator's timezone differs from a schedule's stored timezone, show both.

## 7. Data-table conventions

Use sortable columns, server/local pagination when needed, search, filter chips, empty/loading/error states, and accessible keyboard navigation. For destructive actions use explicit labels rather than icon-only controls. Copy buttons provide confirmation. Link to YouTube only after a valid video ID exists.

## 8. Feedback and operations

Worker health should be visible globally, with a stopped/paused/offline state. Display progress for uploads and report sync. Failures should include human-readable summary, technical details expandable on demand, retry eligibility and next action. Never render raw stack traces as the only explanation.

For `JOB_NEEDS_REVIEW`, the primary action is **Resolve**, not Retry, and the screen must explain why retrying could create a duplicate video.

## 9. Accessibility

- Keyboard-operable navigation and dialogs.
- Visible focus outlines.
- Sufficient contrast and non-color-only status communication.
- Labels for icon buttons and progress indicators.
- Screen-reader-friendly tables/forms.
- Respect reduced motion.
- Do not rely on color alone for success/error.
- Avoid time pickers that cannot be used without a mouse.

## 10. Design handoff

Deliver design tokens, component variants, page wireframes, interaction states, responsive breakpoints, validation copy, error/empty/loading content, accessibility checklist and a mapping to FEAT IDs. Implement common controls as reusable components. Use real sample content labelled as demo in mockups; production UI must display DB/API-backed state.

The handoff must also include, for any screen displaying YouTube-sourced content, the attribution treatment required by the YouTube Branding Guidelines (`COMP-08`).

## 11. Traceability

Every screen maps to the `FEAT-*` IDs in `04` §1; every status label maps to `02` §4.3; every MVP-visible feature maps to an acceptance criterion in `03` §6.
