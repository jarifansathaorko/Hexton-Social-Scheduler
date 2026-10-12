# Phase 0 — Technical Proof of Concept: Feasibility Report

**Date:** 2026-10-11
**Status:** Complete. **Gate: not passed — awaiting review. Do not begin the full application.**
**Companion documents:** `07-feasibility-checklist.md` (verdicts), `phase0-poc/` (code and evidence)

---

## 1. What this phase was asked to do

Establish, by experiment wherever an experiment was possible, whether the product described in
`01`–`06` can be built as specified. Specifically:

1. Re-verify the official Google, YouTube and Windows documentation the design rests on, and pin
   each fact with its URL and a quotation so an unverifiable claim can never later be mistaken for
   a documented one.
2. Build an isolated proof of concept and run everything that can be run without credentials.
3. Mark everything that cannot be run `blocked`, and say exactly what is missing.
4. Record a verdict for every `FEAS-01` … `FEAS-11` item.
5. Stop.

**Ground rules observed.** No undocumented endpoints were used. No cookie extraction, no auth
bypass, no evasion of any policy. No credential, refresh token or cookie appears in this
document, in the code, or in the evidence files. Nothing in this phase contacted Google's
servers. No test upload was performed — not public, not private, not unlisted.

---

## 2. Environment

| | |
|---|---|
| Machine | `DESKTOP-PT2TMIR`, Windows 11 Pro build 26200 |
| User | `DESKTOP-PT2TMIR\jarif` — **not elevated** |
| Python | CPython 3.14.7 (system `C:\Python314`) |
| PoC interpreter | `phase0-poc\.venv` — httpx 0.28.1, pytest 9.1.1, tzdata 2026.5 |
| PowerShell | 5.1.26100.9168 |
| System timezone | `Bangladesh Standard Time` (IANA `Asia/Dhaka`) |
| Git | 2.55.0 — **the repository is not a git repo; every change is uncommitted** |
| Network | reachable; PyPI reachable. Google endpoints **not contacted** |

Captured by `phase0-poc/scripts/probe_env.py` and `scripts/win_persistence_probe.ps1`.

---

## 3. Commands actually executed

```text
# Isolated environment
python -m venv phase0-poc\.venv
phase0-poc\.venv\Scripts\python.exe -m pip install httpx pytest tzdata

# Full test suite
cd phase0-poc
phase0-poc\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
phase0-poc\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --tb=no -rN   # for the evidence file
#   -> 151 collected, 150 passed, 1 skipped, exit code 0
#   (pytest 9.1.1 suppresses the summary line under -q; it is emitted at default verbosity)

# Windows persistence experiments (records + cleans up after itself)
#   run twice, with different interpreters, so the P3 result is recorded on both sides:
powershell -NoProfile -ExecutionPolicy Bypass -File phase0-poc\scripts\win_persistence_probe.ps1
#   -> evidence/win_persistence_20261011T091754Z.txt   (system python, tzdata absent)
#   -> evidence/win_persistence_20261011T094355Z.txt   (PoC venv, tzdata present)

# tzdata A/B evidence
C:\Python314\python.exe -u phase0-poc\scripts\probe_env.py   > phase0-poc/evidence/env_no_tzdata.json
phase0-poc\.venv\Scripts\python.exe -u phase0-poc\scripts\probe_env.py > phase0-poc/evidence/env_with_tzdata.json
```

No other network-bearing command was run. Nothing was published, created, or modified in any
Google, YouTube or Microsoft online service.

---

## 4. Test results

**151 tests collected. 150 passed. 1 skipped. Exit code 0.**

| File | Tests | Subject |
|---|---:|---|
| `test_docfacts.py` | 9 | Documentation pins are attributable, quoted, correctly classified |
| `test_metadata.py` | 27 | Limits, quoting-aware tag cost, `publishAt` preflight, explicit privacy |
| `test_jobstate.py` | 12 | One canonical state model, one display mapping |
| `test_mention.py` | 11 | An `@handle` in text can never reach `VERIFIED` |
| `test_oauth.py` | 15 | Loopback, `state`, PKCE S256, minimal scopes |
| `test_resumable_local.py` | 20 | Protocol correctness, resume, restart, ambiguous outcome |
| `test_reconcile.py` | 10 | R1→R4 ladder and the retry gate |
| `test_sweep.py` | 14 | Publication is verified, not assumed |
| `test_sheets.py` | 18 | Sync-key dedupe against a non-idempotent stand-in |
| `test_store.py` | 15 | Credential Manager and DPAPI round-trips (real OS calls) |

The single skip is the platform guard `test_credential_manager_is_refused_off_windows`.

**Scope caveat, stated once and relied on throughout:** every test that involves
`mock_youtube` is evidence about *this client*. It is not evidence about YouTube or Sheets.
Nothing in §4 establishes any `FEAS-*` item by itself.

---

## 5. Evidence files

| Path | Contents |
|---|---|
| `phase0-poc/evidence/win_persistence_20261011T091754Z.txt` | Full P1–P6 Windows probe output — system interpreter, `tzdata` **absent** |
| `phase0-poc/evidence/win_persistence_20261011T094355Z.txt` | Second probe run — PoC venv, `tzdata` **present**; P4/P5/P6 reproduce identically |
| `phase0-poc/evidence/env_no_tzdata.json` | System Python: IANA lookups fail |
| `phase0-poc/evidence/env_with_tzdata.json` | PoC venv: same lookups succeed |
| `phase0-poc/evidence/pytest_run_20261011T094046Z.txt` | Test run, environment, per-file counts, exit code |
| `phase0-poc/hexton_poc/docfacts.py` | `DF-01` … `DF-28`, each with URL, quotation, classification |

---

## 6. FEAS verdicts

Verdict vocabulary per `07`: `verified` / `not verified` / `blocked` / `not supported`.
Full per-item detail, including the procedure and the exact blockers, is in `07-feasibility-checklist.md`.

| ID | Subject | Verdict | What was actually established |
|---|---|---|---|
| FEAS-01 | Brand account / multi-channel | **blocked** | Documentation only: `managedByMe` is partner-only (`DF-09`); a Brand Account *may* authorize the project's scopes (`DF-18`); where `videos.insert` then lands is undocumented (`DF-27`). Needs an authorized identity that manages a brand-account channel. |
| FEAS-02 | Unverified-project privacy | **blocked** | Both halves of the contradiction re-fetched and confirmed live on 2026-10-11 (`DF-25`). Client-side fallback implemented and tested: privacy is always written explicitly. |
| FEAS-03 | Minimum lead time | **blocked** | Conservative fallback implemented: `preflight()` hard-blocks past and too-soon times, refuses zone-less datetimes, refuses `publishAt` on an already-published video. Actual minimum unmeasured. |
| FEAS-04 | Resumable recovery | **blocked** | Client half verified over real HTTP against a local protocol server: resume after a simulated kill, recovery from SQLite alone after a process restart, exact byte match. Says nothing about YouTube. |
| FEAS-05 | Ambiguous outcome reconciliation | **blocked** | Ladder and retry gate verified: an accepted-but-unanswered upload issues **no** second insert; R1 resolves it conclusively; R2/R3 are evidence only; R4 never inserts. |
| FEAS-06 | Mention limitation / survival | **blocked** | Documentation finding (`DF-06`): `videos.update` **deletes** an already-valued property omitted from the request. `may_issue_videos_update()` refuses the dangerous path. Live Studio survival untested. |
| FEAS-07 | OAuth token expiry | **blocked** | Loopback flow, constant-time `state` validation, PKCE S256 and minimal scopes verified locally against a real `127.0.0.1` server. Consent configuration not accessible. |
| FEAS-08 | Windows startup / credentials | **blocked** | **Two sub-results verified:** Credential Manager round-trip (write/read/list/delete confirmed gone) and `tzdata` behaviour. **Two observed denials:** logon-trigger and SYSTEM task registration both denied unelevated. Two non-elevated autostart mechanisms confirmed writable. |
| FEAS-09 | Sheets duplicate prevention | **blocked** | Read-before-append verified against a stand-in that is deliberately non-idempotent. Structural invariant asserted: no import path from the reporter to the uploader. Real spreadsheet not reached. |
| FEAS-10 | Quota constraints | **blocked** | Documented figures re-read verbatim and pinned (`DF-11`, `DF-12`). No project Quotas page accessed. |
| FEAS-11 | Compliance | **blocked** | Retention deadlines resolved from documentation (`DF-20`, `DF-21`). Revoke and keyed-delete mechanisms present. Running application required for the rest. |

**No item is `verified` in full.** Every one ultimately asks a question about Google's
behaviour, or about a reboot that has not happened, or requires a Cloud project this phase did
not have.

---

## 7. Findings that changed the design

These are the things Phase 0 discovered that were not known going in. Each is a reason the
product as originally specified would have been wrong.

### 7.1 `videos.update` deletes what it does not mention (`DF-06`)

> *"If you are submitting an update request, and your request does not specify a value for a
> property that already has a value, the property's existing value will be deleted."*

A partial metadata update does not merely fail to preserve the description — it **removes** it,
along with any mention inside it. The design rule now enforced in code is: never send
`part=snippet` without re-sending every snippet field. This upgrades `D-6` from a suspicion to a
documented risk.

### 7.2 The Authorized Data retention deadline is not an open question (`DF-20`, `DF-21`)

Google's Developer Policies state it directly:

- Client-initiated revocation → delete all Authorized Data within **7 calendar days**.
- Revocation via Google's security settings page, or an unrefreshable token → delete within
  **30 calendar days**, and *"periodically reconfirm that its authorization tokens are still
  valid."*

`D-1` therefore splits in two. The number is resolved. What remains for the owner is
**classification** and the **sweep cadence** that satisfies both deadlines. `07` has been updated
to say so; `02` §15 still records `D-1` as open and must be corrected in a later documentation
pass — deliberately not done here, since this phase was scoped away from PRD edits.

### 7.3 `managedByMe` is not a brand-account path (`DF-09`)

> *"managedByMe boolean — This parameter is intended exclusively for YouTube content partners.
> … the user must be authenticated as a CMS account linked to the specified content owner."*

For a non-partner caller the only documented `channels.list` filters are `id`, `mine`,
`forHandle` and `forUsername`. None establishes authority over a managed channel. An earlier
reading of `managedByMe` as the answer to `FEAS-01` was wrong, and a regression test now asserts
it (`test_docfacts.py::test_managd_by_me_is_recorded_as_partner_only`).

### 7.4 Consent for a Brand Account is expected to work (`DF-18`)

> *"a Brand Account may authorize scopes requested by your project's OAuth clients if a
> specified test user manages the Brand Account."*

So the consent half of `FEAS-01` is documented; the `videos.insert` targeting half is not
(`DF-27`). This narrows what `FEAS-01` needs to prove.

### 7.5 `videos.insert` and `videos.update` differ in cost by 50× (`DF-11`, `DF-12`)

`videos.insert` = 1 unit (its own 100/day bucket). `videos.update` = **50** units. Putting
`publishAt` in the insert body was chosen on correctness grounds; the quota table independently
confirms it is also the only economical choice.

### 7.6 On Windows without `tzdata`, `zoneinfo` cannot resolve *anything* — including `UTC`

Recorded as `C-10` in `README.md` during the documentation phase. Phase 0 proved it, and proved
it more forcefully than expected:

```text
# CPython 3.14.7, Windows 11, tzdata NOT installed
Asia/Dhaka            ZoneInfoNotFoundError: 'No time zone found with key Asia/Dhaka'
America/Los_Angeles   ZoneInfoNotFoundError: 'No time zone found with key America/Los_Angeles'
UTC                   ZoneInfoNotFoundError: 'No time zone found with key UTC'

# after: pip install tzdata   (2026.5)
Asia/Dhaka            ok
America/Los_Angeles   ok
UTC                   ok
```

`tzdata` is a **hard runtime dependency**, not a nicety. Without it the application cannot
convert a single stored `publishAt`, `UTC` included.

---

## 8. Two real bugs this phase found in its own work

Recorded because they are exactly the class of defect that a mock would have hidden.

**8.1 `CredEnumerateW` returns an array of *pointers*, not an array of structures.**
The first implementation traversed the result as an array of `CREDENTIALW`, read the pointer
values as if they were structure fields, and crashed the interpreter with an access violation.
The corrected traversal casts to `POINTER(_CREDSTRUCT) * count` and dereferences each element.
Related: the API's out-parameter must be declared `c_void_p` and passed with `byref()` — declared
as `POINTER(POINTER(X))`, ctypes passes the (initially NULL) pointer value rather than the
address of the holder, which writes through `NULL`.

**8.2 A mock that does not drain its request body corrupts the next request.**
The stand-in returned `404`/`503` before reading the POST body. On an HTTP/1.1 keep-alive
connection the unread bytes became the start of the next request, which the framework then
rejected with `400`. This produced a cascade of confusing test failures that looked like client
defects and were not. Both mocks now drain before any early response. The lesson generalises:
a stand-in must be faithful in its transport behaviour, not just in its happy path.

Neither bug reached any deliverable outside `phase0-poc/`.

---

## 9. What is missing, exactly

The prerequisites that would unblock the `blocked` verdicts:

| Prerequisite | Unblocks |
|---|---|
| A Google Cloud project, with the YouTube Data API v3 enabled | all |
| An OAuth consent screen with the required scopes configured, and the test users / publishing status decided (`D-2`) | FEAS-07, FEAS-11 |
| A YouTube test channel, **plus** a second identity that manages a brand-account channel | FEAS-01, FEAS-02, FEAS-03 |
| Authorization to run a **public** test on a designated test channel | FEAS-02 |
| A test video file of a few megabytes | FEAS-04, FEAS-05 |
| A second channel to mention, with an operator controlling both | FEAS-06 |
| A test spreadsheet and tab, plus the narrowest Sheets scope configured | FEAS-09 |
| An **elevated** prompt, plus a reboot | FEAS-08 steps 2–5 |
| The project's Quotas page | FEAS-10 |

Until these exist, the honest verdict for every one of them is `blocked`. Recording anything
else would misrepresent what was tested.

---

## 10. Unresolved risks

1. **The `FEAS-01` question is genuinely undecidable from documentation.** Both a documented
   consent path and an undocumented landing behaviour exist. There is no safe assumption.
2. **The unverified-project privacy contradiction is still contradictory.** Two incompatible
   Google statements were live on the same day. Neither may be preferred for convenience.
3. **No lead-time measurement exists.** `DEFAULT_MIN_LEAD_MINUTES = 30` is a placeholder and is
   labelled as one in code. `D-4` stays open.
4. **Reconciliation timing is unmeasured.** Whether R2 (activity feed) and R3 (uploads playlist)
   are timely enough to be useful in practice is unknown; the ladder degrades safely if they are
   slow, but usefulness is unproven.
5. **The non-elevated autostart fallbacks are unproven.** The `HKCU\...\Run` key and the Startup
   folder are writable without elevation, but neither has been shown to survive reboot,
   sleep/resume, or a locked workstation. They are candidates, not substitutes.
6. **D-1, D-2, D-3, D-4, D-5, D-6 are not closed.** D-1's *deadline* is now documented; its
   classification and cadence are not.
7. **The repository is not under version control.** All of this work is uncommitted. That is a
   risk to the Phase 0 gate itself, independent of the product.

---

## 11. Gate

**Phase 0 is complete as scoped. The gate is not passed.**

- Every `FEAS-*` item now carries a written verdict with a date, an operator and an evidence
  path, replacing the previous `not run` state.
- Two sub-results are `verified` on the strength of real experiments: the Credential Manager
  round-trip and the `tzdata`/IANA behaviour.
- Two negative results were *observed rather than assumed*: logon-trigger and SYSTEM task
  registration are denied from an unelevated shell on this build.
- Everything requiring Google is `blocked` on §9.

**Requested of the reviewer:** confirm that the `blocked` verdicts are accepted as the outcome of
Phase 0, or supply the prerequisites in §9 so the live experiments can be run. Only after that
should `FEAS-01` and `FEAS-02` be attempted, and only after they pass should any application work
begin.

**No application code has been written. The six PRDs, `README.md` and the three PNGs were not
modified during Phase 0** — with one deliberate exception: `07-feasibility-checklist.md`, which
exists to hold these verdicts.
