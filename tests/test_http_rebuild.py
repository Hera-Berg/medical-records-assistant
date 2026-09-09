"""``POST /api/rebuild`` regenerates the wiki without taking anything with it.

``CLAUDE.md``'s API sketch says "wipe wiki/ + index, replay events", and the
settled decision from phase 3 says how far the wipe goes: **only files the
previous manifest lists, whose bytes still match.** A missing manifest
authorises zero deletions.

Those two sentences are in tension only if "wipe" is read as "empty the
directory", and reading it that way is how a rebuild after a restore takes the
user's own notes with it. The tests below pin the safer reading and, just as
importantly, pin that the files left behind are *reported* — answering "why is
my file still there" with a sentence beats answering "where did my file go" with
an apology.
"""

from __future__ import annotations

from agent.server.index import INDEX_FILENAME

from .conftest import claim, confirm, ingested, on_day


def _record(vault):
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    dose = claim(device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c")
    vault.append(dose)
    vault.append(confirm(device, dose.id, ts=on_day(3)))
    return vault


def test_rebuild_writes_the_wiki_from_the_log(vault, client):
    _record(vault)
    assert list((vault.root / "wiki").rglob("*.md")) == []

    body = client.post("/api/rebuild").json()
    assert "wiki/medications/perindopril.md" in body["written"]
    assert (vault.root / "wiki" / "medications" / "perindopril.md").is_file()
    assert body["stats"]["entities"] == 1


def test_rebuild_is_idempotent(vault, client):
    _record(vault)
    client.post("/api/rebuild")
    second = client.post("/api/rebuild").json()

    assert second["written"] == []
    assert "wiki/medications/perindopril.md" in second["unchanged"]
    assert second["removed"] == []


def test_a_file_the_rebuild_did_not_write_is_reported_not_removed(vault, client):
    """A hand-dropped note in ``wiki/`` is the user's. It stays."""
    _record(vault)
    client.post("/api/rebuild")

    stray = vault.root / "wiki" / "my own notes.md"
    stray.write_text("questions for Dr Nguyen\n", encoding="utf-8")

    body = client.post("/api/rebuild").json()
    assert stray.is_file()
    assert stray.read_text(encoding="utf-8") == "questions for Dr Nguyen\n"
    assert "wiki/my own notes.md" in body["foreign"]
    assert "wiki/my own notes.md" not in body["removed"]


def test_a_generated_page_edited_by_hand_is_left_alone_and_named(vault, client):
    """That edit is theirs. Losing it silently is worse than a stale file."""
    _record(vault)
    client.post("/api/rebuild")

    page = vault.root / "wiki" / "medications" / "perindopril.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nmy own note\n", encoding="utf-8")

    body = client.post("/api/rebuild").json()
    assert "my own note" in page.read_text(encoding="utf-8")
    assert "wiki/medications/perindopril.md" in body["modified"]
    assert any("regenerated wholesale" in problem for problem in body["problems"])


def test_a_missing_manifest_authorises_no_deletions(vault, client):
    """An absent manifest is not evidence of an empty folder."""
    _record(vault)
    client.post("/api/rebuild")

    manifest = vault.root / ".agent" / "projection" / "manifest.json"
    manifest.unlink()
    stray = vault.root / "wiki" / "left-behind.md"
    stray.write_text("still here\n", encoding="utf-8")

    body = client.post("/api/rebuild").json()
    assert body["manifest_missing"] is True
    assert body["removed"] == []
    assert stray.is_file()


def test_rebuild_touches_neither_raw_nor_events(vault, client):
    _record(vault)
    raw_before = {p: p.read_bytes() for p in (vault.root / "raw").rglob("*") if p.is_file()}
    events_before = {
        p: p.read_bytes() for p in (vault.root / "events").rglob("*") if p.is_file()
    }

    client.post("/api/rebuild")

    assert {p: p.read_bytes() for p in (vault.root / "raw").rglob("*") if p.is_file()} == raw_before
    assert {
        p: p.read_bytes() for p in (vault.root / "events").rglob("*") if p.is_file()
    } == events_before


def test_rebuild_discards_the_index_and_it_comes_back(vault, client):
    """The index genuinely is wiped, because it genuinely is a cache."""
    _record(vault)
    client.get("/api/timeline")
    index = vault.root / ".agent" / INDEX_FILENAME
    assert index.is_file()

    client.post("/api/rebuild")
    # Rebuilt within the same request, from the fresh projection.
    assert index.is_file()
    assert client.get("/api/timeline").json()["total"] >= 1


def test_the_response_says_what_was_left_alone_and_why(vault, client):
    _record(vault)
    body = client.post("/api/rebuild").json()
    assert "were left in place" in body["note"]
    for key in ("written", "unchanged", "removed", "foreign", "modified"):
        assert key in body


def test_rebuild_reports_anomalies_and_the_review_queue(vault, client):
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    # Unconfirmed and high-consequence: queued, never applied.
    vault.append(claim(device, "allergy:penicillin", "reaction", "hives", ts=on_day(2)))

    body = client.post("/api/rebuild").json()
    assert body["review"].get("high", 0) >= 1
    assert "hives" not in str(body["review"])
    assert (vault.root / "wiki" / "allergies").exists() is False
