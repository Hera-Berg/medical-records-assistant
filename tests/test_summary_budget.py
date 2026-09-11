"""The one-page budget, on its own.

Everything else about the sheet is tested through a real record. These are the
shapes that are awkward to reach that way and that the budget has to survive
anyway — chiefly the one that made it spin for ever.
"""

from __future__ import annotations

from agent.summary import budget
from agent.summary.model import Line, Section, Source
from agent.projection.citations import Citation

CITATION = Citation(key="a3f91c", text="Photograph, added 1 August 2026", target="raw/x.jpg")
SOURCE = Source(tier="prescriber-issued", when="1 August 2026", citation=CITATION)


def line(value: str, note: str | None = None) -> Line:
    return Line(label="Thing", value=value, note=note, sources=(SOURCE,))


def section(key: str, count: int, *, tall: bool = False, truncatable: bool = True):
    body = tuple(
        line(f"value {index}", "a note that wraps " * (6 if tall else 0) or None)
        for index in range(count)
    )
    return Section(key=key, heading=key.title(), lines=body, truncatable=truncatable)


def test_a_section_down_to_one_tall_row_does_not_spin():
    """The loop must always make progress or stop.

    A section holding a single very tall row was once chosen as the one to
    shorten, ahead of a section holding three short ones. Trimming a one-line
    section returns it unchanged, so the loop made no progress and ran at full
    CPU until it was killed. Terminating is the assertion; the timeout is the
    test.
    """
    fitted = budget.fit(
        (
            section("changes", 1, tall=True),
            section("medications", 30, truncatable=False),
            section("problems", 3),
        ),
        question="why?",
    )
    assert fitted.sections[0].lines  # kept its last row
    assert len(fitted.sections[1].lines) == 30  # never shortened


def test_nothing_is_ever_trimmed_below_one_row():
    fitted = budget.fit(
        (section("changes", 20), section("medications", 40, truncatable=False)),
        question="why?",
    )
    assert len(fitted.sections[0].lines) >= 1
    assert fitted.sections[0].omitted > 0
    assert len(fitted.sections[1].lines) == 40


def test_a_short_sheet_is_left_alone():
    fitted = budget.fit(
        (section("changes", 2), section("medications", 3, truncatable=False)),
        question="why?",
    )
    assert [len(s.lines) for s in fitted.sections] == [2, 3]
    assert not fitted.overflowed
    assert fitted.pages == 1


def test_the_estimate_never_comes_in_under_the_page_when_it_overflows():
    fitted = budget.fit((section("medications", 60, truncatable=False),))
    assert fitted.overflowed
    assert fitted.height_mm > budget.PAGE_MM
    assert fitted.pages > 1
