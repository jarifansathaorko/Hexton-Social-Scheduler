"""Channel identity verification and the confirmation gate.

The credential is the unit of authorization. ``videos.insert`` publishes to whatever
channel the authorizing credential resolves to; there is no documented parameter that
redirects an upload to an arbitrary channel for an ordinary user (``docfacts.DF-10``).

Therefore: a channel ID arriving from the browser is **never** authority. The API is the
only source of the destination channel, and the operator must confirm it before the
connection can be targeted by a job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

CHANNELS_LIST = "https://www.googleapis.com/youtube/v3/channels"

# Recorded as a documented fact, not an observation (DF-09): `managedByMe` is a
# content-partner filter, so there is NO documented way for an ordinary user to list
# channels they manage but do not own.
MANAGED_BY_ME_IS_PARTNER_ONLY = True


@dataclass(frozen=True)
class ChannelIdentity:
    channel_id: str
    title: str
    handle: str | None = None
    custom_url: str | None = None
    thumbnails: dict = field(default_factory=dict)
    subscriber_count: str | None = None
    # Every YouTube-sourced value is Authorized Data and needs a fetched-at stamp so the
    # retention sweep can refresh or purge it (COMP-05).
    fetched_at_utc: str = ""

    @property
    def display(self) -> str:
        return f"{self.title} ({self.channel_id})"


@dataclass(frozen=True)
class Connection:
    """A confirmed credential -> channel binding."""

    connection_id: str
    provider_account_label: str
    identity: ChannelIdentity
    scopes: tuple[str, ...]
    confirmed_by_operator: bool = False
    state: str = "active"          # active | disabled | revoked
    last_verified_at_utc: str = ""

    def may_target(self) -> tuple[bool, str]:
        if self.state == "revoked":
            return False, "This connection has been revoked; its credential was deleted."
        if self.state == "disabled":
            return False, "This connection is disabled locally. Re-enable it to publish."
        if not self.confirmed_by_operator:
            return False, ("The authorized channel has not been confirmed by the operator; "
                           "uploads are blocked until it is.")
        return True, ""


class IdentityError(RuntimeError):
    pass


def fetch_owned_channels(access_token: str, *, client: httpx.Client | None = None) -> list[ChannelIdentity]:
    """`channels.list(mine=true)` - the documented, 1-unit call for 'my channels'.

    Note the deliberate absence of any channel selector: the set returned here *is* the
    set of destinations this credential may address.
    """
    owns = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        resp = client.get(
            CHANNELS_LIST,
            params={
                "part": "snippet,statistics,contentDetails",
                "mine": "true",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
    finally:
        if owns:
            client.close()

    if resp.status_code == 401:
        raise IdentityError("Token is invalid or expired; reauthorization is required.")
    if resp.status_code == 403:
        raise IdentityError(
            f"Forbidden (HTTP 403): {resp.text[:300]}. The token does not carry the "
            "required YouTube scope, or the account has no YouTube channel."
        )
    if resp.status_code != 200:
        raise IdentityError(f"channels.list failed with HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    out: list[ChannelIdentity] = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {}) or {}
        stats = item.get("statistics", {}) or {}
        out.append(
            ChannelIdentity(
                channel_id=item.get("id", ""),
                title=snippet.get("title", ""),
                handle=snippet.get("handle"),
                custom_url=snippet.get("customUrl"),
                thumbnails=snippet.get("thumbnails", {}) or {},
                subscriber_count=stats.get("subscriberCount"),
            )
        )
    return out


def verify_connection(
    connection: Connection,
    access_token: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[bool, str]:
    """Re-resolve the channel for this credential and compare with what is stored.

    Called before the first production upload on a connection and whenever
    ``last_verified_at_utc`` is older than the configured interval. A mismatch blocks the
    upload and surfaces `JOB-BLOCKED` with an explicit reason.
    """
    channels = fetch_owned_channels(access_token, client=client)
    ids = {c.channel_id for c in channels}
    if connection.identity.channel_id in ids:
        return True, ""
    return False, (
        f"The authorized identity no longer resolves to {connection.identity.channel_id}. "
        f"It now returns: {sorted(ids) or 'no channels'}. The upload is blocked rather than "
        "redirected - a channel ID supplied by the UI is never authority."
    )


def build_confirmation_record(
    channels: list[ChannelIdentity],
    chosen_channel_id: str,
    *,
    operator_confirmed: bool,
) -> Connection:
    """Bind a credential to the channel the API returned, with explicit operator consent."""
    match = next((c for c in channels if c.channel_id == chosen_channel_id), None)
    if match is None:
        raise IdentityError(
            f"Channel {chosen_channel_id!r} was not returned by channels.list(mine=true) for "
            "this credential, so it cannot be selected. Only channels the API reports may be "
            "targeted."
        )
    if len(channels) > 1 and not operator_confirmed:
        raise IdentityError(
            f"This credential resolves to {len(channels)} channels; the operator must confirm "
            "which one is the destination before uploads are enabled."
        )
    return Connection(
        connection_id=f"conn_{match.channel_id}",
        provider_account_label=match.title,
        identity=match,
        scopes=(),
        confirmed_by_operator=operator_confirmed,
    )


def brand_account_note() -> str:
    """Truthful fallback wording, pending the FEAS-01 verdict."""
    return (
        "One connected Google identity publishes to exactly one channel: the one the API "
        "returns for that credential. A brand-account channel can be connected by authorizing "
        "with an identity that manages it, but whether that identity can publish to it must be "
        "confirmed by test before this application claims it (FEAS-01)."
    )
