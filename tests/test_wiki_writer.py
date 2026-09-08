"""The writer removes only what it wrote, and never guesses about the rest.

``wiki/`` is a directory inside the user's own folder, which is very likely
inside a sync client. Two things end up in it that this code did not write: notes
the user drops in by hand, and conflicted copies a sync client leaves behind
after two machines diverge. Neither is garbage to sweep up — the second is a
signal worth surfacing — so the manifest is the sole authority on what may be
deleted, and anything else is reported and left exactly where it is.
"""

from __future__ import annotations

from agent import projection
from agent.projection import writer

from .conftest import claim, confirm, ingested, on_day

AS_OF = "2026-09-30T00:00:00Z"


def _events(device: str, subject: str = "med:perindopril", value: str = "5mg daily"):
    proposed = claim(device, subject, "dose", value, ts=on_day(2))
    return [ingested(device), proposed, confirm(device, proposed.id, ts=on_day(3))]


def _rebuild(vault, events=None, as_of: str = AS_OF):
    if events is not None:
        for event in sorted(events, key=lambda e: e.sort_key):
            vault.append(event)
    return projection.rebuild(vault, as_of=as_of)


def test_a_file_the_user_dropped_in_survives_a_rebuild(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))

    hand_written = vault_root / "wiki" / "my own notes.md"
    hand_written.write_text("Things to ask the GP.\n", encoding="utf-8")

    report = _rebuild(vault)

    assert hand_written.read_text(encoding="utf-8") == "Things to ask the GP.\n"
    assert "wiki/my own notes.md" in report.write.foreign
    assert any("was not generated" in problem for problem in report.problems)


def test_a_sync_conflicted_copy_is_reported_not_deleted(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))

    fork = vault_root / "wiki" / "medications" / "perindopril (conflicted copy 2026-09-08).md"
    fork.write_bytes(b"---\nid: med:perindopril\n---\n")

    report = _rebuild(vault)

    assert fork.exists(), "a conflicted copy is evidence of divergence, not litter"
    assert fork.relative_to(vault_root).as_posix() in report.write.foreign


def test_a_generated_file_edited_by_hand_is_never_overwritten(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))
    page = vault_root / "wiki" / "medications" / "perindopril.md"
    page.write_text("I edited this myself.\n", encoding="utf-8")

    report = _rebuild(vault)

    assert page.read_text(encoding="utf-8") == "I edited this myself.\n"
    assert "wiki/medications/perindopril.md" in report.write.modified
    assert any("edited since" in problem for problem in report.problems)


def test_a_missing_manifest_authorises_no_deletions(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))
    stale_page = vault_root / "wiki" / "medications" / "gone.md"
    stale_page.write_bytes(b"---\nid: med:gone\n---\n")

    (vault_root / writer.MANIFEST_REL).unlink()

    report = _rebuild(vault)

    assert report.write.manifest_missing is True
    assert report.write.removed == ()
    assert stale_page.exists()
    assert any("nothing was removed" in problem for problem in report.problems)


def test_a_page_this_code_wrote_is_removed_when_it_stops_being_generated(
    vault, vault_root, identity
):
    """The wiki is regenerated wholesale; what we wrote, we may clean up."""
    _rebuild(vault, _events(identity.id, "med:perindopril"))
    page = vault_root / "wiki" / "medications" / "perindopril.md"
    assert page.exists()

    # A second vault built from a stream that no longer mentions perindopril.
    other = vault_root / "wiki" / "medications" / "atorvastatin.md"
    report = writer.write(
        vault_root,
        {"wiki/medications/atorvastatin.md": b"---\nid: med:atorvastatin\n---\n"},
        as_of=AS_OF,
    )

    assert not page.exists()
    assert "wiki/medications/perindopril.md" in report.removed
    assert other.exists()


def test_writing_is_atomic_and_leaves_no_temporary_files(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))
    leftovers = [p.name for p in (vault_root / "wiki").rglob(".*.tmp")]
    assert leftovers == []


def test_an_unchanged_rebuild_rewrites_nothing(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))
    page = vault_root / "wiki" / "medications" / "perindopril.md"
    before = page.stat().st_mtime_ns

    report = _rebuild(vault)

    assert report.write.written == ()
    assert page.stat().st_mtime_ns == before, "an unchanged page should not be touched"


def test_the_manifest_records_what_was_written(vault, vault_root, identity):
    report = _rebuild(vault, _events(identity.id))
    manifest = writer.read_manifest(vault_root)

    assert manifest.present
    assert manifest.as_of == AS_OF
    assert set(manifest.files) == set(report.projection.files)
    for rel, entry in manifest.files.items():
        assert entry["bytes"] == len(report.projection.files[rel])


def test_os_litter_is_ignored_rather_than_reported(vault, vault_root, identity):
    _rebuild(vault, _events(identity.id))
    (vault_root / "wiki" / ".DS_Store").write_bytes(b"\x00")

    report = _rebuild(vault)

    assert report.write.foreign == ()
    assert (vault_root / "wiki" / ".DS_Store").exists()
