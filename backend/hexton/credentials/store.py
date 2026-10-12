"""Windows credential storage.

Priority order (``02-technical-prd.md`` §10.2, verified in Phase 0):

1. :class:`CredentialManagerStore` — Windows Credential Manager via ``advapi32``
   CredMan, reached with ``ctypes`` so there is no third-party dependency in the
   secret path. Verified round-tripping on this machine (FEAS-08 step 1).
2. :class:`DpapiFileStore` — a DPAPI (user scope) protected blob on disk.
3. :class:`PlaintextStore` — **test only**. Production refuses to select it.

None of these can be read by a Windows Service or by a Task Scheduler principal
running as another identity. That is the constraint FEAS-08 exists to demonstrate, and
it is why this application does not ship a Windows Service.

Two ctypes details are load-bearing and were found the hard way in Phase 0:

* The ``CredReadW``/``CredEnumerateW`` out-parameter is declared ``c_void_p`` and
  passed with ``byref()``. Declaring it ``POINTER(POINTER(X))`` makes ctypes pass the
  pointer *value* (initially NULL) instead of the address of the holder, and the
  process writes through NULL.
* ``CredEnumerateW`` returns an **array of pointers** to credential structures, not an
  array of structures. Traversing it as an array of structures reads pointer values as
  if they were struct fields and crashes the interpreter.
"""

from __future__ import annotations

import ctypes
import json
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

#: Every entry this application writes is namespaced, so an operator inspecting
#: Credential Manager can see exactly which ones are ours and remove them as a set.
CREDENTIAL_PREFIX = "Hexton/"


class CredentialStoreError(RuntimeError):
    """Raised when a credential cannot be stored or retrieved."""


class CredentialStore(ABC):
    """A named, secret-bearing store keyed by connection public ID."""

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
# Windows Credential Manager
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

    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise CredentialStoreError("Credential Manager is only available on Windows")
        self._adv = ctypes.windll.advapi32
        self._adv.CredWriteW.argtypes = [ctypes.POINTER(_CREDSTRUCT), wintypes.DWORD]
        self._adv.CredWriteW.restype = wintypes.BOOL
        self._adv.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._adv.CredReadW.restype = wintypes.BOOL
        self._adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._adv.CredDeleteW.restype = wintypes.BOOL
        self._adv.CredEnumerateW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._adv.CredEnumerateW.restype = wintypes.BOOL
        self._adv.CredFree.argtypes = [ctypes.c_void_p]
        self._adv.CredFree.restype = wintypes.DWORD

    def _target(self, key: str) -> str:
        return f"{CREDENTIAL_PREFIX}{key}"

    def write(self, key: str, secret: str) -> None:
        raw = secret.encode("utf-8")
        blob = ctypes.create_string_buffer(raw, len(raw))
        cred = _CREDSTRUCT()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = self._target(key)
        cred.Comment = "Hexton scheduler credential"
        cred.CredentialBlobSize = len(raw)
        cred.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = "hexton"
        if not self._adv.CredWriteW(ctypes.byref(cred), 0):
            raise CredentialStoreError(
                f"CredWriteW failed for {self._target(key)} (GetLastError={ctypes.GetLastError()})"
            )

    def read(self, key: str) -> str | None:
        holder = ctypes.POINTER(_CREDSTRUCT)()
        if not self._adv.CredReadW(self._target(key), CRED_TYPE_GENERIC, 0, ctypes.byref(holder)):
            if ctypes.GetLastError() == ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError(
                f"CredReadW failed for {self._target(key)} (GetLastError={ctypes.GetLastError()})"
            )
        try:
            size = int(holder.contents.CredentialBlobSize)
            if size <= 0:
                return ""
            raw = ctypes.string_at(holder.contents.CredentialBlob, size)
            return raw.decode("utf-8")
        finally:
            self._adv.CredFree(holder)

    def delete(self, key: str) -> bool:
        ok = self._adv.CredDeleteW(self._target(key), CRED_TYPE_GENERIC, 0)
        if not ok and ctypes.GetLastError() != ERROR_NOT_FOUND:
            raise CredentialStoreError(
                f"CredDeleteW failed for {self._target(key)} (GetLastError={ctypes.GetLastError()})"
            )
        return bool(ok)

    def list_keys(self) -> list[str]:
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
                if target.startswith(CREDENTIAL_PREFIX):
                    out.append(target[len(CREDENTIAL_PREFIX) :])
        finally:
            self._adv.CredFree(holder)
        return out


# --------------------------------------------------------------------------
# DPAPI blob file (user scope)
# --------------------------------------------------------------------------


class DpapiFileStore(CredentialStore):
    """A DPAPI-protected JSON blob, scoped to the current Windows user.

    The bytes, not the file mode, are the control. ``CryptProtectData`` with
    ``CRYPTPROTECT_LOCAL_MACHINE`` unset binds every stored secret to the Windows
    account that wrote it, so a copy of the file is inert on any other machine or for
    any other account — including an administrator's. The ``os.chmod(..., 0o600)`` calls
    are kept because they are correct on POSIX and harmless here, but on Windows they
    only toggle the read-only bit and do not produce a user-only ACL (``F-25``).
    """

    name = "dpapi_file"
    production_safe = True

    def __init__(self, path: Path | str) -> None:
        if not IS_WINDOWS:
            raise CredentialStoreError("DPAPI is only available on Windows")
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            os.chmod(self._path, 0o600)

    # -- DPAPI ---------------------------------------------------------------

    @staticmethod
    def _protect(raw: bytes) -> bytes:
        import ctypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte)),
            ]

        in_buf = ctypes.create_string_buffer(raw, len(raw))
        in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "Hexton credential",
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise CredentialStoreError(f"CryptProtectData failed ({ctypes.GetLastError()})")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    @staticmethod
    def _unprotect(raw: bytes) -> bytes:
        import ctypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte)),
            ]

        in_buf = ctypes.create_string_buffer(raw, len(raw))
        in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise CredentialStoreError(f"CryptUnprotectData failed ({ctypes.GetLastError()})")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    # -- store ---------------------------------------------------------------

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        raw = self._path.read_bytes()
        if not raw:
            return {}
        try:
            return json.loads(self._unprotect(raw).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CredentialStoreError(
                f"The credential file {self._path} is unreadable: {exc}"
            ) from exc

    def _save(self, data: dict[str, str]) -> None:
        blob = self._protect(json.dumps(data).encode("utf-8"))
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

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
        return sorted(self._load())


# --------------------------------------------------------------------------
# Test-only plaintext store, and the selection policy
# --------------------------------------------------------------------------


class InMemoryCredentialStore(CredentialStore):
    """Non-persistent store for tests. Never selected in production wiring."""

    name = "in_memory"
    production_safe = False

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def write(self, key: str, secret: str) -> None:
        self._data[key] = secret

    def read(self, key: str) -> str | None:
        return self._data.get(key)

    def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    def list_keys(self) -> list[str]:
        return sorted(self._data)


def select_credential_store(
    kind: str = "auto",
    *,
    data_dir: Path | None = None,
    allow_plaintext: bool = False,
) -> CredentialStore:
    """Choose a store. This is the only place the choice is made.

    ``kind`` comes from process configuration, not from a runtime flag threaded
    through business logic, so production code paths cannot accidentally reach a
    weaker store.

    An *explicit* ``kind`` is honoured or refused, never downgraded. This function used
    to understand only ``in_memory`` and ``plaintext`` and then fall through to the
    ``auto`` ladder — so both names ``config.py`` documents,
    ``windows_credential_manager`` and ``dpapi_file``, silently produced Credential
    Manager. An operator who asked for a file they could see, audit and delete got a
    Credential Manager entry they could not, with no error and no log line, and the only
    other ``production_safe`` store was unreachable by configuration at all. Silently
    answering a different question than the one that was asked is the failure mode this
    function exists to prevent, so an unrecognised ``kind`` now refuses outright.
    """
    if kind == "in_memory":
        return InMemoryCredentialStore()

    if kind == "plaintext":
        if not allow_plaintext or os.environ.get("HEXTON_ALLOW_PLAINTEXT") != "1":
            raise CredentialStoreError(
                "The plaintext credential store is refused. A refresh token is an "
                "account takeover primitive; storing it in the clear fails AC-MVP-011. "
                "Both the configuration flag and the HEXTON_ALLOW_PLAINTEXT=1 "
                "environment variable must be set, and the latter is never set in a "
                "packaged build."
            )
        return PlaintextStore((data_dir or Path(".")) / "credentials.json")

    if kind == "dpapi_file":
        if data_dir is None:
            raise CredentialStoreError(
                "The dpapi_file credential store was requested but no data directory "
                "was configured, so there is nowhere to put the file. Refusing rather "
                "than falling back to a different store than the one that was asked "
                "for."
            )
        return DpapiFileStore(data_dir / "credentials.dpapi")

    if kind == "windows_credential_manager":
        # No fallback: if the Credential Manager is unavailable this raises, which is
        # the point. Reaching for DPAPI here would repeat the F-24 substitution.
        return CredentialManagerStore()

    if kind != "auto":
        raise CredentialStoreError(
            f"Unknown credential store kind {kind!r}. Accepted values are 'auto' "
            "(the Windows Credential Manager, then a DPAPI file, then refuse), "
            "'windows_credential_manager', 'dpapi_file', 'in_memory' and 'plaintext'."
        )

    if IS_WINDOWS:
        try:
            return CredentialManagerStore()
        except CredentialStoreError:
            if data_dir is None:
                raise
            return DpapiFileStore(data_dir / "credentials.dpapi")

    if not allow_plaintext or os.environ.get("HEXTON_ALLOW_PLAINTEXT") != "1":
        raise CredentialStoreError(
            "No Windows-protected credential store is available on this platform, and "
            "plaintext is refused (AC-MVP-011)."
        )
    return PlaintextStore((data_dir or Path(".")) / "credentials.json")


class PlaintextStore(CredentialStore):
    """Development fallback. Marked unsafe so it cannot be selected by accident."""

    name = "plaintext"
    production_safe = False

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def write(self, key: str, secret: str) -> None:
        data = self._load()
        data[key] = secret
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read(self, key: str) -> str | None:
        return self._load().get(key)

    def delete(self, key: str) -> bool:
        data = self._load()
        if key not in data:
            return False
        del data[key]
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True

    def list_keys(self) -> list[str]:
        return sorted(self._load())
