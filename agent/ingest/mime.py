"""What kind of file is this, decided from the bytes.

Content first, filename second. An extension is a claim made by whichever
program last touched the file, and the ones this project receives are routinely
wrong: iPhone photos arrive as ``.jpg`` while holding HEIC, browsers save
``prescription.pdf.txt``, and a "photo" pasted from a chat client can be
anything. The mime type decides how ``/api/artifact/{hash}`` later serves the
bytes and which reader the extraction phase points at a file, so a wrong answer
here surfaces much later as "the model is bad at reading PDFs".

No ``libmagic``. The set of formats a health record actually ingests is small
and fixed — phone photos, PDFs, scans, browser audio — and a C library and its
system package are not worth carrying for it. Where the bytes say nothing
recognisable, the answer is honestly ``application/octet-stream`` with
``mime_source`` recording that it was a guess, rather than a confident wrong
type.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass

OCTET_STREAM = "application/octet-stream"

SOURCE_SNIFFED = "sniffed"
SOURCE_REFINED_DECLARED = "sniffed+declared"
SOURCE_REFINED_EXTENSION = "sniffed+extension"
SOURCE_EXTENSION = "extension"
SOURCE_UNKNOWN = "unknown"

#: (offset, magic, mime). Checked in order; first match wins.
_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (0, b"%PDF-", "application/pdf"),
    (0, b"\xff\xd8\xff", "image/jpeg"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"GIF87a", "image/gif"),
    (0, b"GIF89a", "image/gif"),
    (0, b"II*\x00", "image/tiff"),
    (0, b"MM\x00*", "image/tiff"),
    (0, b"fLaC", "audio/flac"),
    (0, b"OggS", "audio/ogg"),
    (0, b"ID3", "audio/mpeg"),
    (0, b"PK\x03\x04", "application/zip"),
    (0, b"\x1f\x8b", "application/gzip"),
)

#: ISO base-media brands, read at offset 8 after an ``ftyp`` box. HEIC matters
#: most: it is what an iPhone hands over, and nothing else in the pipeline can
#: read it, so mislabelling it as JPEG produces a silent failure downstream.
_FTYP_BRANDS: dict[bytes, str] = {
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"heim": "image/heic",
    b"heis": "image/heic",
    b"hevc": "image/heic",
    b"hevx": "image/heic",
    b"hevm": "image/heic",
    b"hevs": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"avif": "image/avif",
    b"avis": "image/avif",
    b"qt  ": "video/quicktime",
    b"M4A ": "audio/mp4",
    b"M4B ": "audio/mp4",
    b"M4V ": "video/mp4",
    b"mp41": "video/mp4",
    b"mp42": "video/mp4",
    b"isom": "video/mp4",
    b"iso2": "video/mp4",
    b"avc1": "video/mp4",
    b"dash": "video/mp4",
}

#: mime -> the extension this project writes. Explicit rather than
#: ``mimetypes.guess_extension``, which returns ``.jpe`` for JPEG on some
#: builds and would make filenames depend on the host's mime database.
_EXTENSIONS: dict[str, str] = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/tiff": "tiff",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "image/avif": "avif",
    "audio/webm": "webm",
    "video/webm": "webm",
    "video/x-matroska": "mkv",
    "audio/mp4": "m4a",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/3gpp": "3gp",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/flac": "flac",
    "audio/mpeg": "mp3",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
    "application/json": "json",
    "application/zip": "zip",
    "application/gzip": "gz",
    OCTET_STREAM: "bin",
}

#: Extensions this project trusts to name a text subtype the bytes cannot.
_TEXT_EXTENSIONS: dict[str, str] = {
    "md": "text/markdown",
    "markdown": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "txt": "text/plain",
    "text": "text/plain",
}

#: A sniffed container that a more specific declared type may narrow. Browsers
#: know whether a WebM holds audio or video; the container header does not say
#: without parsing tracks, and the distinction decides whether it reaches
#: Whisper later.
_REFINEMENTS: dict[str, frozenset[str]] = {
    "video/webm": frozenset({"audio/webm", "video/webm"}),
    "video/mp4": frozenset({"audio/mp4", "video/mp4"}),
    "audio/mp4": frozenset({"audio/mp4", "video/mp4"}),
    "text/plain": frozenset({"text/plain", "text/markdown", "text/csv", "application/json"}),
}


@dataclass(frozen=True)
class Detected:
    """A decided mime type and an honest account of how it was decided."""

    mime: str
    source: str

    @property
    def is_guess(self) -> bool:
        return self.source in (SOURCE_EXTENSION, SOURCE_UNKNOWN)

    def extension(self, filename: str | None = None) -> str:
        return extension_for(self.mime, filename)


_SANE_EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")


def extension_for(mime: str, filename: str | None = None) -> str:
    """The extension this project writes for *mime*.

    An unrecognised format keeps the extension it arrived with, where that is a
    plain one. A DICOM study or a vendor export stored as ``.bin`` would need
    this app to work out what it is, and the folder has to stay legible without
    it; ``.bin`` is the last resort, not the default for anything unfamiliar.
    """
    known = _EXTENSIONS.get(mime)
    if known:
        return known
    original = _extension_of(filename)
    if original and _SANE_EXTENSION.match(original):
        return original
    return "bin"


def _looks_like_text(head: bytes) -> bool:
    """Whether *head* is plausibly UTF-8 text.

    A truncated head can split a multi-byte character, so a decode failure in
    the last few bytes is forgiven — that is an artefact of only having the
    head, not evidence about the file.
    """
    if not head:
        return False
    if b"\x00" in head:
        return False
    sample = head
    for _ in range(3):
        try:
            text = sample.decode("utf-8")
            break
        except UnicodeDecodeError as exc:
            if exc.start < len(sample) - 4:
                return False
            sample = sample[: exc.start]
    else:
        return False
    return not any(ord(c) < 9 or 13 < ord(c) < 32 for c in text)


def sniff(head: bytes) -> str | None:
    """Identify *head* from its magic bytes, or return ``None``."""
    for offset, magic, mime in _SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            return mime

    if head[:4] == b"RIFF" and len(head) >= 12:
        form = head[8:12]
        if form == b"WEBP":
            return "image/webp"
        if form == b"WAVE":
            return "audio/wav"

    if head[4:8] == b"ftyp" and len(head) >= 12:
        brand = head[8:12]
        if brand in _FTYP_BRANDS:
            return _FTYP_BRANDS[brand]
        if brand[:3] == b"3gp":
            return "video/3gpp"
        return "video/mp4"

    if head[:4] == b"\x1aE\xdf\xa3":
        # The DocType string sits a little way into the EBML header.
        return "video/webm" if b"webm" in head[:64] else "video/x-matroska"

    if head[:2] == b"\xff\xfb" or head[:2] == b"\xff\xf3" or head[:2] == b"\xff\xf2":
        return "audio/mpeg"

    if _looks_like_text(head):
        return "text/plain"
    return None


def _extension_of(filename: str | None) -> str | None:
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    return ext or None


def detect(
    head: bytes,
    filename: str | None = None,
    declared: str | None = None,
) -> Detected:
    """Decide the mime type of a file from its head, name and declared type.

    Order is deliberate: the bytes decide the container, and the name or the
    client's declared type may only *narrow* what the bytes already said. A
    declared type never overrides a contradicting signature — that is exactly
    the case where the declaration is the thing that is wrong.
    """
    declared = (declared or "").split(";")[0].strip().lower() or None
    extension = _extension_of(filename)

    sniffed = sniff(head)
    if sniffed is not None:
        allowed = _REFINEMENTS.get(sniffed)
        if allowed:
            if declared in allowed and declared != sniffed:
                return Detected(declared, SOURCE_REFINED_DECLARED)
            from_ext = _TEXT_EXTENSIONS.get(extension or "")
            if from_ext in allowed and from_ext != sniffed:
                return Detected(from_ext, SOURCE_REFINED_EXTENSION)
        return Detected(sniffed, SOURCE_SNIFFED)

    if extension:
        guessed, _ = mimetypes.guess_type(f"x.{extension}")
        if guessed:
            return Detected(guessed, SOURCE_EXTENSION)

    return Detected(OCTET_STREAM, SOURCE_UNKNOWN)


#: Mime prefixes the speech model reads. Video is included because a phone will
#: hand over a ``video/mp4`` whose only useful content is its audio track, and
#: the decoder drops the video stream on the way to the working copy.
SPEECH_PREFIXES = ("audio/", "video/")


def is_speech(mime: str | None) -> bool:
    """Whether this artefact is one the speech model reads rather than the VLM.

    Lives here rather than in either reader because it is the question that
    *routes* between them: the extraction queue is one queue, and which of the
    two drains picks a job up is decided by nothing but this.
    """
    return str(mime or "").startswith(SPEECH_PREFIXES)
