"""Who may talk to the server: pages this server served, and nothing else.

Binding ``127.0.0.1`` keeps other *machines* out. It does not keep other *web
pages* out, and on a machine where the app sits in the tray all day that is the
boundary that matters. Two attacks reach a loopback server from an ordinary
browser tab, and each has its own check here.

**DNS rebinding.** A page at ``evil.example`` re-points its own hostname at
``127.0.0.1`` after loading. The browser then considers this server to be
*that page's own origin*, and every response — the medication list, the
artefacts, the whole record — is readable by script it wrote. Nothing about the
request changes except one thing the attacker cannot forge: the ``Host`` header
still says ``evil.example``, because that is the name the browser resolved. So
every request whose ``Host`` is not a loopback name is refused, before any route
runs, reads or not.

**Cross-site writes.** A page anywhere can submit a form to
``http://127.0.0.1:7777/api/capture``. A multipart form post is a "simple"
request, so no preflight asks this server first, and the browser sends it
whether or not the page can read the answer. The answer does not matter: the
file has landed in the record. So every request that can change something —
``POST``, ``PUT``, ``PATCH``, ``DELETE`` — must come from a loopback origin.
Browsers state the origin of those requests (``Origin``, and ``Sec-Fetch-Site``
in every current one), so a browser request with neither is not one a page could
have made. A request with neither comes from something that is not a browser —
the CLI, ``curl``, a test — which a web page cannot drive.

The port is not compared. Rebinding needs a hostname the attacker controls, so a
loopback *name* is what decides it, and development's second origin
(``localhost:5173``, proxied) keeps working without a special case.
"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

#: Methods that change something. Reads are protected by ``Host`` alone: a
#: cross-site page can *send* a GET but cannot read the answer without a CORS
#: header, and this server never sends one.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: ``Sec-Fetch-Site`` values a page served by this server produces. ``none`` is a
#: request the person made themselves — typing the address, a bookmark.
SAME_SITE_FETCH = frozenset({"same-origin", "none"})

HOST_REFUSED = (
    "This address did not come from the health record app. It only answers pages "
    "opened at 127.0.0.1 or localhost on this computer."
)
ORIGIN_REFUSED = (
    "A page from somewhere other than the health record app tried to change your "
    "record, and was stopped. Nothing was changed."
)


def is_loopback_name(hostname: str | None) -> bool:
    """Whether *hostname* can only mean this machine."""
    if not hostname:
        return False
    name = hostname.strip().lower().rstrip(".")
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name.strip("[]")).is_loopback
    except ValueError:
        return False


def host_allowed(host_header: str | None) -> bool:
    """Whether a ``Host`` header names this machine by a loopback name."""
    if not host_header:
        return False
    try:
        return is_loopback_name(urlsplit(f"//{host_header}").hostname)
    except ValueError:
        return False


def origin_allowed(origin: str | None, fetch_site: str | None) -> bool:
    """Whether a state-changing request came from a page this server served."""
    if origin is not None:
        # ``null`` is a sandboxed frame, a ``file://`` page, or a redirect that
        # crossed origins — never this app.
        if origin.strip().lower() == "null":
            return False
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        return parts.scheme in ("http", "https") and is_loopback_name(parts.hostname)
    if fetch_site is not None:
        return fetch_site.strip().lower() in SAME_SITE_FETCH
    return True


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


class OriginGuard:
    """ASGI middleware applying both checks before anything else runs."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not host_allowed(_header(scope, b"host")):
            if scope["type"] == "websocket":
                # No route here speaks websocket; refuse one all the same.
                await send({"type": "websocket.close", "code": 1008})
                return
            await _refuse(send, 421, HOST_REFUSED)
            return
        method = scope.get("method", "GET").upper()
        if method in UNSAFE_METHODS and not origin_allowed(
            _header(scope, b"origin"), _header(scope, b"sec-fetch-site")
        ):
            await _refuse(send, 403, ORIGIN_REFUSED)
            return
        await self.app(scope, receive, send)


async def _refuse(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
