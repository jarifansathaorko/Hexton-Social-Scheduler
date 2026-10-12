"""Reconciliation ladder for interrupted or ambiguous uploads.

``02-technical-prd.md`` §6.2. The problem: a `videos.insert` may be accepted by YouTube
while the client never receives the response, and ``videos.list`` offers no "my recent
uploads" filter (``docfacts.DF-07``) to ask directly. So resolution proceeds in a fixed
order and stops at the first conclusive answer.

THE RULE THIS MODULE EXISTS TO ENFORCE: **never issue a second `videos.insert` for a job
whose previous insert may have succeeded.** Automatic retry is permitted only when the
previous attempt provably never reached the point of acceptance.

Evidence strength matters here and is encoded explicitly:

* **R1 (session probe) is conclusive.** The session URI belongs to *this* job, so a
  ``201`` from it is proof, in either direction.
* **R2 and R3 are evidence, not proof.** A video appearing on the channel around the
  right time may well have been an unrelated manual Studio upload. Those steps therefore
  produce a *candidate for operator confirmation*, never an automatic adoption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import httpx

from .resumable import UploadError, parse_range_header

ACTIVITIES_LIST = "https://www.googleapis.com/youtube/v3/activities"
CHANNELS_LIST = "https://www.googleapis.com/youtube/v3/channels"
PLAYLIST_ITEMS_LIST = "https://www.googleapis.com/youtube/v3/playlistItems"
VIDEOS_LIST = "https://www.googleapis.com/youtube/v3/videos"


class Resolution(str, Enum):
    RESOLVED = "resolved"                    # a definite outcome for THIS job
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    INCONCLUSIVE_RETRY_LATER = "inconclusive_retry_later"   # session alive: resume it


@dataclass(frozen=True)
class CandidateVideo:
    video_id: str
    title: str
    published_at: str
    source: str          # "session" | "activities" | "uploads_playlist"
    evidence: str
    conclusive: bool     # True only for a session-probe result


@dataclass
class ReconciliationResult:
    resolution: Resolution
    step: str                        # the step that produced the conclusion
    candidate: CandidateVideo | None = None
    notes: list[str] = field(default_factory=list)
    steps_run: list[str] = field(default_factory=list)

    @property
    def conclusive(self) -> bool:
        return self.candidate is not None and self.candidate.conclusive


class Reconciler:
    """R1 -> R4. Requires a live token and an ``httpx`` client."""

    def __init__(self, *, access_token_provider, client: httpx.Client,
                 uploader=None, base_url: str = "https://www.googleapis.com/youtube/v3") -> None:
        self._token = access_token_provider
        self._client = client
        self._uploader = uploader      # optional ResumableUploader, used for R1
        self._base = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def reconcile(self, session, *, channel_id: str,
                  attempt_started_at_iso: str | None = None) -> ReconciliationResult:
        notes: list[str] = []
        steps: list[str] = []

        # ---- R1: probe the persisted resumable session ---------------------------
        # The session URI is bound to this job, so its answer is conclusive either way.
        if session.session_uri and self._uploader is not None:
            steps.append("R1")
            try:
                kind, offset, body = self._uploader.probe(session)
            except UploadError as exc:
                notes.append(f"R1 probe raised: {exc}")
                kind, offset, body = "error", session.bytes_uploaded, None
            except httpx.HTTPError as exc:
                notes.append(f"R1 probe could not reach the server: {type(exc).__name__}")
                kind, offset, body = "unreachable", session.bytes_uploaded, None

            if kind == "completed" and body and body.get("id"):
                vid = body["id"]
                snip = body.get("snippet") or {}
                notes.append(f"R1: the session probe returned HTTP 201 and video id {vid}.")
                return ReconciliationResult(
                    Resolution.RESOLVED, "R1",
                    CandidateVideo(vid, snip.get("title", ""), snip.get("publishedAt", ""),
                                   "session", "session probe returned the completed resource "
                                              "for THIS job's own session URI", conclusive=True),
                    notes, steps)

            if kind == "in_progress":
                notes.append(f"R1: the session is still alive and holds {offset} bytes. Resume "
                             "from that offset; do not insert again.")
                return ReconciliationResult(Resolution.INCONCLUSIVE_RETRY_LATER, "R1",
                                            None, notes, steps)

            if kind == "expired":
                notes.append("R1: the session URI has expired (HTTP 404). The upload may still "
                             "have been accepted before the session died, so R2 and R3 follow.")
            elif kind == "error":
                notes.append("R1 returned an unexpected error; falling through to R2/R3.")
        else:
            steps.append("R1")
            notes.append("R1: no session URI was persisted, so the POST that creates the "
                         "insert was never accepted and no video can exist from this attempt.")

        # ---- R2: channel activities ---------------------------------------------
        # Evidence only: an upload activity may be an unrelated manual Studio upload.
        steps.append("R2")
        try:
            items, err = self._activities(channel_id, attempt_started_at_iso)
        except httpx.HTTPError as exc:
            items, err = [], f"R2: activities.list unreachable ({type(exc).__name__})."
        if err:
            notes.append(err)
        for it in items:
            details = (it.get("contentDetails") or {}).get("upload") or {}
            vid = details.get("videoId")
            if vid:
                snip = it.get("snippet") or {}
                notes.append(f"R2: candidate upload {vid} (published "
                             f"{snip.get('publishedAt', '?')}). This is supporting evidence, "
                             "not proof of causality.")
                return self._candidate_result(
                    "R2", steps, notes, vid, snip, "activities",
                    "upload activity after the attempt began; could be an unrelated manual upload")

        # ---- R3: the channel's uploads playlist ---------------------------------
        steps.append("R3")
        try:
            playlist_id, err = self._uploads_playlist(channel_id)
            r3_items, err2 = self._playlist_items(playlist_id) if playlist_id else ([], None)
        except httpx.HTTPError as exc:
            r3_items, err, err2 = [], f"R3: unreachable ({type(exc).__name__}).", None
        for e in (err, err2):
            if e:
                notes.append(e)
        for it in r3_items:
            vid = (it.get("snippet") or {}).get("resourceId", {}).get("videoId")
            if vid:
                snip = it.get("snippet") or {}
                notes.append(f"R3: the head of the uploads playlist is {vid}.")
                return self._candidate_result(
                    "R3", steps, notes, vid, snip, "uploads_playlist",
                    "most recent item on the channel's uploads playlist")

        # ---- R4: operator --------------------------------------------------------
        steps.append("R4")
        notes.append("R4: no conclusive evidence at any step. The job is parked for operator "
                     "resolution and no second insert is issued.")
        return ReconciliationResult(Resolution.NEEDS_MANUAL_REVIEW, "R4", None, notes, steps)

    @staticmethod
    def _candidate_result(step, steps, notes, vid, snip, source, evidence) -> ReconciliationResult:
        """R2/R3 never auto-adopt: the operator confirms or rejects the candidate."""
        return ReconciliationResult(
            Resolution.NEEDS_MANUAL_REVIEW, step,
            CandidateVideo(vid, snip.get("title", ""), snip.get("publishedAt", ""),
                           source, evidence, conclusive=False),
            notes, steps)

    # -- documented reads ---------------------------------------------------------

    def _activities(self, channel_id: str, published_after: str | None):
        params: dict[str, str] = {"part": "snippet,contentDetails", "channelId": channel_id,
                                  "maxResults": "50"}
        if published_after:
            params["publishedAfter"] = published_after
        resp = self._client.get(f"{self._base}/activities", params=params, headers=self._headers())
        if resp.status_code != 200:
            return [], f"R2: activities.list returned HTTP {resp.status_code}."
        return (resp.json() or {}).get("items", []), None

    def _uploads_playlist(self, channel_id: str) -> tuple[str, str | None]:
        resp = self._client.get(
            f"{self._base}/channels",
            params={"part": "contentDetails", "id": channel_id},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return "", f"R3: channels.list returned HTTP {resp.status_code}."
        items = (resp.json() or {}).get("items", [])
        if not items:
            return "", "R3: channels.list returned no item for this channel."
        rel = (items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}
        return rel.get("uploads", ""), None

    def _playlist_items(self, playlist_id: str):
        if not playlist_id:
            return [], "R3: no uploads playlist id available."
        resp = self._client.get(
            f"{self._base}/playlistItems",
            params={"part": "snippet,contentDetails", "playlistId": playlist_id,
                    "maxResults": "5"},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return [], f"R3: playlistItems.list returned HTTP {resp.status_code}."
        return (resp.json() or {}).get("items", []), None


def verify_video_id(access_token: str, client: httpx.Client, video_id: str,
                    base_url: str = "https://www.googleapis.com/youtube/v3") -> dict | None:
    """Prove a recovered video ID exists and read its status. Documented and cheap (1 unit)."""
    resp = client.get(
        f"{base_url.rstrip('/')}/videos",
        params={"part": "id,snippet,status", "id": video_id},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != 200:
        return None
    items = (resp.json() or {}).get("items", [])
    return items[0] if items else None


def decide_retry(session, result: ReconciliationResult) -> tuple[bool, str]:
    """The single gate every retry must pass. Returns ``(allowed, reason)``."""
    if getattr(session, "video_id", ""):
        return False, f"A video ID ({session.video_id}) is already recorded for this job."
    if session.session_uri:
        return False, ("A resumable session exists for this job. Resuming it cannot create a "
                       "second video; issuing a new insert could.")
    if result.resolution is Resolution.RESOLVED:
        return False, "Reconciliation recovered a definite outcome; no retry is required."
    if result.resolution is Resolution.INCONCLUSIVE_RETRY_LATER:
        return False, "The session is alive; resume it rather than inserting again."
    if result.resolution is Resolution.NEEDS_MANUAL_REVIEW:
        if result.candidate is None and "R1" in result.steps_run and not session.session_uri \
                and "no session URI was persisted" in " ".join(result.notes):
            return True, ("No session was ever established and the documented reads show no "
                          "candidate upload after the attempt began, so no insert can have "
                          "been accepted. A retry is safe.")
        return False, ("The outcome is unresolved. This job is routed to JOB-NEEDS_REVIEW; "
                       "retrying could create a duplicate video.")
    return False, "No conclusive evidence; a retry is not permitted."
