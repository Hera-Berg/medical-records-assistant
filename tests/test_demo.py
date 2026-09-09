"""The demo vault: what it seeds, and the guardrails around seeding it.

The command exists so that the renderer's output can be read by hand before
phase 4 puts a model behind it, and so phases 7 and 8 have a record to build an
inbox and a consultation summary against. These tests hold it to that: every
shape the scenario is meant to demonstrate is asserted to be present, because a
demo that silently stops demonstrating a case is worse than no demo — someone
reads it, sees nothing wrong, and concludes the case works.

The guardrails get their own tests because the failure they prevent is seeding
invented medications into a folder holding a real record.
"""

from __future__ import annotations

import re

import pytest

from agent import demo as demo_mod
from agent.errors import HealthAgentError
from agent.projection import entities as entities_mod

#: Footnote targets look like ``→ `raw/2026/09/…` `` in the rendered pages.
_TARGET = re.compile(r"→ `([^`]+)`")


@pytest.fixture
def seeded(tmp_path):
    return demo_mod.seed(tmp_path / "demo-vault")


def test_it_seeds_a_readable_vault(seeded):
    root = seeded.root
    assert (root / demo_mod.MARKER_FILENAME).is_file()
    assert "invented" in (root / demo_mod.MARKER_FILENAME).read_text(encoding="utf-8")
    assert (root / "config.toml").is_file()
    assert seeded.artifacts == len(demo_mod.stream.ARTIFACTS)
    assert seeded.events > 20

    shards = sorted(p.name for p in (root / "events").glob("*.jsonl"))
    assert shards, "the log has to be on disk, not only in memory"
    assert all(demo_mod.DEMO_DEVICE in name for name in shards), (
        "the shard filename is one of the three places the vault says it is demo data"
    )


def test_every_citation_resolves_to_a_file_that_exists(seeded):
    """The point of writing real bytes into raw/ rather than faking the events.

    A citation pointing at a path that is not there is exactly what makes a
    hand-read of the folder useless, and it is invisible from inside the
    projection.
    """
    root = seeded.root
    targets = set()
    for page in (root / "wiki").rglob("*.md"):
        targets.update(_TARGET.findall(page.read_text(encoding="utf-8")))

    assert targets
    missing = sorted(target for target in targets if not (root / target).exists())
    assert missing == []


def test_it_covers_the_shapes_that_stress_the_renderer(seeded):
    entities = seeded.rebuild.projection.entities

    assert entities["med:perindopril"].status == entities_mod.ACTIVE
    assert entities["med:metformin"].status == entities_mod.STALE
    assert entities["med:amitriptyline"].status == entities_mod.STOPPED
    assert entities["med:atorvastatin"].status == entities_mod.CONFLICTED

    # Active *and* carrying a patient-reported stop: the discrepancy, not a status.
    sertraline = entities["med:sertraline"]
    assert sertraline.status == entities_mod.ACTIVE
    assert sertraline.stop_report is not None
    assert sertraline.stop_report.tier == "patient-reported"

    # A correction, so the history section has something in it.
    levothyroxine = entities["med:levothyroxine"]
    assert levothyroxine.slots["dose"].winner.value.literal == "50mcg daily"
    assert [c.value.literal for c in levothyroxine.slots["dose"].superseded] == [
        "5Omcg daily"
    ]

    assert "allergy:penicillin" in entities
    assert "problem:hypertension" in entities
    assert "person:dr-nguyen" in entities


def test_it_leaves_something_in_the_review_queue_and_the_report(seeded):
    """Phases 7 and 8 need a queue that is not empty, and one item per tier."""
    projection = seeded.rebuild.projection
    tiers = projection.review_by_tier
    assert "high" in tiers and "medium" in tiers

    # Subject-less, so it belongs in the report rather than the wiki.
    assert any("names no target claim" in note for note in projection.anomalies)
    assert all(note.subject_id is None or note.subject_id for note in projection.anomalies)


def test_the_rejected_reading_reaches_no_page(seeded):
    """Seeded rather than only unit-tested, because reading the folder by hand is
    how someone would notice it had leaked."""
    for page in (seeded.root / "wiki").rglob("*.md"):
        assert "alcohol" not in page.read_text(encoding="utf-8").lower()
    assert "problem:alcohol-dependence" not in seeded.rebuild.projection.entities


def test_the_timeline_spans_several_months(seeded):
    months = sorted(p.stem for p in (seeded.root / "wiki" / "timeline").glob("*.md"))
    assert len(months) >= 3


def test_ingest_time_is_now_and_document_dates_are_not(seeded):
    """The four timestamps, kept apart in the one place people will read them.

    The bytes really did enter the vault today, so ``ingested_ts`` says today.
    Backdating it to make the demo look tidier would be exactly the substitution
    the four-timestamp rule exists to prevent.
    """
    ingested = [
        event
        for event in seeded.rebuild.projection.reconciliation.admissions
        if event.claim.artifact
    ]
    assert ingested
    for admission in ingested:
        claim = admission.claim
        assert claim.ingested_ts is not None
        if claim.artifact_ts is not None:
            assert claim.artifact_ts < claim.ingested_ts, (
                "a document predates the day its photograph was filed"
            )


def test_it_refuses_a_folder_that_is_not_empty(tmp_path):
    root = tmp_path / "real-vault"
    root.mkdir()
    (root / "config.toml").write_text("port = 7777\n", encoding="utf-8")

    with pytest.raises(HealthAgentError) as raised:
        demo_mod.seed(root)
    assert "not empty" in str(raised.value)
    # And it wrote nothing on the way to refusing.
    assert sorted(p.name for p in root.iterdir()) == ["config.toml"]


def test_it_refuses_a_path_that_is_a_file(tmp_path):
    target = tmp_path / "not-a-folder"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(HealthAgentError):
        demo_mod.seed(target)


def test_seeding_does_not_mint_this_machines_device_identity(tmp_path, isolated_env):
    """The demo runs on someone's laptop. It must not touch their real identity."""
    demo_mod.seed(tmp_path / "demo-vault")
    assert not (isolated_env / "device").exists()


def test_the_cli_reports_a_refusal_as_a_message_not_a_traceback(tmp_path, capsys):
    """Every refusal in this program is written to be read.

    A traceback in front of the sentence that says what to do hides it.
    """
    from agent import cli

    root = tmp_path / "occupied"
    root.mkdir()
    (root / "config.toml").write_text("port = 7777\n", encoding="utf-8")

    code = cli.main(["demo", str(root)])
    out = capsys.readouterr().out

    assert code == cli.EXIT_PROBLEMS
    assert out.startswith("error: ")
    assert "only ever seeds an empty folder" in out


def test_the_cli_seeds_and_points_at_the_marker(tmp_path, capsys):
    from agent import cli

    root = tmp_path / "demo-vault"
    code = cli.main(["demo", str(root)])
    out = capsys.readouterr().out

    assert code == cli.EXIT_OK
    assert "This is invented data" in out
    assert str(root / "wiki") in out
