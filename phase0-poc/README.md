# Hexton — Phase 0 Proof of Concept

An isolated technical proof of concept for the Windows-local YouTube Shorts scheduler.
**This directory is not the application.** It exists to answer the questions in
`../07-feasibility-checklist.md` before any product code is written.

**Report:** [`../08-phase0-feasibility-report.md`](../08-phase0-feasibility-report.md)

---

## Ground rules

- **Nothing here contacts Google.** No token was obtained, no video uploaded, no spreadsheet
  touched, no cookie read. There is no credential anywhere in this directory.
- A local stand-in (`mock_youtube/`) exists to exercise *this client's* behaviour. Passing tests
  against it are evidence about the client, and **never** about YouTube or Sheets.
- Every claim the design rests on is pinned in `hexton_poc/docfacts.py` as `DF-nn` with an
  official URL, a verbatim quotation, and a classification of `documented` / `contradictory` /
  `undocumented`. `tests/test_docfacts.py` enforces that the classification cannot drift.
- Test uploads are private. No public test was authorised, so none was attempted.

## Layout

```text
hexton_poc/              the client under test
  docfacts.py            DF-01..DF-28 — the documentation pins
  jobstate.py            one canonical state model, one display mapping
  metadata.py            limits, tag cost, publishAt preflight, insert-body construction
  mention.py             @handle text extraction and the VERIFIED gate
  store/                 Credential Manager, DPAPI, plaintext-refusal policy
  oauth/                 loopback server, PKCE, state validation, refresh, revoke
  sheets/                sync keys and read-before-append reconciliation
  youtube/               identity, session store, resumable client, reconcile, sweep

mock_youtube/            local protocol stand-ins (not Google)
  server.py              resumable protocol + injected failures
  sheets.py              deliberately non-idempotent append

tests/                   151 tests
scripts/                 Windows probes that record and clean up after themselves
evidence/                recorded output
```

## Running it

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install httpx pytest tzdata
.venv\Scripts\python.exe -m pytest
```

`tzdata` is mandatory — see §7.6 of the report. On this Windows build CPython cannot resolve
`Asia/Dhaka`, `America/Los_Angeles`, or even `UTC` without it.

```powershell
# Windows persistence experiments (records evidence, then cleans up)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\win_persistence_probe.ps1
```

The probe requires **no** elevation to run, and says so in its output: when a step is denied it
records the exact error and marks the item blocked rather than guessing.

## What is deliberately not here

- No token exchange against a real endpoint, beyond a local stand-in.
- No `channels.list(mine=true)` call — that needs an authorized identity (`FEAS-01`).
- No upload attempt of any kind.
- No `spreadsheets.values` call against a real sheet (`FEAS-09`).
- No scheduled-task registration when the shell is unelevated; it records the denial instead.

Everything listed above is `blocked` with its prerequisite stated in `../07-feasibility-checklist.md`.
