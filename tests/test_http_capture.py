"""``/api/capture`` stores the bytes and answers, whatever the box is doing.

The requirement is stated twice in the specs, from both directions.
``CLAUDE.md``: "``/api/capture`` must return before any inference runs. The user
is in a waiting room; never block the UI on a 9B model." ``MODELS.md``: "Capture
never depends on the endpoint. Artefacts land in ``raw/`` and the job queue
persists to ``.agent/jobs.jsonl`` regardless. A capture that fails because a Mac
was asleep is unacceptable."

So the central test here refuses the endpoint at the socket level and asserts
that a capture still succeeds, the bytes are in ``raw/``, the event is in the
log and the job is on disk.

The other thing under test is ``captured_ts``. A browser can tell us
``File.lastModified``, which is a filesystem mtime — rewritten by every
download, copy and sync client. Writing it into ``captured_ts`` would date a
photograph by when its file was last touched, and nothing about the record would
look wrong. It is null on every path this route serves, and is recorded as a
hint under its own name.
"""

from __future__ import annotations

import json

from agent.extract import jobs as jobs_mod
from agent.ingest import naming

from .conftest import JPEG, PDF, PNG


def test_capture_stores_bytes_and_returns_before_anything_reads_them(vault, client):
    response = client.post(
        "/api/capture",
        files={"files": ("script.jpg", JPEG, "image/jpeg")},
        data={"source": "drop"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 1
    assert body["failed"] == 0

    result = body["results"][0]
    assert result["status"] == "stored"
    assert result["mime"] == "image/jpeg"
    stored = vault.root / result["path"]
    assert stored.is_file()
    assert stored.read_bytes() == JPEG

    # The event is in the log, and nothing has read the file.
    events = list(vault.read().events)
    assert [e.type for e in events] == ["artifact.ingested"]
    assert not any(e.type == "extraction.completed" for e in events)

    # The job is on disk, so it survives a restart.
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    assert [job.artifact for job in queue.runnable()] == [result["short"]]
    assert body["queue_depth"] == 1


def test_capture_survives_an_endpoint_refused_at_the_socket(vault, monkeypatch):
    """The Mac is asleep. The capture must not know or care.

    The endpoint is not merely unconfigured here — every attempt to open a
    client raises, so any code path that reached for one on the way in would
    turn a capture into a failure.
    """
    import agent.extract.session as session_mod

    from agent.errors import EndpointUnreachable

    def refuse(*args, **kwargs):
        raise EndpointUnreachable("connection refused")

    monkeypatch.setattr(session_mod, "open_client", refuse)

    from .conftest import api_client

    with api_client(vault) as client:
        response = client.post(
            "/api/capture",
            files={"files": ("path.pdf", PDF, "application/pdf")},
        )
    assert response.status_code == 202
    short = response.json()["results"][0]["short"]

    assert (vault.root / response.json()["results"][0]["path"]).is_file()
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    assert [job.artifact for job in queue.runnable()] == [short]


def test_captured_ts_is_null_for_every_web_capture_path(vault, client):
    """A browser cannot know when a photograph was taken.

    ``File.lastModified`` is a filesystem mtime and is not capture time. Ingest
    time is not capture time either. An unknown timestamp is written as explicit
    null; substituting a neighbour is wrong by however far the two differ and is
    invisible until months of timeline are misdated.
    """
    for source in ("upload", "paste", "drop"):
        client.post(
            "/api/capture",
            files={"files": (f"{source}.png", PNG + source.encode(), "image/png")},
            data={"source": source},
        )

    ingested = [e for e in vault.read().events if e.type == "artifact.ingested"]
    assert len(ingested) == 3
    for event in ingested:
        assert event.payload["captured_ts"] is None
        assert event.payload["artifact_ts"] is None
        # Always known, and the one that drives the filename — never
        # `captured_ts`, which is exactly the substitution this test exists for.
        stamp = event.payload["ingested_ts"]
        assert stamp is not None
        assert naming.filename_ts(stamp) in event.payload["path"]


def test_capture_records_the_source_and_the_original_filename(vault, client):
    client.post(
        "/api/capture",
        files={"files": ("Dr Nguyen letter.pdf", PDF, "application/pdf")},
        data={"source": "paste", "note": "from the clinic portal"},
    )
    event = next(e for e in vault.read().events if e.type == "artifact.ingested")
    capture = event.payload["capture"]
    assert capture["source"] == "paste"
    assert capture["original_filename"] == "Dr Nguyen letter.pdf"
    assert capture["note"] == "from the clinic portal"


def test_duplicate_content_is_reseen_and_not_stored_twice(vault, client):
    """People photograph the same script twice. That is not an error."""
    first = client.post("/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")})
    second = client.post("/api/capture", files={"files": ("b.jpg", JPEG, "image/jpeg")})

    assert first.json()["results"][0]["status"] == "stored"
    assert second.json()["results"][0]["status"] == "reseen"
    assert first.json()["results"][0]["path"] == second.json()["results"][0]["path"]

    types = [e.type for e in vault.read().events]
    assert types == ["artifact.ingested", "artifact.reseen"]

    # One artefact, one job. The second arrival is evidence the script was still
    # in the patient's hand, not a second thing to read.
    queue = jobs_mod.Queue.open(vault.root / ".agent")
    assert len(queue.runnable()) == 1


def test_one_bad_file_does_not_lose_the_others(vault, client):
    """A partial success the caller cannot see is worse than a failure it can."""
    response = client.post(
        "/api/capture",
        files=[
            ("files", ("good.jpg", JPEG, "image/jpeg")),
            ("files", ("empty.jpg", b"", "image/jpeg")),
            ("files", ("also-good.pdf", PDF, "application/pdf")),
        ],
    )
    assert response.status_code == 207
    body = response.json()
    assert body["accepted"] == 2
    assert body["failed"] == 1

    failed = next(r for r in body["results"] if r["status"] == "failed")
    assert failed["filename"] == "empty.jpg"
    assert "zero bytes" in failed["error"]
    assert len(body["queued"]) == 2


def test_capture_refuses_a_source_it_does_not_serve(client):
    """``camera`` and ``recorder`` know a capture time. This route does not."""
    response = client.post(
        "/api/capture",
        files={"files": ("a.jpg", JPEG, "image/jpeg")},
        data={"source": "camera"},
    )
    assert response.status_code == 400
    assert "unknown capture source" in response.json()["detail"]


def test_capture_with_no_files_is_a_message_not_a_crash(client):
    response = client.post("/api/capture", files=[], data={"source": "drop"})
    assert response.status_code in (400, 422)


def test_typed_notes_append_without_queueing_anything(vault, client):
    """A note has no bytes to keep and nothing for a model to read."""
    response = client.post(
        "/api/capture/text", data={"text": "  Headaches started around Easter.  "}
    )
    assert response.status_code == 202
    assert response.json()["text"] == "Headaches started around Easter."

    events = list(vault.read().events)
    assert [e.type for e in events] == ["note.recorded"]
    assert events[0].actor == "user"
    assert jobs_mod.Queue.open(vault.root / ".agent").depth() == 0


def test_an_empty_note_records_nothing(client):
    response = client.post("/api/capture/text", data={"text": "   "})
    assert response.status_code == 400
    assert "empty note" in response.json()["detail"]


def test_capture_does_not_write_the_wiki(vault, client):
    """Only a rebuild writes ``wiki/``. Capture appends and returns.

    A capture that regenerated the wiki would put a whole projection on the path
    of an upload, and would churn a synced folder on every drop.
    """
    client.post("/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")})
    assert list((vault.root / "wiki").rglob("*.md")) == []


def test_the_response_says_nothing_has_been_read(client):
    """The promise the route makes is stated in the response, every time."""
    body = client.post(
        "/api/capture", files={"files": ("a.jpg", JPEG, "image/jpeg")}
    ).json()
    assert "Nothing has been read yet" in body["note"]
    assert body["note"].startswith("1 file stored and queued")
    assert json.dumps(body).count("queued") >= 1


def test_the_note_never_says_stored_when_nothing_was(client):
    """A reassuring sentence beside `accepted: 0` is what a skimmer reads.

    Found by reading a real response: a vault that could not append returned
    `accepted: 0, failed: 1` under the words "Stored and queued."
    """
    body = client.post(
        "/api/capture", files={"files": ("empty.jpg", b"", "image/jpeg")}
    ).json()

    assert body["accepted"] == 0
    assert "stored and queued" not in body["note"].lower()
    assert "Nothing was stored" in body["note"]


def test_a_partial_batch_says_how_many_of_each(client):
    body = client.post(
        "/api/capture",
        files=[
            ("files", ("good.jpg", JPEG, "image/jpeg")),
            ("files", ("empty.jpg", b"", "image/jpeg")),
        ],
    ).json()

    assert body["note"].startswith("1 file stored and queued")
    assert "1 could not be stored" in body["note"]
