"""One page, and what yields when the record will not fit on one.

"ONE PAGE, hard limit. A GP will not read three." That is a real constraint and
this module is where it is enforced, deterministically, with no browser
involved — the budget has to be computable by the same pure function that writes
the Markdown export.

**The limit is enforced by selection, not by clipping.** Nothing is cut off
mid-row and nothing is scaled down until it fits. Sections are shortened from
the end, by whole rows, and every row removed is counted and stated on the
sheet: "3 further entries in this section are in my record and not on this page."
A sheet that quietly shortens itself is worse than one that runs long, because
the reader cannot tell which one they are holding.

**Medications and allergies are never shortened.** This is the one place the
page limit gives way. A missed medication is invisible to the person reading the
sheet and is precisely the failure this project exists to prevent; a second page
is a mild annoyance. So when the two protected sections alone exceed the page,
the sheet runs to two pages, drops nothing, and says on its face that it has
done so. The eval harness's rule — never trade recall for precision on
medications, doses and allergies — is the same rule seen from the other end.

**The arithmetic is in millimetres, and it was calibrated rather than derived.**
An earlier version of this counted abstract "rows" and was wrong by an entire
page, because a row carrying a note under its value is two lines of two
different sizes and no single row height describes both. ``frontend/tools/sheet.py``
renders the print view through a real browser and reports how many pages came
out; the constants in :mod:`agent.summary.model` were tuned until that agreed
with :func:`fit`, and it is the tool to re-run if the stylesheet changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import HEADING_MM, Section, _lines

#: A4, and the margin the print stylesheet sets.
A4_HEIGHT_MM = 297
MARGIN_MM = 13

#: The printable height of one A4 page.
PAGE_MM = A4_HEIGHT_MM - 2 * MARGIN_MM

#: The title, the dateline over two lines, and the rule under them.
MASTHEAD_MM = 20.0

#: The invented-data warning: three bold lines in a box. Not a line, because a
#: sheet that could be mistaken for a real clinical document is the one export
#: capable of causing harm, and the box is what stops it being skimmed past.
DEMO_MM = 23.0

#: The rule above the footer and the sentence under it, which runs to two lines
#: whenever anything high-consequence is being withheld.
FOOTER_MM = 14.0

#: The patient's question is set a size up from the body — it is the one thing
#: on the page they wrote themselves, and it is never trimmed, only counted.
QUESTION_LINE_MM = 5.5
QUESTION_CHARS = 84

#: How close to the bottom of the page the sheet may come. The estimate is an
#: estimate; without slack, a sheet the arithmetic calls exactly one page comes
#: out of a real printer as one page and one orphaned line.
SLACK_MM = 6.0


@dataclass(frozen=True)
class Fit:
    """The sections as they will be printed, and whether they fitted."""

    sections: tuple[Section, ...]
    height_mm: float
    #: True when the protected sections alone exceed one page. The sheet prints
    #: this rather than hiding it.
    overflowed: bool

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.height_mm / PAGE_MM))


def question_height_mm(question: str) -> float:
    """What the patient's own question costs, heading included."""
    lines = max(1, _lines(question.strip(), QUESTION_CHARS))
    # The block is inset and has a millimetre of padding of its own.
    return HEADING_MM + 1.0 + lines * QUESTION_LINE_MM


def fit(sections: tuple[Section, ...], question: str = "", demo: bool = False) -> Fit:
    """Shorten the truncatable sections until the sheet is one page.

    Trimming takes a row at a time from whichever truncatable section is
    currently tallest, so two overlong sections shorten together rather than one
    being emptied to save the other. A section keeps one line while any budget
    remains: a heading followed only by "8 further entries are not on this page"
    tells the reader less than one row and a count do.

    There is deliberately no cap other than the page. An earlier draft trimmed
    "what changed" to ten rows before the budget was consulted at all, which hid
    two changes on a sheet with half a page of white space under it — a limit
    that cost something and bought nothing.
    """
    fixed = (
        MASTHEAD_MM
        + FOOTER_MM
        + SLACK_MM
        + (DEMO_MM if demo else 0.0)
        + question_height_mm(question)
    )
    protected = sum(s.height_mm for s in sections if not s.truncatable)
    budget = PAGE_MM - fixed - protected

    working = list(sections)
    while budget < _height_of(working) and _can_trim(working, budget):
        index = _tallest(working)
        section = working[index]
        working[index] = section.trimmed(len(section.lines) - 1)

    height = fixed + protected + _height_of(working)
    return Fit(sections=tuple(working), height_mm=height, overflowed=height > PAGE_MM)


def _height_of(sections: list[Section]) -> float:
    return sum(section.height_mm for section in sections if section.truncatable)


def _can_trim(sections: list[Section], budget: float) -> bool:
    """Whether anything is left to shorten.

    A truncatable section keeps its last line while the budget is positive; when
    the budget has gone entirely — the protected sections took the page on their
    own — even that goes, because there is nothing left to print it on.
    """
    floor = 1 if budget > 0 else 0
    return any(
        section.truncatable and len(section.lines) > floor for section in sections
    )


def _tallest(sections: list[Section]) -> int:
    """The truncatable section with the most millimetres; ties go to the later.

    Ties broken by position rather than arbitrarily, so the same input always
    produces the same sheet. The sections are already in the spec's fixed order,
    and the later one is the one further from the top of the page.
    """
    best = -1
    best_height = -1.0
    for index, section in enumerate(sections):
        if not section.truncatable:
            continue
        if section.height_mm >= best_height:
            best, best_height = index, section.height_mm
    return best


__all__ = ["Fit", "PAGE_MM", "fit", "question_height_mm"]
