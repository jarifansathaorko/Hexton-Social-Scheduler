"""Windows credential storage backends.

Priority order (``02-technical-prd.md`` section 10.2):

1. :class:`CredentialManagerStore` - Windows Credential Manager via ``advapi32`` CredMan,
   reached with ``ctypes`` so there is no third-party dependency in the secret path.
2. :class:`DpapiStore` - a DPAPI (user scope) protected blob on disk.
3. :class:`PlaintextStore` - **test only**. Production must refuse to select it.

None of these can be read by a Windows Service or by a Task Scheduler principal running
as another identity, which is the constraint FEAS-08 exists to demonstrate.
"""

from __future__ import annotations

import ctypes
import os
import sys
from abc import ABC, abstractmethod
from ctypes import wintypes
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore(ABC):
    """A named, secret-bearing store keyed by connection ID."""

    name: str
    production_safe: bool = True

    @abstractmethod
    def write(self, key: str, secret: str) -> None: ...

    @abstractmethod
    def read(self, key: str) -> str | None: ...

    @abstractmethod
    def delete(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self) -> list[str]: ...


# --------------------------------------------------------------------------
# Windows Credential Manager (advapi32 CredWriteW / CredReadW / CredDeleteW)
# --------------------------------------------------------------------------

class _CREDSTRUCT(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class CredentialManagerStore(CredentialStore):
    """Windows Credential Manager generic credentials, per current interactive user."""

    name = "windows_credential_manager"
    production_safe = True
    PREFIX = "HextonPoC/"

    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise CredentialStoreError("Credential Manager is only available on Windows")
        self._adv = ctypes.windll.advapi32
        self._adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDSTRUCT), wintypes.DWORD]
        self._adv.CredWriteW.restype = wintypes.BOOL
        # The out-parameter is a PCREDENTIALW*: declared as c_void_p so that byref() passes
        # the address of the holder rather than the (initially NULL) pointer value. Passing
        # a POINTER here makes ctypes write through a NULL pointer and crash the process.
        self._adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p]
        self._adv.CredReadW.restype = wintypes.BOOL
        self._adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._adv.CredDeleteW.restype = wintypes.BOOL
        self._adv.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._adv.CredEnumerateW.restype = wintypes.BOOL
        self._adv.CredFree.argtypes = [ctypes.c_void_p]
        self._adv.CredFree.restype = wintypes.DWORD

    def _target(self, key: str) -> str:
        return f"{self.PREFIX}{key}"

    def write(self, key: str, secret: str) -> None:
        raw = secret.encode("utf-8")
        blob = ctypes.create_string_buffer(raw, len(raw))
        cred = _CREDSTRUCT()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = self._target(key)
        cred.Comment = "Hexton scheduler PoC credential"
        cred.CredentialBlobSize = len(raw)
        cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = "hexton"
        if not self._adv.CredWriteW(ctypes.byref(cred), 0):
            raise CredentialStoreError(
                f"CredWriteW failed for {self._target(key)} ( GetLastError="
                f"{ctypes.GetLastError()} )"
            )

    def read(self, key: str) -> str | None:
        ptr = ctypes.POINTER(_CREDSTRUCT)()
        if not self._adv.CredReadW(self._target(key), CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
            err = ctypes.GetLastError()
            if err == ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError(
                f"CredReadW failed for {self._target(key)} (GetLastError={err})"
            )
        try:
            blob = ctypes.string_at(ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize)
        finally:
            self._adv.CredFree(ptr)
        text = blob.decode("utf-8", errors="strict")
        return text.rstrip("\x00")

    def delete(self, key: str) -> bool:
        ok = self._adv.CredDeleteW(self._target(key), CRED_TYPE_GENERIC, 0)
        if not ok and ctypes.GetLastError() != ERROR_NOT_FOUND:
            raise CredentialStoreError(
                f"CredDeleteW failed for {self._target(key)} (GetLastError={ctypes.GetLastError()})"
            )
        return bool(ok)

    def list_keys(self) -> list[str]:
        # CredEnumerateW returns PCREDENTIALW* — an array of *pointers* to credential
        # structures, not an array of structures. Traversing it as an array of structures
        # reads the pointer values as if they were struct fields and crashes the process.
        holder = ctypes.POINTER(_CREDSTRUCT)()
        count = wintypes.DWORD()
        if not self._adv.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(holder)):
            err = ctypes.GetLastError()
            if err == ERROR_NOT_FOUND:
                return []
            raise CredentialStoreError(f"CredEnumerateW failed (GetLastError={err})")
        out: list[str] = []
        try:
            array_type = ctypes.POINTER(_CREDSTRUCT) * count.value
            array = ctypes.cast(holder, ctypes.POINTER(array_type)).contents
            for i in range(count.value):
                target = array[i].contents.TargetName or ""
                if target.startswith(self.PREFIX):
                    out.append(target[len(self.PREFIX):])
        finally:
            self._adv.CredFree(holder)
        return out


# --------------------------------------------------------------------------
# DPAPI blob file (user scope)
# --------------------------------------------------------------------------

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class DpapiStore(CredentialStore):
    """DPAPI (user scope) encrypted blob. Readable only by this Windows user."""

    name = "dpapi_file"
    production_safe = True

    def __init__(self, path: Path) -> None:
        if not IS_WINDOWS:
            raise CredentialStoreError("DPAPI is only available on Windows")
        self._crypt = ctypes.windll.crypt32
        self._kernel = ctypes.windll.kernel32
        self.path = Path(path)
        self._keepalive: list[bytes] = []      # prevents premature buffer collection

    @staticmethod
    def _blob(data: bytes) -> tuple[object, _DATA_BLOB]:
        buf = ctypes.create_string_buffer(data, len(data))
        b = _DATA_BLOB()
        b.cbData = len(data)
        b.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        return buf, b

    def _protect(self, data: bytes) -> bytes:
        keep, src = self._blob(data)
        out = _DATA_BLOB()
        if not self._crypt.CryptProtectData(
            ctypes.byref(src), "HextonPoC", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
        ):
            raise CredentialStoreError(f"CryptProtectData failed ({ctypes.GetLastError()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            self._keepalive.append(keep)
            self._kernel.LocalFree(out.pbData)

    def _unprotect(self, data: bytes) -> bytes:
        keep, src = self._blob(data)
        out = _DATA_BLOB()
        if not self._crypt.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
        ):
            raise CredentialStoreError(f"CryptUnprotectData failed ({ctypes.GetLastError()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            self._keepalive.append(keep)
            self._kernel.LocalFree(out.pbData)

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        import json

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return {k: self._unprotect(bytes.fromhex(v)).decode("utf-8")
                for k, v in payload.items()}

    def _save(self, data: dict[str, str]) -> None:
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: self._protect(v.encode("utf-8")).hex() for k, v in data.items()}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def write(self, key: str, secret: str) -> None:
        data = self._load()
        data[key] = secret
        self._save(data)

    def read(self, key: str) -> str | None:
        return self._load().get(key)

    def delete(self, key: str) -> bool:
        data = self._load()
        if key not in data:
            return False
        del data[key]
        self._save(data)
        return True

    def list_keys(self) -> list[str]:
        return list(self._load().keys())


# --------------------------------------------------------------------------
# Plaintext - tests only
# --------------------------------------------------------------------------

class PlaintextStore(CredentialStore):
    """Explicitly not production-safe. Selected only when ``HEXTON_ALLOW_PLAINTEXT=1``."""

    name = "plaintext_test_only"
    production_safe = False

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, str] = {}
        if self.path.exists():
            import json

            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def _flush(self) -> None:
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data), encoding="utf-8")

    def write(self, key: str, secret: str) -> None:
        self._data[key] = secret
        self._flush()

    def read(self, key: str) -> str | None:
        return self._data.get(key)

    def delete(self, key: str) -> bool:
        if key not in self._data:
            return False
        del self._data[key]
        self._flush()
        return True

    def list_keys(self) -> list[str]:
        return list(self._data.keys())


def select_store(data_dir: Path) -> CredentialStore:
    """Choose the strongest available store; refuse plaintext unless explicitly allowed."""
    if IS_WINDOWS:
        try:
            store = CredentialManagerStore()
            store.write("__probe__", "probe")
            if store.read("__probe__") == "probe":
                store.delete("__probe__")
                return store
        except CredentialStoreError:
            pass
        try:
            return DpapiStore(data_dir / "secrets.dpapi.json")
        except CredentialStoreError:
            pass

    if os.environ.get("HEXTON_ALLOW_PLAINTEXT") == "1":
        return PlaintextStore(data_dir / "secrets.plaintext.json")

    raise CredentialStoreError(
        "No Windows-protected credential store is available and plaintext storage is not "
        "enabled. Refusing to continue: storing a refresh token in plaintext would violate "
        "`AC-MVP-011`."
    )
