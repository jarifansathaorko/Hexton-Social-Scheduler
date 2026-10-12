# Technical Feasibility Checklist

**Version:** 1.2 (created 2026-10-11; Phase 0 partial results recorded 2026-10-11)

This document holds everything the product depends on that is **not yet established by documentation alone**. It is the gate between assessment and implementation.

## How to use this document

- **No live-API result is recorded yet.** Every item that requires a Google Cloud project, a consent configuration, a test channel or a test spreadsheet is `blocked` and says exactly what is missing. Nothing in this pack should be read as claiming that an external integration has been tested.
- Each item has a hypothesis, an experiment, prerequisites, pass criteria, a failure fallback, and an evidence location. Fill in the verdict, the date and the operator.
- Verdict values: `verified` / `not verified` / `blocked` / `not supported`. Only a recorded experiment changes a verdict.
- **These items block implementation.** `FEAS-01` and `FEAS-02` block the entire product premise and must be executed first. No P0 UI work starts before they have written verdicts.

### What Phase 0 did and did not do

The Phase 0 proof of concept lives in `phase0-poc/`. It built and ran the *client* half of each experiment locally, and it ran two Windows experiments for real. It did **not** contact Google. Accordingly:

- Items whose subject is Google's behaviour are `blocked` regardless of how much local work was done.
- Two sub-results are recorded as genuinely `verified` because the experiment was completed on this machine: the Credential Manager round-trip and the `tzdata`/IANA timezone behaviour (both under `FEAS-08`).
- `FEAS-08` steps 2 and 4 were attempted and observed to fail with a specific error, which is itself a recorded finding rather than an assumption.

Full detail, commands and evidence paths: `08-phase0-feasibility-report.md`.

## Documented vs. requires testing

| Claim | Status | Basis |
|---|---|---|
| `videos.insert` accepts `status.publishAt` and `status.privacyStatus` in the request body | **Documented** | `videos.insert` settable-fields list |
| `status.publishAt` requires private privacy and a never-before-published video; a past value publishes immediately | **Documented** | Video resource, `status.publishAt` |
| `videos.update` must re-assert `privacyStatus` even when already private | **Documented** | Video resource, `status.publishAt` |
| `videos.update` **deletes** any property that already has a value and is not present in the request | **Documented** | `videos.update` settable-fields note |
| `videos.list` filters are `id`, `myRating`, `chart` (`mostPopular` only) | **Documented** | `videos.list` parameter list |
| `channels.list(mine=true)` returns channels owned by the authenticated user | **Documented** | `channels.list` parameter list |
| `channels.list(managedByMe=true)` is **partner-only** via `onBehalfOfContentOwner`; it is not a general "channels I manage" filter | **Documented** | `channels.list` parameter list |
| A Brand Account may authorize the project's OAuth scopes if a specified test user manages it | **Documented** | Google Cloud "Manage App Audience" |
| Resumable protocol: POST → `Location`, PUT with `Content-Range`, `308` + `Range` resume, `404` = expired session, honour `Retry-After` | **Documented** | Resumable Uploads guide |
| A resumed upload must start at exactly one byte past the confirmed last byte; overlapping or skipping uploads nothing | **Documented** | Resumable Uploads guide |
| Quota: 100 `videos.insert`/day in its own bucket, 10,000 units/day shared; `videos.insert` 1, `videos.update` 50, `channels.list` 1, `videos.list` 1, `activities.list` 1 | **Documented** | Quota calculator table |
| OAuth Testing status: ≤100 test users; authorizations **and refresh tokens** expire after 7 days | **Documented** | Google Cloud "Manage App Audience" |
| `onBehalfOfContentOwner` / `onBehalfOfContentOwnerChannel` are for Content ID partners only | **Documented** | OAuth server-side guide; `videos.insert` parameters |
| The `videos` resource contains **no** mention entity | **Documented** | Full video resource definition |
| Client-initiated revocation requires deleting all Authorized Data within **7 calendar days** | **Documented** | Developer Policies, Revocation |
| Data deleted after revocation via Google's security settings page, or after a token stops being refreshable, must be removed within **30 calendar days**; clients must periodically reconfirm token validity | **Documented** | Developer Policies, Revocation |
| The privacy policy must link Google's security settings page and give a contact route | **Documented** | Developer Policies, III.A.2.i |
| YouTube may curtail access to a project inactive for 90 consecutive days | **Documented** | Developer Policies, Inactivity |
| Whether unverified-project uploads are forced private | **Contradictory** | Video resource page says forced private; `videos.insert` `privacyStatus` says not restricted |
| Whether one identity can address a brand channel | **Undocumented** | Not stated either way |
| Minimum accepted `publishAt` lead time | **Undocumented** | Not stated |
| Session expiry duration, activity-feed latency | **Undocumented** | "Finite lifetime" / no figure given |
| Whether a Studio mention survives a later `videos.update` | **Untested** | — |

> **Note on the retention deadlines.** Revision 1.1 of this pack recorded the Authorized Data retention deadline as an open owner decision (`D-1`). The Developer Policies state it: 7 calendar days after a client-initiated revocation, and 30 calendar days where revocation came through Google's security settings page or the token can no longer be refreshed. `D-1` is therefore no longer a question of *what the deadline is*; what remains open is the owner's decision on **classification and the internal sweep cadence** that meets both deadlines.

---

## FEAS-01 — Brand account and multi-channel behaviour

**Blocks:** the entire multi-channel premise; `AC-MVP-002`; `FEAT-002`; `03` §2.

**Question.** Can one Google identity publish to more than one of its own channels through the documented API, and specifically to a brand account channel?

**Hypothesis.** `videos.insert` publishes only to the primary channel resolved from the credential, and a brand account channel is not addressable. `channels.list(mine=true)` returns only the primary channel. Consequently, "multiple channels" requires multiple authorized identities, one channel each.

**Documented basis.** `videos.list`/`videos.insert` expose no channel selector for ordinary users. `onBehalfOfContentOwnerChannel` is documented as content-partner only. `channels.list(mine=true)` is documented as returning channels "owned by the authenticated user", which does not settle brand accounts.

**Experiment.**
1. Authorize a Google identity that manages both a personal channel and at least one brand account channel.
2. Call `channels.list(mine=true)` and record every channel returned.
3. Upload a clearly labelled test clip and read `snippet.channelId` from the insert response.
4. Compare the returned channel ID with each intended destination.

**Prerequisites.** A Google identity owning a brand account; a test clip; API project with YouTube Data API enabled; consent recorded.

**Pass criteria.** The intended brand channel appears in `channels.list(mine=true)` **and** the insert response shows `snippet.channelId` equal to it.

**Failure fallback.** State the limitation truthfully in the product and UI: one connected identity publishes to one channel; each publishing channel is connected with an identity that can publish to it. Remove any channel-selection affordance that implies otherwise. Record the wording decision in `02` §15 (D-5) and reflect it in `01` §5 and `05` §4 G.

**Phase 0 partial result (2026-10-11, documentation only).** `channels.list(managedByMe=true)` was checked against the live parameter list and is documented as *"intended exclusively for YouTube content partners… the user must be authenticated as a CMS account linked to the specified content owner"*. It is therefore **not** a brand-account escape hatch for an ordinary user, and an earlier reading of it as one was wrong. For a non-partner caller the only documented filters are `id`, `mine`, `forHandle` and `forUsername` — none of which establishes authority over a managed channel. Conversely, the OAuth testing page *does* document that *"a Brand Account may authorize scopes requested by your project's OAuth clients if a specified test user manages the Brand Account"*, so consent for a brand identity is expected to work; what remains unknown is where `videos.insert` then lands. Both are pinned as `DF-09`, `DF-18` and `DF-27` in `phase0-poc/hexton_poc/docfacts.py` and asserted by `tests/test_docfacts.py::test_managd_by_me_is_recorded_as_partner_only`.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (documentation only) · **Evidence:** `phase0-poc/tests/test_docfacts.py`; `phase0-poc/evidence/`. **Blocked on:** an authorized identity that manages a brand-account channel, a test clip, and consent on the project.

---

## FEAS-02 — Privacy behaviour on an unverified API project

**Blocks:** the safety model; `AC-MVP-003`; `FEAT-001`; `01` §8.4.

**Question.** Does an unverified API project force uploads to private, and can a public video be published from one?

**Hypothesis.** Unknown. Google's two live pages contradict each other, so no safe assumption may be encoded.

**Experiment.**
1. Confirm the project's verification state in Cloud Console and record it.
2. Upload a test clip **explicitly** as `privacyStatus=public` from the unverified project. Record the resulting `status.privacyStatus` and `uploadStatus`.
3. If accepted, note whether publication occurred and whether any audit warning appeared.
4. Repeat with an explicit request that omits `privacyStatus` **only on a throwaway test project**, to observe the server default purely as a recorded fact.

**Prerequisites.** An unverified project; a throwaway test channel and clip; explicit consent to publish publicly **for this test only**.

**Pass criteria.** Observed behaviour recorded with evidence, sufficient to replace the general claim in `01` §8.4, `02` §6.6 and the setup diagnostics with a statement of fact about this project.

**Failure fallback.** Public publishing stays **blocked**. The app always sends an explicit `privacyStatus`, defaults to Private (`01` §7), and the operator is told an audit may be required. This fallback is already the shipped design; no code change is needed, only the documentation verdict.

**Phase 0 partial result (2026-10-11).** Both halves of the contradiction were re-fetched and confirmed still live on the same day: the video-resource page summary says *"Unverified API projects uploading videos will have them set to private by default"*, while `videos.insert`'s `status.privacyStatus` says *"Videos uploaded from unverified API projects are not restricted to private viewing mode."* No safe assumption can be derived from documentation, so the contradiction stands. The client side of the fallback is now implemented and tested: `build_insert_body` always writes `status.privacyStatus` explicitly and refuses an unknown value (`phase0-poc/tests/test_metadata.py::test_build_insert_body_always_writes_privacy_status_explicitly`, `::test_build_insert_body_refuses_an_unknown_privacy_value`), and the local protocol mock rejects a body that omits it, so the behaviour is exercised rather than asserted.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (documentation + local client) · **Evidence:** `phase0-poc/tests/test_metadata.py`, `phase0-poc/tests/test_resumable_local.py::test_privacy_is_sent_explicitly_and_the_body_carries_it`. **Blocked on:** an unverified project, a throwaway channel and clip, and explicit consent for a public test.

---

## FEAS-03 — Minimum scheduling lead time and publication requirements

**Blocks:** scheduling defaults; `AC-MVP-005`; `D-4` in `02` §15.

**Question.** What is the smallest accepted `publishAt` offset from "now", and must remote processing complete before a schedule is accepted?

**Hypothesis.** A practical minimum exists and processing state may matter. Neither is documented.

**Experiment.**
1. Upload a test clip with `publishAt` set at a series of increasing offsets (for example 5, 15, 30, 60, 120 minutes).
2. Record the response for each: accepted, rejected (`invalidPublishAt`), or accepted but not honoured.
3. For one case, check `status.uploadStatus` at the moment of scheduling to see whether the video was still processing.
4. Confirm the final published time matches the requested UTC instant.

**Prerequisites.** `FEAS-02` complete; a test clip with enough processing time to be observable.

**Pass criteria.** A measured minimum offset and a recorded answer on processing dependence, sufficient to set the default `preupload_lead_minutes` and the preflight guard.

**Failure fallback.** Use a conservative configured minimum lead time with a warning band, and classify rejections rather than silently falling back to immediate publication. If no offset is honoured, escalate: scheduling via the API is not viable and the operator must schedule in YouTube Studio.

**Phase 0 partial result (2026-10-11).** No measurement is possible without a channel, so the conservative fallback is what shipped. It is enforced *before* any network request: `preflight()` hard-blocks a `publish_at` in the past, hard-blocks one inside the configured minimum, warns inside a 120-minute band, rejects a naive (zone-less) datetime rather than guessing, and refuses `publish_at` on an already-published video. The past-time block matters most — a past `publishAt` is documented to publish the video immediately, so treating it as an ordinary validation error would be a silent publication. Verified in `phase0-poc/tests/test_metadata.py::test_preflight_blocks_a_publish_time_in_the_past` (whose assertion checks the message explains *why*), `::test_preflight_blocks_a_publish_time_inside_the_minimum_lead_time`, `::test_preflight_warns_inside_the_warning_band_but_still_allows_queueing`, `::test_preflight_blocks_a_naive_datetime_rather_than_guessing_a_zone` and `::test_preflight_blocks_publish_at_on_an_already_published_video`. The configured minimum (`DEFAULT_MIN_LEAD_MINUTES = 30`) is labelled in code as a placeholder pending this item.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (local preflight only) · **Evidence:** `phase0-poc/tests/test_metadata.py`. **Blocked on:** `FEAS-02`, then a test channel and a clip with observable processing time.

---

## FEAS-04 — Resumable upload recovery across a process restart

**Blocks:** `AC-MVP-006`, `AC-MVP-010`; `FEAT-006`.

**Question.** Can an interrupted upload resume from a persisted session URI after a full process restart, producing exactly one video?

**Hypothesis.** Yes: the session URI and the `308` `Range` offset are sufficient, provided the session has not expired.

**Experiment.**
1. Start a resumable upload of a multi-megabyte test clip.
2. Kill the worker process mid-transfer (hard kill, no cleanup).
3. Restart, probe the persisted session URI with `Content-Range: bytes */<total>`, and resume from the reported offset.
4. Confirm the completed video is byte-correct and that exactly one video exists on the channel.

**Prerequisites.** `FEAS-01` complete; a test clip of a few megabytes.

**Pass criteria.** Upload completes after restart from the correct offset, exactly one video is created, and the video plays correctly to the end.

**Failure fallback.** If resumption is unreliable, upload whole files without interruption and mark any interrupted attempt as `JOB_NEEDS_REVIEW` before any restart. Never silently restart from zero, which can create a duplicate.

**Phase 0 partial result (2026-10-11).** The *client* half is implemented and exercised over real HTTP against a local server that implements the documented protocol (`phase0-poc/mock_youtube/server.py`) — real sockets, real `Content-Range` headers, real `308`/`201`/`404`. Results:

- The session URI is persisted **before** any media byte is sent, and the offset is persisted **before** progress is reported, so a kill at either point loses nothing.
- A simulated power cut after two of four chunks left `bytes_uploaded = 524288` on disk and zero videos server-side; a fresh uploader loaded from the same SQLite file resumed from that exact offset and produced one video whose bytes are identical to the source file.
- A genuine process restart is covered: the store is reopened from the file and `journal_mode` is asserted to actually be `wal`, so the WAL claim in `02` §2.1 is tested rather than assumed.
- Overlapping and non-advancing resumes are handled conservatively, and a 256 KiB chunk multiple is enforced, per the documented constraints.

**This is evidence about the client only.** It says nothing about YouTube's session lifetime or resume behaviour.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (local HTTP only) · **Evidence:** `phase0-poc/tests/test_resumable_local.py::test_a_kill_mid_transfer_is_resumed_from_the_persisted_offset`, `::test_a_restarted_process_recovers_from_the_database_alone`, `phase0-poc/evidence/pytest_run_*.txt`. **Blocked on:** `FEAS-01`, a test clip of a few megabytes, and a live channel.

---

## FEAS-05 — Ambiguous upload outcome reconciliation

**Blocks:** `AC-MVP-006`; the enforceable duplicate guarantee; `FEAT-009`.

**Question.** When an insert may have succeeded, can the ladder in `02` §6.2 resolve it to a definite video ID within a useful time?

**Hypothesis.** Partially. R1 (session probe) is conclusive when the session survives. R2/R3 are evidence rather than proof, and their latency is unknown.

**Experiment.**
1. Begin an upload and sever the network after the request is sent but before the response is read.
2. On recovery, run R1 (session probe) and record the result.
3. Run R2 (`activities.list` with `publishedAfter` = attempt start) and measure the latency until an upload activity appears.
4. Run R3 (uploads playlist via `contentDetails.relatedPlaylists.uploads` + `playlistItems.list`) and compare.
5. Repeat with the process killed rather than the network cut, so the session may expire (404) and R2/R3 are the only routes.

**Prerequisites.** `FEAS-04` complete.

**Pass criteria.** Either the ladder resolves the outcome to a definite video ID within a stated SLA, **or** the SLA is documented as "operator review", with measured activity-feed latency recorded so the design can rely on it appropriately.

**Failure fallback.** `JOB_NEEDS_REVIEW` becomes the normal ambiguous path, with candidates presented for operator confirmation. The duplicate guarantee is stated as "zero duplicates from automatic retry" (`01` §10), which is what the evidence supports.

**Phase 0 partial result (2026-10-11).** The ladder and the retry gate are implemented and tested against the local mock, including the hardest case — an upload that YouTube accepts but whose response the client never receives:

- With the response dropped, the client raises with `ambiguous=True`, records `insert_may_have_succeeded`, and issues **no** second insert; exactly one video exists server-side.
- The R1 session probe then resolves it conclusively to the video ID, and `decide_retry()` refuses the retry with an explicit reason.
- With the session expired (`404`), the ladder runs R1 → R2 → R3 → R4 and never restarts.
- R2 and R3 are deliberately modelled as **evidence, not proof**: a candidate recovered from the activity feed or the uploads playlist is marked `conclusive=False` and produces `needs_manual_review` for operator confirmation, because an unrelated manual Studio upload at the same moment is indistinguishable.
- The retry gate refuses once a session URI or a video ID exists, and allows a retry only where no session was ever established and the documented reads show no candidate.

**Not established:** real activity-feed latency, and whether R2/R3 are timely enough to be useful — both need a live channel.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (local HTTP only) · **Evidence:** `phase0-poc/tests/test_resumable_local.py::test_an_accepted_upload_whose_response_is_lost_is_never_retried`, `phase0-poc/tests/test_reconcile.py`. **Blocked on:** `FEAS-04`.

---

## FEAS-06 — Creator mention limitation and survival across updates

**Blocks:** `FEAT-008`; `AC-MVP-009`; `D-6` in `02` §15`.

**Question.** Confirmed limitation: no documented API field creates a verified channel mention. What remains to test is whether the manual workflow is viable and durable.

**Hypothesis.** A mention added in YouTube Studio survives a later `videos.update` that changes unrelated fields — but this is not guaranteed and may not hold.

**Documented basis.** The `videos` resource exposes `snippet.title`, `snippet.description`, `snippet.tags[]`, `localizations` and `status`, and no mention entity. YouTube's help describes mentions as selecting from a suggested list, a UI-only affordance.

**Experiment.**
1. Upload a test video as Private.
2. In YouTube Studio, add a real channel mention to the description by selecting from the suggested list. Record that the mention is present.
3. Issue a `videos.update` from the app changing only the title.
4. Re-inspect the description and confirm whether the mention is still present and still a real mention.

**Prerequisites.** A second channel to mention; a test channel where the operator controls both.

**Pass criteria.** The mention survives step 3, and the manual workflow is confirmed usable end to end.

**Failure fallback.** The app must not issue `videos.update` on `manual_required` jobs at all. Any later metadata edit is performed in YouTube Studio by the operator. The limitation is disclosed in the UI and the runbook.

**Phase 0 partial result (2026-10-11).** A **documentated** fact materially strengthens the fallback and was not in revision 1.1 of this pack. `videos.update` states: *"If you are submitting an update request, and your request does not specify a value for a property that already has a value, the property's existing value will be deleted."* A partial update that omits `snippet.description` therefore does not merely fail to preserve the mention — it **deletes the description that contains it**. The live Studio test is still required to see what a *full* update does to a real mention, but the risky path is now documented rather than merely suspected.

The PoC encodes this rather than trusting the caller: `may_issue_videos_update()` refuses a partial update when a mention lives in the affected metadata, and refuses any update at all for a `manual_required` job. Separately, `assess()` is written so that an `@handle` string in a caption can **never** reach `VERIFIED` — the only two routes to `VERIFIED` are a demonstrated documented API mechanism or an explicit operator attestation, both of which are labelled as such.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (documentation + local rules) · **Evidence:** `phase0-poc/tests/test_mention.py::test_an_at_handle_in_text_can_never_become_verified`, `::test_partial_video_update_is_refused_when_it_would_destroy_the_description`, `::test_a_full_snippet_update_is_allowed_because_values_are_reasserted`. **Blocked on:** a second channel to mention and an operator controlling both.

---

## FEAS-07 — OAuth testing-mode token expiry

**Blocks:** multi-account usability; `FEAT-002`; `D-2` in `02` §15`.

**Question.** What is the real reauthorization burden, and does it change under a production-status project?

**Documented basis.** For external apps in Testing status, authorizations **and** refresh tokens expire after seven days; up to 100 test users; a warning screen is shown. Projects set to Internal are limited to users in the owning Cloud Organization, which conflicts with multi-account use outside that organization.

**Experiment.**
1. With the consent screen in Testing status, authorize and record the refresh token's expiry field.
2. Record the operator experience when the token expires mid-queue: does the job fail safely, and is it visible before it fails?
3. Move to "In production" (or verify the app) and repeat the expiry observation.
4. If the app must stay Internal, confirm whether an authorized identity outside the organization can consent at all.

**Prerequisites.** Access to the Cloud project's OAuth consent configuration.

**Pass criteria.** The reauthorization cadence is characterised for the deployment posture actually chosen, and the failure mode is safe and visible.

**Failure fallback.** Ship a prominent expiry warning, a one-click reauthorize flow, and a documented weekly-cadence runbook. If Internal-only blocks required identities, that becomes a blocking finding for the multi-account requirement.

**Phase 0 partial result (2026-10-11).** No experiment is possible without access to the consent configuration, so the burden is unmeasured. What the client now does about it is implemented and tested: `OAuthFlow.refresh()` raises an explicit `no_refresh_token` error whose message states that under a consent screen in Testing status authorizations expire after seven days, so the operator is told *why* rather than shown a bare 400. The loopback flow itself is verified end-to-end against a local server — `state` is validated in constant time and a mismatched `state` is discarded before any token exchange, PKCE is S256 as documented, and the offline access type is requested so a refresh token is issued at all. Verified in `phase0-poc/tests/test_oauth.py` (15 tests, including a real loopback round trip over `127.0.0.1`).

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (local flow only) · **Evidence:** `phase0-poc/tests/test_oauth.py`. **Blocked on:** access to the Cloud project's OAuth consent configuration and two test identities.

---

## FEAS-08 — Windows Task Scheduler and credential recovery

**Blocks:** `AC-MVP-001`, `AC-MVP-010`, `AC-MVP-011`; `FEAT-012`; packaging.

**Question.** Can a worker started at logon read credentials stored by the interactive user, and survive reboot and sleep/resume?

**Hypothesis.** Yes, provided the task runs as the interactive user with an "At log on" trigger and the "only when user is logged on" option. A Windows Service or a non-interactive principal **cannot** read user-scoped Credential Manager entries or user-scoped DPAPI blobs.

**Experiment.**
1. Store a credential through the app under the operator's account.
2. Create the Task Scheduler task with an "At log on" trigger, principal = the interactive user, run only when logged on.
3. Reboot; confirm the worker starts, reads the credential, and completes a job with no browser open.
4. Create a control task using "Run whether user is logged on or not" or install as a service, and confirm it **fails** to read the credential — this negative result is what justifies the design.
5. Sleep mid-upload, resume, and confirm the worker reconciles rather than restarting the insert.
6. Confirm `tzdata` resolves IANA zones on this machine.

**Prerequisites.** A Windows test machine; a credential stored via the app; one test clip.

**Pass criteria.** Steps 3 and 5 pass, step 4 fails as predicted, and step 6 succeeds. The startup configuration is captured in the runbook with the reason stated.

**Failure fallback.** If the interactive-user constraint cannot be met, the worker must run in the foreground with the API, or credentials move to a store readable by the executing principal. Do not ship a service that appears to work and fails on every job.

**Phase 0 partial results (2026-10-11, run for real on this machine).** This is the one item where live experiments were possible without Google. Environment: Windows 11 Pro build 26200, CPython 3.14.7, PowerShell 5.1, user `DESKTOP-PT2TMIR\jarif`, **not elevated**, timezone `Bangladesh Standard Time`. Driver: `phase0-poc/scripts/win_persistence_probe.ps1`; raw output in `phase0-poc/evidence/win_persistence_20261011T091754Z.txt` (system interpreter, `tzdata` absent) and `win_persistence_20261011T094355Z.txt` (PoC venv, `tzdata` present — a deliberate second run so the P3 result is recorded on both sides).

| Step | Result | Basis |
|---|---|---|
| 1. Store a credential the app can read back | **verified** | Credential Manager round-trip succeeded via `advapi32` `CredWriteW`/`CredReadW`; enumeration and deletion also succeeded; entry confirmed gone afterwards |
| 2. Register an "At log on" task | **blocked — observed denial** | `Register-ScheduledTask -AtLogOn` → `Access is denied.` (unelevated). `schtasks /sc onlogon` → same. Creating a logon-triggered task requires elevation |
| 3. Worker starts at logon and reads the credential | not run | depends on step 2 |
| 4. Control: a SYSTEM principal fails to read it | **blocked — observed denial** | `schtasks /create … /ru SYSTEM` → `ERROR: Access is denied.` The negative control that justifies the design could not be executed |
| 5. Sleep mid-upload and reconcile | not run | depends on steps 2–3 and a live upload |
| 6. `tzdata` resolves IANA zones | **verified (and it fails without the package)** | see below |

**Step 6 in detail — this is the strongest single finding of Phase 0.** With CPython 3.14.7 on this Windows build and no `tzdata` package installed, `zoneinfo.ZoneInfo()` failed for **every** key tried, including `UTC`:

```
Asia/Dhaka            ZoneInfoNotFoundError: 'No time zone found with key Asia/Dhaka'
America/Los_Angeles   ZoneInfoNotFoundError: 'No time zone found with key America/Los_Angeles'
UTC                   ZoneInfoNotFoundError: 'No time zone found with key UTC'
```

Installing `tzdata` (2026.5) resolved all three to `ok` on the same machine, with the Windows timezone ID unchanged at `Bangladesh Standard Time`. This confirms the `C-10` correction in `README.md` and `02` §10.1, and it does so more forcefully than expected: without `tzdata` the application cannot resolve **any** IANA zone on Windows, `UTC` included, so every stored `publishAt` would fail conversion. `tzdata` is therefore a hard runtime dependency, not a nicety. A/B evidence: `phase0-poc/evidence/env_no_tzdata.json` and `phase0-poc/evidence/env_with_tzdata.json`.

**Finding for the design, from steps 2 and 4.** The documented "worker runs as the interactive user" architecture cannot be *registered* from an unelevated installer. Either the installer requests elevation once to register the logon task, or the product falls back to a non-elevated autostart mechanism. Both non-elevated mechanisms were tested and are writable without elevation on this build: the `HKCU\…\CurrentVersion\Run` key (written, read back, removed) and the per-user Startup folder (`C:\Users\jarif\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup`, written and removed). Neither was shown to survive the conditions a logon task would — reboot, sleep/resume, "run only when user is logged on" — so they are candidates to evaluate, not confirmed substitutes.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (Windows experiments run; elevation-dependent steps denied) · **Evidence:** `phase0-poc/evidence/win_persistence_20261011T091754Z.txt`, `phase0-poc/evidence/win_persistence_20261011T094355Z.txt`, `phase0-poc/evidence/env_no_tzdata.json`, `phase0-poc/evidence/env_with_tzdata.json`, `phase0-poc/tests/test_store.py` (15 tests). **Blocked on:** an elevated prompt to register the logon task, a reboot, and one test clip.

---

## FEAS-09 — Google Sheets duplicate prevention

**Blocks:** `AC-MVP-008`; `FEAT-010`.

**Question.** Does the sync-key read-before-append mechanism actually prevent duplicates under rerun, timeout and partial-batch conditions?

**Hypothesis.** Yes. The read of the `Sync Key` column is the source of truth, so a retry after an ambiguous write converges.

**Experiment.**
1. Generate a report for a known set of terminal jobs and record the intended sync keys.
2. Run the report; confirm one row per key.
3. Run it again immediately; confirm zero new rows.
4. Force a timeout **after** the append is accepted but before the response is read; rerun; confirm still one row per key.
5. Append a partial batch manually and rerun; confirm only the missing keys are written.
6. Corrupt the local ledger (mark keys unsynced when they are written) and rerun; confirm the sheet, not the ledger, is treated as authoritative.

**Prerequisites.** A test spreadsheet and tab; the narrowest Sheets scope configured.

**Pass criteria.** Exactly one row per `Sync Key` in every scenario, with the sheet as the authority when the ledger disagrees.

**Failure fallback.** Move to writing a per-day staging tab and reconciling explicitly, or use `values.update` against a deterministic row range. Document whichever mechanism ships; do not fall back to blind append-and-hope.

**Phase 0 partial result (2026-10-11).** The deduplication logic is implemented and exercised against a local stand-in for `spreadsheets.values` that is deliberately unforgiving: `append` is **not** idempotent, exactly like the real API, so a retry after a dropped response writes a second row. Against it:

- Run then re-run of the same two rows: one append call, two rows, no duplicates.
- A dropped response after a successful append produces `read_after_ambiguous_write=True`; the re-read shows the row was written exactly once, and an operator-initiated rerun adds nothing.
- A partially written batch re-runs and writes only the missing key.
- The `Sync Key` is `SCH_<report_date>_<schedule_id>_<schema_version>`, deterministic across 100 calls, normalised for case and whitespace so one job cannot acquire two identities, and rejected outright if the schedule ID contains a character that would make the key unparseable.
- A conflict (same key, different value) is reported as a conflict and never silently appended.
- The sheet is authoritative over the local ledger: a ledger deliberately corrupted to claim everything is synced does not stop the read, and an empty sheet still requires both rows.

A structural invariant is also asserted: the reporter module contains no import path to anything that can upload video, so a Sheets failure cannot trigger a YouTube insert.

**Not established:** that the real Sheets API behaves as the stand-in does, that the narrow scopes are sufficient, and that a real spreadsheet's existing contents reconcile the same way.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (local stand-in only) · **Evidence:** `phase0-poc/tests/test_sheets.py` (18 tests). **Blocked on:** a test spreadsheet and tab, and the narrowest Sheets scope configured.

---

## FEAS-10 — Quota constraints in practice

**Blocks:** scheduling design; `FEAT-011` diagnostics.

**Question.** Do observed quota costs match the documented table on the real project, and is the daily budget sufficient?

**Documented basis.** 100 `videos.insert`/day in a dedicated bucket; 10,000 units/day shared; `videos.insert` 1, `videos.update` 50, `channels.list` 1, `videos.list` 1, `activities.list` 1, `playlistItems.list` 1, `thumbnails.set` 50. Resets at midnight Pacific Time. The quota page's auto-generated summary is stale and quotes an old cost; read the table.

**Experiment.**
1. Record the Cloud Console quota before and after a scripted run of a known number of calls of each type used by the app.
2. Confirm `videos.insert` draws from its own bucket rather than the shared pool.
3. Confirm the reconciliation ladder and the confirmation sweep are cheap enough to run routinely for every job.
4. Confirm the effect of putting `publishAt` in the insert body instead of issuing a follow-up `videos.update`.

**Prerequisites.** `FEAS-04` complete; access to the project's Quotas page.

**Pass criteria.** Measured costs match the documented table, and the daily budget covers the expected job volume with headroom.

**Failure fallback.** Reduce polling frequency, cap daily uploads with a visible warning, and add a quota ledger with pre-exhaustion alerting (`02` §12.1). Request a quota extension through the documented audit process if needed.

**Phase 0 partial result (2026-10-11).** No quota observation is possible without a project, so the documented figures were re-verified against the live pages and pinned instead. The quota page's current wording was re-read verbatim: *"a default quota allocation of 100 search.list calls, 100 videos.insert calls, and 10,000 units per day combined for all other endpoints… Daily quotas reset at midnight Pacific Time."* Unit costs were re-confirmed for every method the app uses: `videos.insert` 1 (its own bucket), `videos.update` **50**, `channels.list` 1, `videos.list` 1, `activities.list` 1, `playlistItems.list` 1.

The 50-unit cost of `videos.update` against the 1-unit cost of `videos.insert` is the concrete economic argument for putting `publishAt` in the insert body rather than issuing a follow-up update — a design decision this pack made on correctness grounds (`DF-03`/`DF-05`) and which the quota table independently supports. A quota ledger table is already in the PoC schema (`quota_ledger` in `phase0-poc/hexton_poc/youtube/session.py`) and the local mock records a quota cost per call, so the accounting path exists; only the numbers are unmeasured.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (documentation only) · **Evidence:** `phase0-poc/hexton_poc/docfacts.py` (`DF-11`, `DF-12`), asserted by `phase0-poc/tests/test_docfacts.py`. **Blocked on:** `FEAS-04` and access to the project's Quotas page.

---

## FEAS-11 — Compliance requirements

**Blocks:** release; `AC-MVP-014`, `AC-MVP-015`; `COMP-01`–`COMP-10`.

**Question.** Are the policy obligations implemented in a way that can be demonstrated, and are the open decisions settled?

**Documented basis.** The YouTube API Services Terms of Service and Developer Policies require: a privacy policy the user must accept before using the client's features; links to the YouTube Terms of Service and Google Privacy Policy, plus a revocation link and a contact route; exactly one API project per client; programmatic revocation with deletion of Authorized Data within the stated deadline; retention limits on Authorized Data; and no modification of user-supplied content without explicit consent. YouTube may curtail a project inactive for 90 consecutive days.

**Experiment.**
1. Fresh install: confirm feature access is blocked until the policy is accepted, and that the accepted version is recorded.
2. Confirm all policy links resolve and the policy names the Authorized Data accessed and stored.
3. Revoke a connection and confirm: the revocation call reaches Google, the credential is deleted from the secret store, and Authorized Data is purged while local bookkeeping is retained.
4. Run the retention sweep and confirm values are refreshed or deleted with an audit record.
5. Confirm an optional metadata transformation is off by default, requires consent, previews the exact text, and refuses to truncate.
6. Confirm the inactivity heartbeat runs when the last successful call is old.
7. Confirm a database backup contains no secret material.

**Prerequisites.** Owner decisions on D-1 (retention deadline and data classification) and D-2 (OAuth app posture) — see `02` §15.

**Pass criteria.** Each item demonstrable in the running application, with the purge and sweep observable rather than asserted.

**Failure fallback.** Where a deadline or classification is undecided, keep the stricter interpretation and record the decision as open. A release may not proceed with an undecided `COMP-04` deadline.

**Phase 0 partial result (2026-10-11).** `D-1` is no longer wholly open: the Developer Policies state the deadlines. Re-reading the Revocation section verbatim, a client-initiated revocation requires that *"you and your API Clients must delete all Authorized Data… within 7 calendar days of the revocation"*, and that where a user revokes through Google's security settings page, *"your API Clients will need to periodically reconfirm that its authorization tokens are still valid and delete API Data associated with users whose authorization tokens cannot be refreshed… all such deletions should happen as soon as possible and must take place within 30 calendar days of that revocation."* The privacy policy obligation is likewise now specific rather than general: it must link the Google security settings page and explain how users can contact the client owner with questions or complaints.

What remains for the owner is **classification and sweep cadence**, not the number. The PoC provides the mechanism those deadlines assume: `OAuthFlow.revoke()` performs the programmatic revocation required the moment the user asks, and the credential store supports keyed deletion so the secret can be purged while local bookkeeping is retained.

**Verdict:** blocked · **Date:** 2026-10-11 · **Operator:** Phase 0 (documentation + local mechanism) · **Evidence:** `phase0-poc/hexton_poc/docfacts.py` (`DF-20`, `DF-21`, `DF-22`, `DF-23`, `DF-24`), `phase0-poc/tests/test_oauth.py`. **Blocked on:** a running application, and the owner's classification decision.

---

## Verdict summary

| ID | Subject | Verdict | Live-API result | Local result | Blocked on |
|---|---|---|---|---|---|
| FEAS-01 | Brand account / multi-channel | **blocked** | none | documentation finding: `managedByMe` is partner-only | an identity managing a brand-account channel |
| FEAS-02 | Unverified-project privacy | **blocked** | none | contradiction re-confirmed live; explicit-privacy client verified | an unverified project + consent for a public test |
| FEAS-03 | Minimum lead time | **blocked** | none | preflight hard-block verified | `FEAS-02`, then a channel |
| FEAS-04 | Resumable recovery | **blocked** | none | resume-after-kill and restart verified over real HTTP | `FEAS-01` + a live channel |
| FEAS-05 | Ambiguous outcome reconciliation | **blocked** | none | R1–R4 ladder and retry gate verified | `FEAS-04` |
| FEAS-06 | Mention limitation / survival | **blocked** | none | documentation finding: `videos.update` deletes unspecified properties | a second channel + operator |
| FEAS-07 | OAuth token expiry | **blocked** | none | loopback flow, `state` and PKCE verified locally | the Cloud project's consent configuration |
| FEAS-08 | Windows startup / credentials | **blocked** | — | **Credential Manager round-trip verified**; **`tzdata` behaviour verified (fails without it, incl. `UTC`)**; logon and SYSTEM task registration both **observed denied** | an elevated prompt + a reboot |
| FEAS-09 | Sheets duplicate prevention | **blocked** | none | read-before-append verified against a non-idempotent stand-in | a test spreadsheet + scopes |
| FEAS-10 | Quota constraints | **blocked** | none | documented figures re-verified and pinned | `FEAS-04` + the Quotas page |
| FEAS-11 | Compliance | **blocked** | none | revocation mechanism present; `D-1` deadlines resolved from documentation | a running application |

**Reading this table.** "Local result" means the client behaved correctly against a local stand-in or a real operating-system facility. It is genuine evidence about the code, and it is **not** evidence about Google. No item is `verified` in full, because every one of them ultimately asks a question about Google's behaviour or about a reboot that has not happened.

## Open owner decisions

Tracked in `02` §15; summarised here because several `FEAS-*` items depend on them.

| # | Decision | Status after Phase 0 | Blocks |
|---|---|---|---|
| D-1 | Authorized Data retention deadline and data classification | **Deadline resolved from documentation** (7 calendar days after a client-initiated revocation; 30 where revocation came via Google's security settings page or the token is no longer refreshable). What remains open is the owner's classification and internal sweep cadence. | FEAS-11, `COMP-04`, `COMP-05` |
| D-2 | OAuth app posture: Testing vs In production/verification | open — still needs the owner and the Cloud project | FEAS-07, FEAS-11, `COMP-01`–`COMP-03` |
| D-3 | Whether optional metadata transformations are wanted at all | open | FEAS-11, `COMP-06` |
| D-4 | Default `preupload_lead_minutes` | open — remains a placeholder (`30` in the PoC) until FEAS-03 measures it | FEAS-03 |
| D-5 | Multi-channel product wording if `FEAS-01` is negative | open — but the fallback wording is now drafted in `phase0-poc/hexton_poc/youtube/identity.py::brand_account_note` | FEAS-01, `01` §5, `05` §4 G |
| D-6 | Whether `videos.update` is ever issued on a `manual_required` mention job | open, but the risk is now documented rather than suspected: an update omitting `snippet.description` deletes the existing description | FEAS-06 |
