"""The read routes say what the wiki says, and no more.

The sharp test here is the one about rejected content. ``CLAUDE.md``: "A
rejection is a retraction, and printing the content re-asserts what the user
said is not true of them. This matters because the wiki gets handed to a
clinician: someone who rejects a mis-OCR'd 'alcohol dependence' must not find it
on their problems page under any heading. No ``## Rejected`` section, not in
entity pages, not in exports." An API is a new surface for exactly that failure,
and the temptation is specific: "entity + full source chain" reads like an
instruction to assemble the chain from the event log, which is the one place the
rejected reading is still written down.

So the assertions walk the *entire* serialised response — every route, not just
the entity one — for the rejected string. Building from reconciled slots is what
makes that hold; a serialiser that reached past them would fail here.

The counterpart is also tested: a **superseded** reading does appear, with its
citation and what replaced it, because "what did I correct, and from what" has to
be answerable from the record alone.
"""

from __future__ import annotations

import json

import pytest

from .conftest import claim, confirm, correct, ingested, merge, note, on_day, reject

REJECTED_TEXT = "alcohol dependence"


@pytest.fixture
def record(vault):
    """A record with one of each awkward shape the routes have to render."""
    device = vault.identity.id
    events = [
        ingested(device, "a3f91c", ts=on_day(1), artifact_ts="2026-06-04T00:00:00Z"),
        ingested(device, "77b210", ts=on_day(2), mime="application/pdf"),
        note(device, ts=on_day(2, hour=18), text="Headaches started around Easter."),
    ]

    # A confirmed medication.
    dose = claim(
        device, "med:perindopril", "dose", {"amount": 5, "unit": "mg", "frequency": "daily"},
        ts=on_day(3), occurred={"value": "2026-06-04", "precision": "day"},
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
    )
    events += [dose, confirm(device, dose.id, ts=on_day(3, hour=11))]

    # A misread that the user corrected. The original must stay visible.
    misread = claim(
        device, "med:atorvastatin", "dose", "2mg daily", ts=on_day(4), artifact="77b210",
        occurred={"value": "2026-08-15"},
    )
    events += [misread, correct(device, value="20mg daily", target=misread.id, ts=on_day(4, hour=12))]

    # Two prescriber-issued sources disagreeing. Both render, neither wins.
    first = claim(device, "med:metformin", "dose", "500mg twice daily", ts=on_day(5), artifact="a3f91c")
    second = claim(device, "med:metformin", "dose", "850mg twice daily", ts=on_day(5, hour=11), artifact="77b210")
    events += [
        first, confirm(device, first.id, ts=on_day(6)),
        second, confirm(device, second.id, ts=on_day(6, hour=11)),
    ]

    # A reading the user rejected. Its content must never surface again.
    bad = claim(
        device, "problem:alcohol-dependence", "diagnosis", REJECTED_TEXT,
        ts=on_day(7), artifact="a3f91c",
    )
    events += [bad, reject(device, bad.id, ts=on_day(7, hour=12))]

    # A high-consequence claim nobody has confirmed.
    events.append(claim(device, "allergy:penicillin", "reaction", "anaphylaxis", ts=on_day(8)))

    for event in sorted(events, key=lambda e: e.sort_key):
        vault.append(event)
    return vault


def test_rejected_content_appears_in_no_response_anywhere(record, client):
    """Every route, every field. A retraction has to hold on all of them."""
    routes = [
        "/api/wiki",
        "/api/timeline",
        "/api/medications",
        "/api/review",
        "/api/artifacts",
        "/api/health",
    ]
    for route in routes:
        body = json.dumps(client.get(route).json())
        assert REJECTED_TEXT not in body, f"{route} reproduced a rejected reading"

    # The entity was created only by the rejected claim, so it should not exist
    # at all — and the 404 must not quote what was rejected either.
    response = client.get("/api/wiki/problem:alcohol-dependence")
    assert response.status_code == 404
    assert REJECTED_TEXT not in json.dumps(response.json())


def test_a_rejected_claim_leaves_no_section_to_hold_it(record, client):
    """There is no ``rejected`` key in the shape, so nothing can populate one."""
    for entity in client.get("/api/wiki").json()["kinds"]["med"]:
        detail = client.get(f"/api/wiki/{entity['id']}").json()
        assert "rejected" not in detail
        for slot in detail["slots"]:
            assert "rejected" not in slot


def test_superseded_readings_are_rendered_with_what_replaced_them(record, client):
    """A correction whose original has vanished is unverifiable."""
    detail = client.get("/api/wiki/med:atorvastatin").json()
    dose = next(s for s in detail["slots"] if s["predicate"] == "dose")

    assert dose["winner"]["value"]["literal"] == "20mg daily"
    assert dose["winner"]["is_correction"] is True

    superseded = [c["value"]["literal"] for c in dose["superseded"]]
    assert "2mg daily" in superseded
    # And it keeps its own citation, so the original document is still reachable.
    original = next(c for c in dose["superseded"] if c["value"]["literal"] == "2mg daily")
    assert original["citation"]["artifact"] == "77b210"
    assert original["citation"]["url"] == "/api/artifact/77b210"


def test_conflicting_sources_render_both_and_neither_wins(record, client):
    """Do not silently pick one. Do not average."""
    detail = client.get("/api/wiki/med:metformin").json()
    assert detail["status"] == "conflicted"
    assert detail["conflicted"] is True

    dose = next(s for s in detail["slots"] if s["predicate"] == "dose")
    assert dose["conflicted"] is True
    literals = sorted(c["value"]["literal"] for c in dose["readings"])
    assert literals == ["500mg twice daily", "850mg twice daily"]
    # Each reading keeps its own source, which is what makes the conflict
    # resolvable by a person rather than a coin toss.
    assert {c["citation"]["artifact"] for c in dose["readings"]} == {"a3f91c", "77b210"}


def test_a_subject_known_only_from_a_pending_claim_has_no_page(record, client):
    """An unconfirmed allergy is not an allergy the record has.

    Invariant 5: a high-consequence claim "never enters the wiki without an
    explicit user tap." Creating a page for a subject whose only claim is
    awaiting that tap would *be* the claim entering the wiki — the page's
    existence asserts the allergy however carefully the body is worded. So there
    is no entity, and the proposal lives in the review queue until someone
    decides.
    """
    assert client.get("/api/wiki/allergy:penicillin").status_code == 404
    assert client.get("/api/wiki").json()["kinds"]["allergy"] == []

    # It is not lost, though: the queue names it and cites its source.
    review = client.get("/api/review").json()
    assert review["counts"]["by_tier"]["high"] >= 1
    high = review["tiers"]["high"]
    awaiting = [item for item in high if item["subject_id"] == "allergy:penicillin"]
    assert awaiting, "a gated claim vanished instead of queuing"
    assert awaiting[0]["citations"], "the source is still named"

    # And nothing anywhere prints what it said.
    assert "anaphylaxis" not in json.dumps(review)
    assert "anaphylaxis" not in json.dumps(client.get("/api/wiki").json())
    assert "anaphylaxis" not in json.dumps(client.get("/api/timeline").json())


def test_a_pending_change_to_an_existing_entity_hides_its_value(vault, client):
    """The page says a change is waiting, and cites it, without printing it.

    A proposed value printed beside the current one gets read as current, which
    is the failure the entity page avoids by naming the predicate and the source
    and stopping there. The API mirrors the page.
    """
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    confirmed = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c",
    )
    vault.append(confirmed)
    vault.append(confirm(device, confirmed.id, ts=on_day(3)))
    # A later reading nobody has confirmed.
    vault.append(
        claim(device, "med:perindopril", "dose", "40mg daily", ts=on_day(4), artifact="a3f91c")
    )

    detail = client.get("/api/wiki/med:perindopril").json()
    dose = next(s for s in detail["slots"] if s["predicate"] == "dose")

    assert dose["winner"]["value"]["literal"] == "5mg daily"
    assert dose["pending"]["count"] == 1
    assert dose["pending"]["citations"], "the proposal's source is still named"
    assert "40mg" not in json.dumps(detail), "a gated value was printed"


def test_entity_detail_carries_the_generated_markdown(record, client):
    """The words the folder holds, not a second rendering that could disagree."""
    detail = client.get("/api/wiki/med:perindopril").json()
    assert detail["markdown"].startswith("---\n")
    assert "id: med:perindopril" in detail["markdown"]
    assert detail["path"] == "wiki/medications/perindopril.md"


@pytest.mark.parametrize(
    "bad",
    [
        "med:..%2F..%2Fetc%2Fpasswd",
        "med:%2Fetc%2Fpasswd",
        "med:../../etc/passwd",
        "notakind:thing",
        "nocolon",
        "med:",
        "med:UPPER",
    ],
)
def test_an_unparseable_subject_is_refused_rather_than_cleaned(client, bad):
    """The subject parser is the path-safety boundary, and it never repairs.

    A sanitised subject is a claim silently filed under the wrong name, so
    ``med:../../etc/passwd`` does not parse at all rather than being cleaned
    into something plausible. Whatever a client sends, the answer is a refusal
    naming the rule — never a file.
    """
    response = client.get(f"/api/wiki/{bad}")
    assert response.status_code in (400, 404)
    detail = response.json()["detail"]
    assert any(
        phrase in detail
        for phrase in ("unusable slug", "no kind prefix", "unknown kind",
                       "nothing in this record is filed under")
    ), detail
    assert "root:" not in detail and "/bin/" not in detail


def test_timeline_says_which_of_the_four_dates_placed_each_row(record, client):
    """A row placed on ingest time and shown as when something happened is a lie."""
    rows = client.get("/api/timeline").json()["rows"]
    assert rows, "the timeline is empty"
    kinds = {row["date_kind"] for row in rows}
    assert kinds <= {"occurred", "document", "captured", "recorded"}
    for row in rows:
        assert row["date_kind"] in row["heading"] or row["date_label"] == "" or \
            row["date_label"] in row["heading"]
        assert row["date"]["band"]["start"] <= row["date"]["band"]["end"]


def test_timeline_is_newest_first(record, client):
    rows = client.get("/api/timeline").json()["rows"]
    dates = [row["date"]["iso"] for row in rows]
    assert dates == sorted(dates, reverse=True)


def test_timeline_range_matches_the_band_not_the_point(vault, client):
    """A fuzzy date overlapping the range belongs in it.

    Testing the point alone would drop exactly the rows whose dating is least
    certain — the ones someone scanning a range most needs to see.
    """
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    fuzzy = claim(
        device, "problem:headache", "onset", "gradual", ts=on_day(2),
        tier="patient-reported", artifact="a3f91c",
        occurred={"value": "2026-06-30", "precision": "day", "uncertainty_days": 10},
    )
    vault.append(fuzzy)
    vault.append(confirm(device, fuzzy.id, ts=on_day(3)))

    # The point is 30 June; the band reaches into July.
    rows = client.get("/api/timeline?from=2026-07-05&to=2026-07-08").json()["rows"]
    assert any(row["subject_id"] == "problem:headache" for row in rows)


def test_timeline_filters_by_entity_and_by_tier(record, client):
    only = client.get("/api/timeline?subject=med:metformin").json()
    assert only["total"] >= 1
    assert {row["subject_id"] for row in only["rows"]} == {"med:metformin"}

    tiered = client.get("/api/timeline?tier=artefact").json()
    assert {row["marker"] for row in tiered["rows"]} == {"artefact"}


def test_wiki_index_groups_by_kind_and_generates_the_medication_view(record, client):
    body = client.get("/api/wiki").json()
    assert set(body["kinds"]) == {"med", "allergy", "problem", "person"}
    names = {row["name"] for row in body["current_medications"]}
    assert "Perindopril" in names
    assert "Metformin" in names

    # The view is computed from the same entities listed beside it, so the two
    # cannot disagree — and it is never written to the vault.
    assert not (record.root / "wiki" / "current-medications.md").exists()


def test_a_stale_medication_stays_on_the_current_list(vault, client):
    """Absence of evidence is never evidence of absence."""
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    dose = claim(
        device, "med:perindopril", "dose", "5mg daily", ts=on_day(2), artifact="a3f91c",
        occurred={"value": "2024-01-02", "precision": "day"},
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
    )
    vault.append(dose)
    vault.append(confirm(device, dose.id, ts=on_day(3)))

    rows = client.get("/api/medications").json()["rows"]
    row = next(r for r in rows if r["id"] == "med:perindopril")
    assert row["stale"] is True
    assert row["status"] == "stale"
    # Still there. Never dropped for having gone quiet.
    assert row["expected_exhaustion"] is not None


def test_a_merge_stub_is_reachable_and_says_where_it_went(vault, client):
    """A confirmed merge keeps its page: it records a decision, reversibly."""
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    for subject in ("med:panadol", "med:paracetamol"):
        proposed = claim(
            device, subject, "dose", "500mg as needed", ts=on_day(2),
            tier="patient-reported", artifact="a3f91c",
        )
        vault.append(proposed)
        vault.append(confirm(device, proposed.id, ts=on_day(3)))
    vault.append(merge(device, "med:panadol", "med:paracetamol", ts=on_day(4)))

    stub = client.get("/api/wiki/med:panadol").json()
    assert stub["is_stub"] is True
    assert stub["merged_into"] == "med:paracetamol"

    target = client.get("/api/wiki/med:paracetamol").json()
    assert "med:panadol" in target["merged_from"]


def test_every_claim_carries_a_resolvable_citation(record, client):
    """A sentence in the wiki without a footnote is a bug. So is a row here."""
    for kind_rows in client.get("/api/wiki").json()["kinds"].values():
        for summary in kind_rows:
            detail = client.get(f"/api/wiki/{summary['id']}").json()
            for slot in detail["slots"]:
                for claim_json in [slot["winner"], *slot["superseded"], *slot["readings"]]:
                    if claim_json is None:
                        continue
                    assert claim_json["citation"]["key"]
                    assert claim_json["citation"]["text"]


def test_the_four_timestamps_are_reported_separately(record, client):
    detail = client.get("/api/wiki/med:perindopril").json()
    winner = next(s for s in detail["slots"] if s["predicate"] == "dose")["winner"]
    for field in ("occurred_at", "artifact_ts", "captured_ts", "ingested_ts"):
        assert field in winner
    # `captured_ts` is unknown for a file that was not captured live, and says
    # so rather than borrowing from a neighbour.
    assert winner["captured_ts"] is None


def test_a_stale_entry_says_how_long_using_the_projections_clock(vault):
    """"8 months ago", and computed on the server.

    ``CLAUDE.md``'s own example of a stale entry is ``stale — last confirmed 8
    months ago``, and the phrase is the point: a bare date makes a clinician do
    arithmetic while skimming. The wiki page already says it, so the screen has
    to as well or the two describe one medication differently — which is the
    divergence this was found by reading both.

    It is computed from ``as_of``, never from the client's clock. A second clock
    in the browser would disagree with the one the whole record is derived
    against, quietly, and by exactly the amount that matters around midnight.
    """
    from datetime import datetime, timezone

    from .conftest import api_client

    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    dose = claim(
        device, "med:metformin", "dose", "500mg twice daily", ts=on_day(2),
        artifact="a3f91c",
        occurred={"value": "2026-01-13", "precision": "day"},
        dispense={"quantity": "60 tablets", "frequency": "twice daily",
                  "repeats": "no repeats"},
    )
    vault.append(dose)
    vault.append(confirm(device, dose.id, ts=on_day(3)))

    pinned = datetime(2026, 9, 10, tzinfo=timezone.utc)
    with api_client(vault, clock=lambda: pinned) as client:
        row = next(
            r for r in client.get("/api/medications").json()["rows"]
            if r["id"] == "med:metformin"
        )
        detail = client.get("/api/wiki/med:metformin").json()

    assert row["stale"] is True
    assert row["last_confirmed_ago"] == "8 months ago"
    assert detail["last_confirmed_ago"] == "8 months ago"

    # And it moves with `as_of`, which is what proves it is not the wall clock.
    later = datetime(2027, 9, 10, tzinfo=timezone.utc)
    with api_client(vault, clock=lambda: later) as client:
        row = next(
            r for r in client.get("/api/medications").json()["rows"]
            if r["id"] == "med:metformin"
        )
    assert row["last_confirmed_ago"] == "20 months ago"


def test_a_current_entry_is_not_labelled_with_an_age(vault, client):
    """The phrase belongs to staleness. On an active entry it is noise."""
    device = vault.identity.id
    vault.append(ingested(device, "a3f91c", ts=on_day(1)))
    dose = claim(device, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
                 artifact="a3f91c")
    vault.append(dose)
    vault.append(confirm(device, dose.id, ts=on_day(3)))

    row = next(
        r for r in client.get("/api/medications").json()["rows"]
        if r["id"] == "med:perindopril"
    )
    assert row["stale"] is False
    # Still computed and sent — the interface decides whether to show it — but
    # the row is not stale, so nothing renders it.
    assert "last_confirmed_ago" in row
