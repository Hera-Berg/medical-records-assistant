"""Loopback binding remains mandatory.

The patient workspace is intentionally unauthenticated. Clinician sign-in adds
an account/session boundary for clinician pages and APIs; it does not turn the
patient-held record into an internet-facing service.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from ..errors import VaultError

#: The only hosts this program will bind. Both are secure contexts for
#: `getUserMedia`, which phase 6's recorder needs; `192.168.x.x` is not, and a
#: microphone that silently fails on the LAN is one more reason not to go there.
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

DEFAULT_HOST = "127.0.0.1"


def check_host(host: str) -> str:
    """Return *host* if it is loopback, else refuse with the reason."""
    if host in LOOPBACK_HOSTS:
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise VaultError(
            f"refusing to bind {host!r}: this server has no authentication and "
            f"never will, so the port is the only boundary around the record. "
            f"Bind 127.0.0.1 and reach it from elsewhere over your own VPN."
        ) from None
    if not address.is_loopback:
        raise VaultError(
            f"refusing to bind {host}: that address is reachable from other "
            f"machines and this server has no authentication. A health record on "
            f"an open port is readable by anything on the network. Bind 127.0.0.1 "
            f"and use your own VPN if you need it elsewhere."
        )
    return host


def serve(
    vault,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    worker: bool | None = None,
    **kwargs: Any,
) -> None:
    """Run the server. Blocks until interrupted."""
    import uvicorn  # noqa: PLC0415 - only needed when actually serving

    from .app import create_app

    bound = check_host(host)
    app = create_app(vault, worker=worker)
    uvicorn.run(
        app,
        host=bound,
        port=port or vault.config.port,
        log_level=kwargs.pop("log_level", "warning"),
        access_log=kwargs.pop("access_log", False),
        **kwargs,
    )
