"""Only pages this server served may read or change the record.

A loopback bind keeps other machines out; it does not keep other *web pages*
out. See ``agent.server.origin`` for the two attacks — DNS rebinding and
cross-site form posts — and why each check is shaped as it is.
"""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest

from agent.server import origin
from .conftest import api_client


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:7777", "localhost:7777", "[::1]:7777", "127.0.0.1", "LOCALHOST:5173", "127.0.0.2:80"],
)
def test_loopback_hosts_are_answered(host):
    assert origin.host_allowed(host)


@pytest.mark.parametrize(
    "host",
    [None, "", "evil.example", "evil.example:7777", "127.0.0.1.evil.example",
     "localhost.evil.example:7777", "192.168.1.5:7777", "0.0.0.0:7777", "testserver"],
)
def test_a_rebinding_hostname_is_refused(host):
    assert not origin.host_allowed(host)


@pytest.mark.parametrize(
    "origin_header, fetch_site, allowed",
    [
        ("http://127.0.0.1:7777", "same-origin", True),
        ("http://localhost:5173", None, True),
        ("https://evil.example", "cross-site", False),
        ("http://127.0.0.1.evil.example", None, False),
        ("null", None, False),
        (None, "cross-site", False),
        (None, "same-site", False),
        (None, "same-origin", True),
        (None, "none", True),
        # Neither header: not a request a web page can make. The CLI, curl.
        (None, None, True),
    ],
)
def test_state_changes_need_a_loopback_origin(origin_header, fetch_site, allowed):
    assert origin.origin_allowed(origin_header, fetch_site) is allowed


def test_a_rebound_page_cannot_read_the_record(vault):
    with api_client(vault) as client:
        assert client.get("/api/wiki").status_code == 200
        refused = client.get("/api/wiki", headers={"host": "evil.example:7777"})
    assert refused.status_code == 421
    assert refused.json()["detail"] == origin.HOST_REFUSED
    assert "medication" not in refused.text


def test_a_cross_site_form_cannot_capture(vault):
    with api_client(vault) as client:
        refused = client.post(
            "/api/capture",
            files={"files": ("note.txt", b"I stopped taking everything", "text/plain")},
            data={"source": "upload"},
            headers={"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
        )
    assert refused.status_code == 403
    assert refused.json()["detail"] == origin.ORIGIN_REFUSED
    assert not any((vault.root / "raw").rglob("*.txt"))
    assert not any(vault.events_dir.glob("*.jsonl"))


def test_the_apps_own_page_can_capture(vault):
    with api_client(vault) as client:
        accepted = client.post(
            "/api/capture",
            files={"files": ("note.txt", b"headache since Tuesday", "text/plain")},
            data={"source": "upload"},
            headers={"origin": "http://127.0.0.1:7777", "sec-fetch-site": "same-origin"},
        )
    assert accepted.status_code < 300, accepted.text


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_guard_holds_on_a_real_socket(vault):
    """TestClient builds its own scope. A real server parses real headers."""
    import uvicorn

    from agent.server import create_app

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(vault, worker=False), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}"
        assert httpx.get(f"{base}/api/build").status_code == 200
        rebound = httpx.get(f"{base}/api/build", headers={"host": f"evil.example:{port}"})
        assert rebound.status_code == 421
        cross = httpx.post(
            f"{base}/api/rebuild", headers={"origin": "https://evil.example"}
        )
        assert cross.status_code == 403
    finally:
        server.should_exit = True
        thread.join(timeout=20)
