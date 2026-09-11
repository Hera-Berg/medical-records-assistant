"""The archival copy: one Markdown file in ``exports/``.

Invariant 6 — "the folder outlives the app" — decides the format. ``exports/``
holds Markdown because Markdown is one of the four things this record is allowed
to be made of, and because someone opening the folder in five years with no app,
no server and no Python reads it in a text editor without decoding anything.

It carries the **full footnote apparatus**, unlike the printed sheet. A footnote
here resolves to a relative path into ``raw/`` that still works with nothing
installed, which is the property that makes an export evidence rather than a
summary of evidence. The printed sheet names the tier and the document's date
inline instead, because a filesystem path on paper is noise to a clinician.

Rendered through :class:`agent.projection.render.Document`, so the wiki's own
rule applies here too: a line asserting something about the patient cannot be
written without a citation. The sheet's furniture — the dateline, the
invented-data warning, the patient's question, an empty section — goes through
:meth:`~agent.projection.render.Document.aside`, which is for statements the
document makes about itself.
"""

from __future__ import annotations

from ..projection.render import Document, Sentence
from .model import DEMO_WARNING, STANDFIRST, Line, Section, Summary

#: Heading for the patient's own question. Last, per the spec's fixed section
#: order, and phrased in the patient's voice like the rest of the sheet.
QUESTION_HEADING = "What I came to ask"


def _line_sentence(line: Line) -> Sentence:
    """One bullet: the fact, its qualifiers, and the source in brackets."""
    qualifiers = [part for part in (line.note, line.state) if part]
    qualifiers.append(line.source_text)
    text = f"**{line.label}** — {line.value_text} ({', '.join(qualifiers)})"
    return Sentence(text, [source.citation for source in line.sources])


def _section(document: Document, section: Section) -> None:
    document.heading(section.heading)
    if section.subnote:
        document.aside(section.subnote)
        # A bullet immediately under a block quote runs into it in most
        # renderers and reads as cramped in a plain text editor, which is where
        # this file is most likely to be read.
        document.blank()
    if not section.lines:
        # An absence with no artefact behind it. "Nothing changed" is a real
        # finding and the reader needs it, but there is no document that says
        # so, and inventing a citation for it would be worse than saying it
        # plainly as something the sheet asserts about itself.
        #
        # Unless something *was* dropped, in which case the absence is the page's
        # doing and not the record's, and saying "nothing changed" would be the
        # sheet asserting one over the other.
        note = section.omitted_note
        document.aside(f"{note}." if note else section.empty_note)
        return
    for line in section.lines:
        document.bullet(_line_sentence(line))
    note = section.omitted_note
    if note:
        document.aside(f"{note}.")


def render(summary: Summary) -> bytes:
    """The whole export, as bytes. UTF-8, ``\\n`` endings, one trailing newline."""
    document = Document()
    document.field_("id", summary.id)
    document.field_("prepared", summary.prepared.isoformat())
    if summary.label:
        document.field_("for", summary.label)
    if summary.since is not None:
        document.field_("since", summary.since.isoformat())
    document.field_("demo", summary.demo)
    if summary.sources:
        document.field_("sources", list(summary.sources))

    document.heading(summary.title, level=1)
    if summary.demo:
        # First, before anything that reads like a clinical fact. A synthetic
        # sheet mistaken for a real one is the only export this project can
        # produce that could actually cause harm, so the warning does not wait
        # its turn at the bottom of the page.
        document.aside(DEMO_WARNING)
    document.aside(f"{summary.dateline}. {STANDFIRST}")

    for section in summary.sections:
        _section(document, section)

    document.heading(QUESTION_HEADING)
    document.aside(
        summary.question
        if summary.question
        else "I did not write a question before this appointment."
    )

    if summary.overflowed:
        document.aside(
            "This sheet runs past one page. Nothing was dropped from my "
            "medications or allergies to make it fit — a missing medication is "
            "worse than a second page."
        )
    document.aside(summary.waiting.sentence)

    return document.render()
