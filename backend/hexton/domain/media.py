"""Media-file rules: what counts as an acceptable source clip, and how to read a
container's own metadata without a transcoding dependency.

Pure. :mod:`hexton.media.validate` supplies the bytes; everything here is a function
of them, so the rules are testable without touching a disk.

Not needing ``ffmpeg`` is deliberate. Shell-ing out to an uninstalled binary produces
an opaque failure at exactly the moment the operator is trying to add a file, and it
would make "is this clip valid?" depend on something that may not be on the machine.
Parsing the ISO-BMFF boxes directly answers the two questions this application
actually has — *is this a video container?* and *how long is it?* — and stops there.

What this does **not** do: transcode, re-encode, or inspect codecs. Source bytes are
never rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What YouTube's resumable upload will accept as a source clip for this product.
#: ``01`` §FEAT-041 restricts the MVP to clip formats rather than accepting anything
#: and discovering the problem after a multi-hundred-megabyte transfer.
ALLOWED_EXTENSIONS: dict[str, str] = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}

#: Refused outright regardless of extension. A zero-byte or near-empty file is almost
#: always an interrupted download or a placeholder, and uploading it would burn quota
#: to learn that.
MIN_SIZE_BYTES = 1

#: Generous ceiling. The real ceiling is the quota and time cost of a failed transfer,
#: not a product rule; this exists to fail fast on an obviously wrong selection.
MAX_SIZE_BYTES = 12 * 1024 * 1024 * 1024

#: ISO-BMFF brands that identify a video container. Anything else in ``ftyp`` is a
#: still image, an audio-only file, or something else with a box structure.
_VIDEO_BRANDS = frozenset(
    {
        b"isom",
        b"iso2",
        b"iso4",
        b"iso5",
        b"iso6",
        b"mp41",
        b"mp42",
        b"avc1",
        b"qt  ",
        b"M4V ",
        b"dash",
    }
)

#: How much of the head and tail to scan for ``moov``. In practice ``moov`` is one of
#: the two; a fragmented stream written by a recorder can put it at the end.
_SCAN_BYTES = 512 * 1024


@dataclass(frozen=True)
class ContainerInfo:
    container: str
    duration_seconds: float | None
    brand: str | None
    duration_parsed: bool
    note: str | None = None


def content_type_for(path: str) -> str:
    from pathlib import Path

    return ALLOWED_EXTENSIONS.get(Path(path).suffix.lower(), "application/octet-stream")


def check_extension(path: str) -> str | None:
    """Return a blocking reason when the extension is not an accepted clip format."""
    from pathlib import Path

    suffix = Path(path).suffix.lower()
    if not suffix:
        return (
            "The selected file has no extension, so its container cannot be determined. "
            "Hexton does not guess."
        )
    if suffix not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return f"Extension {suffix!r} is not an accepted clip container. Accepted: {allowed}."
    return None


def check_size(size_bytes: int) -> str | None:
    if size_bytes < MIN_SIZE_BYTES:
        return (
            "The file is empty (0 bytes). This is usually an interrupted download or a placeholder."
        )
    if size_bytes > MAX_SIZE_BYTES:
        gb = MAX_SIZE_BYTES / (1024**3)
        return f"The file is {size_bytes / (1024**3):.1f} GB; the ceiling is {gb:.0f} GB."
    return None


def sniff_container(head: bytes) -> ContainerInfo | None:
    """Identify the container from its leading bytes, or return ``None``.

    ``None`` means "not an ISO-BMFF video container", which is a blocking result —
    the caller must not fall back to the file extension, because an extension is
    attacker- and accident-writable and the box header is the file speaking for
    itself.
    """
    if len(head) < 12:
        return None
    if head[4:8] != b"ftyp":
        # WebM/Matroska start with the EBML header.
        if head[:4] == b"\x1a\x45\xdf\xa3":
            container = "webm" if b"webm" in head[:64] else "matroska"
            return ContainerInfo(container, None, None, False, "EBML header; duration not parsed.")
        return None

    brand = head[8:12]
    container = "quicktime" if brand == b"qt  " else "mp4"
    if brand not in _VIDEO_BRANDS and container != "quicktime":
        return ContainerInfo(
            container,
            None,
            brand.decode("latin-1", "replace"),
            False,
            f"Unrecognised major brand {brand!r}; accepted on the strength of the ftyp box.",
        )
    return ContainerInfo(container, None, brand.decode("latin-1", "replace"), False)


def parse_duration(chunk: bytes) -> float | None:
    """Best-effort duration from a ``mvhd`` box inside ``chunk``.

    Returns ``None`` when no timescale/duration pair could be read. ``None`` is an
    honest answer: this application does not fabricate a duration it did not find, and
    a missing duration never blocks an upload — YouTube reports the real one during
    processing.
    """
    start = 0
    while True:
        index = chunk.find(b"mvhd", start)
        if index < 0:
            return None
        # 'mvhd' is preceded by a 4-byte box size and followed by version/flags.
        payload = index + 4
        if payload + 24 > len(chunk):
            return None
        version = chunk[payload]
        try:
            if version == 1:
                timescale = int.from_bytes(chunk[payload + 20 : payload + 24], "big")
                duration = int.from_bytes(chunk[payload + 24 : payload + 32], "big")
            else:
                timescale = int.from_bytes(chunk[payload + 12 : payload + 16], "big")
                duration = int.from_bytes(chunk[payload + 16 : payload + 20], "big")
        except (IndexError, ValueError):
            return None
        if timescale > 0 and duration not in (0, 0xFFFFFFFF):
            return round(duration / timescale, 3)
        start = index + 4


def read_container(head: bytes, tail: bytes = b"") -> ContainerInfo | None:
    """Identify the container and, if possible, its duration.

    Scans both ends because a recorded or fragmented stream often places ``moov`` last;
    reading only the head would report "no duration" for a perfectly good clip.
    """
    info = sniff_container(head)
    if info is None:
        return None
    if info.container in ("webm", "matroska"):
        return info
    duration = parse_duration(head) or parse_duration(tail)
    if duration is None:
        return ContainerInfo(
            info.container,
            None,
            info.brand,
            False,
            "Duration box not found in the scanned regions.",
        )
    return ContainerInfo(info.container, duration, info.brand, True)
