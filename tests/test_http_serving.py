"""Binding, the endpoint guard, the SPA fallback, and the credential sweep.

Four rules that all live at the edges of the process rather than in a route.

**Loopback only.** There is no authentication here and there never will be:
``CLAUDE.md`` puts "Multi-user accounts, roles, or an auth system" on the list of
things not to build, and adds that "binding to ``0.0.0.0`` is never a default".
The port is the whole boundary, so binding it wider does not widen access a
little — it publishes a complete health record to the network with no credential
of any kind.

**A public endpoint is a startup failure.** ``MODELS.md``: reaching a commercial
API is "a startup failure, not a config option". The app must refuse to be built
at all, before a port is bound and before a credential is read.

**A missing SPA build explains itself**, because the build is committed and its
absence means someone cleaned it, not that the record is broken.

**No route returns the key.** ``test_credentials_never_reach_frontend`` in
``MODELS.md``'s list is stated as "no route, including ``/api/health`` and error
handlers". This walks every route registered on the app, including the error
paths, rather than a list someone has to remember to extend.
"""

from __future__ import annotations

import json

import pytest

from agent.errors import EndpointNotPrivate, VaultError
from agent.llm import redaction
from agent.server import create_app, static
from agent.server.runtime import check_host

from .conftest import JPEG, api_client, claim, confirm, ingested, on_day

KEY = "sk-this-key-must-never-reach-a-browser-9182"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_hosts_are_accepted(host):
    assert check_host(host) == host


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.10", "::", "10.0.0.5", "example.com"]
)
def test_anything_reachable_from_another_machine_is_refused(host):
    """Not a warning and not a setting. The port is the only boundary."""
    with pytest.raises(VaultError) as caught:
        check_host(host)
    message = str(caught.value)
    assert "no authentication" in message
    assert "127.0.0.1" in message


def test_a_public_inference_endpoint_stops_the_app_from_being_built(vault_root, identity):
    """Paste a commercial API base URL in and nothing starts.

    The privacy premise of the project would otherwise evaporate with no visible
    change in behaviour, which is exactly why this is a refusal rather than a
    warning buried in settings.
    """
    from agent.config import CONFIG_FILENAME
    from agent.vault import Vault

    (vault_root / CONFIG_FILENAME).write_text(
        'sync_profile = "local"\n'
        "port = 7777\n"
        'locale = "en"\n'
        "\n[models.vlm]\n"
        'base_url = "https://api.openai.com/v1"\n'
        'model = "gpt-4o"\n',
        encoding="utf-8",
    )
    with pytest.raises(EndpointNotPrivate):
        create_app(Vault.open(vault_root, identity=identity), worker=False)


def test_a_vault_with_no_endpoint_still_serves(client):
    """A demo vault is deliberately one of these. Capture works regardless."""
    assert client.get("/api/health").status_code == 200
    response = client.post("/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")})
    assert response.status_code == 202


def test_an_unbuilt_spa_explains_itself(client, monkeypatch, tmp_path):
    """A 404 at the root of an app that started fine is the least useful answer."""
    monkeypatch.setattr(static, "static_dir", lambda: tmp_path / "never-built")
    response = client.get("/")
    assert response.status_code == 503
    assert "has not been built" in response.text
    assert "npm --prefix frontend run build" in response.text
    # And it says the record itself is fine, because it is.
    assert "your record is fine" in response.text


def test_an_unknown_api_path_is_json_not_the_spa(client):
    """Otherwise a typo in a fetch silently returns an HTML page."""
    response = client.get("/api/nonsense")
    assert response.status_code == 404
    assert response.json()["detail"].startswith("no API route")


def test_the_bundle_cannot_be_escaped_with_a_path(client):
    """The client controls this string and the vault is on the same disk."""
    for path in ("../../../etc/hostname", "..%2f..%2fetc%2fhostname"):
        response = client.get(f"/{path}")
        assert b"root:" not in response.content


def test_build_info_says_which_frontend_commit_the_bundle_came_from(client):
    """A committed build can drift from its source. That has to be detectable."""
    body = client.get("/api/build").json()
    assert set(body) >= {"present", "commit", "built", "source"}
    # The build is committed, so it is present in a checkout and says where it
    # came from. A bundle that could not name its commit would make "is this
    # interface current" unanswerable from a running server.
    if body["present"]:
        assert body["commit"], "the bundle does not say which commit built it"


def test_the_built_interface_is_committed_to_the_repository():
    """`pip install -e .` then `health-agent serve` must work with no node.

    The project is meant to be self-hosted by someone who has Python and
    nothing else, so the build is a committed product artefact rather than a
    step in the install instructions. This is the test that keeps it that way:
    if it fails, someone changed the frontend without committing the rebuilt
    bundle, and a fresh clone would start a server with no page at `/`.
    """
    directory = static.static_dir()
    assert (directory / "index.html").is_file(), (
        "the built interface is missing. Run `npm --prefix frontend ci && "
        "npm --prefix frontend run build` and commit the result — a user "
        "installing this has Python and nothing else."
    )
    assert any(directory.glob("assets/*.js")), "the bundle has no script"
    assert (directory / static.BUILD_INFO_FILENAME).is_file(), (
        "the bundle does not record which commit built it, so a stale build "
        "could not be identified from a running server"
    )


def test_the_spa_is_served_with_a_policy_that_blocks_outbound_calls(client):
    """Invariant 3, enforced in the browser rather than trusted to the bundler.

    ``connect-src 'self'`` is what stops a compromised dependency posting the
    record somewhere. No telemetry, no analytics, no CDN — and no way for a
    future dependency to add one without this failing.
    """
    response = client.get("/")
    if response.status_code == 503:
        pytest.skip("the interface has not been built in this checkout")
    policy = response.headers["content-security-policy"]
    assert "connect-src 'self'" in policy
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy


def test_the_built_bundle_reaches_for_nothing_on_the_network(client):
    """No webfont, no CDN, no source-map host in what actually ships."""
    response = client.get("/")
    if response.status_code == 503:
        pytest.skip("the interface has not been built in this checkout")

    directory = static.static_dir()
    for path in sorted(directory.rglob("*")):
        if path.suffix not in (".js", ".css", ".html"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in (
            "fonts.googleapis.com",
            "fonts.gstatic.com",
            "cdn.jsdelivr.net",
            "cdnjs.cloudflare.com",
            "unpkg.com",
        ):
            assert marker not in text, f"{path.name} reaches {marker}"


def test_no_route_returns_the_key_or_any_prefix_of_it(vault, app):
    """Every registered route, including the error paths.

    Walked from the app's own routing table rather than a hand-kept list, so a
    route added later is covered without anyone remembering to add it here.
    """
    from fastapi.testclient import TestClient

    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    proposed = claim(device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c")
    vault.append(proposed)
    vault.append(confirm(device, proposed.id, ts=on_day(3)))

    redaction.register(KEY)
    try:
        with TestClient(app) as client:
            # Make the endpoint state as informative as it ever gets.
            from agent.errors import AuthRejected

            app.state.record.record_endpoint_error(
                AuthRejected(f"401 from the box, Authorization: Bearer {KEY}")
            )

            checked = 0
            for route in _all_routes(app):
                path = getattr(route, "path", "")
                methods = getattr(route, "methods", set()) or set()
                if "GET" not in methods:
                    continue
                url = (
                    path.replace("{entity_id:path}", "med:perindopril")
                    .replace("{token}", "a3f91c")
                    .replace("{path:path}", "index.html")
                )
                if "{" in url:
                    continue
                response = client.get(url)
                checked += 1
                _assert_no_key(response.text, url)
                _assert_no_key(json.dumps(dict(response.headers)), f"{url} headers")

            # The error paths too, which is where httpx would put headers.
            for url in ("/api/wiki/med:nonexistent", "/api/artifact/zzzzzz",
                        "/api/wiki/not-a-subject", "/api/nonsense"):
                response = client.get(url)
                checked += 1
                _assert_no_key(response.text, url)

            assert checked >= 12, "the sweep did not reach the routes"
    finally:
        redaction.forget_all()


def test_the_key_is_never_written_anywhere_in_the_vault(vault):
    """After a full run, walk the folder. ``config.toml``, the log, the logs."""
    redaction.register(KEY)
    try:
        with api_client(vault) as client:
            client.post("/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")})
            client.get("/api/health")
            client.post("/api/rebuild")

        for path in sorted(vault.root.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            assert KEY.encode() not in data, f"a credential reached {path}"
    finally:
        redaction.forget_all()


def test_api_responses_are_not_cached_by_anything_in_between(client):
    response = client.get("/api/health")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def _all_routes(app):
    """Every route, including ones inside included routers.

    FastAPI wraps an included router rather than flattening it into
    ``app.routes``, so walking that list alone reaches the handful of routes
    registered directly on the app and silently misses every one that came from
    a router — which is all of them. Recursing is what makes this sweep cover
    the surface it claims to.
    """

    def walk(routes):
        for route in routes:
            # FastAPI wraps `include_router` results; the real routes hang off
            # `original_router`. Both spellings are handled so this keeps
            # working either way round.
            nested = getattr(route, "routes", None)
            if nested is None:
                inner = getattr(route, "original_router", None)
                nested = getattr(inner, "routes", None) if inner is not None else None
            if nested:
                yield from walk(nested)
            else:
                yield route

    return list(walk(app.routes))


def _assert_no_key(text: str, where: str) -> None:
    assert KEY not in text, f"the key reached {where}"
    for length in range(12, len(KEY) + 1):
        assert KEY[:length] not in text, f"a {length}-character prefix reached {where}"
