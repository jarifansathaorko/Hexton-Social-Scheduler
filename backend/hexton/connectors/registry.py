"""Connector selection. The one place a connector is chosen.

The rule this module enforces: **a test connector cannot be reached from production
wiring.** Selection happens once, at process startup, from configuration — not from a
flag threaded through business logic — and the :class:`~hexton.config.BuildProfile`
gate refuses the fake connector outright in a packaged build, so even a mis-set
environment variable cannot put a fake provider behind a real schedule.
"""

from __future__ import annotations

from enum import Enum

from hexton.config import Settings
from hexton.connectors.base import PlatformConnector
from hexton.connectors.fake import FakeConnector, FakeState
from hexton.connectors.fake_sheets import FakeSheetsConnector, SheetState
from hexton.connectors.sheets import SheetConnector
from hexton.connectors.youtube import YouTubeConnector


class BuildProfile(str, Enum):
    """How this process was launched.

    ``DEVELOPMENT`` is used by ``hexton dev`` and by tests. ``PACKAGED`` is used by
    the installed application and is the only profile that may reach a live provider.
    """

    DEVELOPMENT = "development"
    PACKAGED = "packaged"


class ConnectorSelectionError(RuntimeError):
    """Raised when configuration asks for something the build profile forbids."""


def select_connector(
    settings: Settings,
    *,
    profile: BuildProfile = BuildProfile.DEVELOPMENT,
    fake_state: FakeState | None = None,
) -> PlatformConnector:
    """Build the connector this process will use. Called once, at startup."""
    kind = settings.connector_kind

    if kind == "fake":
        if profile is BuildProfile.PACKAGED:
            raise ConnectorSelectionError(
                "connector_kind='fake' is refused in a packaged build. The fake "
                "connector performs no real work and exists only for automated tests "
                "and first-run development; wiring it into an installed application "
                "would silently accept schedules it can never fulfil."
            )
        return FakeConnector(fake_state)

    if kind == "youtube":
        return YouTubeConnector(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )

    raise ConnectorSelectionError(f"Unknown connector kind: {kind!r}")


# --------------------------------------------------------------------------
# Sheets
# --------------------------------------------------------------------------
#
# The Sheets connector is *not* selected at startup the way the platform connector is,
# because its credentials live per-connection in the database and are only known once the
# operator has connected Google. What is selected at startup is the *factory* — the same
# profile gate applies, and a packaged build still cannot reach a fake sheet.


class SheetsSelectionError(RuntimeError):
    """Raised when Sheets configuration asks for something the build profile forbids."""


#: A credential, already resolved from the store, as the live connector needs it.
CredentialLike = object


def select_sheets_connector_factory(
    settings: Settings,
    *,
    profile: BuildProfile = BuildProfile.DEVELOPMENT,
    fake_state: SheetState | None = None,
):
    """Return a callable ``(credentials) -> SheetConnector``.

    ``credentials`` is the OAuth credential object for the Sheets connection. The fake
    factory ignores it entirely; the live factory hands it to the Google client.
    """
    kind = settings.connector_kind

    if kind == "fake":
        if profile is BuildProfile.PACKAGED:
            raise SheetsSelectionError(
                "connector_kind='fake' is refused in a packaged build. The fake sheet "
                "performs no real writes and exists only for automated tests and "
                "first-run development; wiring it into an installed application would "
                "silently accept report configuration it can never fulfil."
            )
        shared = FakeSheetsConnector(fake_state)

        def _fake(_credentials: CredentialLike = None) -> SheetConnector:
            return shared

        return _fake

    def _live(credentials: CredentialLike) -> SheetConnector:
        from hexton.connectors.sheets_live import LiveSheetsConnector

        return LiveSheetsConnector(credentials)

    return _live
