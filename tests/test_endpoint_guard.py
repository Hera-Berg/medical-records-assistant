"""Where the inference endpoint is allowed to be.

The named test in ``MODELS.md`` is ``test_endpoint_guard_rejects_public_host``,
and the clause that makes it more than a boot check is "resolve and check on
every call, not just at boot. A hostname that resolved privately at startup can
resolve elsewhere later."

So the guard is exercised twice: once at parse time, and once against a resolver
that changes its answer between calls.
"""

from __future__ import annotations

import pytest

from agent.errors import EndpointNotPrivate
from agent.llm import endpoint as endpoint_mod


def _resolver(*addresses: str):
    return lambda host, port=None: list(addresses)


def _changing(*rounds: tuple[str, ...]):
    """A resolver that answers differently each time it is called."""
    answers = iter(rounds)
    return lambda host, port=None: list(next(answers))


# --- what counts as private ------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "100.94.135.1",   # Tailscale CGNAT, the normal case
        "100.64.0.1",
        "100.127.255.254",
        "10.0.0.5",
        "172.16.3.9",
        "192.168.1.10",
        "127.0.0.1",
        "::1",
        "fd7a:115c:a1e0::1",   # Tailscale IPv6, inside fc00::/7
        "::ffff:192.168.1.10",  # IPv4-mapped, unwrapped before the check
    ],
)
def test_private_addresses_are_allowed(address):
    assert endpoint_mod.is_private(address)


@pytest.mark.parametrize(
    "address",
    [
        "162.159.140.245",
        "8.8.8.8",
        "100.128.0.1",   # just outside 100.64/10 — CGNAT ends at 100.127.x
        "100.63.255.255",
        "2606:4700:7::f3",
        "172.32.0.1",    # just outside 172.16/12
        "not-an-address",
        "",
    ],
)
def test_public_addresses_are_refused(address):
    assert not endpoint_mod.is_private(address)


# --- the guard -------------------------------------------------------------


def test_endpoint_guard_rejects_public_host():
    """A commercial API base URL is a startup failure, not a warning."""
    guarded = endpoint_mod.parse("https://api.openai.com/v1")
    with pytest.raises(EndpointNotPrivate) as raised:
        guarded.verify(_resolver("162.159.140.245"))

    message = str(raised.value)
    assert "outside private address space" in message
    assert "162.159.140.245" in message, "says which address, so it can be checked"
    assert "fork the project" in message, "and that this is not a setting to change"


def test_the_guard_runs_again_on_a_host_that_re_resolves_publicly():
    """The whole difference between a guard and a boot-time assertion.

    A MagicDNS name resolving to 100.x at startup can resolve to a public
    address an hour later — DNS rebinding, a changed record, a different
    resolver on a new network. A check that ran once would not notice.
    """
    guarded = endpoint_mod.parse("https://box.tailnet.ts.net/v1")
    resolver = _changing(("100.94.135.1",), ("203.0.113.7",))

    assert guarded.verify(resolver) == ("100.94.135.1",)
    with pytest.raises(EndpointNotPrivate):
        guarded.verify(resolver)


def test_every_resolved_address_must_pass_not_merely_one():
    """A host answering with both can send the request anywhere.

    Taking the first answer would make the guard depend on resolver ordering,
    which is not a property anything here controls.
    """
    guarded = endpoint_mod.parse("https://box.tailnet.ts.net/v1")
    with pytest.raises(EndpointNotPrivate) as raised:
        guarded.verify(_resolver("100.94.135.1", "203.0.113.7"))

    assert "203.0.113.7" in str(raised.value)


def test_a_host_that_does_not_resolve_is_refused_not_passed_through():
    guarded = endpoint_mod.parse("https://box.tailnet.ts.net/v1")

    def broken(host, port=None):
        raise __import__("socket").gaierror("Name or service not known")

    with pytest.raises(EndpointNotPrivate, match="does not resolve"):
        guarded.verify(broken)


def test_an_empty_resolution_is_refused():
    guarded = endpoint_mod.parse("http://127.0.0.1:8080/v1")
    with pytest.raises(EndpointNotPrivate, match="no addresses"):
        guarded.verify(_resolver())


# --- parsing ---------------------------------------------------------------


def test_a_url_carrying_a_credential_is_refused():
    """The config secret scan cannot see a key hidden in a URL's userinfo.

    config.toml syncs with the vault, so a key written there in any form has
    already been handed to a third party.
    """
    with pytest.raises(EndpointNotPrivate) as raised:
        endpoint_mod.parse("https://elwood:sk-live-abc@box.tailnet.ts.net/v1")

    message = str(raised.value)
    assert "username or password" in message
    assert "sk-live-abc" not in message, "and the refusal does not echo the key back"


@pytest.mark.parametrize(
    "url",
    ["ftp://box/v1", "file:///etc/passwd", "box.tailnet.ts.net/v1", "", "   ", None, 7],
)
def test_an_unusable_base_url_is_refused(url):
    with pytest.raises(EndpointNotPrivate):
        endpoint_mod.parse(url)


def test_parse_keeps_the_path_so_v1_is_not_lost():
    parsed = endpoint_mod.parse("https://box.tailnet.ts.net/v1")
    assert parsed.url("/chat/completions") == (
        "https://box.tailnet.ts.net/v1/chat/completions"
    )
    assert parsed.url("chat/completions").endswith("/v1/chat/completions")


def test_a_port_is_carried_through():
    parsed = endpoint_mod.parse("http://100.94.135.1:8080/v1")
    assert parsed.port == 8080
    assert parsed.host == "100.94.135.1"


def test_the_funnel_warning_says_it_is_operational_not_checked():
    """Nothing in this codebase can detect Funnel, so it must not imply it does."""
    assert "cannot detect" in endpoint_mod.FUNNEL_WARNING
    assert "tailscale serve" in endpoint_mod.FUNNEL_WARNING
