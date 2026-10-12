"""Configuration safety: loopback binding, connector selection, credential stores.

These are the refusals that have to happen at startup rather than in a deployment
note: binding a token-bearing local API beyond loopback, or wiring a test connector
into an installed build, are both mistakes that are invisible until they are live.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hexton.config import Settings, default_data_dir
from hexton.connectors.fake import FakeConnector, FakeState
from hexton.connectors.registry import (
    BuildProfile,
    ConnectorSelectionError,
    select_connector,
)
from hexton.connectors.youtube import YouTubeConnector
from hexton.credentials.store import (
    IS_WINDOWS,
    CredentialStoreError,
    DpapiFileStore,
    InMemoryCredentialStore,
    PlaintextStore,
    select_credential_store,
)
from pydantic import ValidationError

# ----------------------------------------------------------------------#
# Binding
# ----------------------------------------------------------------------#


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_only_loopback_addresses_are_accepted(host: str) -> None:
    assert Settings(host=host).host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_binding_beyond_loopback_is_refused_at_configuration_time(host: str) -> None:
    """This API can mint and read authorization state, so the refusal is a validator."""
    with pytest.raises(ValidationError) as excinfo:
        Settings(host=host)
    assert "loopback only" in str(excinfo.value)


def test_an_unknown_connector_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(connector_kind="mock_youtube")
    assert "connector_kind" in str(excinfo.value)


def test_the_data_directory_is_per_user_not_shared(tmp_path: Path) -> None:
    path = default_data_dir()
    assert path.name == "Hexton"
    assert "ProgramData" not in str(path)  # never machine-wide


def test_directories_are_created_under_the_data_root(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_directories()
    assert settings.database_path.parent == settings.data_dir
    assert settings.media_dir.is_dir()
    assert settings.log_dir.is_dir()


def test_environment_overrides_the_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEXTON_PORT", "9999")
    monkeypatch.setenv("HEXTON_DATA_DIR", str(tmp_path / "elsewhere"))
    settings = Settings()
    assert settings.port == 9999
    assert settings.data_dir == tmp_path / "elsewhere"


# ----------------------------------------------------------------------#
# Connector selection
# ----------------------------------------------------------------------#


def test_the_fake_connector_is_refused_in_a_packaged_build(tmp_path: Path) -> None:
    """A mis-set environment variable must not put a fake provider behind a real
    schedule in an installed application."""
    settings = Settings(data_dir=tmp_path, connector_kind="fake")
    with pytest.raises(ConnectorSelectionError) as excinfo:
        select_connector(settings, profile=BuildProfile.PACKAGED)
    assert "refused in a packaged build" in str(excinfo.value)


def test_the_fake_connector_is_available_in_development(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, connector_kind="fake")
    connector = select_connector(settings, profile=BuildProfile.DEVELOPMENT)
    assert isinstance(connector, FakeConnector)
    assert connector.provider == "fake"


# ----------------------------------------------------------------------#
# Build profile (F-03): the gates above must actually be reachable
# ----------------------------------------------------------------------#


def test_the_source_checkout_marker_detects_this_repository() -> None:
    from hexton.cli import _in_source_checkout

    assert _in_source_checkout() is True


def test_the_profile_falls_back_to_packaged_outside_a_checkout(tmp_path, monkeypatch) -> None:
    """F-03: the packaged profile used to be unreachable.

    It was decided solely by a ``data_dir/packaged`` marker that no code ever writes,
    so ``BuildProfile.PACKAGED`` could never be selected — which made the fake-connector
    refusal, the plaintext-store refusal and the ``attach-test-credential`` gate all
    dead code. A default run therefore looked like a working application that reported
    published figures for uploads that were only ever simulated.
    """
    from hexton import cli

    settings = Settings(data_dir=tmp_path)

    monkeypatch.setattr(cli, "_in_source_checkout", lambda: True)
    assert cli._profile(settings) is BuildProfile.DEVELOPMENT

    # An installer marker, if one is ever written, still wins over the checkout signal.
    (settings.data_dir / "packaged").touch()
    assert cli._profile(settings) is BuildProfile.PACKAGED
    (settings.data_dir / "packaged").unlink()

    monkeypatch.setattr(cli, "_in_source_checkout", lambda: False)
    assert cli._profile(settings) is BuildProfile.PACKAGED


def test_a_packaged_build_refuses_to_start_with_the_fake_connector(tmp_path, monkeypatch) -> None:
    """The refusal exists; this pins that it is reachable without an installer."""
    from hexton import cli
    from hexton.bootstrap import build_context

    settings = Settings(data_dir=tmp_path, connector_kind="fake")
    monkeypatch.setattr(cli, "_in_source_checkout", lambda: False)
    with pytest.raises(ConnectorSelectionError) as excinfo:
        build_context(settings, profile=cli._profile(settings))
    assert "refused in a packaged build" in str(excinfo.value)


def test_the_stub_youtube_connector_does_not_claim_to_be_ready() -> None:
    """Wiring ``connector_kind=youtube`` must not read as working until FEAS-01..05 pass."""
    assert YouTubeConnector().reaches_platform is False
    assert FakeConnector().reaches_platform is False
    assert FakeConnector().provider == "fake"


def test_the_real_connector_is_only_selected_explicitly(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, connector_kind="youtube")
    assert isinstance(select_connector(settings), YouTubeConnector)


def test_the_real_connector_is_a_stub_until_the_gate_passes(tmp_path: Path) -> None:
    """FEAS-01..FEAS-05 must pass before this is wired; nothing here pretends."""
    from hexton.connectors.base import ConnectorError

    connector = YouTubeConnector()
    with pytest.raises(ConnectorError) as excinfo:
        connector.start_resumable_upload(
            "token",
            metadata={"status": {"privacyStatus": "private"}},
            total_bytes=1,
            content_type="video/mp4",
            endpoint="https://example.invalid",
        )
    assert "FEAS-01..FEAS-05" in str(excinfo.value)


def test_a_fake_state_is_carried_through_selection(tmp_path: Path) -> None:
    state = FakeState()
    settings = Settings(data_dir=tmp_path, connector_kind="fake")
    connector = select_connector(settings, fake_state=state)
    assert connector.state is state


# ----------------------------------------------------------------------#
# Credential stores
# ----------------------------------------------------------------------#


def test_plaintext_is_refused_by_default(tmp_path: Path) -> None:
    with pytest.raises(CredentialStoreError) as excinfo:
        select_credential_store("plaintext", data_dir=tmp_path, allow_plaintext=False)
    assert "HEXTON_ALLOW_PLAINTEXT" in str(excinfo.value)


def test_plaintext_can_be_enabled_explicitly_for_tests(tmp_path: Path, monkeypatch) -> None:
    """Both switches are required; neither alone is enough to reach the clear text."""
    monkeypatch.setenv("HEXTON_ALLOW_PLAINTEXT", "1")
    store = select_credential_store("plaintext", data_dir=tmp_path, allow_plaintext=True)
    assert isinstance(store, PlaintextStore)


def test_the_flag_alone_is_not_enough_without_the_environment_variable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("HEXTON_ALLOW_PLAINTEXT", raising=False)
    with pytest.raises(CredentialStoreError):
        select_credential_store("plaintext", data_dir=tmp_path, allow_plaintext=True)


def test_an_in_memory_store_is_only_reachable_by_explicit_request(tmp_path: Path) -> None:
    """It is a test double; ``auto`` on Windows must never resolve to it."""
    assert InMemoryCredentialStore.production_safe is False
    selected = select_credential_store("in_memory", data_dir=tmp_path)
    assert isinstance(selected, InMemoryCredentialStore)
    # The default path on this machine picks a Windows-protected store instead.
    default = select_credential_store("auto", data_dir=tmp_path)
    assert not isinstance(default, InMemoryCredentialStore)
    assert not isinstance(default, PlaintextStore)


@pytest.mark.skipif(not IS_WINDOWS, reason="both named stores are Windows-only")
def test_the_two_store_kinds_config_documents_are_actually_honoured(tmp_path: Path) -> None:
    """F-24: ``config.py`` documents ``windows_credential_manager`` and ``dpapi_file``.

    ``select_credential_store`` understood only ``in_memory`` and ``plaintext`` and then
    fell through to the ``auto`` ladder, so *both* documented names silently resolved to
    Credential Manager. An operator who set ``dpapi_file`` to get a file they could see,
    audit and delete got a Credential Manager entry they could not, with no error and no
    log line — and the ``dpapi_file`` store, which is the only other ``production_safe``
    option, was unreachable by configuration.
    """
    assert select_credential_store("windows_credential_manager", data_dir=tmp_path).name == (
        "windows_credential_manager"
    )

    chosen = select_credential_store("dpapi_file", data_dir=tmp_path)
    assert chosen.name == "dpapi_file"
    # An explicit choice is honoured, not downgraded to whatever else was available.
    assert isinstance(chosen, DpapiFileStore)


def test_an_unrecognised_store_kind_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """A typo in an environment variable used to become "Credential Manager, probably"."""
    with pytest.raises(CredentialStoreError) as excinfo:
        select_credential_store("cred_manager", data_dir=tmp_path)
    message = str(excinfo.value)
    assert "cred_manager" in message
    assert "auto" in message and "dpapi_file" in message, "name what *is* accepted"


def test_dpapi_file_without_a_data_directory_refuses(tmp_path: Path) -> None:
    """Refuse loudly: silently falling back to another store is the F-24 bug itself."""
    with pytest.raises(CredentialStoreError) as excinfo:
        select_credential_store("dpapi_file", data_dir=None)
    assert "data_dir" in str(excinfo.value) or "data directory" in str(excinfo.value)


def test_an_in_memory_store_round_trips_and_forgets() -> None:
    store = InMemoryCredentialStore()
    assert store.read("missing") is None
    store.write("conn:abc:access", "secret")
    assert store.read("conn:abc:access") == "secret"
    assert "conn:abc:access" in store.list_keys()
    assert store.delete("conn:abc:access") is True
    assert store.delete("conn:abc:access") is False
    assert store.read("conn:abc:access") is None


def test_a_plaintext_file_is_readable_back(tmp_path: Path) -> None:
    store = PlaintextStore(tmp_path / "creds.json")
    store.write("k", "v")
    assert PlaintextStore(tmp_path / "creds.json").read("k") == "v"
    assert store.delete("k") is True
    assert store.delete("k") is False


def test_the_in_memory_store_declares_its_own_safety() -> None:
    store = InMemoryCredentialStore()
    assert store.production_safe is False
    assert getattr(PlaintextStore, "production_safe", True) is False
