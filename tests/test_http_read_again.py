"""A reading that stopped for something a person fixed can be asked for again.

Messages that say "restore it, then read it again from its page" need that
page to have the button, or they are the terminal instruction they replaced
wearing different words.
"""

from __future__ import annotations

from agent.extract import jobs as jobs_mod

from .conftest import JPEG


def _capture(client):
    response = client.post("/api/capture", files={"files": ("script.jpg", JPEG, "image/jpeg")})
    return response.json()["results"][0]["short"]


def _stop(vault, short, reason="the bytes are not in raw/"):
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    job = queue.for_artifact(short) or queue.add(short)
    queue.update(job, jobs_mod.NEEDS_ATTENTION, reason)


def test_a_stopped_reading_offers_to_try_again(client, vault):
    short = _capture(client)
    _stop(vault, short)

    meta = client.get(f"/api/artifact/{short}/meta").json()
    assert meta["reading"]["may_retry"] is True

    asked = client.post(f"/api/artifact/{short}/read-again")
    assert asked.status_code == 200, asked.text
    job = jobs_mod.Queue.open(vault.root / ".agent").for_artifact(short)
    assert job.state == jobs_mod.QUEUED
    assert job.attempts == 0

    after = client.get(f"/api/artifact/{short}/meta").json()
    assert after["reading"]["may_retry"] is False


def test_only_a_reading_waiting_on_a_person_is_offered(client, vault):
    short = _capture(client)
    meta = client.get(f"/api/artifact/{short}/meta").json()
    assert meta["reading"]["may_retry"] is False

    refused = client.post(f"/api/artifact/{short}/read-again")
    assert refused.status_code == 409
    assert "nothing to try again" in refused.json()["detail"]
