"""Native file picker integration for Windows.

Provides a file-explorer dialog that runs in the foreground without
blocking FastAPI's event loop, allowing operators to select local video clips.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def pick_video_file() -> str | None:
    """Prompt the operator to select a video file using the native OS file picker.

    Returns the absolute path to the selected file, or None if cancelled.
    """
    # 1. Try Windows Forms via PowerShell (native Windows look and feel)
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$f = New-Object System.Windows.Forms.OpenFileDialog; "
        '$f.Filter = "Video Files (*.mp4;*.mov;*.mkv;*.webm;*.avi;*.flv)|*.mp4;*.mov;*.mkv;*.webm;*.avi;*.flv|All Files (*.*)|*.*"; '
        '$f.Title = "Select Video Clip"; '
        "$f.ShowHelp = $false; "
        "$f.RestoreDirectory = $true; "
        "$dummy = New-Object System.Windows.Forms.Form; "
        "$dummy.TopMost = $true; "
        "$dummy.Opacity = 0; "
        "$dummy.Show(); "
        "$result = $f.ShowDialog($dummy); "
        "$dummy.Close(); "
        "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::WriteLine($f.FileName) }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode == 0:
            chosen = proc.stdout.strip()
            if chosen and Path(chosen).exists():
                return str(Path(chosen).resolve())
            if not chosen:
                return None
    except Exception as exc:
        logger.warning("PowerShell file dialog failed: %s", exc)

    # 2. Fallback to Tkinter (works on all Windows Python installs)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        root.lift()
        root.focus_force()
        chosen = filedialog.askopenfilename(
            title="Select Video Clip",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.mkv *.webm *.avi *.flv"),
                ("All files", "*.*"),
            ],
        )
        root.destroy()
        if chosen and Path(chosen).exists():
            return str(Path(chosen).resolve())
    except Exception as exc:
        logger.warning("Tkinter file dialog failed: %s", exc)

    return None
