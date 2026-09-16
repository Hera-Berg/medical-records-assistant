"""What this record is able to be asked about, in the words it holds.

Resolving "the perindopril" to ``med:perindopril`` is a lookup, not an
inference, and this is the table it looks in. It is built from the projection —
the same reduction the wiki is written from — so a name that is not in the
record cannot resolve to an entity, and a name the record spells three ways
resolves from all three.

Three spellings per entity, and each is there for a reason:

- the **entity name**, which is what the wiki page is titled;
- the **slug**, because someone may type ``dr-nguyen`` off a URL or a footnote;
- every **salt variant's literal wording**, because the pharmacy label said
  ``LEVOTHYROXINE SODIUM`` and that is what the person reading the box in their
  hand will type. The salt table aliases those onto one entity in the
  projection, and this is where that aliasing becomes reachable from a question.

Nothing here is fuzzy. There is no edit distance, no stemming and no synonym
list: a match is an exact match on a folded string, and anything looser belongs
in the one bounded expansion where the model proposes terms and code still
decides what they retrieve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ..projection.entities import Entity
from .text import fold, phrase_key


@dataclass(frozen=True)
class Vocabulary:
    """Folded phrase → subject id, for every name this record answers to."""

    #: Keyed by :func:`agent.query.text.phrase_key`, so ``"dr nguyen"`` and
    #: ``"dr-nguyen"`` are one key.
    names: Mapping[str, str]
    #: The longest phrase in the table, in words. Bounds the n-gram scan over a
    #: question so it never depends on the question's length times the record's.
    longest: int
    #: Subject id → the name the wiki gives it. What a sentence prints.
    display: Mapping[str, str]

    @classmethod
    def of(cls, entities: Mapping[str, Entity]) -> Vocabulary:
        names: dict[str, str] = {}
        display: dict[str, str] = {}
        longest = 1

        def add(phrase: object, subject_id: str) -> None:
            nonlocal longest
            key = phrase_key(phrase)
            folded = fold(phrase)
            if not key or not folded:
                return
            # First writer wins, and entities are visited in sorted order, so
            # two entities sharing a spelling resolve the same way on every
            # machine. A collision here is a merge question, not a tiebreak.
            names.setdefault(key, subject_id)
            longest = max(longest, len(folded.split()))

        for subject_id in sorted(entities):
            entity = entities[subject_id]
            # An aliased entity is not a destination: the salt variant's page
            # deliberately does not exist, and its wording is added below under
            # the entity it was filed on.
            if entity.merged_into:
                continue
            display[subject_id] = entity.name
            add(entity.name, subject_id)
            add(entity.subject.slug, subject_id)
            for variant in entity.salt_names:
                add(variant.literal, subject_id)
            for source in entity.merged_from:
                add(source.partition(":")[2], subject_id)

        return cls(names=names, longest=longest, display=display)

    def resolve(self, phrase: object) -> str | None:
        return self.names.get(phrase_key(phrase))

    def mentions(self, question: object) -> tuple[str, ...]:
        """Every entity the question names, longest phrase first.

        Scans n-grams from the longest phrase in the table downwards, so
        "perindopril arginine" resolves as one mention rather than as
        "perindopril" plus a word nothing claimed. A word already consumed by a
        longer match is not offered to a shorter one.
        """
        tokens = fold(question).split()
        if not tokens:
            return ()
        taken = [False] * len(tokens)
        found: list[str] = []
        for size in range(min(self.longest, len(tokens)), 0, -1):
            for start in range(0, len(tokens) - size + 1):
                if any(taken[start : start + size]):
                    continue
                subject_id = self.resolve(" ".join(tokens[start : start + size]))
                if subject_id is None:
                    continue
                for position in range(start, start + size):
                    taken[position] = True
                if subject_id not in found:
                    found.append(subject_id)
        return tuple(found)

    def names_for_prompt(self, limit: int) -> tuple[str, ...]:
        """The entity names, for the expansion prompt. Names only, never text.

        This is the whole of what a model is shown before it proposes search
        terms. The vault is synced by a third party and its contents are
        untrusted input; a document that has been tampered with must not be able
        to put words in front of the model that steer what gets retrieved next.
        These names come from the projection and are already the titles of wiki
        pages.
        """
        return tuple(sorted(self.display.values()))[:limit]


def kinds_present(entities: Iterable[Entity]) -> frozenset[str]:
    """Which of the four entity kinds this record actually holds."""
    return frozenset(entity.subject.kind for entity in entities if not entity.merged_into)


__all__ = ["Vocabulary", "kinds_present"]
