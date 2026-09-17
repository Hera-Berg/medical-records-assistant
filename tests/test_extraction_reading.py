"""Reading an artefact: the schema, the prompt, dates, validation, images.

The single rule underneath most of this file is ``MODELS.md``'s: "**Validate,
then reject. Never repair.** A malformed claim that gets coerced into a valid one
is how a dose becomes wrong silently."

The other is that the model copies and Python computes. Several of these tests
assert on what the schema *lacks*, which is the strongest form the guarantee can
take: a field that does not exist cannot be filled in by a future prompt edit.
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from agent.extract import crossverify, dates as xdates, families, images, prompts, schema, validate
from agent.extract.text import DeterministicRead, METHOD_OCR, METHOD_TEXT_LAYER
from agent.projection import tiers


def _medication(**overrides):
    base = {
        "name": "Perindopril",
        "strength": "5mg",
        "frequency": "daily",
        "stopped": None,
        "dispense": None,
        "evidence_tier": "prescriber-issued",
        "occurred_at": None,
        "occurred_span": None,
        "source_span": "PERINDOPRIL 5mg — one daily",
        "confidence": 0.9,
    }
    return {**base, **overrides}


def _answer(**overrides):
    """What the medications turn answers."""
    base = {
        "artifact_kind": "prescription",
        "readable": True,
        "unreadable_reason": None,
        "document_date": None,
        "medications": [_medication()],
        "unclear": [],
    }
    return {**base, **overrides}


def _allergies(*items, unclear=()):
    return {"allergies": list(items), "unclear": list(unclear)}


def _allergy(**overrides):
    base = {
        "substance": "Penicillin",
        "reaction": "rash",
        "evidence_tier": "prescriber-issued",
        "occurred_at": None,
        "occurred_span": None,
        "source_span": "rash with penicillin",
        "confidence": 0.9,
    }
    return {**base, **overrides}


def _read(answer, mime="image/jpeg", family=schema.MEDICATIONS):
    return families.read(family, answer, mime=mime)


# --- what the schema does not contain --------------------------------------


def _every_schema():
    return [*schema.FAMILY_SCHEMAS.values(), *schema.TRANSCRIPT_SCHEMAS.values()]


def test_the_schema_has_no_consequence_field_anywhere():
    """Stronger than stripping one: there is no way to express a tier.

    "If the model could label something low-consequence, a bad extraction could
    route itself around review."
    """
    rendered = json.dumps(_every_schema())

    assert "consequence" not in rendered
    for name in tiers.CONSEQUENCE_TIERS:
        assert f'"{name}"' not in rendered


def _property_names(node, found=None):
    """Every field name the schema defines, at any depth."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for name, sub in node.get("properties", {}).items():
            found.add(name)
            _property_names(sub, found)
        for key in ("items",):
            if key in node:
                _property_names(node[key], found)
    return found


def test_the_schema_has_no_field_for_a_computed_number():
    """The model copies spans; Python counts. There is nowhere to put an answer."""
    names = set()
    for item in _every_schema():
        _property_names(item, names)

    for computed in ("days_supply", "expected_exhaustion", "total", "exhaustion", "dose"):
        assert computed not in names
    assert "quantity" in names, "the spans themselves are still there"


def test_every_group_offers_a_place_to_say_it_could_not_read_something():
    for family, item in schema.FAMILY_SCHEMAS.items():
        assert "unclear" in item["required"], family


def test_the_dispense_block_is_only_literal_spans():
    fields = schema._DISPENSE["properties"]

    assert set(fields) == {"quantity", "frequency", "repeats", "dose_units"}
    for spec in fields.values():
        assert spec["type"] == ["string", "null"], "spans, never numbers"


def test_the_schema_offers_only_kinds_that_have_somewhere_to_live():
    from agent.projection.subjects import KIND_DIRS

    assert set(schema.SUBJECT_KINDS) == set(KIND_DIRS)


def test_the_consequence_tier_is_looked_up_not_read_from_the_answer():
    (claim,) = _read(_answer()).claims

    assert claim.consequence == tiers.HIGH
    assert claim.consequence == tiers.consequence_for("med", "dose")


# --- validate, then reject -------------------------------------------------


def test_a_valid_answer_reads_cleanly():
    read = _read(_answer())

    assert read.readable
    assert read.rejections == () and read.abstentions == ()
    (claim,) = read.claims
    assert claim.subject == "med:perindopril"
    assert claim.predicate == "dose"
    assert claim.value_literal == "5mg daily", "strength and frequency, both as copied"


@pytest.mark.parametrize(
    "answer, expected",
    [
        ({"readable": True}, "missing"),
        ("a string", "must be object"),
        ([], "must be object"),
        (None, "must be object"),
    ],
)
def test_a_structurally_wrong_answer_yields_no_claims_at_all(answer, expected):
    read = _read(answer)

    assert read.claims == ()
    assert not read.readable and read.refused
    assert any(expected in r.reason for r in read.rejections)


def test_an_unexpected_field_is_a_rejection_not_something_to_ignore():
    read = _read(_answer(consequence="low"))

    assert read.claims == ()
    assert any("unexpected field 'consequence'" in r.reason for r in read.rejections)


def test_an_unreadable_page_is_a_first_class_answer():
    read = _read(_answer(readable=False, unreadable_reason="the photograph is out of focus"))

    assert not read.readable and not read.refused
    assert "out of focus" in read.unreadable_reason
    assert read.claims == ()


def test_an_out_of_range_confidence_is_rejected_not_clamped():
    read = _read(_answer(medications=[_medication(confidence=1.4)]))

    assert read.claims == ()
    assert any("at most 1" in r.reason for r in read.rejections)


# --- failing safely -------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["Atorvastatin 20mg tablets", "Atorvastatin 20", "Metformin tablets", "   "]
)
def test_a_name_carrying_a_strength_or_form_is_unread_never_filed(name):
    """``med:atorvastatin-20`` means the dose leaked into the identifier."""
    read = _read(_answer(medications=[_medication(name=name)]))

    assert read.claims == ()
    (abstention,) = read.abstentions
    assert abstention.field == "name" and abstention.subject is None


def test_a_strength_without_its_frequency_proposes_no_dose():
    """A dose without its frequency is not the dose on the page."""
    read = _read(_answer(medications=[_medication(frequency=None)]))

    assert read.claims == ()
    (abstention,) = read.abstentions
    assert abstention.subject == "med:perindopril"
    assert abstention.field == "frequency"


def test_a_frequency_without_its_strength_proposes_no_dose_either():
    read = _read(_answer(medications=[_medication(strength=None)]))

    assert read.claims == ()
    assert read.abstentions[0].field == "strength"


def test_a_medicine_named_with_no_dose_on_the_page_is_still_recorded():
    """Neither a dose nor silence: the page names it, and the record keeps that."""
    read = _read(_answer(medications=[_medication(strength=None, frequency=None)]))

    (claim,) = read.claims
    assert (claim.subject, claim.predicate, claim.value_literal) == (
        "med:perindopril", "name", "Perindopril"
    )
    assert read.abstentions == ()


def test_what_the_reader_says_it_cannot_read_becomes_an_abstention():
    unclear = [
        {"name_if_readable": "Metformin", "field": "frequency", "reason": "handwriting", "source_span": "Metformin 500mg tw…"},
        {"name_if_readable": None, "field": "whole entry", "reason": "blurred", "source_span": None},
    ]
    read = _read(_answer(medications=[], unclear=unclear))

    assert read.claims == ()
    named, nameless = read.abstentions
    assert named.subject == "med:metformin" and named.field == "frequency"
    assert nameless.subject is None and nameless.reason == "blurred"


def test_a_stopped_medicine_is_a_status_claim():
    read = _read(_answer(medications=[_medication(strength=None, frequency=None, stopped="I stopped the sertraline", name="Sertraline")]))

    (claim,) = read.claims
    assert (claim.subject, claim.predicate, claim.value_literal) == ("med:sertraline", "status", "stopped")


def test_an_allergy_reads_as_its_reaction():
    read = _read(_allergies(_allergy()), family=schema.ALLERGIES)

    (claim,) = read.claims
    assert (claim.subject, claim.predicate, claim.value_literal) == ("allergy:penicillin", "reaction", "rash")


def test_an_allergy_with_a_number_in_the_substance_is_unread():
    read = _read(_allergies(_allergy(substance="Penicillin 500mg")), family=schema.ALLERGIES)

    assert read.claims == ()
    assert read.abstentions[0].field == "substance"


# --- the tier a claim is allowed to carry ----------------------------------


def test_a_recording_can_never_be_prescriber_issued():
    """The tier is a fact about the artefact, known before the model runs."""
    (claim,) = _read(_answer(), mime="audio/webm").claims

    assert claim.evidence_tier == "patient-reported"
    assert any("held at patient-reported" in note for note in claim.notes)


def test_a_photograph_keeps_the_tier_the_model_judged():
    assert _read(_answer()).claims[0].evidence_tier == "prescriber-issued"


def test_a_recording_claiming_a_weaker_tier_is_left_alone():
    read = _read(_answer(medications=[_medication(evidence_tier="inferred")]), mime="audio/webm")
    assert read.claims[0].evidence_tier == "inferred"


def test_the_voice_note_grammar_cannot_express_a_prescribers_authority():
    """A voice note is patient-reported, and the grammar is where that is settled."""
    item = schema.TRANSCRIPT_SCHEMAS[schema.MEDICATIONS]["properties"]["medications"]["items"]
    assert item["properties"]["evidence_tier"]["enum"] == ["patient-reported"]
    assert schema.TRANSCRIPT_SCHEMAS[schema.MEDICATIONS]["properties"]["artifact_kind"]["enum"] == [
        "note", "unreadable",
    ]
    document = schema.FAMILY_SCHEMAS[schema.MEDICATIONS]["properties"]["medications"]["items"]
    assert "prescriber-issued" in document["properties"]["evidence_tier"]["enum"]


# --- dates: enumerated, never guessed --------------------------------------


@pytest.mark.parametrize(
    "span, iso, precision",
    [
        ("2026-06-04", "2026-06-04", "day"),
        ("4 June 2026", "2026-06-04", "day"),
        ("4th June 2026", "2026-06-04", "day"),
        ("June 4, 2026", "2026-06-04", "day"),
        ("4 Jun 2026", "2026-06-04", "day"),
        ("June 2026", "2026-06-15", "month"),
        ("2026-06", "2026-06-15", "month"),
        ("2026", "2026-07-01", "year"),
        ("25/06/2026", "2026-06-25", "day"),
    ],
)
def test_the_enumerated_date_vocabulary(span, iso, precision):
    read = xdates.read(span, locale="en-au")
    assert read is not None
    assert read.value.isoformat() == iso
    assert read.precision == precision


def test_early_mid_late_keeps_month_precision_with_slack():
    read = xdates.read("late June 2026", locale="en")
    assert read.precision == "month"
    assert read.uncertainty_days == 5


@pytest.mark.parametrize(
    "span",
    [
        "around Easter",
        "last Christmas",
        "the week before the wedding",
        "a couple of months ago",
        "sometime after the operation",
        "when I was in hospital",
        "",
        None,
        "the 32nd of Boptember 2026",
    ],
)
def test_a_phrase_this_does_not_read_yields_nothing(span):
    """Never a guess. A wrong date corrupts a timeline the way a wrong quantity
    ages a medication to `stale` on fiction."""
    assert xdates.read(span) is None


def test_an_ambiguous_numeric_date_is_refused_where_the_locale_does_not_settle_it():
    """04/06/2026 is 4 June in Australia and 6 April in the United States."""
    assert xdates.read("04/06/2026", locale="en-au").value.isoformat() == "2026-06-04"
    assert xdates.read("04/06/2026", locale="en-us").value.isoformat() == "2026-04-06"
    assert xdates.read("04/06/2026", locale="xx-yy") is None


def test_a_model_cannot_sharpen_a_date_the_span_does_not_support():
    """"June 2026" stays month precision however it is labelled."""
    payload, problem = xdates.normalise(
        {"value": "June 2026", "precision": "day", "uncertainty_days": 0}
    )

    assert problem is None
    assert payload["precision"] == "month"


def test_an_unreadable_date_is_reported_rather_than_dropped_silently():
    payload, problem = xdates.normalise(
        {"value": "around Easter", "precision": "month", "uncertainty_days": 14}
    )

    assert payload is None
    assert "not in a form this build reads" in problem
    assert "guessed at" in problem


def test_an_unreadable_date_becomes_a_span_the_record_keeps():
    """The phrase is information the record exists to keep, so it survives.

    CLAUDE.md: unresolvable temporal references are preserved, never dropped and
    never auto-resolved.
    """
    answer = _answer(
        medications=[
            _medication(
                occurred_at={"value": "around Easter", "precision": "month", "uncertainty_days": 14}
            )
        ]
    )
    (claim,) = _read(answer, mime="audio/webm").claims

    assert claim.occurred_at is None
    assert claim.occurred_span == "around Easter"


def test_a_phrase_the_model_put_in_occurred_span_is_carried_through():
    answer = _answer(medications=[_medication(occurred_span="the week before the wedding")])
    (claim,) = _read(answer, mime="audio/webm").claims

    assert claim.occurred_at is None
    assert claim.occurred_span == "the week before the wedding"


# --- prompts ---------------------------------------------------------------


def _page(width=800, height=1000):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return images.prepare(buffer.getvalue(), long_edge=1280, page=1)


def test_the_prompt_hash_covers_the_schema_as_well_as_the_words():
    """A schema edit changes what was asked as surely as a wording change."""
    first = prompts.prompt_hash("s", "u", {"type": "object"})
    second = prompts.prompt_hash("s", "u", {"type": "object", "x": 1})

    assert first != second
    assert first.startswith("sha256:")


def test_the_prompt_hash_is_stable_across_runs():
    page = _page()
    assert prompts.build(page).prompt_hash == prompts.build(page).prompt_hash


def test_one_page_per_prompt():
    """Concatenating pages destroys per-page citation granularity."""
    built = prompts.build(_page(), total_pages=5)
    parts = built.messages[1]["content"]

    assert sum(1 for part in parts if part["type"] == "image_url") == 1
    assert "page 1 of 5" in parts[-1]["text"]


def test_the_prompt_tells_the_model_not_to_compute_or_diagnose():
    assert "COPY, NEVER COMPUTE" in prompts.SYSTEM
    assert "NEVER INVENT A DATE" in prompts.SYSTEM
    assert "Do not diagnose" in prompts.SYSTEM


# --- images ----------------------------------------------------------------


def test_a_large_photograph_is_downscaled_before_it_reaches_the_model():
    """The image, not the prompt, is what blows the context and the RAM budget."""
    buffer = io.BytesIO()
    Image.new("RGB", (4032, 3024), "white").save(buffer, format="JPEG")
    original = buffer.getvalue()

    prepared = images.prepare(original, long_edge=1280)

    assert max(prepared.width, prepared.height) == 1280
    assert prepared.data != original, "a derived working copy, never the original"
    assert len(prepared.data) < len(original)
    assert "downscale-to-1280px" in prepared.ops


def test_a_small_image_is_not_upscaled():
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")

    prepared = images.prepare(buffer.getvalue(), long_edge=1280)
    assert (prepared.width, prepared.height) == (400, 300)


def test_the_vision_token_estimate_tracks_pixel_count():
    small = _page(400, 400)
    large = _page(1200, 1200)

    assert large.vision_tokens > small.vision_tokens
    assert small.vision_tokens >= 1


def test_deskew_is_reported_as_not_run_rather_than_silently_skipped():
    """MODELS.md lists deskew. A gap that says so can be traced; one that
    does not looks like the model reading badly."""
    assert "deskew-not-implemented" in _page().ops
    assert "not implemented" in images.DESKEW_NOTE


def test_preprocessing_is_described_for_the_extraction_event():
    described = _page().describe()

    assert described["vision_tokens_estimate"] > 0
    assert described["source_width"] == 800
    assert "greyscale" in described["ops"]


def test_an_unopenable_image_is_reported_not_raised_through_prepare_artifact(tmp_path):
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"this is not a JPEG")

    document = images.prepare_artifact(path, "image/jpeg", 1280)
    assert not document.is_readable
    assert "does not open as an image" in document.unreadable


def test_a_recording_is_not_sent_to_the_vision_model(tmp_path):
    path = tmp_path / "note.webm"
    path.write_bytes(b"\x1a\x45\xdf\xa3")

    document = images.prepare_artifact(path, "audio/webm", 1280)
    assert not document.is_readable
    assert "speech model" in document.unreadable


# --- cross-verification ----------------------------------------------------


def _read_claim(value="5mg daily", subject="med:perindopril"):
    return validate.ReadClaim(
        subject=subject,
        subject_literal=subject.split(":", 1)[-1].replace("-", " ").title(),
        predicate="dose",
        value_literal=value,
        evidence_tier="prescriber-issued",
        confidence=0.9,
        source_span=value,
    )


def test_agreement_marks_a_claim_cross_verified():
    read = DeterministicRead("PERINDOPRIL 5mg daily, 30 tablets", METHOD_TEXT_LAYER)
    assert crossverify.verify(_read_claim(), read).state == crossverify.CROSS_VERIFIED


def test_spacing_differences_are_not_disagreement():
    """OCR inserts and drops spaces almost at random."""
    read = DeterministicRead("perindopril 5 mg daily", METHOD_OCR)
    assert crossverify.verify(_read_claim("5mg daily"), read).state == (
        crossverify.CROSS_VERIFIED
    )


def test_silence_is_uncorroborated_not_contradiction():
    """Tesseract reading nothing is the expected case, not a conflict.

    Treating it as disagreement would put every claim in the review queue, and
    inbox debt is what kills these systems.
    """
    read = DeterministicRead("...smudge...", METHOD_OCR)
    verification = crossverify.verify(_read_claim(), read)

    assert verification.state == crossverify.UNCORROBORATED
    assert not verification.is_contradicted


def test_a_missing_deterministic_reader_is_uncorroborated_with_a_reason():
    read = DeterministicRead(unavailable="pytesseract is not installed")
    verification = crossverify.verify(_read_claim(), read)

    assert verification.state == crossverify.UNCORROBORATED
    assert "not installed" in verification.reason


def test_two_readers_disagreeing_about_a_dose_is_a_contradiction():
    """The signal the whole dual-path architecture exists to surface."""
    read = DeterministicRead("PERINDOPRIL 10mg daily, 30 tablets", METHOD_TEXT_LAYER)
    verification = crossverify.verify(_read_claim("5mg daily"), read)

    assert verification.state == crossverify.CONTRADICTED
    assert verification.rival == "10mg"
    assert "neither is chosen" in verification.reason


def test_a_dose_belonging_to_a_different_drug_is_not_a_contradiction():
    """A script listing two medications must not manufacture a conflict."""
    read = DeterministicRead(
        "PERINDOPRIL 5mg daily, 30 tablets\n"
        "PARACETAMOL 500mg as needed\n"
        "ATORVASTATIN 40mg nocte",
        METHOD_TEXT_LAYER,
    )
    assert crossverify.verify(_read_claim("5mg daily"), read).state == (
        crossverify.CROSS_VERIFIED
    )


def test_a_different_unit_family_is_not_a_rival_reading():
    read = DeterministicRead("PERINDOPRIL 5mg daily, 30 tablets", METHOD_TEXT_LAYER)
    verification = crossverify.verify(_read_claim("5mg"), read)

    assert verification.state == crossverify.CROSS_VERIFIED
