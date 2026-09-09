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

from agent.extract import crossverify, dates as xdates, images, prompts, schema, validate
from agent.extract.text import DeterministicRead, METHOD_OCR, METHOD_TEXT_LAYER
from agent.projection import tiers


def _claim(**overrides):
    base = {
        "subject_kind": "med",
        "subject_name": "Perindopril",
        "predicate": "dose",
        "value_literal": "5mg daily",
        "evidence_tier": "prescriber-issued",
        "occurred_at": None,
        "occurred_span": None,
        "dispense": None,
        "source_span": "PERINDOPRIL 5mg — one daily",
        "confidence": 0.9,
    }
    return {**base, **overrides}


def _answer(**overrides):
    base = {
        "artifact_kind": "prescription",
        "readable": True,
        "unreadable_reason": None,
        "document_date": None,
        "claims": [_claim()],
    }
    return {**base, **overrides}


# --- what the schema does not contain --------------------------------------


def test_the_schema_has_no_consequence_field_anywhere():
    """Stronger than stripping one: there is no way to express a tier.

    "If the model could label something low-consequence, a bad extraction could
    route itself around review."
    """
    rendered = json.dumps(schema.EXTRACTION_SCHEMA)

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
    names = _property_names(schema.EXTRACTION_SCHEMA)

    for computed in ("days_supply", "expected_exhaustion", "total", "exhaustion"):
        assert computed not in names
    assert "quantity" in names, "the spans themselves are still there"


def test_the_dispense_block_is_only_literal_spans():
    fields = schema._DISPENSE["properties"]

    assert set(fields) == {"quantity", "frequency", "repeats", "dose_units"}
    for spec in fields.values():
        assert spec["type"] == ["string", "null"], "spans, never numbers"


def test_the_schema_offers_only_kinds_that_have_somewhere_to_live():
    from agent.projection.subjects import KIND_DIRS

    assert set(schema.SUBJECT_KINDS) == set(KIND_DIRS)


def test_the_consequence_tier_is_looked_up_not_read_from_the_answer():
    read = validate.read(_answer(), mime="image/jpeg")
    (claim,) = read.claims

    assert claim.consequence == tiers.HIGH
    assert claim.consequence == tiers.consequence_for("med", "dose")


# --- validate, then reject -------------------------------------------------


def test_a_valid_answer_reads_cleanly():
    read = validate.read(_answer(), mime="image/jpeg")

    assert read.readable
    assert read.rejections == ()
    (claim,) = read.claims
    assert claim.subject == "med:perindopril"
    assert claim.value_literal == "5mg daily"


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
    read = validate.read(answer, mime="image/jpeg")

    assert read.claims == ()
    assert not read.readable
    assert any(expected in r.reason for r in read.rejections)


def test_an_unexpected_field_is_a_rejection_not_something_to_ignore():
    read = validate.read(_answer(consequence="low"), mime="image/jpeg")

    assert read.claims == ()
    assert any("unexpected field 'consequence'" in r.reason for r in read.rejections)


def test_one_bad_claim_does_not_discard_the_good_ones_beside_it():
    """A page's allergy should survive an unreadable line about a drug."""
    answer = _answer(
        claims=[
            _claim(subject_name="   "),
            _claim(subject_kind="allergy", subject_name="Penicillin",
                   predicate="reaction", value_literal="rash"),
        ]
    )
    read = validate.read(answer, mime="application/pdf")

    assert [c.subject for c in read.claims] == ["allergy:penicillin"]
    assert len(read.rejections) == 1
    assert read.rejections[0].index == 0


def test_an_unreadable_page_is_a_first_class_answer():
    read = validate.read(
        _answer(readable=False, unreadable_reason="the photograph is out of focus"),
        mime="image/jpeg",
    )

    assert not read.readable
    assert "out of focus" in read.unreadable_reason
    assert read.claims == ()


def test_an_out_of_range_confidence_is_rejected_not_clamped():
    read = validate.read(_answer(claims=[_claim(confidence=1.4)]), mime="image/jpeg")

    assert read.claims == ()
    assert any("at most 1" in r.reason for r in read.rejections)


def test_a_predicate_outside_the_vocabulary_is_rejected():
    read = validate.read(_answer(claims=[_claim(predicate="vibe")]), mime="image/jpeg")

    assert read.claims == ()
    assert any("must be one of" in r.reason for r in read.rejections)


# --- the tier a claim is allowed to carry ----------------------------------


def test_a_recording_can_never_be_prescriber_issued():
    """The tier is a fact about the artefact, known before the model runs."""
    read = validate.read(_answer(), mime="audio/webm")
    (claim,) = read.claims

    assert claim.evidence_tier == "patient-reported"
    assert any("held at patient-reported" in note for note in claim.notes)


def test_a_photograph_keeps_the_tier_the_model_judged():
    read = validate.read(_answer(), mime="image/jpeg")
    assert read.claims[0].evidence_tier == "prescriber-issued"


def test_a_recording_claiming_a_weaker_tier_is_left_alone():
    read = validate.read(
        _answer(claims=[_claim(evidence_tier="inferred")]), mime="audio/webm"
    )
    assert read.claims[0].evidence_tier == "inferred"


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
        claims=[
            _claim(
                occurred_at={
                    "value": "around Easter",
                    "precision": "month",
                    "uncertainty_days": 14,
                }
            )
        ]
    )
    (claim,) = validate.read(answer, mime="audio/webm").claims

    assert claim.occurred_at is None
    assert claim.occurred_span == "around Easter"


def test_a_phrase_the_model_put_in_occurred_span_is_carried_through():
    answer = _answer(claims=[_claim(occurred_span="the week before the wedding")])
    (claim,) = validate.read(answer, mime="audio/webm").claims

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
