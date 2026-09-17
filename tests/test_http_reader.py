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


@pytest.fixture
def no_real_download(monkeypatch):
    started = []
    monkeypatch.setattr(download.Downloader, "start", lambda self: started.append(1) or True)
    monkeypatch.setattr(download.Downloader, "join", lambda self, timeout=None: None)
    return started


def test_a_download_without_the_agreed_size_starts_nothing(vault, no_real_download):
    """Guarded by asking, on every vault: the exact bytes must come back."""
    with api_client(vault) as client:
        remaining = client.get("/api/reader").json()["files"]["remaining_bytes"]
        refused = client.post("/api/reader/download", json={})
        stale = client.post("/api/reader/download", json={"confirm_bytes": remaining - 1})
    assert no_real_download == []
    for response in (refused, stale):
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert f"{remaining:,} bytes" in detail
        assert "huggingface.co" in detail and "github.com" in detail


def test_the_download_starts_once_the_exact_size_is_agreed(vault, no_real_download):
    with api_client(vault) as client:
        remaining = client.get("/api/reader").json()["files"]["remaining_bytes"]
        response = client.post("/api/reader/download", json={"confirm_bytes": remaining})
    assert response.status_code == 200
    assert no_real_download == [1]


def test_a_demo_record_downloads_the_same_way_as_any_other(vault, monkeypatch, no_real_download):
    monkeypatch.setattr(type(vault), "is_demo", property(lambda self: True))
    with api_client(vault) as client:
        remaining = client.get("/api/reader").json()["files"]["remaining_bytes"]
        assert client.post("/api/reader/download", json={}).status_code == 409
        assert client.post("/api/reader/download", json={"confirm_bytes": remaining}).status_code == 200
    assert no_real_download == [1]
    assert not choice.path().exists(), "a demo still never settles this machine's choice"


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
        "model": manifest.DEFAULT_VISION_MODEL,
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


# --- choosing a local model --------------------------------------------------


@pytest.fixture
def measured(tmp_path, monkeypatch):
    """A measurements file of the test's own, so the real one can change freely."""
    from agent.runtime import measurements

    path = tmp_path / "measurements.json"
    monkeypatch.setattr(measurements, "PATH", path)
    measurements.record(
        manifest.QWEN_4B.alias,
        measurements.Accuracy(correct=7, abstained=0, wrong=5, fixtures=10, prompt_version=2, measured="2026-09-17"),
        platforms.current(),
        measurements.Speed(seconds_per_document=112, generation_tokens_per_second=8.0,
                           machine="an Intel Core Ultra 7 155U machine", measured="2026-09-17"),
        path=path,
    )
    import json as json_mod

    data = json_mod.loads(path.read_text())
    data["models"][manifest.QWEN_4B.alias]["known_failures"] = ["It read metformin as mefenamic acid."]
    path.write_text(json_mod.dumps(data))
    return path


def _models(client):
    return {entry["id"]: entry for entry in client.get("/api/reader").json()["models"]}


def test_the_list_shows_every_number_at_the_point_of_choosing(vault, measured, monkeypatch):
    monkeypatch.setattr(platforms, "total_memory_bytes", lambda: 32 * 1024**3)
    with api_client(vault) as client:
        models = _models(client)

    small, large = models[manifest.QWEN_4B.id], models[manifest.QWEN_9B.id]
    assert small["recommended"] and small["current"], "the recommended model is preselected"
    assert not large["current"]
    assert small["size_bytes"] == manifest.QWEN_4B.bundle.size
    assert small["ram_needed_bytes"] == 8 * 1024**3 and large["ram_needed_bytes"] == 16 * 1024**3
    assert "7 read correctly, 0 left for you to check, 5 wrong" in small["accuracy"]["sentence"]
    assert "measured on an Intel Core Ultra 7 155U machine" in small["speed"]["sentence"]
    assert small["known_failures"] == ["It read metformin as mefenamic acid."]
    # Not measured is said, never left blank or guessed.
    assert large["accuracy"]["sentence"] == "Accuracy not measured."
    assert large["speed"]["sentence"].startswith("Speed not measured on")
    assert small["memory_warning"] is None and large["memory_warning"] is None


def test_too_little_memory_is_warned_about_and_never_refused(vault, measured, monkeypatch, no_real_download):
    monkeypatch.setattr(platforms, "total_memory_bytes", lambda: 8 * 1024**3)
    with api_client(vault) as client:
        warning = _models(client)[manifest.QWEN_9B.id]["memory_warning"]
        chosen = client.post(
            "/api/settings/reader",
            json={"reads_on": "this-computer", "model": manifest.QWEN_9B.id},
        )
    assert "8 GB of memory" in warning and "about 16 GB" in warning
    assert "It will still run" in warning
    assert chosen.status_code == 200
    body = chosen.json()
    assert body["choice"]["model"] == manifest.QWEN_9B.id
    # The files wanted now are the chosen model's, and the size confirmation asks
    # for exactly those bytes.
    expected = sum(b.size for b in manifest.required(platforms.current(), True, manifest.QWEN_9B.id))
    assert body["files"]["total_bytes"] == expected
    assert manifest.QWEN_9B.bundle.size <= body["files"]["remaining_bytes"]


def test_an_unknown_model_is_refused(vault):
    with api_client(vault) as client:
        response = client.post(
            "/api/settings/reader", json={"reads_on": "this-computer", "model": "qwen9000"}
        )
    assert response.status_code == 400


def test_a_download_size_is_quoted_in_decimal_gigabytes(vault, no_real_download):
    """6,598,688,544 bytes is 6.6 GB. Calling 1024³ a GB made it "6.1 GB"."""
    with api_client(vault) as client:
        client.post("/api/settings/reader", json={"reads_on": "this-computer", "model": manifest.QWEN_9B.id})
        remaining = client.get("/api/reader").json()["files"]["remaining_bytes"]
        refused = client.post("/api/reader/download", json={})
    assert f"({remaining / 1000**3:.1f} GB)" in refused.json()["detail"]
    assert f"({remaining / 1024**3:.1f} GB)" not in refused.json()["detail"]
