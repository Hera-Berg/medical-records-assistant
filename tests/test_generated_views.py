"""Generated views are returned, never stored.

"'Current medications' is a **generated view**, not a stored file." The moment a
second copy of the medication list exists on disk, one of them is out of date and
nothing says which — so the projection hands the view to its caller and the
folder keeps one file per entity.
"""

from __future__ import annotations

from agent import projection

from .conftest import claim, confirm, correct, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2027-01-01T00:00:00Z"
THIRTY_DAYS = {"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}


def _med(subject, value, ts, **kwargs):
    proposed = claim(DEVICE, subject, "dose", value, ts=ts, **kwargs)
    return [proposed, confirm(DEVICE, proposed.id, ts=ts)]


def _projection():
    events = [ingested(DEVICE), ingested(DEVICE, "77b210", mime="application/pdf")]
    events += _med("med:perindopril", "5mg daily", on_day(2),
                   occurred={"value": "2026-06-04"}, dispense=THIRTY_DAYS)
    events += _med("med:metformin", "500mg twice daily", on_day(3),
                   occurred={"value": "2026-07-01"})
    events += _med("med:metformin", "850mg twice daily", on_day(4),
                   artifact="77b210", occurred={"value": "2026-07-01"})
    stop = claim(DEVICE, "med:codeine", "status", "stopped", ts=on_day(5),
                 tier="patient-reported")
    events += [stop, correct(DEVICE, value="stopped", target=stop.id, ts=on_day(6))]
    return projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)


def test_the_view_is_not_written_to_the_vault():
    files = _projection().files
    assert not any(rel.endswith("index.md") for rel in files)
    assert not any("current" in rel for rel in files)
    assert sorted(files) == [
        "wiki/medications/codeine.md",
        "wiki/medications/metformin.md",
        "wiki/medications/perindopril.md",
        "wiki/timeline/2026-06.md",
        "wiki/timeline/2026-07.md",
        "wiki/timeline/2026-09.md",
    ]


def test_stale_and_conflicted_medications_stay_on_the_current_list():
    rows = {row.id: row for row in _projection().current_medications}

    assert set(rows) == {"med:perindopril", "med:metformin"}
    assert rows["med:perindopril"].stale is True
    assert rows["med:perindopril"].status == "stale"
    assert rows["med:metformin"].conflicted is True
    assert rows["med:metformin"].dose is None, "a conflict has no single dose to show"


def test_a_stopped_medication_leaves_the_current_list_but_keeps_its_page():
    result = _projection()
    assert "med:codeine" not in {row.id for row in result.current_medications}
    assert "wiki/medications/codeine.md" in result.files


def test_every_row_carries_its_evidence_tier_and_sources():
    for row in _projection().current_medications:
        assert row.evidence_tier
        assert row.sources
        assert row.to_dict()["sources"] == list(row.sources)
