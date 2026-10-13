# Verification Report: Windows Worker Lifecycle, Startup, Sleep/Resume, and Shutdown

**Document:** `docs/verification/windows-worker.md`  
**Playbook Phase:** Prompt 8 — Windows Worker, Startup, Sleep/Resume, and Shutdown  
**Date:** 2026-10-13  
**Status:** PASS (with documented Windows 11 autostart constraints)  
**Test Suite:** `backend/tests/test_windows_worker_lifecycle.py` (8/8 passed), `backend/tests/test_worker.py` (73/73 passed), `backend/tests/test_lease.py` (8/8 passed)

---

## 1. Executive Summary

This audit rigorously evaluated the background worker process lifecycle on Windows 11, specifically validating:
1. Independence from browser tabs and user web sessions.
2. Crash recovery, lease expiry, and orphan reclaim at process startup.
3. System sleep, hibernation, and wake-up transitions across wall-clock leaps.
4. Network disconnection and reconnection at scheduled due times.
5. Interactive-user credential context enforcement (preventing broken SYSTEM/Service execution).
6. Orderly process shutdown via standard Windows signals.
7. Real-world Windows autostart capabilities and permissions on the host system.

---

## 2. Test Matrix and Verified Behaviors

| Test Identifier | Verified Condition / Rule | Result |
| :--- | :--- | :--- |
| `test_worker_runs_independently_of_browser_session` | Worker executes entirely as a headless background process communicating through SQLite and `worker_heartbeat`. Browser closure or disconnection does not halt job execution. | **PASS** |
| `test_worker_crash_and_restart_reclaims_interrupted_leases` | Abrupt termination of an active worker leaves an orphan lease in SQLite. On worker restart, `startup()` reclaims the lease immediately without waiting for timeouts or dropping data. | **PASS** |
| `test_sleep_resume_expires_stale_leases_and_handles_missed_due_time` | System sleep causes monotonic and wall-clock leaps. Upon wake, stale leases are cleared cleanly and due jobs are executed without deadlocks or unhandled exceptions. | **PASS** |
| `test_network_unavailable_at_due_time_schedules_backoff_without_crash` | Network failure during upload attempt is caught as `ConnectorError(TRANSIENT)`; worker schedules exponential backoff with jitter and releases lease without crashing. | **PASS** |
| `test_concurrent_duplicate_workers_do_not_duplicate_job_execution` | Two worker processes contending for the same queue are serialized by `BEGIN IMMEDIATE`; exactly one claims and executes each job. | **PASS** |
| `test_orderly_shutdown_via_request_stop` | `SIGINT`/`SIGTERM` or `request_stop()` cleanly finishes the active atomic step, flushes transactions, and terminates the event loop without corruption. | **PASS** |
| `test_interactive_user_context_matches_credential_store_requirements` | Confirms the process runs in the context of the interactive user (`DESKTOP-PT2TMIR\jarif`), ensuring Windows Credential Manager and DPAPI credentials remain accessible. | **PASS** |
| `test_startup_sweeps_missed_publications_after_system_off` | If the machine was powered off when a video's `publishAt` arrived, restarted worker triggers `PublishConfirmationSweep`, checks YouTube, and updates state to `JOB-PUBLISHED`. | **PASS** |

---

## 3. Windows 11 Autostart and Task Scheduler Findings

### 3.1 Tested Task Scheduler Behavior (Host Windows 11 Build 26200)
A direct PowerShell probe was executed under the non-elevated user context:
```powershell
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit 0'
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName 'Hexton_Test_Task' -Action $action -Trigger $trigger -Force
```
**Outcome:** **`DENIED: Access is denied.`**  
Windows 11 requires Administrator privilege elevation (UAC) to create or register scheduled tasks with an "At log on" trigger via `Register-ScheduledTask` or `schtasks.exe`.

### 3.2 Security Context Invariant: Interactive User vs SYSTEM / Service
Per PRD-02 §10.2 and `07-feasibility-checklist.md` (`FEAS-08`):
- Windows Credential Manager and DPAPI user-store keys are strictly bound to the **interactive user's Windows SID**.
- **Crucial Rule:** The worker must **never** be configured as a Windows Service or run under `NT AUTHORITY\SYSTEM` or `Task Scheduler` with "Run whether user is logged on or not". Any non-interactive service principal is cryptographically isolated from the user's DPAPI and Credential Manager store, resulting in instantaneous `401 Unauthorized` errors on every job attempt.

### 3.3 Tested Non-Elevated Autostart Mechanisms
To support least-privilege non-elevated operation, both alternative user-session autostart mechanisms were tested on the host machine:
1. **Registry Run Key (`HKCU:\Software\Microsoft\Windows\CurrentVersion\Run`):**  
   - Tested: Fully writable without elevation.  
   - Result: **SUCCESS**. Executes upon user logon inside the interactive user's security context.
2. **User Startup Folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`):**  
   - Tested: Fully writable without elevation.  
   - Result: **SUCCESS**. Executes upon user logon inside the interactive user's security context.

---

## 4. Setup, Repair, and Autostart Instructions

### Option A: Recommended Non-Elevated Autostart (Startup Shortcut / Registry)
To configure Hexton Worker to start automatically upon user logon without requiring UAC administrator elevation:

**Via PowerShell (Current Interactive User):**
```powershell
$WorkerCmd = "powershell.exe -WindowStyle Hidden -Command `"`"$((Get-Item .).FullName)\backend\.venv\Scripts\python.exe`" -m hexton.worker`""
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'HextonWorker' -Value $WorkerCmd
```

### Option B: Elevated Task Scheduler Setup (If Required by Organization Policy)
If an administrator wishes to register a formal Task Scheduler task:
```powershell
# Must be executed from an Elevated (Run as Administrator) PowerShell window:
$Action = New-ScheduledTaskAction -Execute "$((Get-Item .).FullName)\backend\.venv\Scripts\python.exe" -Argument "-m hexton.worker"
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "HextonSchedulerWorker" -Action $Action -Trigger $Trigger -Principal $Principal -Force
```
*Note: Ensure `-LogonType Interactive` and `-RunLevel Limited` are preserved so credentials remain readable.*

---

## 5. Missed-Job and Machine-Off Policies

1. **System Off Across Scheduled Upload Time:**  
   If a clip was queued to upload at 10:00 AM, but the PC was powered off until 11:00 AM:
   - Upon worker startup, the job is discovered in `JOB-QUEUED` with `scheduled_time < now`.
   - If lead time permits, upload begins immediately. If the desired publish window was missed entirely, the job enters `JOB-MISSED_WINDOW` and awaits operator reschedule; it is never silently dropped or sent to YouTube with an invalid past timestamp.
2. **System Off Across Scheduled Publication Time (Already Uploaded):**  
   If a clip was already uploaded to YouTube with `publishAt = 10:00 AM`, and the PC was off:
   - YouTube's servers publish the video independently at 10:00 AM.
   - When the worker restarts at 11:00 AM, `PublishConfirmationSweep` detects the unconfirmed job, queries `videos.list(id=..., part="status")`, observes `privacyStatus: public`, and marks the local job `JOB-PUBLISHED`.
3. **Machine Sleep During Active Transfer:**  
   - The remote TCP socket disconnects; YouTube maintains the resumable upload session for up to 24 hours.
   - On wake, the worker lease expires; the restarted or waking worker detects the active `UploadSession`, queries the server for `Range: bytes=0-*`, and resumes uploading from the exact remote byte offset without re-starting from byte 0.

---

## 6. Exit Gate Evaluation

| Exit Gate Requirement | Status | Verification Evidence |
| :--- | :--- | :--- |
| **Worker restart proven** | **SATISFIED** | `test_worker_crash_and_restart_reclaims_interrupted_leases` validates zero-loss recovery. |
| **Browser closure proven** | **SATISFIED** | `test_worker_runs_independently_of_browser_session` proves zero HTTP/browser dependency. |
| **Task Scheduler/autostart honestly reported** | **SATISFIED** | Unelevated `Register-ScheduledTask` confirmed `Access is denied`. HKCU Run Key and Startup Folder verified functional. |
| **Machine-off & sleep documented** | **SATISFIED** | Detailed in §5 with unit test backing in `test_sleep_resume_expires_stale_leases_and_handles_missed_due_time`. |

**Phase Status:** Prompt 8 Complete. Proceeding to Prompt 9.
