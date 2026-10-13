# Verification Report: Database, State Machine, and Job Claiming (Prompt 4)

**Date:** 2026-10-13  
**Environment:** Windows 11 (win32), Python 3.14.7, SQLite 3 (WAL mode, `busy_timeout=15000ms`, `foreign_keys=ON`), Alembic (Migrations 0001 -> 0002)  
**Status:** PASS (Exit Gate Met)

---

## 1. Executive Summary

Prompt 4 requires rigorous verification of SQLite schema and concurrency behavior, canonical job state transitions, separate remote YouTube state tracking, worker lease claiming under `BEGIN IMMEDIATE`, crash/expired lease recovery, transaction rollback guarantees, foreign key integrity, and migration schema evolution.

All invariants have been validated across dedicated unit and integration suites, culminating in 12 comprehensive tests in `backend/tests/test_state_and_database.py` and 5 migration tests in `backend/tests/test_migration.py`:
- **Atomic Concurrency:** Multithreaded claim contention proves that under `BEGIN IMMEDIATE`, concurrent workers racing for the same job serialize write locks before reading. Exactly 1 claimant wins; competitors receive `None` with zero double-claims and zero deadlocks.
- **Lease Expiry & Recovery:** Leases expiring after worker termination/crash are seamlessly reclaimed on subsequent attempts, incrementing attempt counts without state corruption. Stale lease sweeps clear expired owners safely.
- **Idempotency & Duplicate Prevention:** The database-level unique constraint on `ScheduledJob.idempotency_key` guarantees duplicate schedule deliveries fail with an `IntegrityError` before duplicate jobs or uploads can be created.
- **Foreign Keys & Transaction Boundaries:** `PRAGMA foreign_keys = ON` actively rejects dangling references. Rolled-back transactions cleanly restore previous job states and discard uncommitted events without orphaned records.
- **State Machine Invariants:** Canonical `JobState` transitions are strictly validated. Illegal transitions (e.g. `DRAFT -> PUBLISHED`, `QUEUED -> PUBLISHED`) raise `InvalidTransition`. Terminal states (`PUBLISHED`, `CANCELLED`, `FAILED`) possess zero legal successor states.
- **Publication Invariant:** A job can *never* be displayed as `Published (verified)` without an observed remote `public` status and persisted video ID. Any job in the database with state `PUBLISHED` that lacks remote public confirmation is automatically coerced by the API presentation layer to `NEEDS_REVIEW`.
- **Migrations:** Upgrading an existing database from revision `0001` to `0002` (head) cleanly preserves existing connections, jobs, and foreign keys, while creating the new tables (`sheets_config`, `sheet_summary_record`, `policy_acceptance_record`) and adding `platform_connection.disabled_at`.

---

## 2. Architecture & Design Decisions

### 2.1 SQLite Pragmas & Connection Configuration (`backend/hexton/db/session.py`)
- `PRAGMA journal_mode = WAL`: Permits concurrent API reads while the worker holds write locks.
- `PRAGMA busy_timeout = 15000`: Ensures transient lock contention between API and background worker processes is waited out rather than failing abruptly.
- `PRAGMA foreign_keys = ON`: Mandatory per-connection pragma so relational integrity is enforced on all inserts and cascades.
- `PRAGMA synchronous = NORMAL`: Optimal balance between write throughput and write-ahead durability on Windows local storage.

### 2.2 Atomic Job Claiming via `immediate_transaction`
- Job claiming executes inside `immediate_transaction(engine)`, executing `BEGIN IMMEDIATE` directly at the driver level.
- This immediately acquires a SQLite RESERVED lock prior to evaluating `WHERE state IN ('JOB-QUEUED', ...) AND unleased AND due`.
- Prevents the classic race condition where two workers both read an unleased job under deferred transactions and conflict at commit time.

### 2.3 Local vs Remote State Separation
- **Local State (`ScheduledJob.state`):** Encoded exclusively by canonical enum `JobState` (`JOB-DRAFT`, `JOB-READY`, `JOB-QUEUED`, `JOB-UPLOADING`, `JOB-UPLOADED`, `JOB-SCHEDULED`, `JOB-PUBLISHED`, `JOB-FAILED_RETRY`, `JOB-BLOCKED`, `JOB-MISSED_WINDOW`, `JOB-NEEDS_REVIEW`, `JOB-CANCELLED`, `JOB-FAILED`).
- **Remote State (`RemoteObservation.remote_state`):** Kept as an append-only log using `RemoteState` (`remote.unknown`, `remote.not_uploaded`, `remote.uploading`, `remote.uploaded_processing`, `remote.scheduled`, `remote.public`, `remote.other_privacy`, `remote.failed`).
- The UI layer (`hexton/domain/jobstate.py:display_for` and `hexton/api/views.py:job_to_out`) maps local and remote states together, guaranteeing no optimistic or false publication claims can reach the frontend.

---

## 3. Test Matrix & Verification Evidence

| Test Case | Description | Command / File | Result |
| :--- | :--- | :--- | :--- |
| **`test_concurrent_multithreaded_claim_competition_single_job`** | 5 concurrent threads competing for 1 job under `BEGIN IMMEDIATE`. Exactly 1 claim awarded. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_concurrent_multithreaded_claim_competition_multiple_jobs`** | 8 concurrent threads claiming 10 due jobs simultaneously. Exactly 10 claims awarded with 0 duplicates. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_expired_lease_automatically_claimable_by_another_worker`** | Worker crash / expired lease reclaimed by next worker; attempt count incremented. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_expire_stale_leases_maintenance_sweep`** | Background maintenance sweep clears expired owners without altering state. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_idempotency_key_uniqueness_enforced_by_database`** | Inserting duplicate `idempotency_key` triggers SQLite `IntegrityError`. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_foreign_key_enforcement_rejects_dangling_references`** | Inserting job with invalid foreign keys triggers SQLite `IntegrityError`. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_interrupted_transaction_rolls_back_cleanly`** | Exception mid-transaction rolls back all writes; 0 orphaned events. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_canonical_state_machine_legal_and_illegal_transitions`** | Verifies canonical state machine table and invalid transition exceptions. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_published_state_cannot_be_displayed_without_remote_public_evidence`** | `display_for` rejects `JobState.PUBLISHED` without `RemoteState.PUBLIC`. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_job_to_out_downgrades_unbacked_published_job_to_needs_review`** | Unbacked `PUBLISHED` jobs coerced to `NEEDS_REVIEW` at view layer. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_job_to_out_accepts_verified_published_job_with_evidence`** | Verified published job rendered as `Published (verified)` with evidence. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **`test_migration_upgrade_from_v1_to_v2_preserves_existing_data`** | Migrating pre-populated database from v1 (`0001`) to v2 (`0002`) preserves data and relations. | `pytest backend/tests/test_state_and_database.py` | **PASS** |
| **Alembic Schema Invariance & Drift Check** | All 5 Alembic migration verification tests (upgrade, downgrade to base, schema comparison). | `pytest backend/tests/test_migration.py` | **PASS** |

---

## 4. Exit Gate Verification

- **Requirement:** Concurrent claim tests prove one claimant per job, migrations pass, and state invariants are tested.
- **Confirmation:**
  - ✅ Multithreaded concurrent claim tests demonstrate strict mutual exclusion and atomic leasing under `BEGIN IMMEDIATE`.
  - ✅ All Alembic migration upgrade, downgrade, and drift check tests pass.
  - ✅ Invariants governing `JobState.PUBLISHED`, remote evidence, idempotency, foreign keys, and rollbacks are 100% verified.
  - ✅ Full test suite: 591 passed, 0 failed.

**Exit Gate Satisfied.** Ready for Prompt 5 (Media validation and metadata preflight).
