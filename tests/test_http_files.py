"""Browsing the folder through the app, and what the app will not remove from it.

The file browser is the first surface in this project that **deletes**, which
makes it the first one where a bug loses a record rather than renders it badly.
Two properties carry that weight and both are asserted here directly:

* **The event log cannot be deleted through the API.** Invariant 1 says the log
  is the only source of truth and everything else is derived from it. Every
  other guarantee in the system — byte-identical rebuild, corrections
  outranking extractions, absence of evidence never meaning absence — is a
  statement about replaying that log. A route that can unlink a shard can
  destroy all of them with one mis-typed query string.
* **No path escapes the vault.** The path arrives from a query string. It is
  checked by shape and then again after resolution, so neither ``..`` nor a
  symlink planted in a synced folder reaches outside the root.

The rest is about the browser being honest: a refusal says why in a sentence, a
truncated file says it was truncated, and a raw artefact says how much of the
record was read off it *before* someone deletes it.
"""

from __future__ import annotations

import json

import pytest

from agent.server import files as files_mod

from .conftest import claim, confirm, ingested, on_day


#: Where :func:`ingested` records its artefact, and so where the bytes must be.
RAW_MONTH = "raw/2026/09"


@pytest.fixture
def record(vault):
    """A small record with an artefact two claims were read off.

    The bytes are written where the log says they are. A fixture that appends
    the event without the file would be testing the browser against a folder no
    real vault ever has — and the missing-bytes case has its own test.
    """
    device = vault.identity.id
    event = ingested(device, "a3f91c", ts=on_day(1), artifact_ts="2026-06-04T00:00:00Z")
    stored = vault.root / event.payload["path"]
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(b"\xff\xd8\xff\xe0 not really a photograph")
    (vault.root / event.payload["sidecar"]).write_text(
        json.dumps({"short": "a3f91c", "mime": "image/jpeg"}), encoding="utf-8"
    )
    events = [event]
    dose = claim(
        device, "med:perindopril", "dose", {"amount": 5, "unit": "mg", "frequency": "daily"},
        ts=on_day(2), artifact="a3f91c",
    )
    started = claim(
        device, "med:perindopril", "started", "2024-11-02", ts=on_day(2, hour=11),
        artifact="a3f91c",
    )
    events += [dose, confirm(device, dose.id, ts=on_day(3)), started]
    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    return vault


# --- the log is not deletable ------------------------------------------------


def test_the_event_log_cannot_be_deleted_through_the_api(record, client):
    """Invariant 1, defended at the one route that unlinks files.

    The shard is still on disk afterwards, and the refusal says why rather than
    returning a bare 403 — this is the app telling its owner no about their own
    files, which it may only do with a reason.
    """
    shard = next((record.root / "events").glob("*.jsonl"))
    rel = f"events/{shard.name}"

    response = client.request("DELETE", "/api/files", params={"path": rel})

    assert response.status_code == 403
    assert shard.is_file(), "the event log was deleted through the API"
    detail = response.json()["detail"]
    assert "your record itself" in detail.lower()
    assert "file manager" in detail.lower()


def test_the_events_directory_itself_cannot_be_deleted(record, client):
    response = client.request("DELETE", "/api/files", params={"path": "events"})
    assert response.status_code == 403
    assert (record.root / "events").is_dir()


def test_a_listing_marks_every_shard_undeletable_with_its_reason(record, client):
    """The screen must not offer a button the API would refuse."""
    body = client.get("/api/files", params={"path": "events"}).json()
    assert body["entries"], "the log should not be empty"
    for entry in body["entries"]:
        assert entry["deletable"] is False
        assert entry["refusal"]
        assert entry["kind"] == "log"


# --- nothing escapes the vault ----------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../../../etc",
        "..",
        "wiki/../../..",
        "/etc/passwd",
        "wiki/../../secrets",
        "./../..",
    ],
)
def test_a_path_that_climbs_out_is_refused(record, client, path):
    response = client.get("/api/files", params={"path": path})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert any(word in detail for word in ("outside", "climbs", "absolute")), detail


def test_a_symlink_pointing_out_of_the_vault_is_not_followed(record, client, tmp_path):
    """A synced folder can contain a link somebody else's machine wrote.

    Shape alone does not catch this one — the path has no ``..`` in it — so the
    check has to happen after resolution as well as before it.
    """
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.md").write_text("not part of the record", encoding="utf-8")
    link = record.root / "wiki" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # pragma: no cover - platforms without symlink permission
        pytest.skip("symlinks not available here")

    listing = client.get("/api/files", params={"path": "wiki/escape"})
    assert listing.status_code == 400

    content = client.get("/api/files/content", params={"path": "wiki/escape/secret.md"})
    assert content.status_code == 400
    assert "not part of the record" not in json.dumps(content.json())


def test_deleting_through_a_symlink_out_of_the_vault_is_refused(record, client, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    victim = outside / "keep.md"
    victim.write_text("keep me", encoding="utf-8")
    link = record.root / "wiki" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:  # pragma: no cover
        pytest.skip("symlinks not available here")

    response = client.request(
        "DELETE", "/api/files", params={"path": "wiki/escape/keep.md"}
    )

    assert response.status_code == 400
    assert victim.is_file(), "a file outside the vault was deleted"


# --- derived files, which is what the feature is for -------------------------


def test_a_wiki_page_can_be_deleted_and_a_rebuild_puts_it_back(record, client):
    """The whole point of the tier: deleting derived output loses nothing."""
    page = record.root / "wiki" / "medications" / "perindopril.md"
    client.post("/api/rebuild")
    assert page.is_file()
    before = page.read_bytes()

    response = client.request(
        "DELETE", "/api/files", params={"path": "wiki/medications/perindopril.md"}
    )
    assert response.status_code == 200
    assert response.json()["rebuildable"] is True
    assert not page.exists()

    client.post("/api/rebuild")
    assert page.read_bytes() == before, "the rebuild did not restore it byte for byte"


def test_a_folder_with_anything_in_it_is_refused(record, client):
    client.post("/api/rebuild")
    response = client.request("DELETE", "/api/files", params={"path": "wiki/medications"})
    assert response.status_code == 403
    assert "not empty" in response.json()["detail"]
    assert (record.root / "wiki" / "medications").is_dir()


def test_an_empty_folder_of_your_own_can_go(record, client):
    (record.root / "wiki" / "spare").mkdir()
    response = client.request("DELETE", "/api/files", params={"path": "wiki/spare"})
    assert response.status_code == 200
    assert not (record.root / "wiki" / "spare").exists()


def test_the_config_is_refused_because_the_app_will_not_start_without_it(record, client):
    response = client.request("DELETE", "/api/files", params={"path": "config.toml"})
    assert response.status_code == 403
    assert "text editor" in response.json()["detail"]
    assert (record.root / "config.toml").is_file()


# --- originals: deletable, but never by accident -----------------------------


def test_an_original_says_how_much_of_the_record_was_read_off_it(record, client):
    """The count is the confirmation step's whole argument.

    "Delete this file?" and "two entries in your record were read off this file
    — delete it?" are different questions, and only one of them can be answered
    honestly.
    """
    entries = client.get("/api/files", params={"path": RAW_MONTH}).json()["entries"]
    artefact = next(e for e in entries if e["artifact"] == "a3f91c")
    assert artefact["deletable"] is True
    assert artefact["claims"] == 2


def test_deleting_an_original_retracts_nothing_from_the_record(record, client):
    """The claims stand; only the citation stops resolving.

    This is the behaviour that makes the delete safe to offer at all. A record
    that quietly dropped a dose because a photograph was deleted would be
    inferring from absence, which invariant 4 forbids.
    """
    client.post("/api/rebuild")
    before = client.get("/api/wiki/med:perindopril").json()

    entries = client.get("/api/files", params={"path": RAW_MONTH}).json()["entries"]
    artefact = next(e for e in entries if e["artifact"] == "a3f91c")
    assert client.request("DELETE", "/api/files", params={"path": artefact["path"]}).status_code == 200

    after = client.get("/api/wiki/med:perindopril").json()
    assert after["slots"] == before["slots"]

    # And the artefact route says the bytes are gone rather than 404ing, so the
    # citation still names something the record knows about.
    assert client.get("/api/artifact/a3f91c").status_code == 410


def test_a_sidecar_is_told_apart_from_the_artefact_it_describes(record, client):
    """Phase 2 appends ``.json`` to the full name so a scan can do exactly this."""
    entries = client.get("/api/files", params={"path": RAW_MONTH}).json()["entries"]
    kinds = {entry["name"]: entry["kind"] for entry in entries}
    assert any(kind == "sidecar" for kind in kinds.values())
    for name, kind in kinds.items():
        assert kind == ("sidecar" if name.endswith(".json") else "raw")


# --- reading files in place --------------------------------------------------


def test_a_wiki_page_can_be_read_as_text(record, client):
    client.post("/api/rebuild")
    body = client.get(
        "/api/files/content", params={"path": "wiki/medications/perindopril.md"}
    ).json()
    assert body["text"].startswith("---")
    assert body["truncated"] is False


def test_a_long_file_is_truncated_and_says_so(record, client, monkeypatch):
    """A file quietly cut off looks like a file that ends there."""
    monkeypatch.setattr(files_mod, "MAX_TEXT_BYTES", 64)
    big = record.root / "exports" / "long.txt"
    big.write_text("x" * 500, encoding="utf-8")

    body = client.get("/api/files/content", params={"path": "exports/long.txt"}).json()

    assert body["truncated"] is True
    assert body["bytes"] == 500
    assert body["shown"] == 64


def test_a_binary_file_is_not_offered_as_text(record, client):
    entries = client.get("/api/files", params={"path": RAW_MONTH}).json()["entries"]
    artefact = next(e for e in entries if e["artifact"] == "a3f91c")
    assert artefact["text"] is False

    response = client.get("/api/files/content", params={"path": artefact["path"]})
    assert response.status_code == 415
    assert "timeline" in response.json()["detail"]


def test_the_listing_is_ordered_the_same_way_on_every_machine(record, client):
    """Folders first, then code-point order. Never the locale's collation."""
    for name in ("zulu.md", "Alpha.md", "beta.md"):
        (record.root / "exports" / name).write_text("x", encoding="utf-8")
    (record.root / "exports" / "sub").mkdir()

    names = [e["name"] for e in client.get("/api/files", params={"path": "exports"}).json()["entries"]]

    assert names == ["sub", "Alpha.md", "beta.md", "zulu.md"]


def test_the_trail_leads_back_to_the_root(record, client):
    body = client.get("/api/files", params={"path": RAW_MONTH}).json()
    assert [crumb["path"] for crumb in body["crumbs"]] == ["", "raw", "raw/2026", RAW_MONTH]
