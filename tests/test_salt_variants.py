"""Salt-named labels, and the one entity per drug they have to land on.

Found by running extraction over the demo vault: the model read the full salt
name off every label — ``PERINDOPRIL ARGININE``, ``METFORMIN HYDROCHLORIDE``,
``LEVOTHYROXINE SODIUM`` — and each minted its own entity. The medication list
showed every drug twice.

The doubled list is the visible half. The half that matters is in
:func:`test_a_split_entity_would_break_staleness`: with the evidence split
across two entities, one copy ages to ``stale`` while the other stays
``active``, so the mechanism meant to catch a medication that was quietly
dropped stops working on exactly the drugs a real Australian script names by
salt — which is nearly all of them.
"""

from __future__ import annotations

from agent import projection
from agent.projection import drugs, entities as entities_mod, reconcile, subjects
from .conftest import claim, confirm, correct, ingested, merge, on_day

DEVICE = "elwood-laptop"
AS_OF = "2026-09-30T00:00:00Z"


def _project(events, as_of=AS_OF):
    return projection.project(sorted(events, key=lambda e: e.sort_key), as_of)


def _page(result, subject_id):
    return result.files[result.entities[subject_id].rel_path].decode("utf-8")


def _script(subject, value, artifact, day, name=None, dispense=None, tier="prescriber-issued"):
    """One confirmed prescriber-issued claim, with the label's own wording."""
    extra = {"subject_name": name} if name else {}
    if dispense is not None:
        extra["dispense"] = dispense
    proposed = claim(
        DEVICE, subject, "dose", value, ts=on_day(day), artifact=artifact,
        occurred={"value": f"2026-06-{day:02d}"}, tier=tier, **extra,
    )
    return [proposed, confirm(DEVICE, proposed.id, ts=on_day(day + 1))]


# --- the table itself ------------------------------------------------------


def test_the_table_is_well_formed():
    """Every pair's base is a prefix of its variant, so the salt read off as the
    remainder cannot drift away from the two columns it came from."""
    for slug, variant in drugs.VARIANTS.items():
        assert slug == variant.variant
        assert subjects.parse(f"med:{variant.variant}") is not None
        assert subjects.parse(f"med:{variant.base}") is not None
        assert variant.salt
        assert variant.variant.startswith(variant.base + "-")


def test_no_base_is_itself_a_variant():
    """A two-step alias would mean the table disagreed with itself."""
    assert drugs.BASES.isdisjoint(drugs.VARIANTS)


def test_the_common_australian_salts_alias():
    for written, base in (
        ("med:perindopril-arginine", "med:perindopril"),
        ("med:metformin-hydrochloride", "med:metformin"),
        ("med:levothyroxine-sodium", "med:levothyroxine"),
        ("med:atorvastatin-calcium", "med:atorvastatin"),
    ):
        assert drugs.alias_for(subjects.parse(written)) == base


def test_modified_release_salts_are_deliberately_absent():
    """Metoprolol tartrate and succinate are not two labels for one therapy.

    Tartrate is immediate release and succinate is modified release; they are
    dosed differently and are not interchangeable, so two entities is the
    *correct* answer. Absence from the table is a decision, and this test is
    here so that a later pass "completing" the table has to argue with it.
    """
    for written in ("med:metoprolol-tartrate", "med:metoprolol-succinate"):
        assert drugs.alias_for(subjects.parse(written)) is None


def test_nothing_but_medications_is_touched():
    """A drug vocabulary has nothing to say about an allergen or a diagnosis."""
    assert drugs.alias_for(subjects.parse("allergy:sulfonamides")) is None
    assert drugs.alias_for(subjects.parse("problem:hypothyroidism")) is None
    assert drugs.salt_of(subjects.parse("allergy:penicillin-sodium")) is None


def test_a_plain_name_aliases_to_nothing():
    assert drugs.alias_for(subjects.parse("med:perindopril")) is None
    assert drugs.salt_of(subjects.parse("med:perindopril")) is None


# --- what the projection does with them ------------------------------------


def test_a_salt_variant_lands_on_the_base_drug():
    events = [ingested(DEVICE, "a3f91c")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    result = _project(events)

    assert "med:perindopril" in result.entities
    assert "med:perindopril-arginine" not in result.entities
    assert result.entities["med:perindopril"].slots["dose"].winner.value.literal == "5mg daily"


def test_an_aliased_variant_gets_no_stub_page():
    """A confirmed merge keeps one because it records a user decision that must
    stay visible and reversible. A table alias is normalisation — a rebuild from
    scratch would never have created the page — and keeping stubs would
    accumulate one per salt ever photographed."""
    events = [ingested(DEVICE, "a3f91c")]
    events += _script(
        "med:metformin-hydrochloride", "500mg twice daily", "a3f91c", 4,
        name="Metformin Hydrochloride",
    )
    result = _project(events)

    assert "med:metformin-hydrochloride" not in result.entities
    assert not any("hydrochloride" in path for path in result.files)
    # And it is not claimed as a merge, either.
    assert result.entities["med:metformin"].merged_from == ()


def test_a_confirmed_merge_still_keeps_its_stub():
    """The distinction this rests on, asserted from the other side."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script("med:panadol", "500mg as needed", "a3f91c", 4)
    events += _script("med:paracetamol", "500mg as needed", "77b210", 6)
    events.append(
        merge(DEVICE, "med:panadol", "med:paracetamol", ts=on_day(8))
    )
    result = _project(events)

    stub = result.entities["med:panadol"]
    assert stub.merged_into == "med:paracetamol"
    assert stub.is_stub
    assert "med:paracetamol" in result.entities["med:panadol"].merged_into


def test_a_split_entity_would_break_staleness():
    """The finding that made this urgent rather than cosmetic.

    An old script and a recent one for the same drug, the recent one labelled
    with the salt. Filed apart, the old entity ages to `stale` while the new one
    sits `active` — so the list shows a drug twice *and* the mechanism meant to
    catch a dropped medication is looking at half the evidence. Filed together,
    the recent script confirms the drug and staleness is computed once.
    """
    supply = {"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"}
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    # An old script, plain name, long since exhausted.
    old = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts="2026-01-05T09:00:00Z",
        artifact="a3f91c", occurred={"value": "2026-01-04"}, dispense=supply,
    )
    events += [old, confirm(DEVICE, old.id, ts="2026-01-05T18:00:00Z")]
    # A recent one off a salt-named label.
    recent = claim(
        DEVICE, "med:perindopril-arginine", "dose", "5mg daily",
        ts="2026-09-20T09:00:00Z", artifact="77b210",
        occurred={"value": "2026-09-19"}, dispense=supply,
        subject_name="Perindopril Arginine",
    )
    events += [recent, confirm(DEVICE, recent.id, ts="2026-09-20T18:00:00Z")]

    entity = _project(events).entities["med:perindopril"]

    assert entity.status == entities_mod.ACTIVE
    assert entity.stale is False, (
        "the recent script confirms the drug; only a split entity would age on it"
    )
    assert entity.last_confirmed.iso == "2026-09-19"


def test_a_merge_confirmed_against_the_base_drug_still_catches_a_variant():
    """Table first, then merges. A merge the user confirmed was made against the
    drug they saw on a page — the base — so a variant id that skipped
    normalisation would slip straight past it."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    events += _script("med:coversyl", "5mg daily", "77b210", 6)
    events.append(merge(DEVICE, "med:perindopril", "med:coversyl", ts=on_day(8)))
    result = _project(events)

    assert result.entities["med:coversyl"].slots["dose"].winner is not None
    assert "med:perindopril-arginine" not in result.entities


# --- the label's own words survive -----------------------------------------


def test_the_claim_carries_the_wording_the_source_used():
    events = [ingested(DEVICE, "a3f91c")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    result = _project(events)
    winner = result.entities["med:perindopril"].slots["dose"].winner

    assert winner.subject_literal == "Perindopril Arginine"
    assert winner.salt == "arginine"


def test_an_event_without_the_field_falls_back_to_the_slug():
    """Older logs were written before `subject_name` existed and must render
    exactly as they did."""
    events = [ingested(DEVICE, "a3f91c")]
    events += _script("med:perindopril", "5mg daily", "a3f91c", 4)
    winner = _project(events).entities["med:perindopril"].slots["dose"].winner

    assert winner.subject_literal == "Perindopril"


def test_a_correction_inherits_the_wording_it_is_correcting():
    """A correction is a better reading of the same thing, not a new name."""
    events = [ingested(DEVICE, "a3f91c")]
    misread = claim(
        DEVICE, "med:levothyroxine-sodium", "dose", "5Omcg daily", ts=on_day(4),
        artifact="a3f91c", occurred={"value": "2026-06-04"},
        subject_name="Levothyroxine Sodium",
    )
    events += [
        misread,
        correct(DEVICE, value="50mcg daily", target=misread.id, ts=on_day(5)),
    ]
    winner = _project(events).entities["med:levothyroxine"].slots["dose"].winner

    assert winner.is_correction
    assert winner.subject_literal == "Levothyroxine Sodium"


def test_the_page_says_what_the_label_read_and_where_it_was_filed():
    events = [ingested(DEVICE, "a3f91c")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    page = _page(_project(events), "med:perindopril")

    assert "also_labelled: [Perindopril Arginine]" in page
    assert "## Names on sources" in page
    assert "labelled this “Perindopril Arginine” — the arginine salt" in page
    assert "filed here under `med:perindopril`" in page
    # Cited like every other sentence in the wiki.
    assert "[^a3f91c]" in page.split("## Names on sources")[1]


def test_a_reading_off_a_salt_label_says_which_salt_on_its_own_line():
    """Perindopril arginine 5mg is the equivalent of erbumine 4mg, so two
    readings on one page can differ by a milligram and describe the same
    therapy. A reader can only tell if each line says which salt it came off."""
    events = [ingested(DEVICE, "a3f91c")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    page = _page(_project(events), "med:perindopril")

    assert "5mg daily (prescriber-issued, labelled Perindopril Arginine" in page


def test_a_page_with_no_salt_variant_says_nothing_about_salts():
    events = [ingested(DEVICE, "a3f91c")]
    events += _script("med:perindopril", "5mg daily", "a3f91c", 4)
    page = _page(_project(events), "med:perindopril")

    assert "## Names on sources" not in page
    assert "also_labelled" not in page
    assert "labelled" not in page


def test_two_salts_disagreeing_on_the_number_conflict_with_both_named():
    """The outcome the table is arranged to produce rather than avoid. A false
    alarm cleared in one tap beats showing 4mg to somebody taking 5mg."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    events += _script(
        "med:perindopril-erbumine", "4mg daily", "77b210", 4, name="Perindopril Erbumine"
    )
    result = _project(events)
    entity = result.entities["med:perindopril"]
    page = _page(result, "med:perindopril")

    assert entity.status == entities_mod.CONFLICTED
    assert "labelled Perindopril Arginine" in page
    assert "labelled Perindopril Erbumine" in page


# --- the rejected reading still never renders ------------------------------


def test_a_rejected_variant_discloses_nothing():
    """Rejected content never renders, under any heading — and "Names on
    sources" is a heading."""
    from .conftest import reject

    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script("med:perindopril", "5mg daily", "a3f91c", 4)
    misheard = claim(
        DEVICE, "med:perindopril-erbumine", "dose", "4mg daily", ts=on_day(6),
        artifact="77b210", occurred={"value": "2026-06-06"},
        subject_name="Perindopril Erbumine",
    )
    events += [misheard, reject(DEVICE, misheard.id, ts=on_day(7))]
    page = _page(_project(events), "med:perindopril")

    assert "Erbumine" not in page
    assert "erbumine" not in page


# --- one review item per fact, not per source ------------------------------
#
# Two `allergy:sulfonamides` items reading exactly "waiting for you to confirm
# it", with nothing on either line to tell them apart. The data to distinguish
# them was on the items all along; the point is that they should not have been
# two items at all.


def _pending(subject, predicate, value, artifact, day, name=None):
    extra = {"subject_name": name} if name else {}
    return claim(
        DEVICE, subject, predicate, value, ts=on_day(day), artifact=artifact,
        occurred={"value": f"2026-06-{day:02d}"}, **extra,
    )


def _awaiting(result):
    return [
        item for item in result.review if item.kind == reconcile.AWAITING
    ]


def test_two_documents_asserting_one_fact_are_one_review_item():
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events.append(_pending("allergy:sulfonamides", "reaction", "swelling", "a3f91c", 4))
    events.append(_pending("allergy:sulfonamides", "reaction", "swelling", "77b210", 6))
    items = _awaiting(_project(events))

    assert len(items) == 1
    assert len(items[0].claims) == 2
    assert sorted(c.artifact for c in items[0].claims) == ["77b210", "a3f91c"]


def test_the_folded_item_keeps_every_citation_so_one_tap_decides_both():
    """Confirming is one tap emitting one `claim.confirmed` per claim; the item
    has to carry every claim for phase 7 to be able to do that."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events.append(_pending("allergy:sulfonamides", "reaction", "swelling", "a3f91c", 4))
    events.append(_pending("allergy:sulfonamides", "reaction", "swelling", "77b210", 6))
    item = _awaiting(_project(events))[0]

    assert {c.event_id for c in item.claims} == {
        e.id for e in events if e.type == "claim.proposed"
    }


def test_a_normalised_match_folds_even_when_the_wording_differs():
    """Keyed on the normalised value, so two scripts writing one dose two ways
    are still one fact."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events.append(_pending("med:perindopril", "dose", "5mg daily", "a3f91c", 4))
    events.append(_pending("med:perindopril", "dose", "5.0 mg daily", "77b210", 6))
    items = _awaiting(_project(events))

    assert len(items) == 1
    assert len(items[0].claims) == 2


def test_different_values_stay_separate_because_that_is_a_conflict():
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events.append(_pending("med:perindopril", "dose", "5mg daily", "a3f91c", 4))
    events.append(_pending("med:perindopril", "dose", "10mg daily", "77b210", 6))
    items = _awaiting(_project(events))

    assert len(items) == 2
    assert {c.value.literal for item in items for c in item.claims} == {
        "5mg daily", "10mg daily"
    }


def test_salt_variants_of_one_drug_fold_into_one_review_item():
    """The case that produced the duplicates: two labels, one fact, one tap."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events.append(
        _pending("med:perindopril", "dose", "5mg daily", "a3f91c", 4)
    )
    events.append(
        _pending(
            "med:perindopril-arginine", "dose", "5mg daily", "77b210", 6,
            name="Perindopril Arginine",
        )
    )
    items = _awaiting(_project(events))

    assert len(items) == 1
    assert items[0].subject_id == "med:perindopril"


def test_a_folded_item_is_still_one_line_on_the_entity_page():
    """A confirmed claim so the entity has a page at all, then two pending
    readings of one dose off two scripts."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script("med:perindopril", "5mg daily", "a3f91c", 2)
    events.append(_pending("med:perindopril", "status", "stopped", "a3f91c", 4))
    events.append(_pending("med:perindopril", "status", "stopped", "77b210", 6))
    page = _page(_project(events), "med:perindopril")

    assert page.count("waiting for you to review it in the inbox") == 1


# --- the merge review kind, ahead of its producer --------------------------


def test_nothing_emits_a_merge_proposal_yet():
    """A brand-name proposer is phase 7. The salt table covers the
    deterministic case without a tap, which is why this can wait."""
    events = [ingested(DEVICE, "a3f91c")]
    events += _script("med:panadol", "500mg as needed", "a3f91c", 4)
    result = _project(events)

    assert not any(item.kind == reconcile.MERGE_PROPOSED for item in result.review)


def test_a_merge_proposal_renders_where_phase_7_will_put_it():
    """The kind exists now so the page already accounts for it. A review item
    the queue counts and no page renders is a decision waiting with nowhere to
    be seen, which is what rule 3 forbids."""
    import datetime

    from agent.projection import citations as citations_mod
    from agent.projection.citations import Citer
    from agent.projection.pages import entity_page
    from agent.projection.reconcile import ReviewItem

    events = [ingested(DEVICE, "a3f91c")]
    events += _script("med:panadol", "500mg as needed", "a3f91c", 4)
    result = _project(events)
    entity = result.entities["med:panadol"]

    proposal = ReviewItem(
        kind=reconcile.MERGE_PROPOSED,
        consequence="high",
        subject_id="med:panadol",
        predicate="name",
        summary="med:panadol may be the same thing as med:paracetamol",
        claims=entity.slots["dose"].supporting,
    )
    rebuilt = entity_page(
        entities_mod.build(
            entity.subject,
            entity.slots,
            datetime.datetime(2026, 9, 30, tzinfo=datetime.timezone.utc),
            review=(proposal,),
        ),
        Citer(
            citations_mod.index_artifacts(sorted(events, key=lambda e: e.sort_key)),
            sorted(events, key=lambda e: e.sort_key),
        ),
        datetime.date(2026, 9, 30),
    ).decode("utf-8")

    assert "## Needs review" in rebuilt
    assert "may be the same thing as med:paracetamol" in rebuilt


# --- a restatement is not a correction -------------------------------------


def test_a_repeat_script_stating_the_same_dose_reports_no_change():
    """"5mg daily, replaced by 5mg daily" tells a reader a change happened where
    none did, and teaches them to skim the one section whose job is to make a
    real change auditable. The source stays in `sources:`."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script("med:perindopril", "5mg daily", "a3f91c", 4)
    events += _script("med:perindopril", "5mg daily", "77b210", 6)
    page = _page(_project(events), "med:perindopril")

    assert "## Earlier readings" not in page
    assert "sources: [77b210, a3f91c]" in page


def test_the_same_number_off_a_different_salt_is_still_reported():
    """Perindopril arginine 5mg and erbumine 5mg are not the same statement, so
    the value matching is not enough to call it a restatement."""
    events = [ingested(DEVICE, "a3f91c"), ingested(DEVICE, "77b210")]
    events += _script(
        "med:perindopril-arginine", "5mg daily", "a3f91c", 4, name="Perindopril Arginine"
    )
    events += _script(
        "med:perindopril-erbumine", "5mg daily", "77b210", 6, name="Perindopril Erbumine"
    )
    page = _page(_project(events), "med:perindopril")

    assert "## Earlier readings" in page


def test_a_real_correction_is_never_suppressed_as_a_restatement():
    """The section's actual job, asserted so the narrowing cannot swallow it."""
    events = [ingested(DEVICE, "a3f91c")]
    misread = claim(
        DEVICE, "med:levothyroxine", "dose", "5Omcg daily", ts=on_day(4),
        artifact="a3f91c", occurred={"value": "2026-06-04"},
    )
    events += [
        misread,
        correct(DEVICE, value="50mcg daily", target=misread.id, ts=on_day(5)),
    ]
    page = _page(_project(events), "med:levothyroxine")

    assert "## Earlier readings" in page
    assert "5Omcg daily" in page


def test_the_timeline_shows_one_drug_and_links_to_the_entity_it_was_filed_under():
    """A salt-named row must not read as a second medication.

    Found by rendering the interface against the demo vault. The timeline row
    for a ``PERINDOPRIL ARGININE`` label printed ``perindopril-arginine`` — the
    normalised slug, which is neither the label's wording nor the entity's name
    — directly above a row for ``Perindopril``. Two lines, two apparent drugs,
    and the first of them linked to a page that does not exist, because an
    aliased entity deliberately gets no stub.

    The alias table exists to stop exactly that doubling in the medication list;
    it has to hold on the timeline too, which is the screen a clinician actually
    scans. Where the label's own wording is disclosed is the entity page, which
    is what the settled decision says and where there is room for the sentence.
    """
    events = [
        ingested(DEVICE, "a3f91c", ts=on_day(1)),
        *_script("med:perindopril-arginine", "5mg daily", "a3f91c", 2,
                 name="Perindopril Arginine"),
        *_script("med:perindopril", "5mg daily", "a3f91c", 3, name="Perindopril"),
    ]
    result = _project(events)

    rows = [row for row in result.rows if row.subject_id]
    assert rows, "no claim rows were built"

    # Every row points at an entity that exists, so every link resolves.
    for row in rows:
        assert row.subject_id in result.entities, (
            f"a timeline row is filed under {row.subject_id}, which has no page"
        )

    # And the salt spelling is not presented as a second drug.
    med_rows = [row for row in rows if row.subject_id == "med:perindopril"]
    assert len(med_rows) == 2, "the two readings did not land on one entity"
    for row in med_rows:
        assert "perindopril-arginine" not in row.text
        assert row.text.startswith("Perindopril —")


def test_the_label_wording_is_still_disclosed_on_the_entity_page():
    """Moving the timeline to the base name must not lose the disclosure."""
    events = [
        ingested(DEVICE, "a3f91c", ts=on_day(1)),
        *_script("med:levothyroxine-sodium", "50mcg daily", "a3f91c", 2,
                 name="Levothyroxine Sodium"),
    ]
    result = _project(events)
    page = _page(result, "med:levothyroxine")

    # Frontmatter is the machine-readable canonical state, so the disclosure
    # lives there whether or not the body has a sentence's worth of context.
    assert "also_labelled: [Levothyroxine Sodium]" in page
    assert "id: med:levothyroxine" in page
