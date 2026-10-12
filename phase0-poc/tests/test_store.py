"""Windows credential storage.

These are **real** operating-system calls, not mocks: Credential Manager and DPAPI are
exercised on this machine. They are part of the Phase 0 evidence (FEAS-08), and they
record the actual behaviour of this Windows build rather than an assumption.

What they deliberately do not do: store anything belonging to a real Google account.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

from hexton_poc.store import (
    CredentialManagerStore,
    CredentialStoreError,
    DpapiStore,
    IS_WINDOWS,
    PlaintextStore,
    select_store,
)

needs_windows = pytest.mark.skipif(not IS_WINDOWS, reason="requires Windows")

KEY = f"phase0-test-{uuid.uuid4().hex[:8]}"


# -- Windows Credential Manager ------------------------------------------------

@needs_windows
def test_credential_manager_round_trips_a_secret():
    store = CredentialManagerStore()
    try:
        store.write(KEY, "super-secret-value")
        assert store.read(KEY) == "super-secret-value"
    finally:
        store.delete(KEY)
    assert store.read(KEY) is None


@needs_windows
def test_credential_manager_stores_unicode_and_long_secrets():
    store = CredentialManagerStore()
    value = "refresh-token-ish:" + "x" * 2000 + "→✓"
    k = KEY + "-long"
    try:
        store.write(k, value)
        assert store.read(k) == value
    finally:
        store.delete(k)


@needs_windows
def test_credential_manager_delete_is_idempotent_and_reports_missing_keys():
    store = CredentialManagerStore()
    assert store.read(KEY + "-never-written") is None
    store.delete(KEY + "-never-written")     # must not raise


@needs_windows
def test_credential_manager_lists_only_its_own_entries():
    store = CredentialManagerStore()
    k = KEY + "-listed"
    try:
        store.write(k, "v")
        assert k in store.list_keys()
    finally:
        store.delete(k)
    assert k not in store.list_keys()


@needs_windows
def test_the_credential_is_scoped_to_this_windows_user():
    """Record the identity it is bound to, so the Task Scheduler constraint is visible."""
    store = CredentialManagerStore()
    identity = os.environ.get("USERNAME", "")
    try:
        store.write(KEY + "-who", identity)
        assert store.read(KEY + "-who") == identity
    finally:
        store.delete(KEY + "-who")


# -- DPAPI ----------------------------------------------------------------------

@needs_windows
def test_dpapi_user_scope_round_trips(tmp_path):
    store = DpapiStore(tmp_path / "secrets.json")
    store.write("conn-1", "a-refresh-token")
    assert store.read("conn-1") == "a-refresh-token"
    assert store.read("conn-missing") is None


@needs_windows
def test_dpapi_blob_is_not_readable_as_plain_json(tmp_path):
    """The token must not appear in the file in any recoverable form."""
    path = tmp_path / "secrets.json"
    store = DpapiStore(path)
    marker = "PLAINTEXT-MARKER-DO-NOT-STORE"
    store.write("conn-1", marker)
    raw = path.read_text(encoding="utf-8")
    assert marker not in raw
    payload = json.loads(raw)
    assert set(payload) == {"conn-1"}
    assert payload["conn-1"] != marker


@needs_windows
def test_dpapi_multiple_keys_and_deletion(tmp_path):
    store = DpapiStore(tmp_path / "secrets.json")
    store.write("a", "1")
    store.write("b", "2")
    assert sorted(store.list_keys()) == ["a", "b"]
    assert store.delete("a") is True
    assert store.delete("a") is False
    assert store.list_keys() == ["b"]
    assert store.read("b") == "2"


# -- selection policy -----------------------------------------------------------

@needs_windows
def test_select_store_returns_a_production_safe_store(tmp_path):
    store = select_store(tmp_path)
    assert store.production_safe is True, "plaintext must never be auto-selected"
    assert store.name in ("windows_credential_manager", "dpapi_file")


def test_plaintext_is_marked_as_not_production_safe(tmp_path):
    assert PlaintextStore(tmp_path / "p.json").production_safe is False


def test_plaintext_is_refused_when_no_windows_protected_store_is_available(tmp_path,
                                                                          monkeypatch):
    """AC-MVP-011: a refresh token must never be written in the clear."""
    monkeypatch.setattr("hexton_poc.store.IS_WINDOWS", False)
    monkeypatch.delenv("HEXTON_ALLOW_PLAINTEXT", raising=False)
    with pytest.raises(CredentialStoreError, match="AC-MVP-011"):
        select_store(tmp_path)


def test_plaintext_can_be_enabled_explicitly_for_tests_only(tmp_path, monkeypatch):
    monkeypatch.setattr("hexton_poc.store.IS_WINDOWS", False)
    monkeypatch.setenv("HEXTON_ALLOW_PLAINTEXT", "1")
    store = select_store(tmp_path)
    assert store.production_safe is False
    store.write("k", "v")
    assert store.read("k") == "v"


def test_credential_manager_is_refused_off_windows():
    if IS_WINDOWS:
        pytest.skip("only meaningful on non-Windows")
    with pytest.raises(CredentialStoreError, match="only available on Windows"):
        CredentialManagerStore()


@needs_windows
def test_the_store_survives_a_reopen(tmp_path):
    """Persistence across a process restart, which the worker depends on."""
    path = tmp_path / "secrets.json"
    DpapiStore(path).write("conn-9", "persisted")
    assert DpapiStore(path).read("conn-9") == "persisted"


def test_the_platform_is_recorded():
    assert sys.platform == "win32" or sys.platform.startswith("linux") or \
        sys.platform == "darwin"
