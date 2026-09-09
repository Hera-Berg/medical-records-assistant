"""Where the inference endpoint is allowed to be.

``MODELS.md``: "An ``openai_base_url`` config field is a foot-gun: paste
``https://api.openai.com/v1`` and a key, and the entire privacy premise of the
project evaporates with no visible change in behaviour."

So the host must resolve into private address space — Tailscale's CGNAT range,
RFC1918, loopback, or their IPv6 equivalents — and it is checked **on every
call**, not only at startup. A hostname that resolved privately at boot can
resolve elsewhere an hour later, and a check that only ran once would not
notice. MagicDNS names are ordinary hostnames that resolve to ``100.x``, so they
pass; HTTPS through Tailscale Serve carries a real certificate, so TLS
verification stays on and is never disabled.

**Every resolved address must pass, not merely one of them.** A host answering
with both a private and a public address is a host that can send the request
anywhere, and taking the first answer would make the guard depend on resolver
ordering.

Two things this cannot see, stated here so they are not mistaken for covered.
Tailscale **Funnel** exposes a machine to the public internet while it still
resolves to ``100.x`` from inside the tailnet: nothing in this codebase can
detect that, so it is an operational rule — serve the endpoint with
``tailscale serve``, never ``tailscale funnel`` — and ``health-agent probe``
says so. And a private address says nothing about who else is on the tailnet,
which is why the endpoint is authenticated anyway and belongs behind an ACL.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
from urllib.parse import urlsplit

from ..errors import EndpointNotPrivate

#: Address space the endpoint may live in. Tailscale hands out ``100.64/10``
#: (CGNAT) for IPv4 and ``fd7a:115c:a1e0::/48`` for IPv6, which is inside the
#: unique-local ``fc00::/7`` — the IPv6 analogue of RFC1918, included for that
#: reason rather than as a widening.
_ALLOWED_V4 = (
    ipaddress.ip_network("100.64.0.0/10"),   # Tailscale CGNAT
    ipaddress.ip_network("10.0.0.0/8"),      # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),   # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
)
_ALLOWED_V6 = (
    ipaddress.ip_network("fc00::/7"),        # unique local, incl. Tailscale
    ipaddress.ip_network("::1/128"),         # loopback
)

_ALLOWED_SCHEMES = ("http", "https")

#: Resolver seam, so the guard can be tested without DNS and without a network.
Resolver = Callable[[str, int | None], Sequence[str]]


def system_resolver(host: str, port: int | None = None) -> list[str]:
    """Every address *host* resolves to right now, as strings."""
    try:
        answers = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EndpointNotPrivate(
            f"the inference endpoint host {host!r} does not resolve: {exc}. The address "
            f"guard cannot pass a host it cannot resolve, so this is a refusal rather "
            f"than a request that goes out unchecked."
        ) from None
    return [str(info[4][0]) for info in answers]


def is_private(address: str) -> bool:
    """Whether one resolved address is inside the allowed space."""
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    networks = _ALLOWED_V4 if parsed.version == 4 else _ALLOWED_V6
    return any(parsed in network for network in networks)


@dataclass(frozen=True)
class Endpoint:
    """A base URL that has been parsed and found structurally acceptable.

    Structurally acceptable is not the same as usable: :meth:`verify` is what
    checks the addresses, and it is called again before every request.
    """

    base_url: str
    scheme: str
    host: str
    port: int | None

    @property
    def is_loopback_only(self) -> bool:
        return self.host in ("localhost", "127.0.0.1", "::1")

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def verify(self, resolver: Resolver = system_resolver) -> tuple[str, ...]:
        """Resolve and check. Returns the addresses; raises if any is public.

        Called before **every** request rather than cached, because that is the
        whole difference between a guard and a boot-time assertion.
        """
        addresses = tuple(resolver(self.host, self.port))
        if not addresses:
            raise EndpointNotPrivate(
                f"the inference endpoint host {self.host!r} resolved to no addresses"
            )
        public = [address for address in addresses if not is_private(address)]
        if public:
            raise EndpointNotPrivate(_public_message(self, addresses, public))
        return addresses


def _public_message(
    endpoint: Endpoint, addresses: Iterable[str], public: Sequence[str]
) -> str:
    return (
        f"the inference endpoint {endpoint.base_url} resolves to "
        f"{', '.join(public)}, which is outside private address space. This vault's "
        f"whole premise is that the record never leaves infrastructure you control, "
        f"so this is a startup failure rather than a warning.\n\n"
        f"The endpoint must resolve into Tailscale's 100.64.0.0/10, RFC1918, or "
        f"loopback. A MagicDNS name such as macbook-pro.tailnet.ts.net is fine — it "
        f"resolves to 100.x. Reaching a commercial API is not a configuration "
        f"option: if you genuinely want one, fork the project.\n"
        f"(resolved: {', '.join(addresses)})"
    )


def parse(base_url: object) -> Endpoint:
    """Read ``models.vlm.base_url`` into an :class:`Endpoint`.

    Rejects a URL carrying userinfo outright. ``https://user:secret@box/v1`` is a
    credential written into ``config.toml``, which lives in a folder that syncs
    to third-party storage — the same failure the config secret scan exists to
    catch, arriving by a route that scan cannot see.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise EndpointNotPrivate(
            "models.vlm.base_url is missing. Set it to your inference box's "
            "OpenAI-compatible endpoint, for example "
            '"https://macbook-pro.tailnet.ts.net/v1".'
        )
    split = urlsplit(base_url.strip())
    if split.scheme not in _ALLOWED_SCHEMES:
        raise EndpointNotPrivate(
            f"models.vlm.base_url must start with http:// or https://, got "
            f"{base_url!r}"
        )
    if split.username or split.password:
        raise EndpointNotPrivate(
            "models.vlm.base_url carries a username or password. config.toml lives "
            "in the vault and syncs to Dropbox, Drive or Nextcloud, so a credential "
            "there has already been handed to a third party: remove it, rotate it, "
            "and keep the key in the OS keychain (see MODELS.md, 'Credentials')."
        )
    if not split.hostname:
        raise EndpointNotPrivate(f"models.vlm.base_url has no host: {base_url!r}")
    try:
        port = split.port
    except ValueError:
        raise EndpointNotPrivate(
            f"models.vlm.base_url has an unreadable port: {base_url!r}"
        ) from None
    return Endpoint(
        base_url=base_url.strip(),
        scheme=split.scheme,
        host=split.hostname,
        port=port,
    )


#: Printed by ``health-agent probe``. An operational rule, because the address
#: guard genuinely cannot check it — see the module docstring.
FUNNEL_WARNING = (
    "the address guard cannot detect Tailscale Funnel: a Funnel-exposed endpoint "
    "still resolves to 100.x from inside the tailnet while being reachable from the "
    "public internet. Serve the box with `tailscale serve`, never `tailscale funnel`, "
    "and check with `tailscale serve status` if anything looks wrong."
)
