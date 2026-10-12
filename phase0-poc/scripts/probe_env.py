"""Environment + Windows-protection probe. Prints a JSON report; makes no network calls.

Run:  python scripts/probe_env.py > evidence/env.json

Everything here is an *observation on this machine*, not a documentation claim.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hexton_poc.store import (  # noqa: E402
    CredentialManagerStore,
    CredentialStoreError,
    DpapiStore,
    IS_WINDOWS,
)


def is_elevated() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        token = wintypes_token()
        return token
    except Exception:
        return False


def wintypes_token() -> bool:
    from ctypes import wintypes

    TOKEN_QUERY = 0x0008
    handle = wintypes.HANDLE()
    if not ctypes.windll.advapi32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(handle)):
        return False
    try:
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        ok = ctypes.windll.advapi32.GetTokenInformation(
            handle, 20, ctypes.byref(elevation), ctypes.sizeof(elevation),
            ctypes.byref(size))     # TokenElevation = 20
        return bool(ok and elevation.value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def timezone_probe() -> dict:
    """CPython on Windows ships no IANA database; zoneinfo needs the tzdata package."""
    import importlib.util

    has_tzdata = importlib.util.find_spec("tzdata") is not None
    out = {
        "windows_timezone_id": None,
        "tzdata_package_installed": has_tzdata,
        "iana_lookup": {},
    }
    try:
        from ctypes import wintypes

        class TIME_ZONE_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("Bias", wintypes.DWORD),
                ("StandardName", ctypes.c_wchar * 32),
                ("StandardDate", ctypes.c_byte * 16),
                ("StandardBias", wintypes.DWORD),
                ("DaylightName", ctypes.c_wchar * 32),
                ("DaylightDate", ctypes.c_byte * 16),
                ("DaylightBias", wintypes.DWORD),
            ]

        tz = TIME_ZONE_INFORMATION()
        ctypes.windll.kernel32.GetTimeZoneInformation(ctypes.byref(tz))
        out["windows_timezone_id"] = tz.StandardName
    except Exception as exc:                                    # pragma: no cover
        out["windows_timezone_id"] = f"error: {exc}"

    try:
        from zoneinfo import ZoneInfo

        for key in ("Asia/Dhaka", "America/Los_Angeles", "UTC"):
            try:
                ZoneInfo(key)
                out["iana_lookup"][key] = "ok"
            except Exception as exc:
                out["iana_lookup"][key] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:                                    # pragma: no cover
        out["iana_lookup"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def credential_store_probe(tmp: Path) -> dict:
    result: dict = {"available": False, "backend": None, "round_trip": None,
                    "enumeration_count": None, "error": None}
    if not IS_WINDOWS:
        result["error"] = "not Windows"
        return result

    marker = "__HEXTON_PHASE0_ENV_PROBE__"
    key = "phase0-env-probe"

    try:
        store = CredentialManagerStore()
        store.write(key, marker)
        result["backend"] = "windows_credential_manager"
        result["round_trip"] = store.read(key) == marker
        result["enumeration_count"] = len(store.list_keys())
        store.delete(key)
        result["cleaned_up"] = store.read(key) is None
    except Exception as exc:
        result["error"] = f"CredentialManagerStore: {type(exc).__name__}: {exc}"
        try:
            dp = DpapiStore(tmp / "env-probe.dpapi.json")
            dp.write(key, marker)
            result["backend"] = "dpapi_file"
            result["round_trip"] = dp.read(key) == marker
            dp.delete(key)
        except Exception as exc2:
            result["error"] += f" | DpapiStore: {type(exc2).__name__}: {exc2}"

    result["available"] = result["round_trip"] is True
    return result


def main() -> None:
    tmp = Path(os.environ.get("TEMP", ".")) / "hexton-phase0"
    tmp.mkdir(parents=True, exist_ok=True)

    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "executing_interpreter": sys.executable,
        },
        "identity": {
            "user": os.environ.get("USERNAME"),
            "domain": os.environ.get("USERDOMAIN"),
            "computer": os.environ.get("COMPUTERNAME"),
            "elevated": is_elevated(),
        },
        "timezone": timezone_probe(),
        "credential_store": credential_store_probe(tmp),
        "non_elevated_startup_mechanisms": startup_probe(),
    }
    print(json.dumps(report, indent=2))


def startup_probe() -> dict:
    """Which user-context autostart mechanisms this unelevated shell can actually write."""
    import winreg

    out: dict = {}
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run")
        winreg.SetValueEx(key, "HextonPoCProbe", 0, winreg.REG_SZ, "cmd /c echo probe")
        value, _ = winreg.QueryValueEx(key, "HextonPoCProbe")
        winreg.DeleteValue(key, "HextonPoCProbe")
        key.Close()
        out["hkcu_run_key"] = {"writable": True, "value_read_back": value}
    except Exception as exc:
        out["hkcu_run_key"] = {"writable": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        startup = Path(os.environ["APPDATA"]) / (
            r"Microsoft\Windows\Start Menu\Programs\Startup")
        probe = startup / "hexton-phase0-probe.txt"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        out["startup_folder"] = {"writable": True, "path": str(startup)}
    except Exception as exc:
        out["startup_folder"] = {"writable": False, "error": f"{type(exc).__name__}: {exc}"}

    return out


if __name__ == "__main__":
    main()
