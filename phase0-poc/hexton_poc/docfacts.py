"""Machine-readable snapshot of the official documentation facts this PoC relies on.

Why this module exists
----------------------
`06-ai-coding-prd.md` Phase 0 item 3 requires the facts the design depends on to be
*pinned* so that a change in upstream documentation surfaces as a failing test rather
than as a silent assumption.

CRITICAL: nothing in this module is an observation of live API behaviour. Every entry is
either (a) a direct quotation of official documentation retrieved on ``retrieved_utc``,
or (b) explicitly marked as ``CONTRADICTORY`` / ``UNDOCUMENTED``.

Do not "fix" a value here because it looks wrong. Re-fetch the URL, and if the upstream
page changed, that is a finding - record it in `07-feasibility-checklist.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RETRIEVED_UTC = "2026-10-11"

Evidence = Literal["documented", "contradictory", "undocumented"]


@dataclass(frozen=True)
class DocFact:
    id: str
    evidence: Evidence
    claim: str
    url: str
    quote: str = ""


# --------------------------------------------------------------------------
# Verified against the live pages on 2026-10-11.
# --------------------------------------------------------------------------

DOC_FACTS: tuple[DocFact, ...] = (
    DocFact(
        id="DF-01",
        evidence="documented",
        claim="videos.insert can set status.publishAt in the request body.",
        url="https://developers.google.com/youtube/v3/docs/videos/insert",
        quote="status.publishAt",
    ),
    DocFact(
        id="DF-02",
        evidence="documented",
        claim=(
            "videos.insert defaults privacyStatus to public when the property is omitted; "
            "therefore the application must always send it explicitly."
        ),
        url="https://developers.google.com/youtube/v3/docs/videos/insert",
        quote=(
            "status.privacyStatus: If you don't specify a value for this property, the "
            "uploaded video's privacy status defaults to public."
        ),
    ),
    DocFact(
        id="DF-03",
        evidence="documented",
        claim="publishAt may only be set on a private video that has never been published.",
        url="https://developers.google.com/youtube/v3/docs/videos",
        quote=(
            "It can be set only if the privacy status of the video is private. [...] This "
            "property can only be set if the video's privacy status is private and the video "
            "has never been published."
        ),
    ),
    DocFact(
        id="DF-04",
        evidence="documented",
        claim=(
            "A publishAt value in the past publishes the video immediately, with the same "
            "effect as changing privacyStatus from private to public."
        ),
        url="https://developers.google.com/youtube/v3/docs/videos",
        quote=(
            "If your request schedules a video to be published at some time in the past, the "
            "video will be published right away. As such, the effect of setting the "
            "status.publishAt property to a past date and time is the same as of changing the "
            "video's privacyStatus from private to public."
        ),
    ),
    DocFact(
        id="DF-05",
        evidence="documented",
        claim=(
            "videos.update must re-assert status.privacyStatus=private when setting publishAt, "
            "even if the video is already private."
        ),
        url="https://developers.google.com/youtube/v3/docs/videos/insert",
        quote=(
            "If you set this property's value when calling the videos.update method, you must "
            "also set the status.privacyStatus property value to private even if the video is "
            "already private."
        ),
    ),
    DocFact(
        id="DF-06",
        evidence="documented",
        claim=(
            "videos.update DELETES any property that already has a value but is not specified "
            "in the request. A partial update therefore destroys unspecified metadata."
        ),
        url="https://developers.google.com/youtube/v3/docs/videos/update",
        quote=(
            "If you are submitting an update request, and your request does not specify a value "
            "for a property that already has a value, the property's existing value will be "
            "deleted."
        ),
    ),
    DocFact(
        id="DF-07",
        evidence="documented",
        claim="videos.list accepts exactly one of chart, id, myRating - there is no 'mine'.",
        url="https://developers.google.com/youtube/v3/docs/videos/list",
        quote="Filters (specify exactly one of the following parameters): chart, id, myRating",
    ),
    DocFact(
        id="DF-08",
        evidence="documented",
        claim="channels.list(mine=true) returns channels owned by the authenticated user.",
        url="https://developers.google.com/youtube/v3/docs/channels/list",
        quote=(
            "mine boolean [...] Set this parameter's value to true to instruct the API to only "
            "return channels owned by the authenticated user."
        ),
    ),
    DocFact(
        id="DF-09",
        evidence="documented",
        claim=(
            "channels.list(managedByMe=true) is restricted to YouTube Content ID partners via "
            "onBehalfOfContentOwner. It is NOT a general 'channels I manage' filter for "
            "ordinary users - so no documented filter exposes brand-account channels to them."
        ),
        url="https://developers.google.com/youtube/v3/docs/channels/list",
        quote=(
            "managedByMe boolean [...] Note: This parameter is intended exclusively for YouTube "
            "content partners. [...] The user must be authenticated as a CMS account linked to "
            "the specified content owner and onBehalfOfContentOwner must be provided."
        ),
    ),
    DocFact(
        id="DF-10",
        evidence="documented",
        claim="onBehalfOfContentOwner / onBehalfOfContentOwnerChannel are content-partner only.",
        url="https://developers.google.com/youtube/v3/docs/videos/insert",
        quote=(
            "onBehalfOfContentOwnerChannel [...] This parameter is intended exclusively for "
            "YouTube content partners."
        ),
    ),
    DocFact(
        id="DF-11",
        evidence="documented",
        claim="Default quota: 100 search.list, 100 videos.insert and 10,000 units/day for all others.",
        url="https://developers.google.com/youtube/v3/determine_quota_cost",
        quote=(
            "Projects that enable the YouTube Data API have a default quota allocation of 100 "
            "search.list calls, 100 videos.insert calls, and 10,000 units per day combined for "
            "all other endpoints. [...] Daily quotas reset at midnight Pacific Time"
        ),
    ),
    DocFact(
        id="DF-12",
        evidence="documented",
        claim="Unit costs: videos.insert=1 (own bucket), videos.update=50, channels.list=1, "
        "videos.list=1, activities.list=1, playlistItems.list=1.",
        url="https://developers.google.com/youtube/v3/determine_quota_cost",
        quote="Quota impact: A call to this method has a quota cost of 1 unit.",
    ),
    DocFact(
        id="DF-13",
        evidence="documented",
        claim=(
            "Resumable protocol: POST returns 200 + Location; PUT returns 201 on completion and "
            "308 + Range while incomplete; an empty PUT with 'Content-Range: bytes */TOTAL' "
            "queries status; 404 means the session expired and the upload must restart."
        ),
        url="https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol",
        quote=(
            "If you send a request with an expired session URI, the server returns a 404 HTTP "
            "response code (Not Found). In this case, you will need to start a new resumable "
            "upload, obtain a new session URI, and start the upload from the beginning"
        ),
    ),
    DocFact(
        id="DF-14",
        evidence="documented",
        claim=(
            "A resumed upload must start at exactly one byte past the Range header's last byte; "
            "overlapping or skipping bytes causes none of the remaining content to be uploaded."
        ),
        url="https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol",
        quote=(
            "if you try to resume the upload from byte 999999 or lower (overlapping bytes) or "
            "byte 1000001 or higher (skipping bytes), none of the binary content will be uploaded."
        ),
    ),
    DocFact(
        id="DF-15",
        evidence="documented",
        claim="Each resumable session URI has a finite lifetime and eventually expires.",
        url="https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol",
        quote=(
            "Remember that each resumable session URI has a finite lifetime and eventually "
            "expires."
        ),
    ),
    DocFact(
        id="DF-16",
        evidence="documented",
        claim="Title limit 100 characters; description limit 5000 bytes; tags limit 500 characters.",
        url="https://developers.google.com/youtube/v3/docs/videos",
        quote=(
            "The property value has a maximum length of 100 characters [...]. The property value "
            "has a maximum length of 5000 bytes [...]. The property value has a maximum length "
            "of 500 characters."
        ),
    ),
    DocFact(
        id="DF-17",
        evidence="documented",
        claim=(
            "OAuth consent screen in Testing status: authorizations expire seven days from "
            "consent, and a refresh token issued for offline access also expires."
        ),
        url="https://support.google.com/cloud/answer/15549945",
        quote=(
            "Authorizations by a test user will expire seven days from the time of consent. If "
            "your OAuth client requests an offline access type and receives a refresh token, "
            "that token will also expire."
        ),
    ),
    DocFact(
        id="DF-18",
        evidence="documented",
        claim="A Brand Account may authorize the project's OAuth scopes if a test user manages it.",
        url="https://support.google.com/cloud/answer/15549945",
        quote=(
            "A Brand Account may authorize scopes requested by your project's OAuth clients if a "
            "specified test user manages the Brand Account."
        ),
    ),
    DocFact(
        id="DF-19",
        evidence="documented",
        claim=(
            "The video resource exposes no mention entity; YouTube's help describes mentions as "
            "typing @ and choosing a channel from a recommended list."
        ),
        url="https://support.google.com/youtube/answer/9637404",
        quote=(
            "To add a mention when you're creating or editing the video title or description: "
            "Type the '@' symbol fol[lowed by] [...] channel name or handle from a recommended list."
        ),
    ),
    DocFact(
        id="DF-20",
        evidence="documented",
        claim=(
            "Client-initiated revocation requires deleting all Authorized Data within 7 calendar "
            "days."
        ),
        url="https://developers.google.com/youtube/terms/developer-policies",
        quote=(
            "following revocation of consent through this mechanism, you and your API Clients "
            "must delete all Authorized Data that was accessed or stored pursuant to that "
            "consent. That deletion should happen as soon as possible and must take place within "
            "7 calendar days of the revocation."
        ),
    ),
    DocFact(
        id="DF-21",
        evidence="documented",
        claim=(
            "Data deleted after revocation via Google's security settings page, or because a "
            "token can no longer be refreshed, must be removed within 30 calendar days; clients "
            "must periodically reconfirm that tokens are still valid."
        ),
        url="https://developers.google.com/youtube/terms/developer-policies",
        quote=(
            "your API Clients will need to periodically reconfirm that its authorization tokens "
            "are still valid and delete API Data associated with users whose authorization "
            "tokens cannot be refreshed. Based on the requirements defined in section (III.E.4) "
            "regarding stored data, all such deletions should happen as soon as possible and "
            "must take place within 30 calendar days of that revocation."
        ),
    ),
    DocFact(
        id="DF-22",
        evidence="documented",
        claim="YouTube may disable or curtail access to an API Project inactive for 90 consecutive days.",
        url="https://developers.google.com/youtube/terms/developer-policies",
        quote=(
            "YouTube reserves the right to disable or curtail your access to, or use of, "
            "specific YouTube API Services if your API Project has been inactive for 90 "
            "consecutive days."
        ),
    ),
    DocFact(
        id="DF-23",
        evidence="documented",
        claim=(
            "The privacy policy must link to Google's security settings page so users can revoke "
            "access, and must give a contact route for questions or complaints."
        ),
        url="https://developers.google.com/youtube/terms/developer-policies",
        quote=(
            "users can revoke that API Client's access to their data via the Google security "
            "settings page at https://security.google.com/settings/security/permissions, and if "
            "the API Client uses Authorized Data, explain how users can contact the API Client "
            "owner or developer with questions or complaints"
        ),
    ),
    DocFact(
        id="DF-24",
        evidence="documented",
        claim="API Clients must not store user data indefinitely.",
        url="https://developers.google.com/youtube/terms/developer-policies",
        quote=(
            "Don't store user data indefinitely, and provide a clear, straightforward process "
            "for them to delete data in your possession."
        ),
    ),
    # -- The two facts that CANNOT be settled from documentation -----------------
    DocFact(
        id="DF-25",
        evidence="contradictory",
        claim=(
            "Whether an unverified API project forces uploads to private. Google's own pages "
            "state both behaviours."
        ),
        url="https://developers.google.com/youtube/v3/docs/videos",
        quote=(
            "videos resource page summary: 'Unverified API projects uploading videos will have "
            "them set to private by default, and must undergo an audit to make them public.'  ||| "
            "videos.insert status.privacyStatus: 'Videos uploaded from unverified API projects "
            "are not restricted to private viewing mode.'"
        ),
    ),
    DocFact(
        id="DF-26",
        evidence="undocumented",
        claim="The minimum accepted publishAt lead time, and whether processing must finish first.",
        url="https://developers.google.com/youtube/v3/docs/videos",
        quote="No minimum offset is stated anywhere in the resource or method documentation.",
    ),
    DocFact(
        id="DF-27",
        evidence="undocumented",
        claim="Whether videos.insert can address a brand-account channel rather than the owner's primary channel.",
        url="https://developers.google.com/youtube/v3/docs/videos/insert",
        quote=(
            "No channel selector exists for ordinary (non content-partner) callers; whether "
            "channels.list(mine=true) surfaces brand-account channels is not stated."
        ),
    ),
    DocFact(
        id="DF-28",
        evidence="undocumented",
        claim="The precise lifetime of a resumable session URI, and the latency of the activity feed.",
        url="https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol",
        quote='Only "a finite lifetime" is stated; no duration, and activities.list states no latency.',
    ),
)


def facts_by_evidence(evidence: Evidence) -> list[DocFact]:
    return [f for f in DOC_FACTS if f.evidence == evidence]


def get(fact_id: str) -> DocFact:
    for f in DOC_FACTS:
        if f.id == fact_id:
            return f
    raise KeyError(fact_id)
