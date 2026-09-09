"""What may be handed to a browser inline, and what may not.

The vault is a folder that syncs from Dropbox, Drive or Nextcloud. Its contents
are **untrusted input** — not because the owner is untrusted, but because the
bytes in it have travelled through a third party's storage, arrived by restore,
been dragged in from a download folder, or been written by another device the
owner has not looked at in a year. Ownership of a folder is not provenance for
what is in it.

That matters here because ``/api/artifact/{hash}`` serves those bytes from the
**same origin as the SPA**. An ingested SVG or HTML file rendered inline is
script running with the app's origin, which means it can read every API route
this server exposes — the whole record — with the user's own credentials, from a
page the user opened deliberately. Single-origin is the right architecture for
everything else in this layer and it is exactly what makes this the sharp edge.

So there are three defences, and each of them holds on its own:

1. **An allowlist of media types that may render inline.** Not a blocklist:
   a blocklist has to anticipate every scriptable type, and ``image/svg+xml``
   is only the obvious one. Anything not on the list is served as an
   ``attachment`` with a neutral type, which a browser saves rather than runs.
2. **``X-Content-Type-Options: nosniff``**, so a mislabelled file cannot be
   promoted to HTML by content sniffing.
3. **A restrictive ``Content-Security-Policy``**, so that even a rendering
   context we were wrong about executes nothing and fetches nothing.

The type comes from the ingest record, which sniffed the bytes when they landed,
never from anything the requester supplies.
"""

from __future__ import annotations

#: Media types a browser may render in place. Deliberately short. Each entry is
#: something a person needs to *look at* to check a citation — the whole point of
#: keeping the artefact next to the claim.
#:
#: ``image/svg+xml`` is absent on purpose. An SVG is a document that can carry
#: script, so it belongs with the attachments however much it behaves like an
#: image everywhere else.
INLINE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/heic",
        "image/heif",
        "image/tiff",
        "image/bmp",
        "application/pdf",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-m4a",
        "audio/flac",
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "text/plain",
    }
)

#: What anything not on the list is served as. A type no browser renders.
DOWNLOAD_TYPE = "application/octet-stream"

#: ``default-src 'none'`` stops every fetch, script and frame. The three that
#: are re-enabled are what a PDF viewer, an ``<img>`` and an ``<audio>`` need to
#: display the bytes themselves and nothing else. ``sandbox`` without
#: ``allow-scripts`` means even an HTML document served here by mistake runs no
#: code and gets a unique opaque origin, so it cannot reach the API either.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; img-src 'self' data:; media-src 'self'; "
    "object-src 'self'; frame-ancestors 'self'; sandbox"
)


def may_render_inline(mime: str) -> bool:
    return normalise(mime) in INLINE_TYPES


def normalise(mime: str) -> str:
    """The bare type, lowercased, without parameters.

    ``audio/webm;codecs=opus`` is what ``MediaRecorder`` produces in Chrome and
    it is the same type as ``audio/webm``.
    """
    return (mime or "").split(";", 1)[0].strip().lower()


def disposition_for(mime: str, filename: str) -> tuple[str, str]:
    """``(content_type, content_disposition)`` for one stored artefact.

    Inline where the type is on the allowlist, and a download otherwise. The
    filename is always stated so a saved file keeps the name the vault uses,
    which is what makes it traceable back to the folder.
    """
    if may_render_inline(mime):
        return normalise(mime), f'inline; filename="{_safe_filename(filename)}"'
    return DOWNLOAD_TYPE, f'attachment; filename="{_safe_filename(filename)}"'


def headers_for(mime: str, filename: str, digest: str) -> dict[str, str]:
    """Every header one artefact response carries.

    The ``ETag`` is the content hash. Stored artefacts are addressed by that
    hash and never rewritten, so the validator is exact rather than a heuristic
    on mtime, and a re-request after a resync that changed only the mode is
    still a cache hit.
    """
    content_type, disposition = disposition_for(mime, filename)
    return {
        "content-type": content_type,
        "content-disposition": disposition,
        "x-content-type-options": "nosniff",
        "content-security-policy": CONTENT_SECURITY_POLICY,
        "etag": f'"{digest}"',
        # Private, because a shared cache has no business holding this, and
        # immutable because the bytes behind a hash cannot change.
        "cache-control": "private, max-age=31536000, immutable",
        "referrer-policy": "no-referrer",
    }


def _safe_filename(name: str) -> str:
    """A filename safe to put in a header, with no room for injection."""
    cleaned = "".join(
        ch for ch in name if ch.isprintable() and ch not in '"\\\r\n;'
    ).strip()
    return cleaned or "artifact"


#: Headers on every API response. The SPA is same-origin and makes no
#: cross-origin request, so none of this costs anything.
API_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    # Nothing here is ever fetched by another site, and this record must not be
    # cached by anything between the process and the browser.
    "cache-control": "no-store",
}

#: Headers on the SPA document itself. ``connect-src 'self'`` is what stops a
#: compromised dependency in the bundle from posting the record anywhere:
#: invariant 3 says no telemetry and no network calls, and this enforces it in
#: the browser rather than trusting the bundle.
APP_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; frame-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
