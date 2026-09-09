"""Notes about things wrong with the log, and where they belong.

Settled decision: **anomalies live in the rebuild report, not the wiki**, when
they have no subject. A malformed payload or a shard naming violation is
integrity information about the log, not record content, and inventing a wiki
file for it would put log plumbing in the folder a clinician reads.

An anomaly that *does* resolve to a subject is different. "The payload for this
claim says its consequence is low and the code says high" is a fact about
``med:perindopril``, and the page for that entity is where someone asking whether
it is gated correctly will look — so it attaches there as well as to the report.

Anomalies are regenerable from the log and so are never persisted on their own,
but they must be surfaced rather than scrolling past: ``/api/health`` in phase 5
and the review inbox in phase 7 both report the count.
"""

from __future__ import annotations


class Anomaly(str):
    """One anomaly line, optionally tied to the subject it concerns.

    A ``str`` subclass rather than a dataclass so that every existing consumer —
    the rebuild report's JSON, the CLI, a substring check in a test — keeps
    treating it as the line of text it has always been, while the code that
    renders an entity can ask which subject it belongs to.

    ``cite`` is the artefact or event the note points at, so a page rendering one
    can footnote it like every other sentence.
    """

    subject_id: str | None
    cite: str | None

    def __new__(
        cls, message: str, subject_id: str | None = None, cite: str | None = None
    ) -> "Anomaly":
        note = super().__new__(cls, message)
        note.subject_id = subject_id
        note.cite = cite
        return note


def for_subject(notes: tuple[Anomaly | str, ...], subject_id: str) -> tuple[Anomaly, ...]:
    """The notes attached to one entity, in the order they were recorded."""
    return tuple(
        note
        for note in notes
        if isinstance(note, Anomaly) and note.subject_id == subject_id
    )
