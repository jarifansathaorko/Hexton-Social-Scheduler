# HexScheduler — UI/UX Redesign Implementation Brief

**Purpose:** A detailed, implementation-ready guide and coding-agent prompt for redesigning the existing Hexton Scheduler into **HexScheduler**, using the supplied 9-panel reference collage as the visual north star.

**Target result:** A polished Windows-local desktop web UI with a dark, minimal black/charcoal foundation, restrained violet/lavender glow, compact professional data surfaces, clear scheduling and upload workflows, and the existing app's real behavior preserved.

> This is a redesign of the interface and brand, not a rewrite of the scheduling system. The screenshots are a visual reference, not a source of truth for functionality or data.

---

## 1. Executive design direction

The reference's main strengths are a consistent app shell, dark immersive canvas, compact left navigation, high contrast, violet active states, fine borders, restrained glow, readable cards, and strong hierarchy. Apply those principles to the scheduler domain instead of copying finance-dashboard widgets literally.

### Design principles

1. **Task first, decoration second.** A creator should be able to add a clip, choose a YouTube channel, set a publication time, verify the schedule, and diagnose failures without hunting through the interface.
2. **One system, every page.** Use the same sidebar, top bar, page-title pattern, content gutters, button hierarchy, card borders, status chips, form controls, and empty states on all pages.
3. **Dark but readable.** Use near-black / charcoal surfaces, not pure black for every panel. Reserve glow for the brand, active navigation, key charts, and primary actions.
4. **Truthful status.** Distinguish transfer completed from publication verified. Never present a job as published simply because the upload request returned successfully.
5. **Progressive disclosure.** Show the core action and summary first. Put less-frequent diagnostic information in dedicated tabs/sections rather than making all pages dense.
6. **Make empty states useful.** When there are no clips, channels, jobs, or reports, explain why and give one obvious next action.
7. **Keep the existing application architecture.** Redesign components and layout while preserving APIs, local storage/database, worker lease and heartbeat behavior, OAuth connections, privacy acceptance, queue semantics, and job-state transitions.

## 2. Brand and visual system

### Brand

- Product name: **HexScheduler** (one word, capital H and S).
- Wordmark: `Hex` in off-white and `Scheduler` in a vivid but restrained lavender/violet treatment. Avoid complex decorative lettering.
- App mark: simple geometric hexagon with a subtle scheduling / forward-motion cue. Must remain recognizable at 16–32 px.
- Product descriptor (optional): “Local YouTube publishing scheduler”.
- Do not leave “Hexton” in page titles, visible UI labels, app navigation, dialogs, default export names, or first-run copy. Preserve technical identifiers if renaming them would break paths, migrations, OAuth, or compatibility; update them only through a deliberate safe migration.

### Color tokens

Create one global theme/token layer. Use these as starting points, then tune against screenshot and contrast checks:

| Token | Starting value | Use |
|---|---|---|
| `--hs-canvas` | `#070910` | Whole app background |
| `--hs-shell` | `#090C15` | Sidebar and top bar |
| `--hs-surface` | `#0D111C` | Main cards |
| `--hs-surface-raised` | `#121727` | Menus, raised panels, fields |
| `--hs-surface-soft` | `#171B2C` | Hover/selected support surfaces |
| `--hs-border` | `#252B40` | Standard fine borders |
| `--hs-border-strong` | `#3A315D` | Active / emphasized border |
| `--hs-text` | `#F4F3FA` | Primary text |
| `--hs-text-secondary` | `#B5B8CC` | Descriptions and secondary labels |
| `--hs-text-muted` | `#7E849D` | Timestamps, hints, disabled metadata |
| `--hs-violet` | `#8B5CF6` | Primary brand / chart line |
| `--hs-violet-bright` | `#A78BFA` | Hover, glow edge, key chart peaks |
| `--hs-violet-soft` | `#21163D` | Active navigation fill, selected pills |
| `--hs-success` | `#34D399` | Running, published, verified, healthy |
| `--hs-warning` | `#FBBF24` | Attention needed, waiting/manual review |
| `--hs-danger` | `#FB7185` | Failed, destructive, unverified failure |
| `--hs-info` | `#60A5FA` | In-progress / informational state |

These are product-level theme values, not permission to sprinkle hardcoded hex values throughout components. If the project uses a tokenized design system such as Lumen, map these values into its semantic theme variables at the theme boundary and use semantic tokens in components. Do not maintain two competing palettes.

### Color rules

- Purple = brand, active selection, primary action, and analytical highlight. Do not use purple to mean success or failure.
- Green = running / healthy / completed / verified, with a text label or icon alongside it.
- Amber = needs attention / pending user decision.
- Red/pink = failed / destructive / warning of a potentially harmful action.
- Blue = in progress / neutral informational state.
- Never use color as the only distinction. Add status text and/or a familiar icon.

### Gradients and glow

Use gradients sparingly. Appropriate: a faint violet radial glow behind the dashboard heading, a narrow active-nav glow, a restrained chart fill, and tiny accent lines in selected KPI cards. Avoid full-page bright gradients, animated neon outlines everywhere, glass blur on all surfaces, and glow behind dense paragraphs or form fields.

Example conceptual background only:

```css
background:
  radial-gradient(ellipse at 68% 0%, rgb(124 58 237 / 14%), transparent 34%),
  var(--hs-canvas);
```

### Typography

- Use the current product's installed font if it is legible and stable. Otherwise use a clean UI sans-serif, e.g. `Inter`, with system sans fallback. Avoid downloading fonts at runtime in an offline/local product unless already bundled.
- Page title: 24–28 px, semibold.
- Section title: 15–18 px, semibold.
- Body: 13–14 px.
- Metadata / table hints: 11–12 px, but never below comfortable legibility.
- Metric number: 25–32 px depending on available width.
- Line-height body: around 1.45–1.6.
- Sentence case for UI labels. Avoid long uppercase labels except small overline-style metrics.

### Spacing, geometry, borders

- Use a consistent 4 px base spacing grid.
- App main padding: 24 px at standard desktop width; 16 px at compact width.
- Sidebar width: approximately 224–248 px expanded, 64–72 px collapsed.
- Header: around 64–72 px high.
- Card radius: 12–16 px. Controls: 8–10 px. Pills only for short status/segmented controls.
- Card border: 1 px low-contrast border; avoid heavy shadows on every card.
- Inter-card gap: 14–18 px. Form-field gap: 16–20 px.
- Align card headers, content gutters, table edges, and controls. Avoid one-off pixel offsets.

## 3. Shared app shell

### Desktop layout

**Top bar** across the main window: compact brand mark / wordmark at left if it does not duplicate the sidebar; global search / command field; optional keyboard shortcut hint; notifications only if the app has real notifications; user/profile menu only if there is a real user identity feature. Since this is a local single-user app, do not fake accounts or show a profile avatar that implies a cloud account when none exists. The screenshot's user chrome is a visual reference, not a required feature.

**Left sidebar** fixed/sticky within the app window:

1. HexScheduler wordmark / logo.
2. Primary CTA: **Create Content**.
3. Dashboard.
4. Schedule Calendar.
5. Upload Queue.
6. Media Library.
7. YouTube Channels.
8. Reports & Google Sheets.
9. Settings & Diagnostics.
10. Bottom utility region with real worker state and collapse control, plus theme toggle only if the product still supports light theme.

Navigation requirements:

- Active page: lavender/violet filled surface, slim accent edge or subtle glow, clear icon and label.
- Inactive: neutral text/icon; hover fill is quiet.
- Each item has an actual accessible name and a sensible tooltip in collapsed mode.
- Use one consistent icon family (Lucide or whichever is already installed). No emoji as functional icons.
- Keep navigation labels exactly consistent across pages.
- On smaller widths, collapse to icon mode or provide a keyboard-accessible drawer; never force horizontal page scroll.

**Main content region:** single main landmark, page-specific title + short supporting sentence, one primary action at top right where relevant, then content. Do not put every section into a separate nested card; card boundaries should reflect real groups.

**Privacy-policy alert:** the existing top-wide privacy notice should remain visible when consent is missing, but restyle it to match the app and make the action easy to find. Use an amber border and a low-contrast amber/charcoal surface; a compact dismissible alert is okay only if dismissal does not bypass consent gating. Make the Settings destination a real link. After accepted consent is recorded, remove the notice everywhere without requiring manual reload.

**Global worker indicator:** keep a compact, persistent worker state in the sidebar footer or a small shell status area. It must reflect the worker's actual latest heartbeat and state, not whether the browser tab is open. Clicking it should take the user to diagnostics or reveal real details.

## 4. Page-by-page implementation guide

### 4.1 Dashboard

**Goal:** answer “Is everything okay, what is scheduled next, and what needs my attention?” in one glance.

Recommended hierarchy:

1. Header: `Dashboard`, a short description, optional time-zone-aware date/time, and primary `Create Content` button.
2. KPI row (6 cards at wide desktop, responsive wrap at narrower widths):
   - Pending Scheduled
   - Active Uploads
   - Uploads Completed (transfer completed only)
   - Publication Verified (confirmed public on YouTube)
   - Failed Jobs
   - Manual Review
3. Main analytics row:
   - Upload activity chart for a selected range (e.g. last 30 days), using real local job/event data only.
   - Worker Status card with actual running/failed/stale state, last heartbeat in the user's configured time zone, lease length, and poll interval.
4. Supporting row:
   - Upcoming Publication Times / upcoming scheduled jobs.
   - Recent Upload Outcomes.
   - Needs Attention: failed jobs, uncertain upload outcomes, privacy acceptance missing, channel disconnected, Google Sheets not configured, or other actual blockers.
5. Connected Channels mini-summary only when the app has channel connection data; otherwise a clear empty state with `Connect YouTube Channel`.

Rules:
- Never fill the dashboard with sample counts in production. Show 0 when actual values are zero; show a truthful empty state for chart data.
- Keep “Uploads Completed” distinct from “Publication Verified”.
- The activity chart needs a date-range selector and useful empty/loading/error states.
- The dashboard should avoid repeating the same worker status in many large cards. The sidebar status can be compact; the main Worker Status card contains details.
- The reference has a large chart + side status panel and a compact bottom row; follow that geometry.

### 4.2 Schedule Calendar

**Goal:** view and change intended publication times safely.

Layout:
- Page header: `Schedule Calendar`, supporting text that dates use the configured time zone, and primary `Schedule Upload` / `Create Content` action.
- View controls: previous/next, Today, Month / Week / List. Ensure these controls do what their labels imply.
- Status legend: Scheduled, In Progress, Needs Attention, Warning/Failed, Completed/Verified (use the existing app's canonical states; don't add a fake state just for color).
- Large calendar surface, with a detail/agenda panel at right on wide screens or below it on compact screens.
- Calendar items show a short title, time, channel hint if relevant, and a readable status marker. When many jobs exist on one date, show a `+N more` overflow affordance.
- Clicking an event opens a detail/editor view, not a random route. Rescheduling must go through the existing editor/API validation and enforce pre-upload lead time and time-zone rules.

Behavior:
- Prefer the configured IANA time zone (e.g. `Asia/Dhaka`) for display; do not hardcode Pacific or the computer's locale when user settings override it.
- Do not implement drag/drop unless the product already supports it or the backend validation flow can safely support it. Current UI copy explicitly says drag-to-reschedule is not implemented, so keep the editor-based reschedule flow unless implementation and tests are added deliberately.
- Show unscheduled content separately if the existing model supports jobs with no time.
- Support keyboard navigation and a list/agenda alternative so calendar information isn't color-only.

### 4.3 Upload Queue

**Goal:** make current work, uncertain outcomes, and completed uploads understandable.

Layout:
- Title `Upload Queue`, one sentence explaining that the worker runs separately from the browser.
- Segmented tabs: `In Progress`, `Needs Your Decision`, `Recently Finished` with true counts sourced from the queue.
- Queue filters by status/channel/time only if backed by data.
- Each job row/card: thumbnail if available, title, target channel, status text, progress when progress is real, elapsed/ETA only when reliable, last update, and contextual action.
- For an empty tab, use a useful empty panel (icon, short heading, one explanatory sentence, one CTA if appropriate).

Critical behavior:
- Explicitly separate bytes transferred from publication verified.
- For uncertain upload outcomes, do **not** auto-retry if the app's current safe policy warns of duplicate creation. Show `Needs your decision` and explain the consequence.
- `Retry`, `Cancel`, `Remove`, `Review`, `Open on YouTube`, and similar actions must use the existing state machine and permissions. Destructive/duplicate-risk actions need context and appropriate confirmation.
- Never show arbitrary/animated progress for an operation that doesn't report progress.

### 4.4 Media Library

**Goal:** quickly find and register local video clips that may be scheduled.

Layout:
- Header `Media Library`, description that only the selected path is inspected, and `Add Clip` action.
- Search field; filters/sort only if these are supported by the actual data model (All clips, unused/queued, file type, newest first).
- Responsive thumbnail grid with a consistent aspect ratio (16:9 for horizontal videos), a play/preview affordance if supported, concise title/path, duration and file size when known, and a menu for real clip actions.
- Provide list view only if it improves actual management. Keep grid as the visual default to match the reference.
- Empty state explains that HexScheduler does not scan folders automatically and offers `Add Clip`.

Behavior and trust:
- Preserve the current path-based local file registration workflow; do not convert to automatic folder scanning.
- Preserve current file re-hash/check-before-upload behavior so changed media is blocked instead of silently publishing different content.
- File picker, `Check file`, and `Add to library` need distinct, correct loading/error states.
- Don't invent thumbnails if no thumbnail-generation feature exists. Use a consistent neutral preview tile or existing thumbnail pipeline.

### 4.5 YouTube Channels

**Goal:** connect the precise target channel and manage authorizations safely.

Layout:
- Header `YouTube Channels`, brief explanation that each authorization resolves and confirms a particular channel, and `Connect YouTube Channel` primary action.
- Channel cards/rows with channel name, handle/identifier, avatar or YouTube icon, connected/expired status, last verified time where available, and real actions such as View on YouTube, refresh/reconnect, or disconnect.
- Use connected-channel cards with a small status badge and compact details; avoid adding creator analytics that aren't returned by current APIs.
- Empty state: “No YouTube channels connected yet” + one clear `Connect YouTube Channel` CTA.

Behavior:
- Preserve the existing OAuth flow and channel-identity confirmation step. Never imply that a generic Google authorization necessarily targets the intended channel without confirmation.
- Do not display fabricated subscribers, video counts, channel IDs, or “default channel” labels.
- Disconnect and revoke actions should explain effect; update the UI only when the actual operation succeeds.

### 4.6 Reports & Google Sheets

**Goal:** review what happened and sync truthful records to the user's spreadsheet.

Layout:
- Header `Reports & Google Sheets`, short explanation of the one-row-per-scheduled-upload / idempotent sync-key behavior, and `Open Reporting` or `Configure Google Sheets` action depending on configuration state.
- A setup/consent state if the privacy policy has not been accepted. Reporting and creator-mention workflows remain gated.
- After configured: report sections/tabs such as Daily Completion Summary, Per-Upload Details, Sync Status (use existing supported data), plus filters for date range and channel if available.
- Compact KPI row: total upload attempts, transfer completions, publication verified, failed, manual review/uncertain. Use exact existing definitions.
- Use a semantic table for records: time, video/title, channel, state, YouTube video ID when available, sync result, actions/details. Use pagination or virtualized rows if actual data can grow substantially.
- Google Sheets status card should say `Not configured`, `Connected`, `Syncing`, `Needs attention`, or the actual API state.

Behavior:
- Keep durable sync keys/idempotency, preventing duplicate sheet rows on retry.
- Only mark a row Published after both a YouTube video ID and verified remote evidence exist, as the current application explains.
- Avoid optimistic success messages before Google reports success. Include useful error messages and a retry action only when safe.
- Don't show fabricated reports or pretend the Google Sheet exists before configuration.

### 4.7 Settings & Diagnostics

**Goal:** allow scheduling preferences to be changed safely and show the real local runtime / privacy state.

Use a Settings page with clear internal tabs/sections. Suggested order:

1. **Scheduling**: time zone, pre-upload lead time (minutes), warning band (minutes), missed-window grace (minutes), local data retention (days), save/discard.
2. **Connections & Channels**: status and actions that already exist for YouTube authorization. Do not duplicate the full channels-management flow if linking to the channel page is clearer.
3. **Google Sheets reporting**: configuration and state, relevant authorization and privacy gating.
4. **Worker & Runtime**: running/stopped/stale signal, last heartbeat, lease length, poll interval, connector, quota remaining and reset timing, data directory, credential storage type. Values read from actual runtime; values that only change on worker restart need explicit explanation.
5. **Privacy Policy**: show the complete required policy and required-reading links; name/acknowledgement input and accept action. Consent must be saved using the existing durable policy mechanism.
6. **Diagnostics Export**: explain what is included and excluded, and download the actual JSON snapshot.

Design:
- Use tabs or section navigation, with one clear content area at a time. Don't cram the whole long settings page into a wall of compact cards.
- Group fields into a tidy 2-column form at wide desktop and one column at narrow widths. Every field has a visible label, helper text if useful, validation and save feedback.
- Distinguish editable from read-only system facts. Use disabled/readonly treatments intentionally.
- `Save`, `Discard`, `Accept Policy`, `Download Diagnostics`, `Open Reporting`, and `Restart Worker` must map to real operations only. Avoid adding a restart button if no safe restart action exists.
- Preserve the actual privacy policy text and semantics. Never use the redesign to weaken or skip consent gating.

### 4.8 Create Content flow (global primary action)

This is not a sidebar page in the reference, but it is the app's most important action and must receive equivalent UI polish.

Recommend a focused multi-step modal/sheet only if the existing form is short enough; otherwise use a dedicated full page. Suggested steps:

1. Select clip from Media Library (or add/register one).
2. Choose the target YouTube channel and enter/confirm required title/description/visibility/privacy fields supported by the existing model.
3. Schedule publication time using the configured time zone or choose to leave unscheduled if the app permits it.
4. Review the summary, visibility, selected media identity, target channel, publication time and any metadata transformation consent.
5. Confirm scheduling.

Requirements:
- Preserve input when validation fails and offer clear inline errors.
- Show only options that really exist in the product.
- Default visibility/metadata transformation must follow current privacy and product rules.
- Explain if the video will be private unless the user explicitly authorizes a public test on a designated test channel.
- After successful scheduling, show a truthful success message and link to calendar or queue. Don't claim it has uploaded or published yet.

---

## 5. Shared component patterns

Create reusable components instead of independently styling every page:

- `AppShell`: sidebar, top bar, main scroll region, worker footer, global privacy alert.
- `SidebarNav` / `NavItem`: consistent icon, label, selected, hover, focus, collapsed states.
- `PageHeader`: title, description, primary and secondary actions.
- `SectionHeader`: title + optional helper text + one contextual action.
- `MetricCard`: label, value, optional trend only when calculated from actual historical data, optional small icon.
- `StatusBadge`: one canonical mapping from backend states to icon + label + semantic color.
- `WorkerStatusPanel`: real health/heartbeat details and clear healthy/stale/failed rendering.
- `EmptyState`: message, description, one primary next action.
- `PrivacyAlert`: global consent notice and settings destination.
- `FilterBar`: consistent search, date/time, channel and status filters.
- `MediaCard`: thumbnail, duration, concise metadata, menu.
- `ChannelCard`: verified target channel identity, connection state, actions.
- `QueueJobRow`: title, target, state, genuine progress, last update and safe actions.
- `DataTable` wrapper: empty/loading/error states, accessible headers, row actions, overflow handling.
- `SettingsField`: label, helper copy, control, validation state.
- `ConfirmActionDialog`: only for irreversible or duplicate-risk actions.
- `Toast/InlineFeedback`: success/error response after an operation returns.

Keep status enum-to-UI mapping in one place so a backend state change does not cause inconsistent colors and labels across pages.

## 6. Interaction and UX requirements

### Button hierarchy

- **Primary**: violet filled, for one most important action per page/region.
- **Secondary**: dark raised/outline, for adjacent alternatives.
- **Tertiary**: quiet text/icon actions, for low-frequency reversible actions.
- **Destructive**: red semantic style, only for removal/revocation/cancel if those actions are actually destructive.
- Buttons need visible hover, focus, pressed, disabled, and loading states. Disable duplicate submits while a request is pending.

### Forms

- Visible labels; don't rely on placeholder text as a label.
- Explain expected units directly in labels, e.g. “Pre-upload lead time (minutes)”.
- Validate time zone as an IANA zone, numeric bounds, required fields, and server acceptance. Show server validation near the field when possible.
- Changed-but-unsaved form state should be obvious. Discard should restore the last saved values, not merely look active.

### Notifications and errors

- Use inline errors beside the affected item and a persistent summary for serious issues.
- Don't show every benign action as a toast; use toast for saved/completed/failed operations that need brief feedback.
- Error copy: describe what failed, whether any action already reached YouTube/Google, the next safe step, and potential duplicate risk where relevant.
- Never swallow errors behind a generic “Something went wrong” when known detail exists.

### Loading and empty states

For every data surface implement:
- Initial loading/skeleton (not fake values).
- Empty, with a relevant CTA.
- Success/populated.
- Error and retry where retry is safe.
- Disabled/unauthorized or privacy-gated state where applicable.
- Stale data/worker state where relevant.

### Accessibility

- Keyboard-operable navigation, dialog, calendar, tabs, dropdowns, tables and all job actions.
- Visible focus indicators, not just a color change.
- Correct semantic heading order and `main` landmark.
- Icon-only buttons have accessible labels and comfortable hit areas.
- Status includes text as well as color.
- Check contrast on all text surfaces and selected/disabled states.
- Respect reduced-motion preferences. Use quick transitions only; no perpetual glow animation.
- At 200% zoom, important workflows should remain usable without overlapping controls.

## 7. Responsive behavior

Treat the screenshot as a desktop-first reference; the actual scheduler should be responsive to content pressure:

- **Wide desktop (>= 1440 px)**: expanded sidebar, six KPI cards across when each remains readable; chart + worker panel in two columns; three supporting panels across.
- **Medium desktop (1024–1439 px)**: expanded or collapsible sidebar, KPI cards 3×2, analytics 2 columns when space permits, supporting sections 2 columns.
- **Tablet/narrow window (768–1023 px)**: sidebar collapses to icon rail or drawer; KPI cards 2–3 per row; chart and worker panel stack if labels become cramped; calendar may default to week/list.
- **Small window (<768 px)**: drawer navigation, one-column cards, horizontally scrollable tables only within their own region, list/agenda alternative for calendar, no global horizontal page overflow.

Avoid hardcoded desktop widths. Use grid/flex minmax, `min-width: 0`, and container-driven changes. Keep main action reachable at compact width.

## 8. Motion and polish

- Transition color/border/background/transform over about 120–180 ms.
- Animate only meaningful changes: sidebar collapse, dialog entry, progress transition if real progress updates.
- No blinking status dots. A small static green dot plus “Running” is enough; update its accessible text when state changes.
- Charts can have a gentle initial reveal only if it respects `prefers-reduced-motion` and doesn't delay data access.
- Use a consistent line icon set, 16–20 px for navigation / controls and 20–24 px for prominent empty state.
- Use thin inner border and a very subtle shadow to create depth. Avoid making the UI look like a neon gaming launcher.

## 9. Technical implementation notes for the coding agent

- First inspect the complete repository, running app, framework, package manager, global stylesheet, routing, component structure, API/service layer, worker processes and tests. Do not assume React, Tailwind, Astro, or any particular stack until verified.
- If it is a browser UI in a Python-served local application, preserve the server routes and template system. If it is React/Next, preserve that structure. Do not migrate frameworks to match the mockup.
- If Lumen UI is already installed or explicitly adopted by the project, check actual installed package versions and use the matching target component APIs. Do not silently add or upgrade Lumen packages. Use semantic theme tokens and existing component primitives, rather than re-implementing common controls.
- If existing component libraries already cover the needed controls, prefer them rather than stacking multiple competing libraries.
- Use the provided mockup image as a visual reference asset for review, not as an entire background image and not as production content. Rebuild interface elements as accessible UI components.
- Separate UI state, domain types and services. The UI must call existing services rather than reimplement upload or scheduling logic.
- Use existing endpoints/schema and enum values as source of truth. Don't create new fake data shapes to make a card easy to render.
- Avoid risky renames of directories, local data folders, credential keys, database keys, OAuth redirect URIs, event names, API routes or settings identifiers unless there is a fully backward-compatible migration plan and tests. Brand visible strings and packaging metadata separately from persisted compatibility identifiers.
- Do not replace credentials, token storage, Windows Credential Manager/DPAPI behavior, local file paths, job persistence, or worker process architecture as part of UI work.
- No unrelated dependencies. If a new dependency is justified (charts/icons/calendar), check whether a suitable dependency already exists and keep it minimal.
- Do not mock a successful operation, fabricate API results, swallow exceptions, or change semantics just to make the UI prettier.

## 10. Suggested implementation phases

### Phase A — Audit and establish baseline

1. Record framework, runtime, package manager, routes, styles and component libraries.
2. Map each current screen to routes/components and list the services/endpoints/state models it uses.
3. Capture screenshots of every existing page at desktop and compact widths.
4. Inventory state transitions and edge cases for queue, publication verification, OAuth, Google Sheets, privacy consent and worker health.
5. Run current tests/typecheck/build, record existing failures before making changes.

### Phase B — Theme and application shell

1. Add global design tokens/theme and a safe dark-mode root.
2. Rebrand visible strings and logo/mark to HexScheduler.
3. Build the shared shell, sidebar, top bar, global privacy alert, worker indicator, typography, buttons and card patterns.
4. Verify routes and interactions still work before restyling page internals.

### Phase C — Pages

Recommended order:
1. Dashboard (establish shared card/metric/status patterns).
2. Media Library and YouTube Channels (establish media and connection patterns).
3. Upload Queue (stateful status and safety UX).
4. Schedule Calendar (date/time-zone and event states).
5. Reports & Google Sheets (table, sync and privacy gating).
6. Settings & Diagnostics (forms, policy, runtime, exports).
7. Create Content flow (primary workflow, integrated across pages).

### Phase D — Responsive and accessibility pass

Test keyboard-only use, focus return after dialogs, screen-reader labels, contrast, reduced motion, zoom and different widths. Fix overflow and cramped status/labels.

### Phase E — Verification and handoff

Run lint, typecheck, unit/integration tests, production build, and the narrowest possible end-to-end tests. Manually test the whole create → schedule → worker claims → upload → publication verification → report sync process where test fixtures/environment permit. Capture before/after screenshots and list anything that could not be verified. Inspect Git diff and ensure no secrets, local credential data, user videos or generated databases were committed.

## 11. Acceptance checklist

### Visual
- [ ] Every page uses the same HexScheduler shell and dark theme.
- [ ] Black/charcoal canvas, understated borders, restrained violet accents and clear typography match the reference direction.
- [ ] Primary CTA is consistent and unmistakable.
- [ ] Cards are only used for meaningful grouping; no excessive nesting.
- [ ] All content aligns on a consistent spacing grid.
- [ ] At least desktop and compact-width screenshots have been reviewed.

### Functional
- [ ] Every navigation item routes correctly and active state matches current route.
- [ ] Create Content uses the real application workflow.
- [ ] Calendar time zone, lead-time and missed-window validations still apply.
- [ ] Queue tabs and counts come from real job state.
- [ ] Uncertain upload outcomes remain manual decisions if required by current safety policy.
- [ ] Transfers and publication verification are never conflated.
- [ ] Media registration and file re-hashing still work.
- [ ] YouTube connection still confirms the exact target channel.
- [ ] Google Sheets reporting remains gated by consent/configuration and idempotent on retry.
- [ ] Settings save/discard and diagnostics export produce real results.
- [ ] Worker status reflects the separate process's actual heartbeat.

### Accessibility and robustness
- [ ] All key flows work with keyboard only.
- [ ] Icon-only controls have accessible names.
- [ ] Status communicates meaning in words/icons, not color alone.
- [ ] Loading, empty, error, disabled and success states exist for all major surfaces.
- [ ] No persistent animation is required to understand status.
- [ ] No horizontal overflow at supported narrow widths or 200% zoom.

### Engineering
- [ ] Existing build/tests were run before and after.
- [ ] No unnecessary framework migration or dependency replacement.
- [ ] No accidental changes to upload/scheduling domain behavior.
- [ ] No secrets or local runtime data in Git diff.
- [ ] Rebrand doesn't break app data directories, credential stores, OAuth callbacks or saved preferences.
- [ ] A summary of changed files, verification, known limitations and screenshots is delivered.

---

# 12. Copy-ready implementation prompt for the coding agent

You are a senior product designer and senior software engineer working in the existing HexScheduler repository. Your task is to implement the UI/UX redesign described in this brief and shown in the attached reference screenshot, **inside the current application**.

## Mission

Redesign the existing Hexton Scheduler into **HexScheduler**, a polished local-first YouTube publishing scheduler. Follow the reference visual language: dark near-black/charcoal surfaces, delicate borders, restrained violet/lavender highlights, compact rounded panels, subtle selective glow, consistent left navigation, clean sans-serif typography, uncluttered hierarchy, meaningful empty states and a dashboard chart + worker-status layout.

The redesign must retain the current product features and operational semantics. This is not permission to replace the backend, rewrite scheduling, change OAuth, alter credential storage, migrate frameworks, or fabricate behavior.

## Required process — do these in order

1. **Inspect before editing.** Read repository instructions and inspect the repository tree, package manifests/lockfiles, current route/page structure, global CSS, shared components, API/service modules, data models, job state machine, worker process, OAuth/channel connection flow, Google Sheets sync, privacy policy handling, diagnostics export, and tests. Determine the actual framework and existing UI libraries. Do not guess.
2. **Capture a baseline.** Run existing tests, lint, typecheck and build where available; note failures that predate the change. Run the application and capture screenshots of every page at a wide desktop and compact viewport if the environment allows. Note existing behavior and route paths.
3. **Create a page-to-service map.** For each screen, identify the existing source of truth for all displayed values and the action/service called by each control. Reuse those. If a visual component has no corresponding data source, omit it instead of inventing fake metrics.
4. **Create a design-token layer.** Use the palette, typography, spacing, radii, borders, semantic status mappings and motion/accessibility rules from this report. Prefer the project's existing token/primitive system. If Lumen UI is installed or explicitly in use, use the Lumen MCP/documented contract for the detected framework and actual installed version; never assume package versions or invent component props. Apply theme variables at one boundary and use semantic tokens in components.
5. **Implement a shared shell.** Build/reuse `AppShell`, `SidebarNav`, `PageHeader`, global privacy alert, persistent real worker status, consistent controls, surface components, and canonical status badge mapping. Keep current routes and worker/backend contracts intact. Rebrand visible product strings to HexScheduler.
6. **Restyle every existing page.** Implement the page-specific guidance in this report for Dashboard, Schedule Calendar, Upload Queue, Media Library, YouTube Channels, Reports & Google Sheets, and Settings & Diagnostics. Also redesign the global Create Content flow. Do not stop at the dashboard or produce static mockup-only pages.
7. **Wire every interaction.** Sidebar navigation, filter tabs, calendar navigation, event open/edit, media file selection/check/add, channel connection/reconnect/disconnect, reporting setup and sync, settings save/discard, policy acceptance, diagnostics export, and upload actions must be wired to the real existing behaviors. Do not add fake controls. If a requested action is unsupported, do not make a deceptive clickable button; keep the current behavior, improve explanation, and record the limitation.
8. **Preserve risky invariants.** Never equate upload-transfer completion with public publication verification. Keep uncertain upload outcomes from being auto-retried where that can create duplicates. Preserve sync-key idempotency for Google Sheets. Keep privacy policy gates. Preserve exact YouTube channel identity confirmation. Preserve file path registration / rehash safeguards. Preserve worker lease and heartbeat truth. Do not change OAuth scopes or credential storage while redesigning.
9. **Implement all product states.** Each page or major content region must handle initial loading, populated, empty, errors, disabled/consent-gated, and stale/worker-failure states as applicable. Use meaningful copy and only safe retry actions.
10. **Responsive and accessible.** Support wide desktop, smaller desktop, and compact widths without global horizontal overflow. Keep keyboard access, visible focus, labeled controls, accessible dialog behavior, meaningful table semantics, status labels in text, contrast, and reduced-motion support.
11. **Verify.** Run formatting, lint, typecheck, tests and production build. Test real UI navigation and important flows. Review browser/runtime logs. Compare fresh screenshots against the provided reference and current baseline. Fix layout, contrast, truncation and overflow. Inspect the full diff for accidental domain changes, removed behavior, secrets, local data, and unintended dependency changes.
12. **Report truthfully.** Finish with a concise implementation summary: pages/components changed, design system introduced, behavior preserved, tests/build commands and results, screenshots produced, pre-existing failures, and any remaining limitations. Never claim a check passed unless you ran it.

## Design non-negotiables

- Visual style follows the attached nine-panel HexScheduler reference.
- One shared theme and component language across the entire app.
- Violet is a brand/selection accent; green/amber/red/blue remain semantic status colors.
- Use restrained glow and avoid heavy glass effects behind dense content.
- No fake numbers, sample jobs, fabricated channel analytics, invented ETAs or false success states in production.
- No reimplementation of application/business logic in the UI layer.
- No unnecessary dependencies, framework changes or destructive data migrations.
- No automatic rescheduling drag-and-drop unless it is implemented safely and tested against current validation; preserve the editor-based flow otherwise.
- Visible brand becomes HexScheduler, but legacy identifiers and local data paths are changed only through a safe compatibility plan.

## Definition of done

All existing pages are visually coherent under the new design; Create Content and primary actions still work; actual data and worker status are shown truthfully; privacy and duplicate-safety rules remain intact; responsive and accessibility checks are performed; and the final report distinguishes verified results from anything not tested.

Begin with repository inspection and a brief plan, then proceed without asking for confirmation unless a change would be destructive or requires unavailable credentials/environment setup. If the scope is too large for one patch, complete the shell and highest-priority screens first, but do not hide incomplete pages: list exactly what remains.
