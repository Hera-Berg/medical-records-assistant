"""A proposed stop is its own kind of thing to decide.

Rule 4 in ``CLAUDE.md``: the review UI "must render stop proposals as their own
distinct action, never as a generic accept in a tap-through queue. A careless tap
must not be able to drop a medication."

That is a requirement about an interface, so it would be natural to test it in
the interface — and useless there. A guard that lives in the frontend lasts until
the next redesign, and the redesign will not know why the button was different.
So the distinction is made where it cannot be lost: the projection raises a stop
proposal under its own review kind, the route that acts on the queue refuses a
generic confirmation against that kind, and the screen is then merely the third
place that agrees.

The other half of the rule is the tier gate, which is tested next door in
``test_medication_lifecycle``. What is checked here is that the queue *says which
of the two will happen* before the tap, because a button reading "take it off the
list" that then does not is worse than no button at all.
"""

from __future__ import annotations

from agent import projection
from agent.projection import reconcile, stops

from .conftest import claim, confirm, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"


def _record(*extra, as_of: str = AS_OF):
    """A confirmed medication, so the entity exists, plus whatever is under test."""
    dose = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        occurred={"value": "2026-09-02", "precision": "day", "uncertainty_days": 0},
    )
    events = [ingested(DEVICE), dose, confirm(DEVICE, dose.id, ts=on_day(2, hour=10))]
    events.extend(extra)
    return projection.project(sorted(events, key=lambda e: e.sort_key), as_of)


def _items(result, kind: str):
    return [item for item in result.review if item.kind == kind]


def test_a_proposed_stop_is_not_an_ordinary_pending_claim():
    """It leaves the generic queue entirely rather than appearing in both."""
    stop = claim(DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4))
    result = _record(stop)

    assert [item.kind for item in result.review] == [reconcile.STOP_PROPOSED]
    assert _items(result, reconcile.AWAITING) == []


def test_a_prescriber_issued_stop_says_it_will_take_the_medication_off_the_list():
    stop = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4),
        tier="prescriber-issued",
    )
    (item,) = _items(_record(stop), reconcile.STOP_PROPOSED)

    assert "takes it off your medication list" in item.summary
    assert stops.transitions(item.claims[0]) is True


def test_a_patient_reported_stop_says_the_medication_stays_on_the_list():
    """Confirming is still worth doing; it is a different act and says so.

    The patient is the authority on what they actually take and the prescriber
    on what was prescribed, so this records a real discrepancy — it simply does
    not transition the status, and the queue must not imply that it will.
    """
    stop = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4),
        tier="patient-reported",
    )
    (item,) = _items(_record(stop), reconcile.STOP_PROPOSED)

    assert "keeps it on your medication list" in item.summary
    assert "only a prescriber-issued or lab-issued source" in item.summary
    assert stops.transitions(item.claims[0]) is False


def test_a_resolved_problem_is_not_a_medication_stop():
    """The guard rail is the medication list's. A problem is a different act.

    Widening this to every ``status`` predicate would put diagnoses behind the
    medication list's wording — "takes it off your medication list" about
    hypertension — and the tier gate that goes with it was written about
    supply, not about diagnoses.
    """
    resolved = claim(
        DEVICE, "problem:hypertension", "status", "resolved", ts=on_day(4),
    )
    result = _record(resolved)

    assert _items(result, reconcile.STOP_PROPOSED) == []
    assert [item.subject_id for item in _items(result, reconcile.AWAITING)] == [
        "problem:hypertension"
    ]


def test_two_documents_saying_stop_are_one_item_and_one_tap():
    """Folding is per fact here as everywhere else.

    Two sources agreeing that something stopped is one decision. A queue that
    asked twice about one fact is what makes an inbox uncompletable, and the
    stop path does not get an exemption from that.
    """
    first = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4),
        artifact="a3f91c", tier="prescriber-issued",
    )
    second = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(5),
        artifact="77b210", tier="prescriber-issued",
    )
    result = _record(ingested(DEVICE, "77b210"), first, second)

    (item,) = _items(result, reconcile.STOP_PROPOSED)
    assert {c.event_id for c in item.claims} == {first.id, second.id}
    assert {c.cite for c in item.claims} == {"a3f91c", "77b210"}


def test_a_rejected_stop_leaves_the_queue_entirely():
    """A rejection is a retraction. Nothing about it renders anywhere."""
    from .conftest import reject

    stop = claim(DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4))
    result = _record(stop, reject(DEVICE, stop.id, ts=on_day(5)))

    assert result.review == ()
    page = result.files[result.entities["med:perindopril"].rel_path].decode("utf-8")
    assert "stopped" not in page


def test_the_entity_page_says_a_decision_is_waiting_without_stating_it():
    """The page must not print the proposed value, and must not go quiet either.

    Printing "this may have been stopped" on a medication page reads as though
    it had been. Printing nothing loses the fact that something is waiting on a
    person, which is the failure rule 3 exists to stop.
    """
    stop = claim(
        DEVICE, "med:perindopril", "status", "stopped", ts=on_day(4),
        tier="prescriber-issued",
    )
    result = _record(stop)
    page = result.files[result.entities["med:perindopril"].rel_path].decode("utf-8")

    assert "waiting for you to review it in the inbox" in page
    assert "status: active" in page
    assert "stop" not in page.replace("stopped being", "")
