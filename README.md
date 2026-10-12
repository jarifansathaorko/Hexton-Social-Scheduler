# Local YouTube Shorts Scheduler — PRD pack

This folder contains six coordinated Markdown PRDs for a Windows-first, local YouTube Shorts scheduling application.

## Documents

1. `01-standard-prd.md` — product requirements and overall scope.
2. `02-technical-prd.md` — local architecture, YouTube OAuth/API, worker, database, Sheets and security.
3. `03-mvp-prd.md` — staged MVP and acceptance criteria.
4. `04-feature-prd.md` — detailed feature behavior and states.
5. `05-visual-uiux-prd.md` — web UI screens and design system.
6. `06-ai-coding-prd.md` — implementation phases, testing, release gates and coding-agent behavior.
7. `07-feasibility-checklist.md` — proof-of-concept items that gate implementation, with verdict tracking.
8. `08-phase0-feasibility-report.md` — what Phase 0 actually ran, what it proved, what it could not.

Supporting material: `phase0-poc/` — the isolated proof of concept (code, tests, recorded evidence), and three UI reference images. They are **visual inspiration, not scope**. Where an image conflicts with the PRDs — third-party platform tabs, thumbnail controls, optional report identity columns, Public as the default privacy — the PRDs win. See `05-visual-uiux-prd.md` §1.2.

## Correct implementation order

Read in this order, and respect the gates:

| Step | Document | Gate before proceeding |
|---|---|---|
| 1 | `01` | Product intent understood; scope boundaries clear |
| 2 | `02` | Mechanism agreed; open decisions in `02` §15 acknowledged |
| 3 | `04` | User-visible behavior specified |
| 4 | `05` | Presentation and status labels defined |
| 5 | `03` | What actually ships in the MVP is fixed |
| 6 | `06` | Working protocol and release gates understood |
| 7 | `07` | **All `FEAS-*` verdicts recorded before P0 feature work** |
| 8 | `08` | **Phase 0 gate reviewed and the `blocked` verdicts accepted (or the prerequisites supplied)** |

**The single most important rule:** `FEAS-01` (can one identity publish to more than one channel?) and `FEAS-02` (what does an unverified API project do to privacy?) block the product premise.

**Status after Phase 0 (2026-10-11):** every `FEAS-*` item now carries a written verdict. No item is `verified` — each one ultimately asks a question about Google's behaviour, or about a reboot that has not happened, and the Cloud project, consent configuration, test channel and test spreadsheet were not available. Two sub-results *are* verified (the Windows Credential Manager round-trip, and the `tzdata` failure reproduced and fixed), and two negative results were **observed rather than assumed** (logon-trigger and SYSTEM scheduled-task registration are both denied from an unelevated shell). See `08-phase0-feasibility-report.md` §9 for the exact prerequisites. **No P0 UI work starts until `FEAS-01` and `FEAS-02` are run and pass.**

Dependency-ordered build sequence, from `06` §2.1:

```
FEAS-* gate → foundation → jobs/media → identity → upload → scheduling
           → confirmation sweep → Sheets → UI → compliance → packaging
```

## Recommended use

Give all eight files to the coding agent at the beginning. Ask it to read and summarize the constraints, inspect the target repository, and produce the feasibility verdicts before implementing. Keep the documents in one folder and update them as decisions change.

## Non-negotiable technical notes

*(Revised 2026-10-11. See "Correction log" at the end of this file for what changed and why.)*

- YouTube uploads require OAuth user authorization; ordinary personal-channel uploads must not assume service-account authentication works. Service accounts and the `onBehalfOfContentOwner` / `onBehalfOfContentOwnerChannel` parameters are documented as **exclusive to YouTube Content ID partners**.
- **The credential is the unit of authorization, not the channel.** `videos.insert` publishes to the channel resolved from the authorizing credential. No documented parameter lets an ordinary user target an arbitrary channel ID. A `channel_id` supplied by the UI is never authority.
- `status.publishAt` **is** a settable field on `videos.insert`. The system should send `status.privacyStatus=private` together with `status.publishAt` in the same insert body, then verify the remote scheduled state. Any later `videos.update` must re-assert `status.privacyStatus=private`.
- Publication is delegated to YouTube once the schedule is accepted, so the Windows app need not be awake at the publish instant. The consequence is that the app also **cannot know whether the video actually published** unless it polls. A publish-confirmation sweep is therefore a required component, not an optional extra.
- **Unverified YouTube API project behaviour is currently UNKNOWN, not "private by default".** Google's live documentation states both behaviours on different pages. Treat public publishing as blocked until it is empirically proven on the actual API project, always set `status.privacyStatus` explicitly, and never rely on a server-side default.
- The YouTube creator UI's @mention workflow uses selection from suggestions, and the documented `videos` resource exposes **no** mention entity. Do not claim a plain `@handle` string is a verified mention. The PRDs require a safe manual fallback and forbid private endpoints or UI automation to evade the limitation.
- `videos.list` no longer supports `mine=true` or `chart=mostRecent`. Ambiguous-upload reconciliation must use the documented ladder in `02-technical-prd.md`, and any outcome it cannot prove goes to manual review — never to an automatic re-insert.
- "Zero duplicate videos" is **not achievable** with documented APIs. The enforceable guarantee is: **zero duplicates caused by automatic retry**.
- Google Sheets is a report destination; the local database is the canonical job history. Sheet writes require idempotent reconciliation driven by a stable `Sync Key` column and a read-before-append strategy.
- The web UI can be local, but background jobs must run in a persistent backend/worker independently from the browser tab, **as the same interactive Windows user that authorized the accounts**, so that Credential Manager and user-scoped DPAPI remain readable.
- Disconnecting a channel and revoking Google access are **different operations** with different consequences. Only revocation is governed by the YouTube API Services Terms of Service.

## Running the build (updated 2026-10-12)

Everything below runs with no Google Cloud project and can publish nothing. The Phase 0
gate (`08` §9) has not passed, so the real `YouTubeConnector` and live Sheets are still
deliberately unwired; the app runs against the fake connector and says so in the UI.

### Prerequisites

- Windows, Python ≥ 3.12 (verified on 3.14.7), Node 24 / npm 11.
- One virtualenv at `backend/.venv`.

### Backend

```powershell
cd backend
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m hexton migrate      # serve does NOT run migrations
.venv\Scripts\python.exe -m hexton serve        # API + worker on http://127.0.0.1:8787
```

Subcommands: `info` (resolved configuration), `migrate`, `revision`, `worker` (a worker
in its own terminal), `serve --no-start-worker`, `dev` (API and worker in one process).

### Frontend

```powershell
cd frontend
npm install
npm run build                                   # tsc --noEmit && vite build -> dist/
```

`serve` and `dev` serve `frontend/dist` from the API's own origin, so **rebuild after any
frontend change** — editing source alone has no effect on what the server hands out.

### Tests

```powershell
# backend — 562 tests, lint and format
cd backend
.venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q
.venv\Scripts\python.exe -m ruff check hexton tests
.venv\Scripts\python.exe -m ruff format --check hexton tests

# frontend — typecheck, eslint, production build, 21 Playwright tests
cd frontend
npm run typecheck
npm run lint
npm run build
npx playwright test        # npm test does build + playwright
```

Playwright starts its own server (`e2e/server.mjs`) on `127.0.0.1:8797` against a
throwaway data directory in `%TEMP%\opencode\hexton-e2e`, seeds it, then runs
`setup → core → worker` in order against one database.

### Configuration

Every setting is an environment variable with the `HEXTON_` prefix (or a `.env` beside
the backend). The ones that change behaviour:

| Variable | Values | Note |
|---|---|---|
| `HEXTON_CONNECTOR_KIND` | `fake` (default) / `youtube` | `fake` is refused in a packaged build |
| `HEXTON_CREDENTIAL_STORE_KIND` | `auto` (default) / `windows_credential_manager` / `dpapi_file` / `plaintext` | `plaintext` additionally needs `HEXTON_ALLOW_PLAINTEXT_CREDENTIALS=true` **and** `HEXTON_ALLOW_PLAINTEXT=1` |
| `HEXTON_DATA_DIR` | path | defaults to `%LOCALAPPDATA%\Hexton` |
| `HEXTON_HOST` / `HEXTON_PORT` | `127.0.0.1` / `8787` | loopback only; `0.0.0.0` is not offered |

For development, leave `HEXTON_CONNECTOR_KIND=fake` — that is what the banner, the queue
and the fixtures in `frontend/e2e/` are written against.

## Known limitations (2026-10-12 remediation)

Recorded deliberately, with the reason, so nobody re-litigates them as bugs:

1. **Same-user local processes are inside the trust boundary.** The session cookie is
   minted for any local client that can reach `127.0.0.1`, not only for a browser.
   Browser CSRF and DNS rebinding *are* blocked (forged `Origin` and `Host` both 403).
   A non-browser process running as the same Windows user can already read the session
   key, the database and the credential store, so restricting the API to browsers would
   add no security. The alternative — a long-lived credential the operator manages by
   hand — is a worse trade for a single-user local app. (`F-31`.)
2. **`os.chmod(..., 0o600)` is not a user-only ACL on Windows.** The C runtime honours
   only the read-only bit. What protects `session.key` is the inherited DACL of
   `%LOCALAPPDATA%`; what protects the credential file is DPAPI user scope, which makes
   the bytes unreadable to any other account regardless of file mode. No ACL tooling is
   written for this. (`F-25`.)
3. **`mypy --strict` is not a gate.** It reports 46 errors across 17 files; `ruff`,
   `eslint`, `tsc --noEmit` and the two test suites are the enforced gates.
4. **The Phase 0 gate is still closed.** No live-Google `FEAS-*` item is `verified`, so
   no claim about real YouTube behaviour has been made in this codebase.

## Correction log — revision 1.1 (2026-10-11)

An architecture review on 2026-10-11 checked every load-bearing claim in this pack against the live official documentation. The following corrections are now applied across `01`–`06`:

| # | Correction | Reason |
|---|---|---|
| C-01 | Unverified-project behaviour reclassified from fact to **UNKNOWN** | Two live Google pages currently contradict each other on whether uploads from unverified projects are restricted to private |
| C-02 | Scheduling now uses **insert-time `publishAt`** rather than a mandatory second `videos.update` | `publishAt` is a documented settable field on `videos.insert`; the two-call design wasted 50 quota units per job and created a failure window where a video existed unscheduled |
| C-03 | Added the **"never been published"** constraint on reschedule | Documented restriction on `status.publishAt`; the previously promised reschedule path does not exist for already-published videos |
| C-04 | Added **PublishConfirmationSweep** to P0 | Delegating publication to YouTube means success cannot be shown without polling |
| C-05 | Success metric reframed from "0 duplicate videos" to "0 duplicates caused by automatic retry" | An ambiguous insert cannot be proven safe or unsafe with documented APIs; forcing a false guarantee drives unsafe retries |
| C-06 | Added the documented **ambiguous-upload reconciliation ladder** | The previous text demanded reconciliation without naming any available mechanism; `videos.list(mine=true)` is no longer available |
| C-07 | **Disconnect split into Disable and Revoke**, with a 7-day Authorized Data purge on revoke | The YouTube API Services Terms of Service require programmatic revocation and deletion of Authorized Data within 7 calendar days |
| C-08 | Added a **30-day Authorized Data retention** rule and required refresh columns | Same Terms of Service limit stored Authorized Data without addressing it |
| C-09 | Added a full **compliance section** (privacy policy, consent gate, ToS/Google Privacy links, one API project per client, 90-day activity heartbeat, no unconsented content modification) | Required by the YouTube API Services Developer Policies and entirely absent from revision 1.0 |
| C-10 | Added **`tzdata` as a required dependency** and the **interactive-user worker constraint** | Windows CPython ships no system IANA timezone database; user-scoped DPAPI and Credential Manager entries are unreadable from a Windows Service or a non-interactive Task Scheduler principal |
| C-11 | Collapsed three competing status vocabularies into **one `JobState` enum + one `RemoteState` record + a display mapping** | The pack previously specified 11, 13 and 17 incompatible states with no mapping |
| C-12 | Narrowed the **future connector interface** to five methods plus a capability flag, and removed the optional scheduling library | Premature abstraction for MVP; a polling loop over the database is simpler and more reliable on a single PC |
| C-13 | Added a **default privacy of Private** and forbade relying on server defaults | Consequence of C-01 and the mockups defaulting to Public |
| C-14 | Mockup-only features (third-party platform tabs, thumbnail upload/generation, togglable Sheets columns, manual spreadsheet URL entry) declared **out of MVP scope** | They contradict the PRDs and, for platform tabs, risk implying affiliation under the Branding Guidelines |
| C-15 | Added the **credential-is-authority invariant** and mandatory operator confirmation of channel identity | Converts an existing warning into a type-level rule that prevents posting to the wrong channel |

## Phase 0 findings not yet written back into `01`–`06`

Phase 0 (`phase0-poc/`) re-verified the documentation and pinned it as `DF-01`–`DF-28` in `phase0-poc/hexton_poc/docfacts.py`. Four of those pins are consequential enough that `01`–`06` must eventually be corrected, but **they have deliberately not been edited yet** — that is a separate documentation pass, tracked here so the gap cannot be lost:

| Pin | Finding | Where it must land |
|---|---|---|
| `DF-06` | `videos.update` **deletes** any already-valued property you omit from the request. A partial update therefore deletes the description, not merely the mention inside it. | `02` §6.6 / `04` mention workflow — strengthens `D-6` |
| `DF-09` | `channels.list(managedByMe=true)` is **partner-only** (CMS / `onBehalfOfContentOwner`). It is not a brand-account path for an ordinary user. | `01` §5 / `07` `FEAS-01` |
| `DF-18` | A Brand Account **may** authorize the project's OAuth scopes if a test user manages it. Consent for a brand identity is documented; where `videos.insert` then lands is not (`DF-27`). | `01` §5 / `02` §6.1 |
| `DF-20`, `DF-21` | Authorized Data retention is **7 calendar days** after client-initiated revocation, **30 calendar days** where revocation came via Google's security settings page or the token is no longer refreshable, plus periodic token revalidation. | `02` §15 — `D-1`'s deadline is resolved; classification and sweep cadence remain open |

`07` and `08` already reflect all four.

## Official documentation checked for this draft

All pages below were re-checked on **2026-10-11** for revision 1.1.

- YouTube upload: https://developers.google.com/youtube/v3/guides/uploading_a_video
- YouTube `videos.insert`: https://developers.google.com/youtube/v3/docs/videos/insert
- Video resource / `publishAt`: https://developers.google.com/youtube/v3/docs/videos
- `videos.list` (current filters): https://developers.google.com/youtube/v3/docs/videos/list
- `channels.list` (`mine=true`): https://developers.google.com/youtube/v3/docs/channels/list
- `activities.list`: https://developers.google.com/youtube/v3/docs/activities/list
- Quota calculator (current costs): https://developers.google.com/youtube/v3/determine_quota_cost
- Resumable uploads: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- YouTube OAuth web-server flow: https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps
- Desktop OAuth: https://developers.google.com/identity/protocols/oauth2/native-app
- OAuth Testing mode: https://support.google.com/cloud/answer/15549945
- Quota and compliance audits: https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
- YouTube channel mentions: https://support.google.com/youtube/answer/9637404
- **YouTube API Developer Policies** (revocation, retention, privacy policy, branding, inactivity): https://developers.google.com/youtube/terms/developer-policies
- **YouTube API Services Terms of Service**: https://developers.google.com/youtube/terms/api-services-terms-of-service/
- Google Sheets scopes: https://developers.google.com/workspace/sheets/api/scopes
- Google Sheets append: https://developers.google.com/sheets/api/reference/rest/v4/spreadsheets.values/append

### Known documentation inconsistencies to re-check before release

1. **Unverified project behaviour.** The `videos` resource page states uploads from unverified API projects "will have them set to private by default, and must undergo an audit to make them public", while the `videos.insert` `status.privacyStatus` parameter states "Videos uploaded from unverified API projects are not restricted to private viewing mode." Both were live on 2026-10-11. Record the observed behaviour in the project's own API project (`FEAS-02`).
2. **Quota calculator auto-summary.** The page's generated summary still quotes a historical 1600-unit cost for `videos.insert`, while the authoritative table shows a separate 100/day bucket at 1 unit per call. Always read the table, not the summary.
