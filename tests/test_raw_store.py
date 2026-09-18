"""What ``verify()`` reports about ``raw/``, and what it deliberately ignores."""

from __future__ import annotations

import json
import os

import pytest

from agent.ingest import CaptureContext, ingest_bytes
from agent.ingest.store import RawStore, ingested_records

from .conftest import JPEG, PDF


@pytest.fixture
def stocked(vault):
    """A vault with two artefacts in it."""
    first = ingest_bytes(vault, JPEG, CaptureContext(source="upload", original_filename="a.jpg"))
    second = ingest_bytes(vault, PDF, CaptureContext(source="upload", original_filename="b.pdf"))
    return vault, first, second


def check(vault, deep: bool = False):
    records, duplicates = ingested_records(vault.read().events)
    return RawStore(vault.root, vault.profile).verify(records, deep=deep, duplicates=duplicates)


def unlock(path):
    os.chmod(path, 0o600)
    return path


# --- the healthy case -------------------------------------------------------

def test_a_freshly_ingested_store_is_clean(stocked):
    vault, first, second = stocked
    report = check(vault)
    assert report.is_clean
    assert report.artifacts == 2
    assert report.bytes == len(JPEG) + len(PDF)


def test_a_deep_check_is_also_clean(stocked):
    vault, _, _ = stocked
    assert check(vault, deep=True).is_clean


def test_a_deep_check_says_it_re_read_the_bytes(stocked):
    vault, _, _ = stocked
    assert check(vault, deep=True).deep
    assert not check(vault).deep


# --- what is deliberately not corruption ------------------------------------

def test_a_changed_file_mode_is_not_reported_as_damage(stocked):
    """Restores and resyncs lose modes routinely.

    Reporting that as damage would teach the user to ignore the report that
    actually matters, so permission bits are not checked at all.
    """
    vault, first, _ = stocked
    os.chmod(vault.root / first.rel, 0o644)
    assert check(vault, deep=True).is_clean


def test_a_leftover_staging_file_is_a_note_not_a_problem(stocked):
    vault, _, _ = stocked
    leftover = vault.root / "raw" / "2026" / "09" / ".ingest-01ABCDEF.partial"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_bytes(b"half a file")

    report = check(vault)
    # The store records POSIX paths whatever the platform writes, so that a
    # vault written on Windows resolves on a Mac. Compare them as it records
    # them: str() would be backslashes there.
    assert report.partials == (leftover.relative_to(vault.root).as_posix(),)
    assert not report.orphans
    assert not report.foreign


# --- what is corruption -----------------------------------------------------

def test_bytes_recorded_in_the_log_but_missing_from_disk_are_reported(stocked):
    vault, first, _ = stocked
    unlock(vault.root / first.rel).unlink()
    report = check(vault)
    assert len(report.missing) == 1
    assert first.rel in report.missing[0]
    assert not report.is_clean


def test_bytes_on_disk_that_no_event_records_are_reported(stocked):
    """A crash between writing the file and appending the event leaves this."""
    vault, first, _ = stocked
    stray = vault.root / "raw" / "2026" / "09" / "2026-09-08T1432Z_abcdef.jpg"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"unrecorded bytes")

    report = check(vault)
    assert stray.relative_to(vault.root).as_posix() in report.orphans


def test_a_missing_sidecar_is_reported(stocked):
    vault, first, _ = stocked
    (vault.root / (first.rel + ".json")).unlink()
    assert check(vault).sidecar_missing == (first.rel,)


def test_a_sidecar_naming_the_wrong_artefact_is_reported(stocked):
    vault, first, _ = stocked
    path = vault.root / (first.rel + ".json")
    data = json.loads(path.read_text())
    data["artifact"] = "something-else.jpg"
    path.write_text(json.dumps(data))

    report = check(vault)
    assert len(report.sidecar_disagrees) == 1
    assert first.rel in report.sidecar_disagrees[0]


def test_content_that_no_longer_matches_its_hash_is_reported_by_a_deep_check(stocked):
    vault, first, _ = stocked
    unlock(vault.root / first.rel).write_bytes(b"tampered with, or bit-rotted")

    shallow = check(vault)
    deep = check(vault, deep=True)
    assert shallow.is_clean          # a shallow check reads no artefact bytes
    assert len(deep.hash_mismatch) == 1
    assert first.rel in deep.hash_mismatch[0]
    assert len(deep.sidecar_disagrees) == 1


def test_a_sidecar_with_no_artefact_is_reported(stocked):
    vault, first, _ = stocked
    unlock(vault.root / first.rel).unlink()
    assert check(vault).sidecar_orphaned == (first.rel + ".json",)


def test_a_file_that_does_not_match_the_grammar_is_reported(stocked):
    vault, _, _ = stocked
    stray = vault.root / "raw" / "2026" / "09" / "scan.jpg"
    stray.write_bytes(b"dropped in by hand")
    assert check(vault).foreign == ("raw/2026/09/scan.jpg",)


def test_two_ingested_events_for_one_hash_are_reported(vault):
    """The second should have been an artifact.reseen."""
    first = ingest_bytes(vault, JPEG)
    duplicate = first.event
    from agent.events import envelope

    vault.append(
        envelope.new(
            "artifact.ingested",
            vault.identity.id,
            payload=dict(duplicate.payload),
        )
    )
    report = check(vault)
    assert len(report.duplicate_events) == 1
    assert not report.is_clean


# --- lookup -----------------------------------------------------------------

def test_an_artefact_can_be_found_by_its_hash(stocked):
    vault, first, second = stocked
    store = RawStore(vault.root, vault.profile)
    assert store.find(first.digest) == vault.root / first.rel
    assert store.find(second.digest) == vault.root / second.rel


def test_finding_an_unknown_hash_returns_nothing(stocked):
    vault, _, _ = stocked
    assert RawStore(vault.root, vault.profile).find("0" * 64) is None
    assert RawStore(vault.root, vault.profile).find("not a hash") is None


def test_iteration_skips_sidecars(stocked):
    vault, _, _ = stocked
    store = RawStore(vault.root, vault.profile)
    names = [a.name for a in store.iter_artifacts()]
    assert len(names) == 2
    assert not any(n.endswith(".json") for n in names)


# --- living inside a sync client --------------------------------------------

def test_an_artefact_that_has_not_downloaded_yet_is_not_called_corrupt(stocked):
    """"Not on this machine yet" and "the bytes are wrong" need different
    responses from the user, so they are never collapsed into one report."""
    vault, first, _ = stocked
    artefact = vault.root / first.rel
    artefact.with_name(artefact.name + ".nextcloud").write_text("")

    report = check(vault, deep=True)
    assert len(report.unavailable) == 1
    assert first.rel in report.unavailable[0]
    assert not report.hash_mismatch
    assert not report.orphans


def test_sync_client_and_file_manager_litter_is_ignored(stocked):
    vault, _, _ = stocked
    (vault.root / "raw" / "2026" / "09" / ".DS_Store").write_bytes(b"\x00")
    (vault.root / "raw" / "Thumbs.db").write_bytes(b"\x00")
    assert check(vault).is_clean


def test_a_recorded_path_that_escapes_the_vault_is_refused(stocked):
    """Event payloads are editable text, and a restore writes where they point."""
    vault, first, _ = stocked
    store = RawStore(vault.root, vault.profile)
    assert store.resolve_recorded(first.rel) == vault.root / first.rel
    assert store.resolve_recorded("../outside.jpg") is None
    assert store.resolve_recorded("raw/../../outside.jpg") is None
    assert store.resolve_recorded("/etc/passwd") is None
    assert store.resolve_recorded("") is None
