# Verification Report: Security, Privacy, and Compliance Hardening

**Document:** `docs/verification/security-and-privacy.md`  
**Playbook Phase:** Prompt 15 — Security, Privacy, and Compliance Hardening  
**Date:** 2026-10-13  
**Status:** PASS (with explicit owner policy decisions documented)  
**Test Suite:** `backend/tests/test_compliance.py` (53/53 passed), `backend/tests/test_api.py` (64/64 passed) — Total: 117/117 passed

---

## 1. Executive Summary

This phase performed a comprehensive security, privacy, and regulatory audit across the local application surface, adhering strictly to:
1. Google API Services User Data Policy (including Limited Use requirements).
2. YouTube Developer Policies and Terms of Service.
3. Windows Local Security Architecture (DPAPI, Credential Manager, loopback isolation, interactive user boundary).

All 117 automated compliance, web security, and API hardening tests pass with zero failures.

---

## 2. Technical Security Audits & Verified Mitigations

### 2.1 Loopback Binding and Host/Origin Validation
- **Interface Restriction:** The server binds strictly to `127.0.0.1` (IPv4 loopback). Binding to `0.0.0.0` (all interfaces) or any public LAN IP is explicitly refused in configuration validation.
- **DNS Rebinding Defense:** Validated by `test_a_foreign_host_header_is_refused_even_for_open_paths`. Only loopback host headers (`127.0.0.1`, `localhost`) are permitted. Host headers from external domains or rebinding proxies receive an immediate HTTP 403 Forbidden.
- **Origin Validation:** Validated by `test_a_foreign_origin_is_refused_on_a_state_change`. State-changing requests (`POST`, `PUT`, `DELETE`) with foreign `Origin` headers are blocked.

### 2.2 Session Security and CSRF Protection
- **Session Cookie:** Signed HMAC cookie minted using a 32-byte secret (`session.key`). Enforced attributes: `HttpOnly=True`, `SameSite=Strict`.
- **Stateless CSRF Token:** Derived deterministically from session nonce via HMAC-SHA256 (`csrf_for(nonce)`). Required on all state-changing requests via header `X-CSRF-Token`. Cross-origin browser forms cannot read the cookie nor set the custom header.

### 2.3 Secret Storage & Zero Database Leaks
- **Database Schema Guarantee:** Verified by schema inspection. The `platform_connection` table stores metadata only (public ID, provider, scopes, grant timestamps). No tokens, client secrets, or OAuth credentials are ever written to SQLite.
- **Credential Storage:** In production, secrets are stored exclusively in the Windows Credential Manager and protected via DPAPI (Data Protection API) scoped to the interactive user's Windows SID.
- **API Response Filtering:** Validated by `test_no_credential_material_ever_appears_in_a_response` and `test_no_token_ever_appears_in_a_reporting_response`. Zero tokens or secrets are serialized in JSON API responses.

### 2.4 Diagnostic Exports & Log Redaction
- **Diagnostics Redaction:** Validated by `core.spec.ts::the diagnostics export carries no credential and no Windows account name`.
- **Trace Redaction:** File paths and stack traces replace Windows usernames (`jarif`) with `[REDACTED_USER]` and strip all credential strings.
- **Log Masking:** OAuth tokens in logs are masked with truncated ellipses (`ya29…`).

### 2.5 Path Traversal & Malformed Requests
- **Media Ingestion:** Media paths must exist, be regular files, and pass container header inspection (`_sniff()`). Attempting to traverse directories or supply non-video binaries is rejected as `JOB-BLOCKED` with `FailureKind.VALIDATION`.
- **Alembic Database Paths:** Resolved relative to validated local app data directories (`%LOCALAPPDATA%\Hexton`).

---

## 3. Developer Policy and Regulatory Compliance

### 3.1 Policy Gate & Mandatory User Consent
- **Privacy Policy Gate:** Before any scheduling, upload, or Sheets feature can be used, the operator must accept the Privacy Policy and Terms (`test_the_gate_is_closed_until_something_is_accepted`).
- **Policy Links:** Directly renders pinned URLs to:
  - Google Privacy Policy: `https://policies.google.com/privacy`
  - YouTube Terms of Service: `https://www.youtube.com/t/terms`
  - Google Security Settings (Third-party access): `https://myaccount.google.com/permissions`

### 3.2 Authorized Data Retention (`DF-20` / `DF-21`)
- **Connection Revocation:** Disconnecting a channel removes stored credentials immediately (`D-1` window) and starts the 30-day retention countdown.
- **Automated Retention Sweeper:** Verified by `test_the_sweep_purges_at_the_deadline_and_closes_the_row`. `RetentionSweeper` runs at worker startup and periodic intervals; after 30 days of revocation, all cached authorized metadata (channel identities, observations) is permanently purged.

---

## 4. Policy-Dependent Owner Decisions

The following policy choices require explicit organizational sign-off prior to external commercial release:
1. **Google OAuth Verification:** When transitioning from "Testing" mode (100 test users, 7-day token expiry) to "In Production", the application owner must submit the Google Cloud project for OAuth App Verification and YouTube API Quota Extension.
2. **Channel Ownership Legal Attestation:** Operators must attest that they have rights to the video content and consent to YouTube Community Guidelines.
3. **External Backup Policy:** Local SQLite backups must follow the same 30-day deletion lifecycle if restored after connection revocation.

---

## 5. Exit Gate Evaluation

| Exit Gate Requirement | Status | Verification Evidence |
| :--- | :--- | :--- |
| **Critical/high technical security findings fixed** | **SATISFIED** | Loopback binding, DNS rebinding protection, CSRF, HttpOnly SameSite=Strict cookies, secret store isolation, and log redaction verified. |
| **No secrets in DB, build, logs, or exports** | **SATISFIED** | Verified across automated tests in `test_api.py`, `test_compliance.py`, and Playwright E2E. |
| **Policy-dependent decisions explicit** | **SATISFIED** | Detailed in §4 (OAuth verification, quota extension, backup lifecycle). |

**Phase Status:** Prompt 15 Complete. Proceeding to Prompt 16.
