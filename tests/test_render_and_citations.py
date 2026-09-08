"""Citations, literals, and the shape of a generated page.

"A sentence in the wiki without a footnote is a bug." That is enforced
structurally — a ``Sentence`` cannot be built without a citation — and again over
the rendered bytes, so this checks both the mechanism and the result.

The other rule under test here is that the wiki quotes its sources. Values are
rendered as the literal span the source used, never as a reformatted number:
``5mg`` stays ``5mg``, and ``2.5`` never becomes ``2.5000000000000004`` on its
way through a float.
"""

from __future__ import annotations

import pytest

from agent import projection
from agent.errors import ProjectionError
from agent.projection import subjects, values
from agent.projection.citations import Citation
from agent.projection.render import Document, Sentence, frontmatter, quote_yaml

from .conftest import claim, confirm, correct, ingested, on_day

DEVICE = "laptop-a1b2"
AS_OF = "2026-09-30T00:00:00Z"
CITE = Citation("a3f91c", "Photograph, added to the record 2 September 2026", "raw/x.jpg")


def _rich_projection():
    """One of everything, so the citation sweep has something to sweep."""
    events = [
        ingested(DEVICE, "a3f91c", artifact_ts="2026-06-04T00:00:00Z"),
        ingested(DEVICE, "77b210", mime="application/pdf"),
    ]
    dose = claim(
        DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
        occurred={"value": "2026-06-04"},
        dispense={"quantity": "30 tablets", "frequency": "one daily", "repeats": "no repeats"},
    )
    events += [dose, confirm(DEVICE, dose.id, ts=on_day(2, hour=10))]

    misread = claim(DEVICE, "med:atorvastatin", "dose", "2mg daily", ts=on_day(3),
                    artifact="77b210", occurred={"value": "2026-08-15"})
    events += [misread, correct(DEVICE, value="20mg daily", target=misread.id, ts=on_day(4))]
    reread = claim(DEVICE, "med:atorvastatin", "dose", "40mg daily", ts=on_day(5),
                   artifact="77b210", occurred={"value": "2026-08-15"})
    events += [reread, confirm(DEVICE, reread.id, ts=on_day(5, hour=10))]

    first = claim(DEVICE, "med:metformin", "dose", "500mg twice daily", ts=on_day(6),
                  occurred={"value": "2026-07-01"})
    second = claim(DEVICE, "med:metformin", "dose", "850mg twice daily", ts=on_day(6, hour=11),
                   artifact="77b210", occurred={"value": "2026-07-01"})
    events += [
        first, confirm(DEVICE, first.id, ts=on_day(7)),
        second, confirm(DEVICE, second.id, ts=on_day(7, hour=11)),
    ]
    events.append(claim(DEVICE, "allergy:penicillin", "reaction", "rash", ts=on_day(8)))
    return projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)


def test_every_sentence_in_every_generated_page_has_a_citation():
    result = _rich_projection()
    assert result.files

    for rel, data in result.files.items():
        text = data.decode("utf-8")
        body = text.split("---\n", 2)[2]
        for line in body.split("\n"):
            if not line.strip() or line.startswith(("#", "[^")):
                continue
            assert "[^" in line, f"{rel} has an uncited line: {line!r}"


def test_every_citation_marker_resolves_to_a_definition():
    import re

    for rel, data in _rich_projection().files.items():
        text = data.decode("utf-8")
        defined = set(re.findall(r"^\[\^([^\]]+)\]:", text, flags=re.MULTILINE))
        used = set(re.findall(r"\[\^([^\]]+)\](?!:)", text))
        assert used <= defined, f"{rel} cites {used - defined} with no footnote"
        assert defined <= used, f"{rel} defines unused footnotes {defined - used}"


def test_an_uncited_sentence_cannot_be_constructed():
    with pytest.raises(ProjectionError, match="uncited"):
        Sentence("The dose was increased", [])


def test_a_line_appended_without_a_citation_is_caught_at_render():
    """Belt and braces: the structural guard is not the only guard."""
    document = Document()
    document.field_("id", "med:x")
    document.body.append("A sentence someone added by hand.")
    with pytest.raises(ProjectionError, match="no citation"):
        document.render()


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("5mg daily", "dose: 5mg daily"),
        ("2.5 mg twice daily", "dose: 2.5 mg twice daily"),
        ("0.5mg nocte", "dose: 0.5mg nocte"),
    ],
)
def test_the_literal_span_is_what_gets_rendered(literal, expected):
    proposed = claim(DEVICE, "med:perindopril", "dose", literal, ts=on_day(2))
    events = [ingested(DEVICE), proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]
    result = projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)
    page = result.files["wiki/medications/perindopril.md"].decode("utf-8")

    assert expected in page
    assert "5.0mg" not in page
    assert "0000000" not in page


def test_two_spellings_of_one_dose_are_not_a_conflict():
    """Normalised for comparison, literal for rendering."""
    structured = claim(DEVICE, "med:perindopril", "dose",
                       {"amount": 5, "unit": "mg", "frequency": "once daily"},
                       ts=on_day(2), occurred={"value": "2026-06-04"})
    written = claim(DEVICE, "med:perindopril", "dose", "5 mg once a day", ts=on_day(3),
                    artifact="77b210", occurred={"value": "2026-06-04"})
    events = [
        ingested(DEVICE), ingested(DEVICE, "77b210"),
        structured, confirm(DEVICE, structured.id, ts=on_day(4)),
        written, confirm(DEVICE, written.id, ts=on_day(5)),
    ]
    result = projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)
    slot = result.entities["med:perindopril"].slots["dose"]

    assert slot.resolution == "settled"
    assert slot.winner.value.literal == "5 mg once a day"


def test_a_value_that_would_need_a_float_is_never_written_as_one():
    assert values.parse({"amount": 2.5, "unit": "mg"}).literal == "2.5mg"
    assert values.parse({"amount": 5.0, "unit": "mg"}).literal == "5mg"
    assert values.parse(0.1).literal == "0.1"


def test_a_subject_can_never_write_outside_the_wiki():
    for hostile in (
        "med:../../etc/passwd",
        "med:..",
        "med:/etc/passwd",
        "med:a/b",
        "../../etc:passwd",
        "med:a\\b",
    ):
        assert subjects.parse(hostile) is None, hostile

    assert subjects.parse("med:perindopril").rel_path == "wiki/medications/perindopril.md"


def test_a_citation_with_no_artefact_is_reported_rather_than_dropped():
    """A missing artefact is a visible hole, not a quietly uncited sentence."""
    proposed = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2),
                     artifact="deadbe")
    events = [proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]
    result = projection.project(sorted(events, key=lambda e: e.sort_key), AS_OF)

    assert result.unresolved_citations == ("deadbe",)
    page = result.files["wiki/medications/perindopril.md"].decode("utf-8")
    assert "no ingest event describes it" in page
    assert "[^deadbe]" in page


def test_footnotes_name_which_timestamp_they_are_quoting():
    events = [
        ingested(DEVICE, "a3f91c", ts="2026-09-02T09:14:03Z",
                 captured_ts="2026-08-30T11:00:00Z", artifact_ts="2026-06-04T00:00:00Z"),
    ]
    proposed = claim(DEVICE, "med:perindopril", "dose", "5mg daily", ts=on_day(2))
    events += [proposed, confirm(DEVICE, proposed.id, ts=on_day(3))]
    page = projection.project(
        sorted(events, key=lambda e: e.sort_key), AS_OF
    ).files["wiki/medications/perindopril.md"].decode("utf-8")

    assert "photographed 30 August 2026" in page
    assert "added to the record 2 September 2026" in page
    assert "document dated 4 June 2026" in page


@pytest.mark.parametrize(
    "value,expected",
    [
        ("perindopril", "perindopril"),
        ("5mg daily", "5mg daily"),
        ("true", '"true"'),
        ("null", '"null"'),
        ("12", '"12"'),
        ("", '""'),
        ("- leading dash", '"- leading dash"'),
        ("key: value", '"key: value"'),
        (" padded ", '" padded "'),
        ('say "hi"', '"say \\"hi\\""'),
    ],
)
def test_frontmatter_quotes_only_what_would_otherwise_change_meaning(value, expected):
    assert quote_yaml(value) == expected


def test_frontmatter_writes_an_unknown_date_as_an_explicit_null():
    lines = frontmatter([("last_confirmed", None), ("sources", ["a3f91c", "77b210"])])
    assert "last_confirmed: null" in lines
    assert "sources: [a3f91c, 77b210]" in lines


def test_frontmatter_refuses_a_float():
    with pytest.raises(ProjectionError, match="float"):
        frontmatter([("dose", 2.5)])
