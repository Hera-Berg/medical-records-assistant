"""No confidence value and no amount of time lets a high-consequence claim in.

The consequence table is the project's fifth invariant: adding an allergy,
adding or stopping a medication, or changing a dose "never enters the wiki
without an explicit user tap. No exceptions, no confidence threshold that
bypasses this."

The property test below is the important one. It sweeps confidence to 1.0,
elapsed time out to years, every evidence tier, and payloads that claim a lower
consequence tier for themselves, and asserts that nothing in that space reaches
the wiki. Gating on the model's own labels is exactly how a bad extraction would
route itself around review, so the tier is recomputed from the predicate here and
the payload's own value is only ever reported.
"""

from __future__ import annotations

import random

import pytest

from agent import projection
from agent.projection import reconcile, tiers

from .conftest import claim, confirm, correct, ingested, on_day, reject

DEVICE = "laptop-a1b2"
AS_OF = "2030-01-01T00:00:00Z"

HIGH_SUBJECTS = ("med:perindopril", "allergy:penicillin", "problem:hypertension")
PREDICATES = ("dose", "status", "name", "reaction", "severity", "route", "diagnosis")
TIERS = tiers.EVIDENCE_TIERS


def _stream(seed: int):
    """A stream of unconfirmed claims, generated to be as persuasive as possible.

    The expected tier is computed the same way the engine computes it — from the
    subject kind and predicate — rather than assumed from the generator, so the
    sweep covers medium and low claims too and asserts the right thing about
    each. Every claim gets a unique token in its value, which is what the
    rendered pages are searched for.
    """
    rng = random.Random(seed)
    events = [ingested(DEVICE)]
    expected: list[tuple[str, str]] = []
    for index in range(rng.randint(1, 12)):
        subject = rng.choice(HIGH_SUBJECTS)
        predicate = rng.choice(PREDICATES)
        token = f"zq{seed}x{index}"
        events.append(
            claim(
                DEVICE,
                subject,
                predicate,
                f"{token} daily",
                tier=rng.choice(TIERS),
                ts=f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T09:00:00Z",
                confidence=rng.choice([0.0, 0.5, 0.99, 1.0]),
                # The payload lies about its own consequence tier.
                consequence=rng.choice(["low", "medium", "high"]),
            )
        )
        expected.append((token, tiers.consequence_for(subject.split(":")[0], predicate)))
    return sorted(events, key=lambda event: event.sort_key), expected


@pytest.mark.parametrize("seed", range(60))
def test_high_consequence_never_autoapplies(seed):
    events, expected = _stream(seed)
    # ``as_of`` is years after every claim: elapsed time must buy nothing.
    result = projection.project(events, AS_OF)

    rendered = b"\n".join(result.files[rel] for rel in sorted(result.files))
    for token, consequence in expected:
        if consequence != tiers.HIGH:
            continue
        assert token.encode() not in rendered, (
            f"an unconfirmed high-consequence claim reached the wiki (seed {seed})"
        )

    for slot in result.reconciliation.slots.values():
        assert slot.consequence != tiers.HIGH or slot.winner is None

    high = [token for token, consequence in expected if consequence == tiers.HIGH]
    awaiting = [
        item
        for item in result.review
        if item.kind == "awaiting-confirmation" and item.consequence == tiers.HIGH
    ]
    # Held back, not dropped: every one of them is waiting on a person.
    assert len(awaiting) == len(high)


def test_a_payload_claiming_low_consequence_is_still_gated():
    """The model does not get to assign its own tier."""
    proposed = claim(
        DEVICE,
        "med:perindopril",
        "dose",
        "500mg daily",
        consequence="low",
        confidence=1.0,
        ts="2020-01-01T09:00:00Z",
    )
    result = projection.project([ingested(DEVICE), proposed], AS_OF)

    assert result.entities == {}
    assert any("has no effect on review gating" in note for note in result.anomalies)


def test_confirmation_is_what_lets_a_high_claim_in():
    proposed = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts="2026-09-02T09:00:00Z")
    events = [ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts="2026-09-02T10:00:00Z")]

    result = projection.project(events, AS_OF)
    slot = result.entities["med:perindopril"].slots["dose"]
    assert slot.winner.value.literal == "5mg daily"
    assert slot.review_state == reconcile.REVIEW_CONFIRMED


def test_a_correction_also_lets_it_in():
    proposed = claim(DEVICE, "med:perindopril", "dose", "4mg daily", ts="2026-09-02T09:00:00Z")
    events = [
        ingested(DEVICE),
        proposed,
        correct(DEVICE, value="5mg daily", target=proposed.id, ts="2026-09-02T10:00:00Z"),
    ]
    slot = projection.project(events, AS_OF).entities["med:perindopril"].slots["dose"]
    assert slot.winner.value.literal == "5mg daily"


def test_rejection_keeps_it_out_for_good():
    proposed = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts="2026-09-02T09:00:00Z")
    events = [
        ingested(DEVICE),
        proposed,
        reject(DEVICE, proposed.id, ts="2026-09-02T10:00:00Z"),
    ]
    result = projection.project(events, AS_OF)
    assert result.entities == {}
    assert not result.review


@pytest.mark.parametrize(
    "elapsed_days,expected_admitted",
    [(0, False), (6, False), (7, True), (400, True)],
)
def test_medium_consequence_applies_itself_after_a_week(elapsed_days, expected_admitted):
    """Medium claims auto-apply after seven days, and say so until confirmed."""
    proposed = claim(
        DEVICE,
        "problem:hypertension",
        "onset",
        "gradual",
        tier="patient-reported",
        ts="2026-09-01T00:00:00Z",
    )
    as_of = f"2026-09-{1 + elapsed_days:02d}T00:00:00Z" if elapsed_days < 28 else "2027-11-01T00:00:00Z"
    result = projection.project([ingested(DEVICE), proposed], as_of)

    if not expected_admitted:
        assert result.entities == {}
        return

    entity = result.entities["problem:hypertension"]
    assert entity.slots["onset"].winner.value.literal == "gradual"
    assert entity.slots["onset"].review_state == reconcile.REVIEW_UNREVIEWED
    assert b"reviewed: unreviewed" in result.files[entity.rel_path]


def test_low_consequence_applies_immediately():
    proposed = claim(
        DEVICE, "person:dr-nguyen", "contact", "Rooms on Blackburn Road",
        tier="patient-reported", ts="2026-09-30T00:00:00Z",
    )
    result = projection.project([ingested(DEVICE), proposed], "2026-09-30T00:05:00Z")
    entity = result.entities["person:dr-nguyen"]
    assert entity.slots["contact"].review_state == reconcile.REVIEW_AUTO


def test_a_tier_disagreement_attaches_to_the_entity_it_concerns():
    """Settled decision: a subject-less anomaly stays in the report; this one
    also lands on the page a reader would check.

    "The payload said low and the code says high" is a fact about one
    medication, and someone asking whether that claim was gated correctly opens
    that medication's page, not the rebuild log.
    """
    proposed = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        consequence="low", occurred={"value": "2026-06-04"},
    )
    events = [ingested(DEVICE, "a3f91c"), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]
    result = projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)
    entity = result.entities["med:perindopril"]

    assert [note.subject_id for note in entity.anomalies] == ["med:perindopril"]
    page = result.files[entity.rel_path].decode("utf-8")
    assert "## Anomalies" in page
    assert "has no effect on review gating" in page
    # Cited like every other sentence on the page.
    line = next(l for l in page.splitlines() if "review gating" in l)
    assert "[^a3f91c]" in line

    # And still in the report, which is what phases 5 and 7 count.
    assert any("has no effect on review gating" in note for note in result.anomalies)


def test_a_subjectless_anomaly_stays_out_of_the_wiki():
    """A decision naming no target is integrity information about the log.

    It has no subject, so it has no page; inventing a wiki file for log plumbing
    would put it in front of a clinician reading the record.
    """
    from agent.events import envelope

    proposed = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2))
    blank = envelope.new("claim.confirmed", DEVICE, payload={}, ts=on_day(4))
    events = [
        ingested(DEVICE, "a3f91c"),
        proposed,
        confirm(DEVICE, proposed.id, ts=on_day(3)),
        blank,
    ]
    result = projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)

    assert any(blank.id in note for note in result.anomalies)
    assert result.entities["med:perindopril"].anomalies == ()
    assert not any(blank.id in page.decode("utf-8") for page in result.files.values())
