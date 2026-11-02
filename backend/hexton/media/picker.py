"""Native file picker integration for Windows.

Provides a file-explorer dialog that runs in the foreground without
blocking FastAPI's event loop, allowing operators to select local video clips.
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_LAST_DIR: Path | None = None

VIDEO_FILTER = (
    "Video Files (*.mp4;*.mov;*.mkv;*.webm;*.avi;*.flv)|"
    "*.mp4;*.mov;*.mkv;*.webm;*.avi;*.flv|"
    "All Files (*.*)|*.*"
)


def _resolve_target_dir(initial_dir: str | Path | None) -> Path | None:
    global _LAST_DIR
    target: Path | None = None
    if initial_dir:
        p = Path(initial_dir)
        target = p if p.is_dir() else p.parent
        if not target.is_dir():
            target = None

    if target is None:
        if _LAST_DIR is not None and _LAST_DIR.is_dir():
            target = _LAST_DIR
        else:
            for candidate in ("Videos", "Downloads", "Desktop"):
                folder = Path.home() / candidate
                if folder.is_dir():
                    target = folder
                    break
    return target


def _pick_powershell(target_dir: Path | None) -> str | None:
    """Launch native Windows OpenFileDialog via PowerShell EncodedCommand.

    Avoids command-line pipe or quote escaping issues and guarantees foreground
    focus by parenting to a TopMost form.
    """
    initial_cmd = ""
    if target_dir and target_dir.is_dir():
        escaped = str(target_dir.resolve()).replace("'", "''")
        initial_cmd = f"$f.InitialDirectory = '{escaped}'; "

    ps_script = (
        "$ProgressPreference = 'SilentlyContinue'; "
        "[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | Out-Null; "
        "$f = New-Object System.Windows.Forms.OpenFileDialog; "
        f'$f.Filter = "{VIDEO_FILTER}"; '
        '$f.Title = "Select Video Clip"; '
        "$f.ShowHelp = $false; "
        "$f.RestoreDirectory = $true; "
        f"{initial_cmd}"
        "$form = New-Object System.Windows.Forms.Form; "
        "$form.TopMost = $true; "
        "$result = $f.ShowDialog($form); "
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { "
        "    [Console]::WriteLine($f.FileName) "
        "}; "
        "$form.Dispose(); "
        "$f.Dispose();"
    )

    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    # DO NOT pass CREATE_NO_WINDOW so the GUI window has desktop window station access
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if proc.returncode == 0:
        chosen = proc.stdout.strip()
        if chosen and Path(chosen).exists():
            return str(Path(chosen).resolve())
        return None
    logger.warning("PowerShell file dialog exited with code %d: %s", proc.returncode, proc.stderr)
    return None


def _pick_tkinter_subprocess(target_dir: Path | None) -> str | None:
    """Fallback file picker using Python Tkinter executed in a dedicated subprocess.

    Executing in a separate subprocess ensures the GUI runs in its own main thread,
    avoiding 'main thread is not in main loop' errors when called from async workers.
    """
    init_str = str(target_dir.resolve()) if target_dir and target_dir.is_dir() else ""
    code = f"""
import sys
from pathlib import Path
try:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    root.lift()
    root.focus_force()

    initial = {init_str!r} or None
    chosen = filedialog.askopenfilename(
        parent=root,
        title="Select Video Clip",
        initialdir=initial,
        filetypes=[
            ("Video Files", "*.mp4 *.mov *.mkv *.webm *.avi *.flv"),
            ("All Files", "*.*"),
        ],
    )
    root.destroy()
    if chosen and Path(chosen).exists():
        print(str(Path(chosen).resolve()))
except Exception as exc:
    sys.exit(1)
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode == 0:
            chosen = proc.stdout.strip()
            if chosen and Path(chosen).exists():
                return chosen
    except Exception as exc:
        logger.warning("Tkinter subprocess file dialog failed: %s", exc)
    return None


def pick_video_file(initial_dir: str | Path | None = None) -> str | None:
    """Prompt the operator to select a video file using the native OS file picker.

    Returns the absolute path to the selected file, or None if cancelled.
    """
    global _LAST_DIR

    # Test hook: bypass interactive GUI if testing environment specifies an override
    if "HEXTON_TEST_PICKER_PATH" in os.environ:
        override = os.environ["HEXTON_TEST_PICKER_PATH"]
        if override:
            p = Path(override)
            if p.exists():
                return str(p.resolve())
        return None

    target_dir = _resolve_target_dir(initial_dir)

    # 1. Primary: Native Windows Forms via PowerShell EncodedCommand
    try:
        chosen = _pick_powershell(target_dir)
        if chosen:
            _LAST_DIR = Path(chosen).parent
            return chosen
    except Exception as exc:
        logger.warning("PowerShell file dialog failed: %s", exc)

    # 2. Secondary: Subprocess Tkinter Win32 GetOpenFileName
    try:
        chosen = _pick_tkinter_subprocess(target_dir)
        if chosen:
            _LAST_DIR = Path(chosen).parent
            return chosen
    except Exception as exc:
        logger.warning("Tkinter file dialog failed: %s", exc)

    return None
