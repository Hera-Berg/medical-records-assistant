"""The shape of a consultation summary, before anything renders it.

One page, handed to a clinician who has not asked for it. Everything in this
module exists to keep that sheet readable in the ten seconds it will actually
get, and honest about its own limits for the reader who picks it up off a desk
three weeks later with no idea what it is.

Three properties are structural here rather than left to the renderers, because
there are two renderers — Markdown into ``exports/`` and standalone HTML — and a
property enforced in one of them is a property the other will eventually lose.

**Every line names its source.** A :class:`Line` cannot be built without at
least one :class:`Source`, in the same way a wiki :class:`~agent.projection.render.Sentence`
cannot be built without a citation. The sheet's whole claim on a clinician's
trust is that each row says where it came from.

**The evidence tier is a word.** Not a colour, not a symbol. This document is
printed, photocopied, and read by people who do not share the app's palette, and
a tier encoded in hue is gone exactly where it matters most.

**The sheet speaks in the patient's own voice.** "What I came to ask", "waiting
for me to confirm". It is the patient's document about the patient's record,
carried in by the patient — not a clinic's note about them — and the first
person is what stops it reading as an institutional record it is not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from ..errors import ProjectionError
from ..projection.citations import Citation

#: How an evidence tier is written on the sheet.
#:
#: The app's own words, shifted into the first person where the app addresses
#: the patient as "you" — the reader of this sheet is a clinician and the voice
#: is the patient's. A tier whose word needs a legend is a tier the person
#: holding the sheet cannot use, so there are no abbreviations here.
TIER_WORDS: dict[str, str] = {
    "prescriber-issued": "Prescription",
    "lab-issued": "Lab result",
    "device-recorded": "Device",
    "patient-reported": "Told by me",
    "inferred": "Suggested",
}

#: What a status word says on paper. Same words the interface uses, so a patient
#: who checked the screen before the appointment reads the same thing on the
#: sheet.
STATUS_WORDS: dict[str, str] = {
    "active": "",
    "stale": "Needs confirming",
    "stopped": "Stopped",
    "conflicted": "Sources disagree",
}

#: Said beside a value the consequence gate let in without a tap — a medium
#: claim that sat out its review week. The sheet may only show what the record
#: holds, and this is the record holding something the user never agreed to, so
#: it is shown *and* marked rather than either hidden or passed off as agreed.
UNCONFIRMED_WORD = "Not confirmed by me"

# Everything below is millimetres on A4, measured against the rendered sheet
# rather than reasoned about. ``frontend/tools/sheet.py`` renders the print
# view through a real browser and reports both the measured height and the page count, and
# these were calibrated until the two agreed. A budget in abstract "rows" was
# the first attempt and was wrong by a whole page, because a row carrying a note
# under its value is two lines and a section heading costs twice what a row does.

#: One line inside a table cell. Every line in a cell costs this, including the
#: smaller note and source lines: the cell's line box is set by the strut of its
#: own 10.5pt type, so shrinking the text inside it does not shrink the line.
#: This is the measurement that a first-principles estimate gets wrong.
LINE_MM = 5.0

#: One line of a small paragraph outside a table, where the smaller type does
#: govern the leading.
SMALL_LINE_MM = 4.2

#: Cell padding, top and bottom, plus the hairline rule under each row.
ROW_PADDING_MM = 2.1

#: A section heading: its top margin, the line itself, its bottom margin and
#: the rule under it.
HEADING_MM = 10.2

#: A section with nothing in it: one body-size line with a little padding.
EMPTY_MM = 7.0

#: The air above a "what this left out" note and below a section's own
#: explanation, so neither reads as another row of the table.
NOTE_MARGIN_MM = 1.2

#: How many characters fit on one line of each column, at these sizes and the
#: column widths in :data:`agent.summary.html.STYLE`.
VALUE_CHARS = 40
NOTE_CHARS = 48
SOURCE_CHARS = 30


def _lines(text: str, per_line: int) -> int:
    return max(1, math.ceil(len(text) / per_line)) if text else 0


def tier_word(tier: str | None) -> str:
    """The sheet's word for an evidence tier, or "Unsourced" for none.

    Never an empty string. A blank where the tier should be reads as an
    oversight in the layout; "Unsourced" reads as a fact about the row, which is
    what it is.
    """
    if not tier:
        return "Unsourced"
    return TIER_WORDS.get(tier, tier)


@dataclass(frozen=True)
class Source:
    """Where one line came from: the tier, the document's own date, the link.

    ``when`` is ``artifact_ts`` — the date on the document — and never any of
    the other three timestamps. A source line reading "Prescription · 4 June
    2026" is a statement about the piece of paper, and substituting the day it
    was photographed or the day it landed in the vault is the collapse the
    four-timestamp rule exists to prevent. Where the document carries no date,
    this is ``None`` and the row says the tier alone.
    """

    tier: str | None
    when: str | None
    citation: Citation
    #: The artefact's short hash, for the screen's tap-to-open. ``None`` for a
    #: citation that points at the log rather than at a document.
    artifact: str | None = None
    #: Where the original sits, relative to ``exports/``. This is what makes the
    #: exported HTML still open its own evidence with no server running — the
    #: folder is the record, and a sheet that can only be followed through a
    #: running app is a sheet that stops being evidence when the app dies.
    rel: str | None = None

    @property
    def word(self) -> str:
        return tier_word(self.tier)

    @property
    def text(self) -> str:
        """"Prescription · 4 June 2026", the whole source in one span."""
        return f"{self.word} · {self.when}" if self.when else self.word

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_word": self.word,
            "when": self.when,
            "text": self.text,
            "artifact": self.artifact,
            "rel": self.rel,
            "citation": {
                "key": self.citation.key,
                "text": self.citation.text,
                "target": self.citation.target,
                "resolved": self.citation.resolved,
            },
        }


@dataclass(frozen=True)
class Line:
    """One row of the sheet, and the evidence under it.

    Refuses to exist without a source, for the reason :class:`Sentence` refuses
    to exist without a citation: the alternative is writing rows freely and
    checking them afterwards, which fails open on the one row that matters.
    """

    label: str
    value: str
    note: str | None = None
    state: str | None = None
    sources: tuple[Source, ...] = ()
    subject_id: str | None = None
    #: A second value for the same slot, where two sources disagree and the
    #: projection declined to pick. Both are printed; neither is chosen.
    alternatives: tuple[str, ...] = ()
    #: Every claim event id whose *content* appears on this row, including a
    #: "was" value shown for context whose own document the row does not cite.
    #: This is the integrity list, not the citation list: a later rejection of
    #: any of these withdraws the stored sheet rather than re-serving it, and
    #: that has to cover every value printed, not only the ones footnoted.
    claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ProjectionError("a summary line with no label cannot be printed")
        if not self.sources:
            raise ProjectionError(
                f"refusing to print an unsourced line on a consultation summary: "
                f"{self.label!r}. Every row names the document it came from."
            )

    @property
    def source_text(self) -> str:
        """The source column as one string, identical entries folded.

        Two documents written on the same day render the same words. Printing
        them twice says nothing the once did not and reads as a fault; the
        footnotes in the Markdown export and the links in the HTML still carry
        one entry per document, so nothing is lost by folding the text.
        """
        seen: list[str] = []
        for source in self.sources:
            if source.text not in seen:
                seen.append(source.text)
        return "; ".join(seen)

    @property
    def value_text(self) -> str:
        """The value, with any unchosen alternative beside it.

        Both readings of a conflict, joined by "or" and never by a comma: a
        comma reads as a list of things that are all true, and the whole point
        of a conflicted slot is that exactly one of them is and the record does
        not know which.
        """
        if not self.alternatives:
            return self.value
        return " or ".join((self.value, *self.alternatives))

    @property
    def height_mm(self) -> float:
        """How tall this row prints, in millimetres.

        An estimate from character counts rather than a measurement: the budget
        has to be computable in a pure function with no browser, on the machine
        that writes the Markdown export. It is checked against a real A4 render
        — see ``frontend/tools/sheet.py`` — and the constants it rests on were calibrated
        there rather than guessed.

        Both columns are measured and the taller wins, because a row with one
        short value and two sources is as tall as its sources.
        """
        extras = " · ".join(part for part in (self.note, self.state) if part)
        said = _lines(self.value_text, VALUE_CHARS) + _lines(extras, NOTE_CHARS)
        seen: list[str] = []
        for source in self.sources:
            if source.text not in seen:
                seen.append(source.text)
        sourced = sum(_lines(text, SOURCE_CHARS) for text in seen)
        return ROW_PADDING_MM + max(said, sourced, 1) * LINE_MM

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "value_text": self.value_text,
            "source_text": self.source_text,
            "note": self.note,
            "state": self.state,
            "subject_id": self.subject_id,
            "alternatives": list(self.alternatives),
            "claims": list(self.claims),
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass(frozen=True)
class Section:
    """One block of the sheet, in the order the spec fixes.

    ``omitted`` is what the budget took off the end. It is never silent: a
    section that dropped rows says how many and where the rest are. A sheet that
    quietly shortens itself is worse than a sheet that runs long, because the
    reader cannot tell which one they are holding.
    """

    key: str
    heading: str
    lines: tuple[Line, ...] = ()
    #: Rows the budget removed. Only ever non-zero on a truncatable section.
    omitted: int = 0
    #: A sentence under the heading saying how the section was arrived at,
    #: where that is not obvious from the heading — chiefly what "since" was
    #: measured against. A section that reports changes over an unnamed window
    #: is asserting a window it never disclosed.
    subnote: str = ""
    #: What to print when there is nothing at all. An absence is a finding here:
    #: "nothing changed" is exactly what a clinician wants to be told.
    empty_note: str = ""
    #: Whether the budget may shorten this section. Medications and allergies
    #: are never truncatable: a missed medication is invisible, and it is the
    #: failure this whole project exists to prevent. The page limit yields to
    #: that, not the other way round.
    truncatable: bool = True

    @property
    def height_mm(self) -> float:
        """The whole block: its heading, its rows, and anything it says about
        what it left out."""
        total = HEADING_MM
        if self.subnote:
            total += _lines(self.subnote, NOTE_CHARS) * SMALL_LINE_MM + NOTE_MARGIN_MM
        if self.lines:
            total += sum(line.height_mm for line in self.lines)
        else:
            total += EMPTY_MM
        note = self.omitted_note
        if note:
            total += _lines(note, NOTE_CHARS) * SMALL_LINE_MM + NOTE_MARGIN_MM
        return total

    def trimmed(self, keep: int) -> "Section":
        """This section with at most *keep* lines, counting what it dropped."""
        keep = max(0, keep)
        if keep >= len(self.lines):
            return self
        return Section(
            key=self.key,
            heading=self.heading,
            lines=self.lines[:keep],
            omitted=self.omitted + (len(self.lines) - keep),
            subnote=self.subnote,
            empty_note=self.empty_note,
            truncatable=self.truncatable,
        )

    @property
    def omitted_note(self) -> str | None:
        if not self.omitted:
            return None
        thing = "entry" if self.omitted == 1 else "entries"
        return (
            f"{self.omitted} further {thing} in this section are in my record and "
            f"not on this page"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "heading": self.heading,
            "subnote": self.subnote,
            "lines": [line.to_dict() for line in self.lines],
            "omitted": self.omitted,
            "omitted_note": self.omitted_note,
            "empty_note": self.empty_note,
        }


@dataclass(frozen=True)
class Waiting:
    """What the record is holding back from this sheet, and whether it matters.

    A count on its own does not tell a clinician whether the gap is load-bearing.
    ``high`` and ``kinds`` are what turn "4 entries are waiting" into "2 of them
    are about medications, so the list above may be incomplete" — which is the
    difference between disclosing a limit and mentioning one.

    ``kinds`` names **categories, never subjects**. A high-consequence item is by
    definition something the user has not agreed to, and printing "waiting:
    Warfarin" on a sheet a clinician reads would assert the thing the gate exists
    to withhold. The category says the list may be incomplete without asserting
    anything about the patient.
    """

    total: int = 0
    high: int = 0
    kinds: tuple[str, ...] = ()

    #: Subject kind -> the word used in the disclosure sentence, in the order
    #: a clinician reads the sheet rather than alphabetically. Medications
    #: first: that is the list a withheld entry most changes the meaning of.
    KIND_WORDS = {
        "med": "medications",
        "allergy": "allergies",
        "problem": "problems",
        "person": "practitioners",
    }

    @property
    def sentence(self) -> str:
        if self.total == 0:
            return "Nothing in my record is waiting for me to confirm."
        entry = "entry is" if self.total == 1 else "entries are"
        base = (
            f"{self.total} {entry} waiting for me to confirm and "
            f"{'is' if self.total == 1 else 'are'} not on this page."
        )
        if not self.high:
            return base
        order = list(self.KIND_WORDS)
        words = [
            self.KIND_WORDS.get(kind, kind)
            for kind in sorted(
                self.kinds,
                key=lambda k: order.index(k) if k in order else len(order),
            )
        ]
        which = "One of them is" if self.high == 1 else f"{self.high} of them are"
        return (
            f"{base} {which} high-consequence, concerning {_join(words)}, so those "
            f"lists above may be incomplete."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "high": self.high,
            "kinds": list(self.kinds),
            "sentence": self.sentence,
        }


def _join(words: Sequence[str]) -> str:
    """"medications and allergies" — a fixed joiner, never a locale's."""
    items = list(dict.fromkeys(words))
    if not items:
        return "entries"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


#: What a sheet says about itself, wherever it is found. Both renderers print
#: this and a test asserts both do: someone finds this on a desk three weeks
#: later with no context, and a clinical-looking page with no provenance is the
#: thing that causes harm.
DATELINE = "Prepared {prepared} from a patient-held record"

#: The second half of the dateline: what the sheet is for and what it is not.
#: "Reports and cites, does not interpret" is invariant 7 written where the
#: reader is, rather than kept as a design note in a file nobody outside this
#: repository reads.
STANDFIRST = (
    "Every line names the document it came from. This page reports and cites; "
    "it does not interpret, and it is not clinical advice."
)

#: Printed on any sheet generated from a demo vault, in both formats. A
#: synthetic sheet that reads as a real clinical document is the one export this
#: project could produce that would actually cause harm, so the warning is not
#: a footnote and is not conditional on the renderer remembering it — see
#: :func:`agent.summary.markdown.render` and :func:`agent.summary.html.render`,
#: which both take it from here.
DEMO_WARNING = (
    "INVENTED DATA — this sheet was generated from a demonstration record. "
    "Every medication, allergy and date on it is fictional. It is not about a "
    "real person and must not be used for any clinical purpose."
)


@dataclass(frozen=True)
class Summary:
    """One consultation sheet: what is on it, and what it is."""

    id: str
    prepared: date
    prepared_words: str
    #: What the visit is for, as the user typed it — "cardiology". Becomes part
    #: of the export filename, which is why it is slugged separately rather than
    #: being reconstructed from this.
    label: str
    #: The question the patient came with. Typed by them, never generated, and
    #: printed verbatim. An empty question is allowed: a sheet with nothing to
    #: ask is an ordinary thing to want.
    question: str
    since: date | None
    since_ts: str | None
    #: How the reference point was arrived at, printed in the section heading —
    #: "your last summary, 4 June 2026" or "no previous summary — the last 3
    #: months". The reader has to be able to see what "changed" was measured
    #: against, or the section asserts a window it never states.
    since_reason: str
    sections: tuple[Section, ...] = ()
    waiting: Waiting = field(default_factory=Waiting)
    demo: bool = False
    #: Claim event ids every line on this sheet rests on. Recorded in the event
    #: so that a later rejection of any of them can be detected, and the stored
    #: sheet withdrawn rather than served again. See :mod:`agent.summary.store`.
    cited: tuple[str, ...] = ()
    #: Artefact short hashes, for the export's frontmatter `sources:` list.
    sources: tuple[str, ...] = ()
    #: Set when the budget could not fit the sheet on one page without dropping
    #: a medication or an allergy, which it will never do. Printed on the sheet.
    overflowed: bool = False
    #: The whole sheet's estimated printed height, furniture included. Stored
    #: rather than recomputed from the sections: the sections are only part of
    #: the page, and a property that summed them alone reported 193mm for a
    #: sheet that measured 261mm — which is the kind of number that looks
    #: plausible and silently stops being a page limit at all.
    height_mm: float = 0.0

    @property
    def dateline(self) -> str:
        return DATELINE.format(prepared=self.prepared_words)

    @property
    def title(self) -> str:
        return f"Health record — prepared {self.prepared_words}"

    def section(self, key: str) -> Section | None:
        for section in self.sections:
            if section.key == key:
                return section
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prepared": self.prepared.isoformat(),
            "prepared_words": self.prepared_words,
            "title": self.title,
            "dateline": self.dateline,
            "standfirst": STANDFIRST,
            "demo": self.demo,
            "demo_warning": DEMO_WARNING if self.demo else None,
            "label": self.label,
            "question": self.question,
            "since": self.since.isoformat() if self.since else None,
            "since_ts": self.since_ts,
            "since_reason": self.since_reason,
            "sections": [section.to_dict() for section in self.sections],
            "waiting": self.waiting.to_dict(),
            "cited": list(self.cited),
            "sources": list(self.sources),
            "overflowed": self.overflowed,
        }


#: A section heading: its top margin, the line itself, the rule under it.
HEADING_MM = 8.0
