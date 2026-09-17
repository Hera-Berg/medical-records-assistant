"""The corpus, and the harness that scores against it.

The harness itself needs the box, so it is a command rather than a test. What is
tested here is everything around it: that the fixtures render to what they claim
to say, that the scoring cannot report a pass it has not earned, and that recall
is never traded for precision.

That last one deserves the attention it gets. ``MODELS.md``: "Report recall and
precision separately and never trade recall for precision." An eval that reports
one combined number can go up because precision improved while a medication went
missing, which is the failure the whole corpus exists to catch.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from agent.extract import evaluate, images, text as text_mod
from agent.extract.validate import Abstention, ReadClaim

from .fixtures import corpus


def _claim(subject, predicate, value):
    return ReadClaim(
        subject=subject,
        subject_literal=subject.split(":", 1)[-1].replace("-", " ").title(),
        predicate=predicate,
        value_literal=value,
        evidence_tier="prescriber-issued",
        confidence=0.9,
        source_span=value,
    )


def _perfect(fixture):
    return [_claim(e.subject, e.predicate, e.value) for e in fixture.expected]


# --- the fixtures themselves -----------------------------------------------


@pytest.mark.parametrize("fixture", corpus.for_phase(4), ids=lambda f: f.name)
def test_every_phase_four_fixture_renders(fixture):
    data = fixture.bytes()
    assert data, f"{fixture.name} produced no bytes"

    if fixture.mime.startswith("image/"):
        with Image.open(io.BytesIO(data)) as image:
            assert image.width > 200 and image.height > 200
    else:
        assert data.startswith(b"%PDF-")


@pytest.mark.parametrize("fixture", corpus.for_phase(4), ids=lambda f: f.name)
def test_every_fixture_renders_identically_twice(fixture):
    """A corpus that drifts makes a recall regression look like a re-render."""
    assert fixture.bytes() == fixture.bytes()


def test_the_pathology_pdf_has_a_real_text_layer():
    """The point of that fixture. A rasterised page would exercise OCR instead.

    "Pathology reports from patient portals almost always have one and running
    OCR over the rendered page instead makes them worse."
    """
    pdfplumber = pytest.importorskip("pdfplumber")
    fixture = corpus.by_name("pathology-pdf-with-text-layer")

    with pdfplumber.open(io.BytesIO(fixture.bytes())) as pdf:
        extracted = "\n".join(page.extract_text() or "" for page in pdf.pages)

    assert "TSH" in extracted
    assert "8.4" in extracted
    assert "HIGH" in extracted, "the out-of-range flag survives into the text layer"


def test_the_deterministic_reader_takes_the_text_layer_not_ocr(tmp_path):
    pytest.importorskip("pdfplumber")
    fixture = corpus.by_name("pathology-pdf-with-text-layer")
    path = tmp_path / "path.pdf"
    path.write_bytes(fixture.bytes())

    read = text_mod.read(path, "application/pdf")

    assert read.method == text_mod.METHOD_TEXT_LAYER
    assert "TSH" in read.text
    assert read.digest.startswith("sha256:")


def test_a_scanned_image_reports_the_ocr_path_as_unavailable(tmp_path):
    """Without tesseract installed. A reported state, never a crash."""
    fixture = corpus.by_name("printed-script-two-medications")
    path = tmp_path / "script.jpg"
    path.write_bytes(fixture.bytes())

    read = text_mod.read(path, "image/jpeg")

    assert not read.has_text or read.method == text_mod.METHOD_OCR
    if not read.has_text:
        assert read.unavailable, "says why, so it is not mistaken for agreement"


def _edge_energy(data: bytes) -> float:
    """How much sharp detail an image carries. Blur removes it.

    A far better proxy for "is this blurry" than file size, which the rotation
    inflates by expanding the canvas — the first version of this test asserted
    on bytes and failed for a reason that had nothing to do with blur.
    """
    from PIL import ImageFilter, ImageStat

    with Image.open(io.BytesIO(data)) as image:
        edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(edges).stddev[0]


def test_the_degraded_fixtures_really_are_degraded():
    """"Blurry, at an angle" has to actually be blurry and at an angle."""
    clean = corpus.by_name("printed-script-two-medications").bytes()
    blurry = corpus.by_name("blurry-handwritten-script-at-an-angle").bytes()

    with Image.open(io.BytesIO(blurry)) as image:
        rotated = image.size
    with Image.open(io.BytesIO(clean)) as image:
        square = image.size

    assert rotated != square, "rotation expands the canvas"
    assert _edge_energy(blurry) < _edge_energy(clean), "and the blur removes detail"


def test_the_clean_fixtures_are_not_accidentally_degraded():
    """The control: a fixture meant to be crisp has to be crisp."""
    assert _edge_energy(corpus.by_name("printed-script-two-medications").bytes()) > 1.0


def test_the_contradictory_pair_disagrees_about_exactly_one_thing():
    first = corpus.by_name("contradictory-dose-a")
    second = corpus.by_name("contradictory-dose-b")

    assert first.conflicts_with == second.name
    assert first.expected[0].subject == second.expected[0].subject
    assert first.expected[0].value != second.expected[0].value


def test_the_unreadable_fixture_expects_nothing_at_all():
    fixture = corpus.by_name("unreadable-photograph")

    assert fixture.expected == ()
    assert fixture.readable is False


def test_the_voice_fixtures_are_declared_and_deferred_not_forgotten():
    """MODELS.md lists them; phase 4 has no speech model to read them with."""
    deferred = [f.name for f in corpus.FIXTURES if f.phase > 4]

    assert "rambling-voice-note" in deferred
    assert "silence-and-a-cough" in deferred
    assert all(f.mime.startswith("audio/") for f in corpus.FIXTURES if f.phase > 4)


def test_every_medication_and_allergy_expectation_is_critical():
    """The categories MODELS.md requires 100% recall on."""
    for fixture in corpus.FIXTURES:
        for expected in fixture.expected:
            if expected.subject.startswith(("med:", "allergy:")):
                assert expected.critical, f"{fixture.name}: {expected.subject}"


# --- scoring ---------------------------------------------------------------


def _abstain(subject, field="frequency", family="medications"):
    return Abstention(family=family, field=field, reason="handwriting", subject=subject, subject_name=subject)


def test_a_perfect_read_is_all_correct():
    fixture = corpus.by_name("scanned-specialist-letter-skewed")
    report = evaluate.report([evaluate.score(fixture, _perfect(fixture))], "m")

    assert (report.correct, report.abstained, report.wrong) == (2, 0, 0)
    assert report.ok


def test_a_differently_spelled_dose_is_still_correct():
    """"5 mg daily" is a correct reading of "5mg daily", and must score as one."""
    fixture = corpus.by_name("printed-script-two-medications")
    claims = [
        _claim("med:perindopril-arginine", "dose", "5 mg daily"),
        _claim("med:atorvastatin", "dose", "20 MG at night"),
    ]
    result = evaluate.score(fixture, claims)

    assert result.correct == 2 and result.wrong == 0
    assert result.ok


def test_a_silent_miss_is_wrong():
    """No claim and no abstention: nothing reaches a person. The rule this exists for."""
    fixture = corpus.by_name("printed-script-two-medications")
    only_one = [_claim("med:perindopril-arginine", "dose", "5mg daily")]
    result = evaluate.score(fixture, only_one)

    assert result.correct == 1 and result.wrong == 1
    (missing,) = [s for s in result.expected if s.outcome == evaluate.WRONG]
    assert "silently missing" in missing.detail
    assert not result.ok


def test_an_honest_abstention_passes_the_gate():
    """"Could not be read — review manually" is visible, and that is a working system."""
    fixture = corpus.by_name("printed-script-two-medications")
    claims = [_claim("med:perindopril-arginine", "dose", "5mg daily")]
    result = evaluate.score(fixture, claims, abstentions=[_abstain("med:atorvastatin")])

    assert (result.correct, result.abstained, result.wrong) == (1, 1, 0)
    assert result.ok


def test_an_unnamed_abstention_covers_one_unread_medication_and_no_more():
    fixture = corpus.by_name("printed-script-two-medications")
    nameless = Abstention(family="medications", field="whole entry", reason="blurred")
    result = evaluate.score(fixture, [], abstentions=[nameless])

    assert (result.abstained, result.wrong) == (1, 1)


def test_a_page_reported_unreadable_is_abstained_not_wrong():
    fixture = corpus.by_name("blurry-handwritten-script-at-an-angle")
    result = evaluate.score(fixture, [], readable=False)

    assert (result.correct, result.abstained, result.wrong) == (0, 1, 0)
    assert result.ok


def test_a_wrong_dose_is_wrong_even_beside_an_abstention():
    """Asserting a false dose is never rescued by also saying something was unclear."""
    fixture = corpus.by_name("contradictory-dose-a")
    claims = [_claim("med:atorvastatin", "dose", "5mg at night")]
    result = evaluate.score(fixture, claims, abstentions=[_abstain("med:atorvastatin")])

    assert result.wrong == 1 and not result.ok
    assert "asserted" in result.expected[0].detail


def test_the_wrong_drug_is_wrong_not_a_false_positive():
    """Mefenamic acid off a metformin script is the wrong drug in a medical record."""
    fixture = corpus.by_name("blurry-handwritten-script-at-an-angle")
    claims = [_claim("med:mefenamic-acid", "dose", "500mg twice daily")]
    result = evaluate.score(fixture, claims)

    assert result.wrong == 2, "the metformin silently missing, and the invented drug asserted"
    assert not result.ok


def test_a_dose_filed_under_the_wrong_field_is_wrong():
    fixture = corpus.by_name("scanned-specialist-letter-skewed")
    claims = [
        _claim("med:perindopril", "started", "5mg daily"),
        _claim("allergy:penicillin", "route", "rash"),
    ]
    result = evaluate.score(fixture, claims)

    assert result.correct == 0
    assert result.wrong >= 4, "both expected claims missed, both misfiled claims asserted"


def test_a_leaked_identifier_is_wrong():
    fixture = corpus.by_name("contradictory-dose-a")
    claims = [_claim("med:atorvastatin-20mg-tablets", "dose", "20mg at night")]
    assert not evaluate.score(fixture, claims).ok


def test_a_name_claim_for_something_on_the_page_is_not_wrong():
    fixture = corpus.by_name("printed-script-two-medications")
    claims = _perfect(fixture) + [_claim("med:atorvastatin", "name", "Atorvastatin")]
    result = evaluate.score(fixture, claims)

    assert result.wrong == 0 and result.ok


def test_non_critical_extras_are_reported_but_not_gated():
    fixture = corpus.by_name("scanned-specialist-letter-skewed")
    claims = _perfect(fixture) + [_claim("problem:asthma", "name", "asthma")]
    result = evaluate.score(fixture, claims)

    assert result.ok
    assert any(s.key[0] == "problem:asthma" for s in result.unexpected)


def test_there_is_no_combined_score_to_hide_a_regression_behind():
    fixture = corpus.by_name("printed-script-two-medications")
    rendered = evaluate.report([evaluate.score(fixture, _perfect(fixture))], "m").to_dict()

    assert set(rendered["critical"]) >= {"correct", "abstained", "wrong"}
    for combined in ("f1", "f_score", "score", "accuracy", "average", "recall"):
        assert combined not in rendered


def test_claims_from_an_unreadable_page_are_a_fabrication():
    """The worst outcome in the corpus, and it fails the gate."""
    fixture = corpus.by_name("unreadable-photograph")
    invented = [_claim("med:perindopril", "dose", "5mg daily")]
    result = evaluate.score(fixture, invented)

    assert not result.ok
    assert result.wrong == 1


def test_an_unreadable_page_read_as_unreadable_passes():
    fixture = corpus.by_name("unreadable-photograph")
    assert evaluate.score(fixture, [], readable=False).ok


def test_an_error_is_wrong_not_abstained():
    """The system puts no harness exception in front of a person."""
    fixture = corpus.by_name("printed-script-two-medications")
    result = evaluate.score(fixture, [], error="the box was asleep")
    report = evaluate.report([result], "m")

    assert not result.ok and not report.ok
    assert (result.correct, result.abstained, result.wrong) == (0, 0, 2)
    assert any("the box was asleep" in line for line in result.describe())


def test_the_failure_message_names_what_was_wrong():
    fixture = corpus.by_name("printed-script-two-medications")
    report = evaluate.report([evaluate.score(fixture, [])], "m")
    rendered = "\n".join(report.describe())

    assert "FAILED" in rendered
    assert "Wrong must be 0" in rendered
    assert "med:atorvastatin" in rendered


def test_shares_come_from_exact_arithmetic():
    """A float would make two runs of an unchanged reader differ in the last digit."""
    fixture = corpus.by_name("printed-script-two-medications")
    report = evaluate.report([evaluate.score(fixture, _perfect(fixture)[:1])], "m")

    assert report.share(report.correct) == evaluate.Fraction(1, 2)
    assert isinstance(report.share(report.wrong), evaluate.Fraction)


# --- comparing two readers -------------------------------------------------


def test_a_reader_getting_more_wrong_becomes_a_required_review_case():
    """Never quietly accept the worse result because the other reader was unreachable."""
    fixture = corpus.by_name("printed-script-two-medications")
    remote = evaluate.report([evaluate.score(fixture, _perfect(fixture))], "9b")
    bundled = evaluate.report(
        [evaluate.score(fixture, _perfect(fixture)[:1])], "4b", tier="bundled"
    )

    worse = evaluate.compare_tiers(remote, bundled)

    assert len(worse) == 1
    assert "Route this artefact to a person" in worse[0]
    assert "never accept the worse reading" in worse[0]


def test_equal_readers_raise_nothing():
    fixture = corpus.by_name("printed-script-two-medications")
    both = evaluate.report([evaluate.score(fixture, _perfect(fixture))], "m")

    assert evaluate.compare_tiers(both, both) == []


# --- preprocessing the corpus ----------------------------------------------


def _have_pdf_rendering() -> bool:
    try:
        import pdfplumber  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


@pytest.mark.parametrize("fixture", corpus.for_phase(4), ids=lambda f: f.name)
def test_every_fixture_survives_preprocessing_within_the_image_budget(fixture, tmp_path):
    """The budget MODELS.md calls the most likely cause of an OOM crash."""
    path = tmp_path / f"{fixture.name}"
    path.write_bytes(fixture.bytes())

    document = images.prepare_artifact(path, fixture.mime, long_edge=1280)

    if fixture.mime == "application/pdf" and not _have_pdf_rendering():
        # Not a skip: this is the documented behaviour of an optional
        # dependency being absent, and it has to keep being a clear report
        # rather than a crash. Asserting it here is what keeps that true on a
        # machine that does have pdfplumber installed.
        assert not document.is_readable
        assert "pdfplumber is not installed" in document.unreadable
        assert "health-agent[documents]" in document.unreadable
        assert "the artefact stays queued" in document.unreadable
        return

    assert document.is_readable, document.unreadable
    for page in document.pages:
        assert max(page.width, page.height) <= 1280
        # 16k is the configured context cap. An image alone must not approach it.
        assert page.vision_tokens < 4000


def test_a_salt_name_and_a_plain_name_score_as_one_reading():
    """The corpus writes this fixture's perindopril with its salt, because the
    label does. A model that answered with the plain name has still read the
    page right — the projection files both under `med:perindopril` — and
    scoring it a miss would block a model swap for being correct, on the one
    metric MODELS.md says must never be traded away.
    """
    fixture = corpus.by_name("printed-script-two-medications")
    assert any(
        e.subject == "med:perindopril-arginine" for e in fixture.expected
    ), "the fixture still writes the salt the label carries"

    result = evaluate.score(
        fixture,
        [
            _claim("med:perindopril", "dose", "5mg daily"),
            _claim("med:atorvastatin", "dose", "20mg at night"),
        ],
    )

    assert result.wrong == 0 and result.correct == 2
    assert result.ok
