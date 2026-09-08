"""The timeline places every row on a date it can actually justify.

The four timestamps diverge constantly. A row placed on ingest time and rendered
as if it were when something happened is wrong by however long the file sat on
someone's phone, and nothing on the page would reveal it — so every row says
which of the four it is standing on.
"""

from __future__ import annotations

from agent import projection

from .conftest import claim, confirm, ingested, note, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"


def _project(events):
    return projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)


def _page(result, month: str) -> str:
    return result.files[f"wiki/timeline/{month}.md"].decode("utf-8")


def test_a_claim_with_an_occurred_at_is_placed_on_it_without_a_qualifier():
    proposed = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        occurred={"value": "2026-06-04"},
    )
    result = _project([ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))])

    page = _page(result, "2026-06")
    assert "## 4 June 2026" in page
    assert "(recorded)" not in page
    assert "Perindopril — dose: 5mg daily" in page


def test_an_artefact_with_no_capture_time_is_labelled_recorded():
    """Ingest time is never rendered as when the photo was taken."""
    result = _project([ingested(DEVICE, ts="2026-09-02T09:14:03Z")])
    page = _page(result, "2026-09")

    assert "## 2 September 2026 (recorded)" in page
    assert "A photograph was added to the record" in page


def test_an_artefact_with_a_capture_time_is_labelled_captured():
    result = _project(
        [ingested(DEVICE, ts="2026-09-02T09:14:03Z", captured_ts="2026-08-30T11:00:00Z")]
    )
    assert "## 30 August 2026 (captured)" in _page(result, "2026-08")


def test_date_uncertainty_renders_as_a_band():
    """"The headaches started around Easter" is not a date, and is not shown as one."""
    proposed = claim(
        DEVICE, "problem:hypertension", "onset", "gradual", tier="patient-reported",
        ts=on_day(2),
        occurred={"value": "2026-04-05", "precision": "month", "uncertainty_days": 14},
    )
    result = _project([ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))])

    assert "## around April 2026 (±14 days)" in _page(result, "2026-04")


def test_a_note_appears_with_its_own_text_and_cites_the_log():
    result = _project(
        [note(DEVICE, ts="2026-09-05T18:00:00Z", text="Headaches most mornings this week.")]
    )
    page = _page(result, "2026-09")

    assert "Headaches most mornings this week." in page
    assert f"events/2026-09.{DEVICE}.jsonl" in page


def test_rows_are_reverse_chronological_within_a_month():
    events = [
        note(DEVICE, ts="2026-09-01T09:00:00Z", text="First note."),
        note(DEVICE, ts="2026-09-20T09:00:00Z", text="Later note."),
        note(DEVICE, ts="2026-09-10T09:00:00Z", text="Middle note."),
    ]
    page = _page(_project(events), "2026-09")
    assert page.index("Later note.") < page.index("Middle note.") < page.index("First note.")


def test_every_row_carries_its_evidence_tier():
    proposed = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        occurred={"value": "2026-06-04"},
    )
    result = _project([ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))])
    assert "**prescriber-issued**" in _page(result, "2026-06")
    assert "**artefact**" in _page(result, "2026-09")


def test_an_unconfirmed_high_consequence_claim_is_not_on_the_timeline_either():
    proposed = claim(DEVICE, "allergy:penicillin", "reaction", "anaphylaxis", ts=on_day(2),
                     occurred={"value": "2026-06-04"})
    result = _project([ingested(DEVICE), proposed])

    assert "wiki/timeline/2026-06.md" not in result.files
    rendered = b"".join(result.files[rel] for rel in sorted(result.files))
    assert b"anaphylaxis" not in rendered
