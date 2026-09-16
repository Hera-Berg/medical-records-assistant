"""``/api/reader`` and ``/api/settings/reader``.

What matters here is what does *not* happen: no request is made for a model
file by anything but a press of the download button, the choice never lands in
``config.toml``, and the speed a screen quotes is this machine's own.
"""

from __future__ import annotations

import pytest

from agent.events import envelope
from agent.extract import propose
from agent.runtime import choice, download, manifest, platforms
from agent.server import reader_view

from .conftest import api_client


def test_the_reader_screen_says_what_a_download_would_be_before_one_starts(vault, monkeypatch):
    started = []
    monkeypatch.setattr(download.Downloader, "start", lambda self: started.append(1) or True)
    with api_client(vault) as client:
        body = client.get("/api/reader").json()
    assert started == [], "looking at the screen must not start anything"

    assert body["choice"]["reads_on"] == choice.THIS_COMPUTER
    files = body["files"]
    expected = sum(b.size for b in manifest.required(platforms.current(), reads_here=True))
    assert files["total_bytes"] == expected
    assert set(files["hosts"]) == {"github.com", "huggingface.co"}
    assert "Nothing from your record is sent" in files["explanation"]
    assert not files["location"].startswith(str(vault.root))
    assert body["platform"]["verified"] is (platforms.current() in platforms.VERIFIED)
    assert files["state"] == download.IDLE


def test_the_download_starts_only_when_asked(vault, monkeypatch):
    started = []
    monkeypatch.setattr(download.Downloader, "start", lambda self: started.append(1) or True)
    monkeypatch.setattr(download.Downloader, "join", lambda self, timeout=None: None)
    with api_client(vault) as client:
        response = client.post("/api/reader/download")
    assert response.status_code == 200
    assert started == [1]


def test_a_demo_record_downloads_nothing(vault, monkeypatch):
    monkeypatch.setattr(type(vault), "is_demo", property(lambda self: True))
    with api_client(vault) as client:
        assert client.post("/api/reader/download").status_code == 409


def test_choosing_another_computer_writes_this_machine_and_never_config_toml(vault):
    config = (vault.root / "config.toml").read_bytes()
    with api_client(vault) as client:
        body = client.post(
            "/api/settings/reader",
            json={"reads_on": "another-computer", "sleep_after_minutes": 30},
        ).json()
    assert body["choice"] == {
        "reads_on": "another-computer",
        "label": "Read on another computer",
        "sleep_after_minutes": 30,
        "source": "file",
    }
    assert (vault.root / "config.toml").read_bytes() == config
    assert choice.path().exists()
    # Speech is still read here, so its files are still wanted.
    assert body["files"]["total_bytes"] == manifest.SPEECH.size


@pytest.mark.parametrize("minutes", [-5, 100000])
def test_sleep_minutes_out_of_range_are_refused(vault, minutes):
    with api_client(vault) as client:
        response = client.post(
            "/api/settings/reader", json={"reads_on": "this-computer", "sleep_after_minutes": minutes}
        )
    assert response.status_code == 400


def _reading(device: str, seconds: float, ts: str, kind: str = "bundled") -> envelope.Event:
    return envelope.new(
        propose.EXTRACTION_COMPLETED,
        device,
        ts=ts,
        provenance={"model": manifest.ALIAS, "artifact": "a3f91c"},
        payload={"artifact": "a3f91c", "runtime": {"kind": kind, "elapsed_s": seconds}},
    )


def test_speed_is_measured_from_this_devices_own_readings():
    events = [
        _reading("laptop-aaaa", 70.0, "2026-09-10T10:00:00Z"),
        _reading("laptop-aaaa", 65.0, "2026-09-10T10:02:00Z"),
        _reading("laptop-aaaa", 80.0, "2026-09-10T10:04:00Z"),
        _reading("desktop-bbbb", 9.0, "2026-09-10T10:05:00Z"),
        _reading("laptop-aaaa", 12.0, "2026-09-10T10:06:00Z", kind="endpoint"),
    ]
    measured = reader_view.speed(events, "laptop-aaaa", platforms.LINUX_X64, waiting=6)
    assert measured["measured_seconds"] == 70
    assert measured["measured_from"] == 3
    assert measured["sentence"] == (
        "Usually about 1 minute 10 seconds a document on this computer. "
        "6 documents waiting — roughly 7 minutes in all."
    )


def test_before_anything_is_read_the_range_is_honest():
    sentence = reader_view.speed([], "laptop-aaaa", platforms.LINUX_X64, waiting=1)["sentence"]
    assert sentence == (
        "Reading on this computer takes about 1 to 4 minutes a document. 1 document waiting."
    )
    apple = reader_view.speed([], "mac-aaaa", platforms.MACOS_ARM64, waiting=0)["sentence"]
    assert "10 to 30 seconds" in apple


def test_health_carries_the_speed_sentence_when_reading_here(vault):
    with api_client(vault) as client:
        body = client.get("/api/health").json()
    assert body["reading"]["sentence"].startswith("Reading on this computer takes")


def test_no_route_here_returns_a_key(vault):
    with api_client(vault) as client:
        body = client.get("/api/reader").text
    assert "LLAMA_API_KEY" not in body and "Bearer" not in body
