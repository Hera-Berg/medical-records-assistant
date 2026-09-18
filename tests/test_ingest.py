"""Ingest: originals in, one event out, nothing read and nothing invented."""

from __future__ import annotations

import json
import os

import pytest
import sys

from agent.errors import IngestError
from agent.events import envelope
from agent.ingest import (
    STATUS_RESEEN,
    STATUS_RESTORED,
    STATUS_STORED,
    CaptureContext,
    hashing,
    ingest_bytes,
    ingest_path,
)
from agent.ingest.store import ARTIFACT_MODE

from .conftest import HEIC, JPEG, PDF

FROZEN = "2026-09-08T14:32:11Z"


@pytest.fixture
def frozen_clock(monkeypatch):
    """Pin ingest time so filenames and shard months are predictable."""
    monkeypatch.setattr(envelope, "now_ts", lambda: FROZEN)
    return FROZEN


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "inbox" / "IMG_4821.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(JPEG)
    return path


def sidecar_of(vault, result):
    return json.loads((vault.root / (result.rel + ".json")).read_text(encoding="utf-8"))


def events_of(vault, type_):
    return [e for e in vault.read().events if e.type == type_]


# --- where the bytes land ---------------------------------------------------

def test_artefact_lands_in_the_month_folder_under_the_spec_name(vault, script, frozen_clock):
    result = ingest_path(vault, script)
    digest = hashing.hash_bytes(JPEG)
    assert result.rel == f"raw/2026/09/2026-09-08T1432Z_{digest[:6]}.jpg"
    assert (vault.root / result.rel).is_file()


def test_stored_bytes_are_the_original_bytes(vault, script):
    result = ingest_path(vault, script)
    assert (vault.root / result.rel).read_bytes() == JPEG


def test_the_source_file_is_copied_not_moved(vault, script):
    before = script.stat()
    ingest_path(vault, script)
    assert script.read_bytes() == JPEG
    assert script.stat().st_mode == before.st_mode


def test_extension_follows_the_content_not_the_name(vault, tmp_path):
    # An iPhone photo named .jpg that is really HEIC.
    source = tmp_path / "IMG_4821.jpg"
    source.write_bytes(HEIC)
    result = ingest_path(vault, source)
    assert result.rel.endswith(".heic")
    assert result.mime == "image/heic"


def test_stored_artefacts_are_read_only(vault, script):
    """A guardrail against accidental modification, and nothing more.

    The directory is still writable, so this is not immutability and must not be
    relied on as one.
    """
    result = ingest_path(vault, script)
    stored = vault.root / result.rel
    if sys.platform == "win32":
        # Windows keeps one read-only bit rather than a mode. chmod(0o400) sets
        # it, the file reads back 0444, and what the guardrail promises — that a
        # careless write does not land — still holds. CLAUDE.md: a guardrail
        # against accident, not a security control.
        assert not os.access(stored, os.W_OK)
        return
    assert stored.stat().st_mode & 0o777 == ARTIFACT_MODE


def test_sidecar_sits_beside_the_artefact_and_stays_writable(vault, script):
    result = ingest_path(vault, script)
    sidecar_path = vault.root / (result.rel + ".json")
    assert sidecar_path.is_file()
    if sys.platform == "win32":
        # The point of this test is that the sidecar, unlike the artefact, stays
        # writable — which is a thing Windows can say.
        assert os.access(sidecar_path, os.W_OK)
        return
    assert sidecar_path.stat().st_mode & 0o777 == 0o600


def test_sidecar_describes_the_artefact(vault, script, frozen_clock):
    result = ingest_path(vault, script)
    data = sidecar_of(vault, result)
    assert data["hash"] == hashing.hash_bytes(JPEG)
    assert data["hash_algo"] == "sha256"
    assert data["bytes"] == len(JPEG)
    assert data["mime"] == "image/jpeg"
    assert data["artifact"] == result.path.name
    assert data["capture"]["original_filename"] == "IMG_4821.jpg"
    assert data["event"] == result.event.id


# --- timestamps -------------------------------------------------------------

def test_ingest_time_is_recorded_and_used_for_the_filename(vault, script, frozen_clock):
    result = ingest_path(vault, script)
    assert result.event.payload["ingested_ts"] == FROZEN
    assert result.rel.split("/")[-1].startswith("2026-09-08T1432Z_")


def test_ingest_time_is_never_written_into_captured_ts(vault, script, frozen_clock):
    """Dragging in a three-day-old photo must not date it as captured today.

    This is the substitution the timestamp rule exists to prevent, and it is
    invisible afterwards: the record simply carries a wrong date.
    """
    result = ingest_path(vault, script)
    assert result.event.payload["captured_ts"] is None
    assert sidecar_of(vault, result)["captured_ts"] is None


def test_artifact_ts_is_null_until_something_reads_the_document(vault, script):
    result = ingest_path(vault, script)
    assert result.event.payload["artifact_ts"] is None
    assert sidecar_of(vault, result)["artifact_ts"] is None


def test_unknown_timestamps_are_explicit_nulls_not_absent_keys(vault, script):
    """An absent key reads as "never asked"; null says "genuinely unknown"."""
    result = ingest_path(vault, script)
    for field in ("captured_ts", "artifact_ts"):
        assert field in result.event.payload
        assert field in sidecar_of(vault, result)


def test_capture_time_is_recorded_when_the_caller_genuinely_knows_it(vault):
    # The live camera and recorder paths in later phases know this; ingest
    # never derives it.
    result = ingest_bytes(
        vault,
        JPEG,
        CaptureContext(source="camera", captured_ts="2026-09-05T08:15:00Z"),
    )
    assert result.event.payload["captured_ts"] == "2026-09-05T08:15:00Z"
    assert sidecar_of(vault, result)["captured_ts"] == "2026-09-05T08:15:00Z"


def test_a_malformed_capture_time_is_refused_rather_than_normalised(vault):
    with pytest.raises(IngestError):
        ingest_bytes(vault, JPEG, CaptureContext(source="camera", captured_ts="5 Sept"))


def test_source_mtime_is_kept_as_a_hint_and_named_as_one(vault, script):
    result = ingest_path(vault, script)
    capture = sidecar_of(vault, result)["capture"]
    assert capture["source_mtime_hint"] is not None
    assert "captured_ts" not in capture


# --- the event --------------------------------------------------------------

def test_one_ingested_event_is_appended(vault, script):
    result = ingest_path(vault, script)
    ingested = events_of(vault, "artifact.ingested")
    assert len(ingested) == 1
    assert ingested[0].id == result.event.id


def test_the_event_is_user_authored_and_carries_no_model_provenance(vault, script):
    result = ingest_path(vault, script)
    assert result.event.actor == "user"
    assert result.event.provenance is None


def test_the_event_records_where_the_bytes_and_sidecar_are(vault, script):
    result = ingest_path(vault, script)
    payload = result.event.payload
    assert (vault.root / payload["path"]).is_file()
    assert (vault.root / payload["sidecar"]).is_file()
    assert payload["hash"] == hashing.hash_bytes(JPEG)


def test_capture_context_is_recorded(vault, script):
    result = ingest_path(vault, script, CaptureContext(source="drop", note="from the fridge door"))
    assert result.event.payload["capture"]["source"] == "drop"
    assert result.event.payload["capture"]["note"] == "from the fridge door"


def test_ingest_does_not_ask_what_kind_of_document_this_is(vault, script):
    """Classification is the model's job in a later phase, not the user's now."""
    payload = ingest_path(vault, script).event.payload
    assert "category" not in payload
    assert "kind" not in payload
    assert set(payload["capture"]) == {"source", "original_filename", "source_mtime_hint", "note"}


def test_unknown_capture_source_is_refused(vault):
    with pytest.raises(IngestError):
        ingest_bytes(vault, JPEG, CaptureContext(source="telepathy"))


# --- deduplication ----------------------------------------------------------

def test_the_same_file_twice_is_stored_once(vault, script):
    first = ingest_path(vault, script)
    second = ingest_path(vault, script)
    assert first.status == STATUS_STORED
    assert second.status == STATUS_RESEEN
    assert second.rel == first.rel
    assert len(list((vault.root / "raw").rglob("*.jpg"))) == 1


def test_a_duplicate_records_that_it_was_seen_again(vault, script):
    """People photograph the same script twice; that the script was still in
    hand in September is information, not noise."""
    first = ingest_path(vault, script)
    ingest_path(vault, script, CaptureContext(source="drop", note="second photo"))
    reseen = events_of(vault, "artifact.reseen")
    assert len(reseen) == 1
    assert reseen[0].payload["hash"] == first.digest
    assert reseen[0].payload["first_ingested"]["event"] == first.event.id
    assert reseen[0].payload["capture"]["note"] == "second photo"


def test_a_duplicate_does_not_append_a_second_ingested_event(vault, script):
    ingest_path(vault, script)
    ingest_path(vault, script)
    assert len(events_of(vault, "artifact.ingested")) == 1


def test_a_duplicate_does_not_rewrite_the_sidecar(vault, script):
    first = ingest_path(vault, script)
    sidecar_path = vault.root / (first.rel + ".json")
    before = sidecar_path.read_bytes()
    ingest_path(vault, script, CaptureContext(source="paste"))
    assert sidecar_path.read_bytes() == before


def test_different_content_is_not_deduplicated(vault, tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(JPEG)
    b = tmp_path / "b.pdf"
    b.write_bytes(PDF)
    assert ingest_path(vault, a).status == STATUS_STORED
    assert ingest_path(vault, b).status == STATUS_STORED
    assert len(events_of(vault, "artifact.ingested")) == 2


# --- collisions -------------------------------------------------------------

def test_a_short_hash_collision_never_overwrites_an_existing_artefact(
    vault, script, frozen_clock
):
    digest = hashing.hash_bytes(JPEG)
    decoy = vault.root / "raw" / "2026" / "09" / f"2026-09-08T1432Z_{digest[:6]}.jpg"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_bytes(b"different content entirely")

    result = ingest_path(vault, script)

    assert decoy.read_bytes() == b"different content entirely"
    assert result.rel.endswith(f"2026-09-08T1432Z_{digest[:7]}.jpg")
    assert (vault.root / result.rel).read_bytes() == JPEG


# --- refusals ---------------------------------------------------------------

def test_an_empty_file_is_refused_and_leaves_nothing_behind(vault, tmp_path):
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    with pytest.raises(IngestError):
        ingest_path(vault, empty)
    # The month directory may have been created to stage into; no file survives.
    assert [p for p in (vault.root / "raw").rglob("*") if p.is_file()] == []
    assert vault.read().events == ()


def test_a_file_already_in_the_raw_store_is_refused(vault, script):
    result = ingest_path(vault, script)
    with pytest.raises(IngestError, match="already inside the vault"):
        ingest_path(vault, vault.root / result.rel)
    assert len(events_of(vault, "artifact.ingested")) == 1


def test_a_missing_file_is_refused(vault, tmp_path):
    with pytest.raises(IngestError):
        ingest_path(vault, tmp_path / "nothing-here.jpg")


def test_a_directory_is_refused(vault, tmp_path):
    with pytest.raises(IngestError):
        ingest_path(vault, tmp_path)


# --- crash safety -----------------------------------------------------------

def test_bytes_land_before_the_event_so_a_crash_never_orphans_a_citation(
    vault, script, monkeypatch
):
    """A file with no event is recoverable; an event with no file is a broken
    citation in the wiki. So the bytes go first, and a failure to record them
    leaves the file alone rather than deleting it."""
    def refuse(event):
        raise OSError("disk went away")

    monkeypatch.setattr(vault, "append", refuse)
    with pytest.raises(OSError):
        ingest_path(vault, script)

    stored = list((vault.root / "raw").rglob("*.jpg"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == JPEG
    assert events_of(vault, "artifact.ingested") == []


def test_the_same_file_after_a_crash_is_ingested_normally(vault, script, monkeypatch):
    def refuse(event):
        raise OSError("disk went away")

    monkeypatch.setattr(vault, "append", refuse)
    with pytest.raises(OSError):
        ingest_path(vault, script)
    monkeypatch.undo()

    result = ingest_path(vault, script)
    assert result.status == STATUS_STORED
    assert len(events_of(vault, "artifact.ingested")) == 1


def test_no_staging_files_are_left_behind(vault, script):
    ingest_path(vault, script)
    ingest_path(vault, script)
    assert [p.name for p in (vault.root / "raw").rglob("*.partial")] == []


# --- restoring --------------------------------------------------------------

def test_known_content_whose_file_has_gone_is_written_back_to_the_same_path(
    vault, script
):
    """Citations already point at that relative path, so it must not move."""
    first = ingest_path(vault, script)
    os.chmod(vault.root / first.rel, 0o600)
    (vault.root / first.rel).unlink()

    again = ingest_path(vault, script)
    assert again.status == STATUS_RESTORED
    assert again.rel == first.rel
    assert (vault.root / first.rel).read_bytes() == JPEG


def test_restoring_does_not_double_count_the_evidence(vault, script):
    first = ingest_path(vault, script)
    os.chmod(vault.root / first.rel, 0o600)
    (vault.root / first.rel).unlink()
    ingest_path(vault, script)

    assert len(events_of(vault, "artifact.ingested")) == 1
    reseen = events_of(vault, "artifact.reseen")
    assert len(reseen) == 1
    assert reseen[0].payload["restored"] is True


def test_a_restored_artefact_gets_its_sidecar_back(vault, script):
    first = ingest_path(vault, script)
    os.chmod(vault.root / first.rel, 0o600)
    (vault.root / first.rel).unlink()
    (vault.root / (first.rel + ".json")).unlink()

    again = ingest_path(vault, script)
    data = sidecar_of(vault, again)
    # Credited to the original ingest, not to the restore.
    assert data["event"] == first.event.id
    assert data["hash"] == first.digest


# --- living inside a sync client --------------------------------------------

def test_a_write_that_does_not_read_back_records_nothing(vault_root, identity, monkeypatch):
    """A virtual drive can report a successful write it has not committed.

    On a synced vault the stored bytes are re-read before anything is recorded.
    If they do not match, no event is appended — and the file is left alone
    rather than deleted, because removing raw bytes is not something ingest does.
    """
    from agent import config as config_mod
    from agent.ingest import store as store_mod
    from agent.vault import Vault

    (vault_root / config_mod.CONFIG_FILENAME).write_text(
        'sync_profile = "dropbox"\n', encoding="utf-8"
    )
    vault = Vault.open(vault_root, identity=identity)
    monkeypatch.setattr(store_mod, "hash_file", lambda path: ("0" * 64, 0))

    with pytest.raises(IngestError, match="read back with a different hash"):
        ingest_bytes(vault, JPEG)

    stored = [p for p in (vault.root / "raw").rglob("*") if p.is_file()]
    assert len(stored) == 1
    assert stored[0].read_bytes() == JPEG
    assert vault.read().events == ()


def test_a_local_vault_does_not_pay_for_the_readback(vault, monkeypatch):
    from agent.ingest import store as store_mod

    def fail(path):
        raise AssertionError("a local vault should not re-read the file it just wrote")

    monkeypatch.setattr(store_mod, "hash_file", fail)
    assert ingest_bytes(vault, JPEG).status == STATUS_STORED
