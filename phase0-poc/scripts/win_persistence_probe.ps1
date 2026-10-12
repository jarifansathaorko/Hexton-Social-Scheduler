<#
.SYNOPSIS
    Phase 0 persistence experiment (FEAS-08). Records what this Windows build actually
    permits, and cleans up after itself.

.DESCRIPTION
    Answers, by experiment rather than by assumption:

      P1  Can a user-scoped Credential Manager entry be written and read back?
      P2  Can a DPAPI (user scope) blob be protected and unprotected?
      P3  Does zoneinfo resolve IANA zones without the tzdata package?
      P4  Can an "At log on" Task Scheduler task be registered from an unelevated shell?
      P5  What happens if such a task runs under a principal that is NOT the interactive
          user - i.e. can a non-interactive principal read the user's secrets?
      P6  Which non-elevated autostart mechanisms are writable?

    Nothing here stores, transmits, or reads a real Google credential.

.NOTES
    P4/P5 need an elevated prompt for a full answer. When the shell is unelevated the
    script records the exact error and marks the item BLOCKED rather than guessing.
#>

[CmdletBinding()]
param(
    [string]$OutFile = ""
)

$ErrorActionPreference = 'Continue'
$root   = Split-Path -Parent $PSScriptRoot
$evDir  = Join-Path $root 'evidence'
$stamp  = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$lines  = New-Object System.Collections.Generic.List[string]

function Note([string]$k, $v) { $lines.Add(("{0,-34} {1}" -f $k, $v)) }

function Section([string]$t) { $lines.Add(''); $lines.Add("=== $t ===") }

# --------------------------------------------------------------------------- header
Section 'Environment'
$id  = [Security.Principal.WindowsIdentity]::GetCurrent()
$pr  = New-Object Security.Principal.WindowsPrincipal($id)
Note 'captured_at_utc'      ((Get-Date).ToUniversalTime().ToString('o'))
Note 'user'                 $id.Name
Note 'user_sid'             $id.User.Value
Note 'is_elevated'          $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Note 'computer'             $env:COMPUTERNAME
Note 'os_caption'           (Get-CimInstance Win32_OperatingSystem).Caption
Note 'os_build'             (Get-CimInstance Win32_OperatingSystem).BuildNumber
Note 'powershell'           $PSVersionTable.PSVersion.ToString()
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
Note 'python'               $py

# --------------------------------------------------------------------------- P1/P2/P3
Section 'P1-P3  Credential store and timezone (via scripts/probe_env.py)'
# Prefer the PoC venv so a re-run is deterministic. The recorded JSON names the exact
# interpreter that produced it (`platform.executing_interpreter`), so an A/B comparison
# against `evidence/env_no_tzdata.json` (system python, tzdata absent) stays readable.
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $venvPy) { $py = $venvPy }
Note 'interpreter_used'       $py
if ($py) {
    $json = & $py -u (Join-Path $root 'scripts\probe_env.py') 2>&1 | Out-String
    $lines.Add($json.Trim())
}

# --------------------------------------------------------------------------- P4
Section 'P4  Register an "At log on" scheduled task'
$taskName = 'HextonPhase0_LogonProbe'
$created  = $false
try {
    $action  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit 0'
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Force -ErrorAction Stop | Out-Null
    $created = $true
    Note 'P4_onlogon_register' 'SUCCEEDED'
}
catch {
    Note 'P4_onlogon_register' ("DENIED - {0}" -f $_.Exception.Message.Trim())
    # The documented Windows restriction: creating a logon-triggered task needs elevation.
    Note 'P4_interpretation'  'Requires an elevated prompt. Recorded as BLOCKED, not as a failure of the design.'
}

if ($created) {
    try {
        $info = Get-ScheduledTask -TaskName $taskName
        Note 'P4_task_state'    $info.State
        Note 'P4_principal'     ("{0}\{1}" -f $info.Principal.UserId, $info.Principal.LogonType)
        Note 'P4_run_level'     $info.Principal.RunLevel
    }
    catch { Note 'P4_task_state' ("query failed: {0}" -f $_.Exception.Message) }
}

# --------------------------------------------------------------------------- P5
Section 'P5  Can a SYSTEM-principal task read the interactive user secrets?'
$sysName = 'HextonPhase0_SystemProbe'
$sysOk   = $false
try {
    schtasks /create /tn $sysName /tr "cmd /c exit 0" /sc once /st 23:58 /ru SYSTEM /f 2>&1 |
        Out-String | ForEach-Object { Note 'P5_system_register' $_.Trim() }
    $sysOk = -not ($LASTEXITCODE -ne 0)
}
catch {
    Note 'P5_system_register' ("DENIED - {0}" -f $_.Exception.Message.Trim())
    Note 'P5_interpretation'  'BLOCKED without elevation. This is the control case for AC-MVP-011.'
}

if ($sysOk) {
    # Run the probe AS SYSTEM and compare its Credential Manager view with the user's.
    $probe = Join-Path $root 'scripts\probe_env.py'
    $out   = Join-Path $env:TEMP 'hexton-phase0-system.json'
    schtasks /create /tn $sysName /tr "cmd /c `"$py`" -u `"$probe`" > `"$out`" 2>&1" `
        /sc once /st 23:59 /ru SYSTEM /f | Out-Null
    schtasks /run /tn $sysName | Out-Null
    Start-Sleep -Seconds 8
    Note 'P5_system_output'   (Test-Path $out)
    if (Test-Path $out) { $lines.Add((Get-Content $out -Raw)) }
    schtasks /delete /tn $sysName /f | Out-Null
}

# --------------------------------------------------------------------------- P6
Section 'P6  Non-elevated autostart mechanisms'
try {
    Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'HextonPoCProbe' -Value 'cmd /c exit 0'
    $v = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
            -Name 'HextonPoCProbe').HextonPoCProbe
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'HextonPoCProbe'
    Note 'P6_hkcu_run_key'    ("writable, read back = $v, then removed")
}
catch { Note 'P6_hkcu_run_key' ("not writable - {0}" -f $_.Exception.Message) }

try {
    $sd = [Environment]::GetFolderPath('Startup')
    $f  = Join-Path $sd 'hexton-phase0-probe.txt'
    Set-Content -Path $f -Value 'probe'
    Remove-Item $f
    Note 'P6_startup_folder'  ("writable = $sd, then removed")
}
catch { Note 'P6_startup_folder' ("not writable - {0}" -f $_.Exception.Message) }

# --------------------------------------------------------------------------- cleanup
Section 'Cleanup'
if ($created) {
    try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
          Note 'cleanup_onlogon_task' 'deleted' }
    catch { Note 'cleanup_onlogon_task' ("could not delete: {0}" -f $_.Exception.Message) }
}
else { Note 'cleanup_onlogon_task' 'nothing was created' }

try {
    schtasks /delete /tn $sysName /f 2>&1 | Out-Null
    Note 'cleanup_system_task' 'attempted (may not have existed)'
}
catch { Note 'cleanup_system_task' 'attempted' }

$leftover = @()
foreach ($t in @($taskName, $sysName)) {
    schtasks /query /tn $t 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $leftover += $t }
}
if ($leftover.Count -eq 0) { Note 'final_task_check' 'no HextonPoC tasks remain' }
else { Note 'final_task_check' ("still present: {0}" -f ($leftover -join ', ')) }

# --------------------------------------------------------------------------- output
$text = $lines -join [Environment]::NewLine
if ($OutFile -eq '') { $OutFile = Join-Path $evDir "win_persistence_$stamp.txt" }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
Set-Content -Path $OutFile -Value $text -Encoding UTF8
Write-Output $text
Write-Output ""
Write-Output ("Evidence written to: {0}" -f $OutFile)
