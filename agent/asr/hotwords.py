"""Biasing Whisper on the record you already have.

``MODELS.md`` calls this "the single highest-leverage thing in the ASR path" and
it costs nothing: the medication names, allergy terms, problem names and
practitioner names are already in the projection, and passing them as biasing
context is what stops ``small`` turning "perindopril" into "peran doprol".

Why it matters more here than it would elsewhere: a mangled drug name is not a
typo in a transcript. It is an **unmatched entity**. "Peran doprol" resolves to
no medication in the record, so it becomes a second page for a drug that already
has one, and the medication list quietly shows the same tablet twice under two
spellings — which is precisely the duplication the salt-variant alias table
exists to prevent, arriving through a different door.

Two constraints, both from ``MODELS.md``, and the second is not optional:

**Cap the list.** An overlong bias prompt makes Whisper start inventing those
words in silence. For a drug name that is the worst output available — a
medication that was never mentioned, in a transcript, cited to a recording. The
cap is deliberately well below what would fit.

**Most recent first.** What someone is taking now is what they are most likely to
be talking about, and the tail of the list is what gets cut.

The list is built from the projection rather than from the event log directly, so
it sees names as the record actually holds them: aliased salt variants folded
onto their base drug, merged entities under the name the user chose, and stub
pages excluded. It also includes each salt variant's own literal wording, because
that is what is printed on the box someone is reading aloud from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..projection.entities import Entity

#: A few dozen, as ``MODELS.md`` puts it. Chosen low: the failure this cap
#: prevents is invention, and invention is worse than a missed correction.
HOTWORD_LIMIT = 48

#: Which entities are worth biasing on, in the order they are worth it. A drug
#: name is the costliest thing to mishear; a practitioner's name is a page in
#: the record but not a clinical fact, so it goes last.
KINDS = ("med", "allergy", "problem", "person")

#: Below this, a "word" is more likely to be picked up in ordinary speech than
#: to be the term. Biasing on "flu" costs more than it earns.
MIN_LENGTH = 4


def _recency(entity: "Entity") -> str:
    """A sortable string for how recently this entity was confirmed.

    Deliberately a string comparison over an ISO date, and deliberately not a
    clock read: this runs inside a transcription, and ordering by "how recent"
    must not become another place a wall-clock value leaks into something
    derived. Missing dates sort last rather than first.
    """
    for candidate in (entity.last_confirmed, entity.started):
        if candidate is not None and getattr(candidate, "value", None):
            return str(candidate.value)
    return ""


def _terms(entity: "Entity") -> Iterable[str]:
    """Every spelling of this entity worth biasing on.

    The entity's own name, plus the literal wording of any salt variant filed
    under it — ``levothyroxine sodium`` is what is printed on the box, and the
    box is what someone reads aloud.
    """
    yield entity.name
    for salt in getattr(entity, "salt_names", ()):
        literal = getattr(salt, "literal", None) or getattr(salt, "name", None)
        if literal:
            yield str(literal)


def collect(
    entities: Mapping[str, "Entity"], limit: int = HOTWORD_LIMIT
) -> tuple[str, ...]:
    """The biasing list for one transcription: most recent first, capped.

    Stub pages are skipped — a merged entity's stub records a decision, not a
    name anybody says — and so are terms shorter than :data:`MIN_LENGTH`.
    """
    ranked: list[tuple[int, str, str]] = []
    for entity in entities.values():
        if getattr(entity, "is_stub", False):
            continue
        kind = entity.subject.kind
        if kind not in KINDS:
            continue
        for term in _terms(entity):
            cleaned = " ".join(str(term).split())
            if len(cleaned) < MIN_LENGTH:
                continue
            ranked.append((KINDS.index(kind), _recency(entity), cleaned))

    # Three stable passes rather than one clever key. Read bottom-up: within a
    # kind, most recently confirmed first; within a date, alphabetical, so two
    # entities confirmed on the same day cannot swap places between runs and key
    # the same recording differently on two machines. An entity with no date at
    # all has the empty string, which a descending sort puts last — which is
    # where "we do not know when this was last confirmed" belongs.
    ranked.sort(key=lambda row: row[2].lower())
    ranked.sort(key=lambda row: row[1], reverse=True)
    ranked.sort(key=lambda row: row[0])

    seen: set[str] = set()
    chosen: list[str] = []
    for _, _, term in ranked:
        folded = term.lower()
        if folded in seen:
            continue
        seen.add(folded)
        chosen.append(term)
        if len(chosen) >= limit:
            break
    return tuple(chosen)
