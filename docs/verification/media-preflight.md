# Verification Report: Media Validation & Metadata Preflight (Prompt 5)

**Date:** 2026-10-13  
**Environment:** Windows 11 (win32), Python 3.14.7, FastAPI, SQLite (WAL mode), `tzdata` (IANA Timezones)  
**Status:** PASS (Exit Gate Met)

---

## 1. Executive Summary

Prompt 5 audits and validates the content preparation and preflight pipeline prior to initiating any YouTube API uploads. The objective is to verify that corrupt, modified, spoofed, over-limit, or improperly scheduled items are deterministically rejected with clear operator error codes, while valid items pass without silent mutations or data corruption.

All requirements have been validated through 17 dedicated boundary tests in `backend/tests/test_media_preflight.py` alongside the existing domain media/validation test suites:
- **Media File Integrity:** Missing files, zero-byte empty files, unreadable files, and files modified after scheduling (SHA-256 hash mismatch) are trapped before any upload request.
- **Container Sniffing & Extension Spoofing:** Non-video containers (such as text or binary files renamed to `.mp4`) are caught and rejected via ISO-BMFF / EBML box sniffing (`sniff_container`) rather than relying on extension trust.
- **Safe Metadata Probing:** Does not shell out to external uninstalled binaries like `ffmpeg`. Direct box header parsing safely extracts `duration_seconds` and reports `duration_unknown` when absent without inventing values.
- **Metadata Boundaries:**
  - Titles: Empty, whitespace-only, >100 characters, and angle brackets (`<`, `>`) are rejected.
  - Descriptions: Multi-byte UTF-8 bytes are counted accurately; exceeding 5000 bytes is rejected.
  - Tags: Counted with quoting-aware costs (spaces require quotes) and delimiter commas; exceeding 500 characters is rejected.
  - Category ID: Non-numeric YouTube category IDs are rejected.
- **Explicit Privacy & Scheduling:**
  - `privacyStatus` is never defaulted on the server; an explicit status (`private`, `unlisted`, `public`) is required.
  - Scheduled publication (`publishAt`) requires `privacyStatus='private'`; sending `publishAt` with any other visibility is rejected.
  - Naive datetimes and past publication times are rejected as hard preflight errors (`DF-04`: YouTube treats a past `publishAt` as "publish immediately").
- **Consent-Driven Transformations:**
  - Content transformations (`apply_transforms`) require explicit consent (`consent=True`). With `consent=False`, descriptions return 100% byte-identical with zero silent truncation.
- **Pre-Upload Channel Isolation Gate:**
  - Jobs missing an operator-confirmed channel identity or referencing a channel belonging to a different connection are blocked before any network call.

---

## 2. Test Matrix & Boundary Coverage

| Boundary / Edge Case | Test Function | Condition Tested | Result |
| :--- | :--- | :--- | :--- |
| **Missing File** | `test_missing_media_file_rejected` | Non-existent path returns `missing_file` & `file_missing_since_scheduling`. | **PASS** |
| **Empty File** | `test_empty_media_file_rejected` | 0-byte file rejected with `bad_size`. | **PASS** |
| **Digest Mismatch** | `test_file_changed_after_scheduling_sha256_mismatch` | Modified bytes after approval fail SHA-256 check with `file_changed_since_scheduling`. | **PASS** |
| **Container Spoofing** | `test_spoofed_container_extension_rejected` | Text file named `.mp4` rejected via box sniffer (`bad_container`). | **PASS** |
| **Extension Gate** | `test_unsupported_or_missing_extension` | Unsupported extension (`.exe`) and extensionless files rejected. | **PASS** |
| **Safe Probing** | `test_safe_metadata_probe_handles_unknown_duration_gracefully` | Missing `mvhd` reports `duration_parsed=False` without inventing values. | **PASS** |
| **Title Boundaries** | `test_title_boundaries` | Empty, >100 chars, and angle brackets `<` / `>` rejected. | **PASS** |
| **Description Byte Count** | `test_description_byte_boundary` | 5000 bytes OK; 5001 bytes and multi-byte UTF-8 overruns rejected. | **PASS** |
| **Tags Quoting Cost** | `test_tags_quoting_and_comma_cost_accounting` | Quoted tags with spaces, comma warnings, and >500 char budgets enforced. | **PASS** |
| **Category ID & Privacy** | `test_category_id_and_privacy_validation` | Non-numeric category IDs and invalid privacy values rejected. | **PASS** |
| **Insert Body Constraints** | `test_build_insert_body_enforces_privacy_and_publish_at` | `publishAt` with non-private visibility rejected; RFC3339 UTC formatted cleanly. | **PASS** |
| **Timezone & Naive Time** | `test_timezone_resolution_and_naive_datetime` | IANA zone resolution enforced; naive datetimes rejected. | **PASS** |
| **Past Publication Time** | `test_past_publish_time_hard_block` | Past `publishAt` hard blocked (`publish_at_in_past`) preventing instant public leak. | **PASS** |
| **Rescheduling Guard** | `test_already_published_video_cannot_be_rescheduled` | Already public video blocked from rescheduling (`already_published`). | **PASS** |
| **Transformation Consent** | `test_transforms_require_explicit_consent` | Without consent: byte-identical. With consent: hashtags normalised & logged. | **PASS** |
| **Missing Channel Preflight**| `test_runner_preflight_blocks_job_with_missing_channel` | Worker preflight blocks job with `FailureKind.VALIDATION` prior to upload. | **PASS** |
| **Cross-Connection Mismatch**| `test_runner_preflight_blocks_job_with_cross_connection_channel_mismatch` | Channel belonging to different connection blocked with `channel-mismatch`. | **PASS** |

---

## 3. Exit Gate Verification

- **Requirement:** Invalid items are rejected before any YouTube API upload request; valid metadata passes deterministically.
- **Confirmation:**
  - ✅ All 17 boundary test cases pass (608 backend tests passing overall).
  - ✅ Zero invalid items can reach `YouTubeConnector.initiate_resumable_upload`.
  - ✅ Zero silent truncation or unsolicited text transformations.

**Exit Gate Satisfied.** Ready for Prompt 6 (YouTube upload, progress, and resumable recovery).
