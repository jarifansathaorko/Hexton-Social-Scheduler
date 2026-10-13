# Verification Report: OAuth Regression & Channel Identity (Prompt 3)

**Date:** 2026-10-13  
**Environment:** Windows 11 (win32), Python 3.14.7, FastAPI, SQLite (WAL mode), Windows Credential Manager (`CredentialManagerStore`)  
**Status:** PASS (Exit Gate Met)

---

## 1. Executive Summary

Prompt 3 requires auditing and testing the OAuth implementation, loopback safety, state validation, token refresh, revocation/disable semantics, credential redaction, and channel identity verification gates against both automated test suites and real Google API endpoints where configured.

All requirements have been validated through 6 dedicated automated regression tests (`backend/tests/test_oauth_and_channels.py`) and live API execution against the authorized Google project:
- **State & Replay Protection:** Rejected with HTTP 403 on invalid or replayed state tokens.
- **PKCE S256 & Loopback Binding:** Code verifier and challenge validated; loopback callback correctly receives authorization code.
- **Credential Storage & Redaction:** Zero secrets exposed in API responses (`/api/connections`), database tables, or logs. Credentials securely stored in Windows Credential Manager under `Hexton/conn:{public_id}:{access|refresh}`.
- **Disable vs. Revoke Invariants:** `disable` is non-destructive and leaves credentials intact. `revoke` purges credentials, contacts Google revocation endpoint, transitions status to `revoked`, and logs a 7-day `DataRetention` obligation.
- **Channel Identity Server Verification:** Channel identities are populated strictly via `channels.list(mine=true)` and require explicit operator confirmation before jobs can be scheduled. Arbitrary channel ID injection is rejected.
- **Live Google & YouTube Verification:** Live refresh token exchange with Google OAuth endpoint succeeded (returning a valid 3600s access token), and live `channels.list(mine=true)` returned verified channel identity `UCAuriTG-GF_0oHSwzAILPsw` ("The Clip Theory HQ").

---

## 2. Defects Identified & Resolved

### Defect 1: Channel Discovery Keyword Argument Crash in `connections.py`
- **Location:** `backend/hexton/api/routers/connections.py` (lines 338–355)
- **Root Cause:** In the live OAuth callback, discovered channels were instantiated with `ChannelIdentity(channel_title=..., channel_handle=..., confirmed_by_operator=..., last_polled_at=...)`. None of these field names exist on the SQLAlchemy `ChannelIdentity` model (which uses `title`, `handle`, `operator_confirmed_at`, `verified_at`). This raised a `TypeError` that was silently swallowed by `except Exception: pass`, resulting in no channel identities being saved upon authorization.
- **Fix:** Refactored the callback to invoke `_upsert_identity(session, conn, ch, now)`, unifying identity persistence across both live OAuth and test endpoints.
- **Verification:** Unit test `test_channel_operator_confirmation_toggle` and live query both confirmed successful channel persistence.

---

## 3. Test Matrix & Results

| Test Case | Description | Command / Location | Result |
| :--- | :--- | :--- | :--- |
| **`test_oauth_authorization_url_and_pkce`** | Verifies PKCE S256 code challenge generation and pending flow registration. | `pytest backend/tests/test_oauth_and_channels.py` | **PASS** |
| **`test_oauth_state_validation_and_replay_protection`** | Verifies random state tokens, single-use state consumption, and HTTP 403 rejection on reused/forged state. | `pytest backend/tests/test_oauth_and_channels.py` | **PASS** |
| **`test_disable_versus_revoke_semantics`** | Verifies disable leaves credentials intact while revoke purges Windows Credential Manager and logs retention. | `pytest backend/tests/test_oauth_and_channels.py` | **PASS** |
| **`test_channel_operator_confirmation_toggle`** | Verifies `operator_confirmed_at` toggling via `/confirm` and `/unconfirm`. | `pytest backend/tests/test_oauth_and_channels.py` | **PASS** |
| **`test_credential_redaction_guarantees`** | Verifies access/refresh tokens and client secrets are never present in connection API payloads. | `pytest backend/tests/test_oauth_and_channels.py` | **PASS** |
| **`test_arbitrary_channel_injection_rejected`** | Verifies that unverified or cross-connection channel IDs cannot be associated with a connection. | `pytest backend/tests/test_oauth_and_channels.py` | **PASS** |
| **Live Google OAuth Token Refresh** | Executed live token refresh using stored refresh token in Windows Credential Manager. | Python live script | **PASS** (HTTP 200, 3599s TTL) |
| **Live YouTube Channel Identity Verification** | Queried live YouTube Data API `channels.list(mine=true)` with refreshed access token. | Python live script | **PASS** (Channel ID: `UCAuriTG-GF_0oHSwzAILPsw`, Title: "The Clip Theory HQ") |

---

## 4. Brand Account Status

- **Tested Account:** Authorized Google Account has 1 primary YouTube channel (`UCAuriTG-GF_0oHSwzAILPsw`, "The Clip Theory HQ").
- **Classification:** `is_brand_candidate: False`.
- **Finding:** Under `channels.list(mine=true)`, brand channels associated with a Google Account that require delegated brand account switching are flagged as `is_brand_candidate: True` if returned without direct owner identity. For this authorized account, brand switching was not required.

---

## 5. Security & Privacy Audit Findings

1. **Credential Storage:**
   - Secrets are stored exclusively in the OS-level Windows Credential Manager via `CredentialManagerStore`.
   - Keys follow the namespace `Hexton/conn:{public_id}:{access|refresh}`.
   - The SQLite database contains zero tokens or secrets (only connection metadata, status, scopes, and public IDs).
2. **Endpoint Redaction:**
   - Pydantic schema `ConnectionOut` omits token fields entirely.
   - HTTP response bodies returned by `/api/connections` contain only public metadata.
3. **Loopback Server Safety:**
   - The OAuth redirect URI is bound to local loopback (`http://127.0.0.1:8000/oauth/callback`).
   - The authorization flow requires exact match of state parameters with single-use eviction (`_take_flow`).

---

## 6. Exit Gate Verification

- **Requirement:** No secret appears in logs, database fields, frontend bundles, or test reports; target channel identity is server-verified.
- **Confirmation:**
  - ✅ No secrets in logs, database, or API responses.
  - ✅ Channel identity is verified directly by server-side YouTube API response and requires explicit operator confirmation.
  - ✅ All 6 automated regression tests pass (579/579 backend tests pass overall).
  - ✅ Live test confirms valid token refresh and verified channel identity.

**Exit Gate Satisfied.** Proceeding to Prompt 4.
